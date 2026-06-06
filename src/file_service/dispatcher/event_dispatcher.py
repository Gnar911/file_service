from __future__ import annotations

import threading
from typing import Any, Callable

from lw.logger_setup import LOG
from lw.observer import ObservableEvent
from ..api.application_events import (
    DBCLoadedEvent,
    DecodeCompletedEvent,
    DecodeFileNotFoundEvent,
    DecodeProgressEvent,
    DecodeStatusEvent,
    DecodeSignalListEvent,
    DecodeStartedEvent,
    DecoderStatusEvent,
    FileServiceStateEvent,
    FileWorkerHealthEvent,
    FileWorkerRawStatusEvent,
    ParserStatusEvent,
    RecorderStatusEvent,
)

from file_service.parser.native.can_parser_api import (
    PARSER_STATUS_DONE,
    PARSER_STATUS_ERROR,
    PARSER_STATUS_RUNNING,
)
from dataclasses import dataclass
import sys
from PySide6.QtCore import QObject
from .qt_object import IPCWakeup

IS_WINDOWS = sys.platform == "win32"

if IS_WINDOWS:
    from PySide6.QtCore import QWinEventNotifier
else:
    from PySide6.QtCore import QSocketNotifier

@dataclass
class ParserWorkerRegistration:

    file_path: str

    wakeup: Any

    callback: Callable[[], int]

    notifier: Any | None = None

    kind: str = "parser"

class FileServiceDispatcher:
    def __init__(self) -> None:
        self.event_on_service_state_changed = ObservableEvent(FileServiceStateEvent)
        self.event_on_decoder_status_changed = ObservableEvent(DecoderStatusEvent)
        self.event_on_decode_started = ObservableEvent(DecodeStartedEvent)
        self.event_on_decode_completed = ObservableEvent(DecodeCompletedEvent)
        self.event_on_decode_file_not_found = ObservableEvent(DecodeFileNotFoundEvent)
        self.event_on_decode_progress = ObservableEvent(DecodeProgressEvent)
        self.event_on_decode_signal_list = ObservableEvent(DecodeSignalListEvent)
        self.event_on_parser_status_changed = ObservableEvent(ParserStatusEvent)
        self.event_on_decode_status_changed = ObservableEvent(DecodeStatusEvent)
        self.event_on_dbc_loaded = ObservableEvent(DBCLoadedEvent)
        self.event_on_worker_health_changed = ObservableEvent(FileWorkerHealthEvent)
        self.event_on_worker_raw_status_changed = ObservableEvent(FileWorkerRawStatusEvent)
        self.event_on_recorder_status_changed = ObservableEvent(RecorderStatusEvent)

        self._event_channels: dict[type[Any], ObservableEvent] = {
            FileServiceStateEvent: 
            self.event_on_service_state_changed,
            DecoderStatusEvent: 
            self.event_on_decoder_status_changed,
            DecodeStartedEvent: 
            self.event_on_decode_started,
            DecodeCompletedEvent: 
            self.event_on_decode_completed,
            DecodeFileNotFoundEvent: 
            self.event_on_decode_file_not_found,
            DecodeProgressEvent: 
            self.event_on_decode_progress,
            DecodeSignalListEvent: 
            self.event_on_decode_signal_list,
            ParserStatusEvent: 
            self.event_on_parser_status_changed,
            DecodeStatusEvent:
            self.event_on_decode_status_changed,
            DBCLoadedEvent:
            self.event_on_dbc_loaded,
            FileWorkerHealthEvent: 
            self.event_on_worker_health_changed,
            FileWorkerRawStatusEvent: 
            self.event_on_worker_raw_status_changed,
            RecorderStatusEvent:
            self.event_on_recorder_status_changed,
        }

        self._workers: dict[
            int,
            ParserWorkerRegistration,
        ] = {}
        self._any_subscribers: list[Callable[[Any], None]] = []


    #     self._interval_s = max(0.01, float(interval_s))
    #     self._stop_event = threading.Event()
    #     self._thread: threading.Thread | None = None

    # def start(self) -> None:
    #     if self._thread is not None and self._thread.is_alive():
    #         return
    #     self._stop_event.clear()
    #     self._thread = threading.Thread(
    #         target=self._run_loop,
    #         daemon=True,
    #         name="FileService-dispatcher",
    #     )
    #     self._thread.start()

    # def stop(self, timeout_s: float = 1.0) -> None:
    #     self._stop_event.set()
    #     if self._thread is not None:
    #         self._thread.join(timeout=max(0.1, float(timeout_s)))
    #         self._thread = None

    def dispatch_event(self, evt_type: Any) -> None:
        event = self._event_channels.get(type(evt_type))
        if event is not None:
            event.notify(evt_type)
        for callback in list(self._any_subscribers):
            callback(evt_type)

    def subscribe(self, event_type: type[Any], callback: Callable[[Any], None]) -> None:
        event = self._event_channels.get(event_type)
        if event is None:
            LOG.warning("Unsupported event type for subscribe: %s", event_type)
            return
        event.subscribe(callback)

    def subscribe_any(self, callback: Callable[[Any], None]) -> None:
        self._any_subscribers.append(callback)

    def _on_parser_wakeup(self, registration: ParserWorkerRegistration) -> None:
        self.on_parser_callback_event(registration)

    def on_decode_callback_event(self) -> None:
        pass

    def _on_decoder_wakeup(self, registration: ParserWorkerRegistration) -> None:
        self.on_decode_callback_event(registration)

    def on_decode_callback_event(self, registration: ParserWorkerRegistration) -> None:
        registration.wakeup.drain()
        done = True
        try:
            done = bool(registration.callback())
        except Exception:
            LOG.exception("Failed to handle decoder callback")
            done = True
        if done:
            self.unregister_worker(registration)

    def on_parser_callback_event(
         self,
        registration: ParserWorkerRegistration,
    ) -> None:

        registration.wakeup.drain()

        try:

            status = int(
                registration.callback()
            )

        except Exception:

            LOG.exception(
                "Failed to query parser status"
            )

            status = PARSER_STATUS_ERROR

        if status in (PARSER_STATUS_DONE, PARSER_STATUS_ERROR):
            self.unregister_worker(registration)


    def create_worker_wakeup(self):
        return IPCWakeup.create()
    
    def register_parser_worker(
        self,
        *,
        file_path: str,
        wakeup,
        callback: Callable[[], int],
    ) -> None:

        wait_obj = wakeup.wait_object()

        registration = ParserWorkerRegistration(
            file_path=file_path,
            wakeup=wakeup,
            callback=callback,
            kind="parser",
        )

        # ----------------------------------------------------
        # Linux notifier
        # ----------------------------------------------------

        if not IS_WINDOWS:

            notifier = QSocketNotifier(
                wait_obj,
                QSocketNotifier.Read,
            )

            notifier.activated.connect(
                lambda _fd:
                    self._on_parser_wakeup(
                        registration
                    )
            )

        # ----------------------------------------------------
        # Windows notifier
        # ----------------------------------------------------

        else:

            notifier = QWinEventNotifier(
                wait_obj
            )

            notifier.activated.connect(
                lambda _handle:
                    self._on_parser_wakeup(
                        registration
                    )
            )

        registration.notifier = notifier

        self._workers[id(registration)] = \
            registration

    def register_decoder_worker(
        self,
        *,
        file_path: str,
        wakeup,
        callback: Callable[[], bool],
    ) -> None:
        wait_obj = wakeup.wait_object()

        registration = ParserWorkerRegistration(
            file_path=file_path,
            wakeup=wakeup,
            callback=callback,
            kind="decoder",
        )

        if not IS_WINDOWS:
            notifier = QSocketNotifier(wait_obj, QSocketNotifier.Read)
            notifier.activated.connect(lambda _fd: self._on_decoder_wakeup(registration))
        else:
            notifier = QWinEventNotifier(wait_obj)
            notifier.activated.connect(lambda _handle: self._on_decoder_wakeup(registration))

        registration.notifier = notifier
        self._workers[id(registration)] = registration


    def unregister_worker(
        self,
        registration:
            ParserWorkerRegistration,
    ) -> None:

        if registration.notifier is not None:

            registration.notifier.setEnabled(
                False
            )

            registration.notifier.deleteLater()

        registration.wakeup.close()

        self._workers.pop(
            id(registration),
            None,
        )

    # def _run_loop(self) -> None:
    #     while not self._stop_event.is_set():
    #         try:
    #             self.on_parser_callback_event()
    #         except Exception:
    #             LOG.exception("FileService dispatcher parser callback failed")
    #         self._stop_event.wait(self._interval_s)

from __future__ import annotations

"""
    20260715 NOTE:
    This is the dispatcher for application events however, it should not be a normal callback function,
    instead the application will do the UI update then the callback should be executed on the application event loop.
    So this will be implemeted as the MainThreadDispatcher Interface on trying to be framework-independent
"""
from typing import Any, Callable, Protocol

from lw.logger_setup import LOG
from lw.observer import ObservableEvent
from .application_events import (
    DBCLoadedEvent,
    DecodeStatusEvent,
    DecodeCompletedEvent,
    DecodeFileNotFoundEvent,
    DecodeProgressEvent,
    DecodeSignalListEvent,
    DecodeStartedEvent,
    FileServiceStateEvent,
    ParserStatusEvent,
    RecorderStatusEvent,
)
from file_service.status import ParserStatus, DecodeStatus, RecorderStatus

class FileServiceDispatcher:
    def __init__(self) -> None:
        self.event_on_service_state_changed = ObservableEvent(FileServiceStateEvent)
        self.event_on_decode_started = ObservableEvent(DecodeStartedEvent)
        self.event_on_decode_completed = ObservableEvent(DecodeCompletedEvent)
        self.event_on_decode_file_not_found = ObservableEvent(DecodeFileNotFoundEvent)
        self.event_on_decode_progress = ObservableEvent(DecodeProgressEvent)
        self.event_on_decode_signal_list = ObservableEvent(DecodeSignalListEvent)
        self.event_on_decode_status = ObservableEvent(DecodeStatusEvent)
        self.event_on_dbc_loaded = ObservableEvent(DBCLoadedEvent)
        self.event_on_parser_status = ObservableEvent(ParserStatusEvent)
        self.event_on_recorder_status = ObservableEvent(RecorderStatusEvent)

        self._event_channels: dict[type[Any], ObservableEvent] = {
            FileServiceStateEvent: 
            self.event_on_service_state_changed,
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
            DecodeStatusEvent:
            self.event_on_decode_status,
            DBCLoadedEvent:
            self.event_on_dbc_loaded,
            ParserStatusEvent:
            self.event_on_parser_status,
            RecorderStatusEvent:
            self.event_on_recorder_status,
        }

        self._any_subscribers: list[Callable[[Any], None]] = []


    def dispatch_event(self, evt_type: Any) -> None:
        event = self._event_channels.get(type(evt_type))
        if event is not None:
            event.notify(evt_type)
        for callback in list(self._any_subscribers):
            callback(evt_type)

    def subscribe(self, event_type: type[Any], callback: Callable[[Any], None]) -> None:
        # Accept either application event classes or worker status enums.
        mapping: dict[type[Any], type[Any]] = {
            ParserStatus: ParserStatusEvent,
            DecodeStatus: DecodeStatusEvent,
            RecorderStatus: RecorderStatusEvent,
        }

        key = mapping.get(event_type, event_type)
        event = self._event_channels.get(key)
        if event is None:
            LOG.warning("Unsupported event type for subscribe: %s", event_type)
            return
        event.subscribe(callback)

    def unsubscribe_all(self) -> None:
        for event in self._event_channels.values():
            event.remove_all_subscribes()
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Generator

import pytest
from dataclasses import dataclass, field
import threading

from file_service.application_events import FileServiceStateEvent, ParserStatusEvent, RecorderStatusEvent, \
	DecodeStartedEvent, DecodeCompletedEvent, DecodeFileNotFoundEvent, DecodeProgressEvent, DecodeSignalListEvent, DecodeStatusEvent
from file_service.srv_if import FileService, get_file_service
from lw.service.base_service import ServiceState
from lw.logger_setup import setup_logger
from fixture import ParserStatusVM, RecorderStatusVM, ServiceStateVM
from file_service.application_events import FileServiceStateEvent, ParserStatusEvent, RecorderStatusEvent
from file_service.status import ParserStatus, RecorderStatus
from file_service.status import DecodeStatus
from file_service.record_id import RecordId
from lw.service.base_service import ServiceState

def reset_events(events: list[threading.Event]) -> None:
	"""Reset a list of threading events after each test case."""
	for event in events:
		event.clear()


@dataclass
class ServiceStateVM:
	file_state_trace: list[ServiceState] = field(default_factory=list)
	file_running_event: threading.Event = field(default_factory=threading.Event)
	file_stopped_event: threading.Event = field(default_factory=threading.Event)

	def on_file_service_state(self, event: FileServiceStateEvent) -> None:
		state = event.state
		if not isinstance(state, ServiceState):
			raise TypeError(f"FileServiceStateEvent.state must be ServiceState, got {type(state).__name__}")
		self.file_state_trace.append(state)
		if state == ServiceState.RUNNING:
			self.file_running_event.set()
		elif state == ServiceState.STOPPED:
			self.file_stopped_event.set()

	def reset(self) -> None:
		self.file_state_trace.clear()
		reset_events([
			self.file_running_event,
			self.file_stopped_event,
		])


@dataclass
class ParserStatusVM:
	parser_record_id: RecordId | None = None
	parser_status_trace: list[ParserStatus] = field(default_factory=list)
	parser_idle_event: threading.Event = field(default_factory=threading.Event)
	parser_running_event: threading.Event = field(default_factory=threading.Event)
	parser_done_event: threading.Event = field(default_factory=threading.Event)
	parser_failed_event: threading.Event = field(default_factory=threading.Event)

	@property
	def parse_record_id(self):
		return self.parser_record_id

	@property
	def status_trace(self):
		return self.parser_status_trace

	@property
	def idle_event(self):
		return self.parser_idle_event

	@property
	def running_event(self):
		return self.parser_running_event

	@property
	def done_event(self):
		return self.parser_done_event

	@property
	def failed_event(self):
		return self.parser_failed_event

	def on_parser_status(self, event: ParserStatusEvent) -> None:
		status = event.status
		self.parser_status_trace.append(status)

		if status == ParserStatus.IDLE:
			self.parser_idle_event.set()
		elif status == ParserStatus.RUNNING:
			self.parser_running_event.set()
		elif status == ParserStatus.DONE:
			if event.record_id is not None:
				self.parser_record_id = event.record_id
			self.parser_done_event.set()
		elif status == ParserStatus.FAILED:
			self.parser_failed_event.set()

	def reset(self) -> None:
		self.parser_record_id = None
		self.parser_status_trace.clear()
		reset_events([
			self.parser_idle_event,
			self.parser_running_event,
			self.parser_done_event,
			self.parser_failed_event,
		])


@dataclass
class RecorderStatusVM:
	recorder_record_id: RecordId | None = None
	recorder_status_trace: list[RecorderStatus] = field(default_factory=list)
	recorder_stopped_event: threading.Event = field(default_factory=threading.Event)
	recorder_write_batch_event: threading.Event = field(default_factory=threading.Event)
	recorder_paused_event: threading.Event = field(default_factory=threading.Event)
	recorder_wait_ring_event: threading.Event = field(default_factory=threading.Event)
	recorder_active_event: threading.Event = field(default_factory=threading.Event)

	@property
	def record_id(self):
		return self.recorder_record_id

	@property
	def status_trace(self):
		return self.recorder_status_trace

	@property
	def idle_event(self):
		return self.recorder_stopped_event

	@property
	def stopped_event(self):
		return self.recorder_stopped_event

	@property
	def write_batch_event(self):
		return self.recorder_write_batch_event

	@property
	def paused_event(self):
		return self.recorder_paused_event

	@property
	def wait_ring_event(self):
		return self.recorder_wait_ring_event

	@property
	def failed_event(self):
		return self.recorder_stopped_event

	@property
	def active_event(self):
		return self.recorder_active_event

	def on_recorder_status(self, event: RecorderStatusEvent) -> None:
		payload_record_id = event.payload.get("record_id")
		if isinstance(payload_record_id, RecordId):
			self.recorder_record_id = payload_record_id

		status = event.status
		self.recorder_status_trace.append(status)

		if status == RecorderStatus.STOPPED:
			self.recorder_stopped_event.set()
		elif status == RecorderStatus.WRITE_BATCH:
			self.recorder_write_batch_event.set()
			self.recorder_active_event.set()
		elif status == RecorderStatus.PAUSED:
			self.recorder_paused_event.set()
			self.recorder_active_event.set()
		elif status == RecorderStatus.WAIT_RING:
			self.recorder_wait_ring_event.set()
			self.recorder_active_event.set()

	def reset(self) -> None:
		self.recorder_record_id = None
		self.recorder_status_trace.clear()
		reset_events([
			self.recorder_stopped_event,
			self.recorder_write_batch_event,
			self.recorder_paused_event,
			self.recorder_wait_ring_event,
			self.recorder_active_event,
		])



@dataclass(slots=True)
class DecodeStatusVM:
	decode_record_id: RecordId | None = None
	decode_started_event: threading.Event = field(default_factory=threading.Event)
	decode_completed_event: threading.Event = field(default_factory=threading.Event)
	decode_file_not_found_event: threading.Event = field(default_factory=threading.Event)
	decode_progress_event: threading.Event = field(default_factory=threading.Event)
	decode_signal_list_event: threading.Event = field(default_factory=threading.Event)
	decode_failed_event: threading.Event = field(default_factory=threading.Event)

	@property
	def record_id(self):
		return self.decode_record_id

	@property
	def started_event(self):
		return self.decode_started_event

	@property
	def completed_event(self):
		return self.decode_completed_event

	@property
	def file_not_found_event(self):
		return self.decode_file_not_found_event

	@property
	def progress_event(self):
		return self.decode_progress_event

	@property
	def signal_list_event(self):
		return self.decode_signal_list_event

	@property
	def failed_event(self):
		return self.decode_failed_event

	def on_decode_started(self, event: DecodeStartedEvent) -> None:
		self.decode_record_id = event.record_id
		self.decode_started_event.set()

	def on_decode_completed(self, event: DecodeCompletedEvent) -> None:
		self.decode_record_id = event.record_id
		self.decode_completed_event.set()

	def on_decode_file_not_found(self, event: DecodeFileNotFoundEvent) -> None:
		self.decode_file_not_found_event.set()

	def on_decode_progress(self, event: DecodeProgressEvent) -> None:
		self.decode_progress_event.set()

	def on_decode_signal_list(self, event: DecodeSignalListEvent) -> None:
		self.decode_signal_list_event.set()

	def on_decode_status(self, event: DecodeStatusEvent) -> None:
		status = event.status
		if status == DecodeStatus.IDLE:
			self.decode_started_event.set()
		elif status == DecodeStatus.RUNNING:
			self.decode_progress_event.set()
		elif status == DecodeStatus.DONE:
			self.decode_completed_event.set()
		elif status == DecodeStatus.FAILED:
			self.decode_failed_event.set()

	def reset(self) -> None:
		self.recorder_record_id = None
		self.recorder_status_trace.clear()
		reset_events([
			self.decode_started_event,
			self.decode_completed_event,
			self.decode_file_not_found_event,
			self.decode_progress_event,
			self.decode_signal_list_event,
			self.decode_failed_event,
		])

@dataclass(slots=True)
class FileServiceStatusVM(ServiceStateVM, ParserStatusVM, RecorderStatusVM, DecodeStatusVM):
	def __init__(self):
		ServiceStateVM.__init__(self)
		ParserStatusVM.__init__(self)
		RecorderStatusVM.__init__(self)
		DecodeStatusVM.__init__(self)

	def reset(self):
		ServiceStateVM.reset(self)
		ParserStatusVM.reset(self)
		RecorderStatusVM.reset(self)
		DecodeStatusVM.reset(self)

@pytest.fixture(scope="function")
def file_service(qt_app) -> Generator[tuple[FileService, FileServiceStatusVM], None, None]:
	setup_logger(env="DEV", backup_count=30)
	file_srv = get_file_service()
	vm = FileServiceStatusVM()
	vm.reset()

	file_srv.start()
	# assert vm.file_running_event.wait(timeout=0.5), "FileService did not reach RUNNING state"
	registered_callbacks = [
		vm.on_recorder_status,
		vm.on_parser_status,
		vm.on_decode_status,
	]

	for callback in registered_callbacks:
		file_srv.subscribe(callback)

	try:
		yield file_srv, vm
	finally:
		vm.reset()
		# file_srv.unsubscribe_all()

		print("Stop service")
		file_srv.stop()
	#assert vm.file_stopped_event.wait(timeout=0.5), "FileService did not reach STOPPED state"


# @pytest.fixture(scope="module")
# def file_service_with_vm(file_service):
# 	"""Reusable file_service + VM callback wiring for file_srv_* tests."""
# 	file_srv = file_service
# 	vm = FileServiceStatusVM()

# 	file_srv.subscribe(FileServiceStateEvent, vm.on_file_service_state)
# 	file_srv.subscribe(ParserStatusEvent, vm.on_parser_status)
# 	file_srv.subscribe(RecorderStatusEvent, vm.on_recorder_status)

# 	vm.reset()
# 	yield file_srv, vm

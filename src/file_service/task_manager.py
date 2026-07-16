### 20270715 NOTE:
""" Design a task manager need to do the responsibility of 
1. Scheduler async task
2. Result callback function
3. Handle failed exception
4. Maybe a task queue for many async tasks

Created
    │
    ▼
Running
    │
    ├── Completed
    ├── Failed
    └── Cancelled

- Python
    concurrent.futures.ThreadPoolExecutor
    concurrent.futures.ProcessPoolExecutor

- ROS 2
    rclcpp::Executor
    rclcpp::SingleThreadedExecutor
    rclcpp::MultiThreadedExecutor

- Qt
    QThreadPool
    QtConcurrent::run()

- C#
    Task
    Task.Run()
    TaskScheduler
    ThreadPool

- Android
    Executor
    ExecutorService
    WorkManager
    CoroutineDispatcher (Kotlin)

- Java
    Executor
    ExecutorService
    ScheduledExecutorService

- Chromium
    base::ThreadPool
    base::SequencedTaskRunner
    base::SingleThreadTaskRunner

These frameworks all follow the same concept:
    submit work -> execute asynchronously -> notify completion -> reclaim resources.

    
The executor of framework do not know about your app event loop, do its dispatcher is called back from the python thread, not GUI thread.
"""
from concurrent.futures import Future, ProcessPoolExecutor
from functools import partial


"""
self._worker_alive: dict[str, bool] = {}
self._recorder_state = StatusChannel(int(RecorderStatus.STOPPED))
self.parser_channel = StatusChannel(int(-1))
self._decoder_state = StatusChannel(int(-1))

def parse_log_file(self, file_path: str) -> LogId | None:
    self._require_running()
    log_id = LogId.new()

    self._qt_dispatcher.attach(self.parser_channel, self._on_parser_event)

    TODO: worker track thread system: 20270707 Spawn process/thread worker, the process may be died while doing work, in that case it never emit
            any status so the service never know it is done the work or not and the state is hanged at running.
            -> Need the heart beat thread here to tracking the pid of worker
    proc = run_parser_async(
        file_path,
        log_id,
        self.parser_channel,
    )
    return log_id
"""
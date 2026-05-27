from PySide6.QtCore import QObject
import os
import sys

IS_WINDOWS = sys.platform == "win32"

if IS_WINDOWS:
    from PySide6.QtCore import QWinEventNotifier
else:
    from PySide6.QtCore import QSocketNotifier

# ============================================================
# Wakeup object
# ============================================================

class IPCWakeup:

    @staticmethod
    def create():
        if IS_WINDOWS:
            return WindowsWakeup()

        return LinuxWakeup()


# ============================================================
# Linux
# ============================================================

class LinuxWakeup:

    def __init__(self):

        self.read_fd, self.write_fd = os.pipe()

    def wait_object(self):

        return self.read_fd

    def signal_object(self):

        return self.write_fd

    def drain(self):

        try:
            os.read(self.read_fd, 4096)
        except OSError:
            pass

    def close(self):

        try:
            os.close(self.read_fd)
        except OSError:
            pass

        try:
            os.close(self.write_fd)
        except OSError:
            pass

    def signal(self):

        os.write(
            self.write_fd,
            b"1",
        )

# ============================================================
# Windows
# ============================================================

if IS_WINDOWS:

    import ctypes

    kernel32 = ctypes.windll.kernel32

    CreateEventW = kernel32.CreateEventW
    SetEvent = kernel32.SetEvent
    CloseHandle = kernel32.CloseHandle

    class WindowsWakeup:

        def __init__(self):

            self.handle = CreateEventW(
                None,
                False,
                False,
                None,
            )

        def wait_object(self):

            return self.handle

        def signal_object(self):

            return self.handle

        def drain(self):

            #
            # auto-reset event
            #
            pass

        def close(self):

            CloseHandle(self.handle)

        def signal(self):
            ctypes.windll.kernel32.SetEvent(
                self.handle
            )
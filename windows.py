import ctypes
from ctypes import wintypes as w
import hashlib
import json
import mmap
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import time
import uuid


class PureRefError(RuntimeError):
    pass


user32 = ctypes.WinDLL("user32", use_last_error=True)
kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)

WM_KEYDOWN, WM_KEYUP = 0x100, 0x101
VK_SHIFT, VK_CONTROL, VK_ESCAPE = 0x10, 0x11, 0x1b
# Shift, Ctrl, Alt, both Windows keys, and the left/right Shift, Ctrl and Alt keys.
MODIFIERS = (0x10, 0x11, 0x12, 0x5b, 0x5c, 0xa0, 0xa1, 0xa2, 0xa3, 0xa4, 0xa5)


def bind(dll, name, result, *args):
    fn = getattr(dll, name)
    fn.restype = result
    fn.argtypes = args
    return fn


enum_callback = ctypes.WINFUNCTYPE(w.BOOL, w.HWND, w.LPARAM)
bind(user32, "EnumWindows", w.BOOL, enum_callback, w.LPARAM)
bind(user32, "GetWindowThreadProcessId", w.DWORD, w.HWND, ctypes.POINTER(w.DWORD))
bind(user32, "GetWindowTextW", ctypes.c_int, w.HWND, w.LPWSTR, ctypes.c_int)
bind(user32, "IsWindowVisible", w.BOOL, w.HWND)
bind(user32, "IsWindow", w.BOOL, w.HWND)
bind(user32, "IsWindowEnabled", w.BOOL, w.HWND)
bind(user32, "EnableWindow", w.BOOL, w.HWND, w.BOOL)
bind(user32, "AttachThreadInput", w.BOOL, w.DWORD, w.DWORD, w.BOOL)
bind(user32, "PeekMessageW", w.BOOL, ctypes.POINTER(w.MSG), w.HWND, w.UINT, w.UINT, w.UINT)
bind(user32, "SendMessageTimeoutW", w.LPARAM, w.HWND, w.UINT, w.WPARAM, w.LPARAM, w.UINT, w.UINT, ctypes.POINTER(ctypes.c_size_t))
bind(user32, "GetKeyboardState", w.BOOL, ctypes.POINTER(ctypes.c_ubyte))
bind(user32, "SetKeyboardState", w.BOOL, ctypes.POINTER(ctypes.c_ubyte))
bind(user32, "MapVirtualKeyW", w.UINT, w.UINT, w.UINT)
bind(user32, "CreateDesktopW", w.HANDLE, w.LPCWSTR, w.LPCWSTR, ctypes.c_void_p, w.DWORD, w.DWORD, ctypes.c_void_p)
bind(user32, "CloseDesktop", w.BOOL, w.HANDLE)
bind(kernel32, "GetCurrentThreadId", w.DWORD)


def title(hwnd):
    text = ctypes.create_unicode_buffer(2048)
    user32.GetWindowTextW(hwnd, text, len(text))
    return text.value


def matches_document(caption, document):
    return caption.rstrip("*").casefold().endswith(" - " + document.casefold())


def windows():
    found = []

    @enum_callback
    def collect(hwnd, _):
        if user32.IsWindowVisible(hwnd):
            pid = w.DWORD()
            user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
            found.append((hwnd, pid.value, title(hwnd)))
        return True

    user32.EnumWindows(collect, 0)
    return found


def wait_until(test, message, timeout=60):
    until = time.monotonic() + timeout
    while time.monotonic() < until:
        if test():
            return
        time.sleep(.05)
    raise PureRefError(message)


def verify_saved(path, files=()):
    with Path(path).open("rb") as stream, mmap.mmap(stream.fileno(), 0, access=mmap.ACCESS_READ) as data:
        if not all(data.find(hashlib.md5(Path(image).read_bytes()).hexdigest().encode("ascii")) >= 0 for image in files):
            raise PureRefError("PureRef did not save every image.")


class Board:
    def __init__(self, directory):
        self.directory = Path(directory)
        self.directory.mkdir(parents=True, exist_ok=True)
        self.process = None
        self.pid = None
        self.hwnd = None
        self.document = ""
        self.executable = ""
        self.updating = False

    def alive(self):
        if self.process is not None and self.process.poll() is not None:
            return False
        if self.hwnd is None or not user32.IsWindow(self.hwnd):
            return False
        pid = w.DWORD()
        user32.GetWindowThreadProcessId(self.hwnd, ctypes.byref(pid))
        return pid.value == self.pid

    def profile(self, name):
        path = self.directory / name
        path.write_text("[General_Settings]\nOpenFilesIn=CurrentWindow\nCheckForUpdates=false\nActivateSelectedImages=false\nSelectNew=false\nWatchOpenSceneForChanges=false\nEmbed%20Local%20Files=true\nAutoOptimizeRulesPreset=0\n[Analytics_Settings]\nAllowAnalytics=false\n", encoding="utf-8")
        return path

    def open(self, executable, document):
        self.executable = executable
        record = self.directory / "window.json"
        if not self.alive() and record.exists():
            owner = json.loads(record.read_text(encoding="utf-8"))
            self.hwnd, self.pid, self.document = owner["hwnd"], owner["pid"], owner["document"]
            self.process = None
        if self.alive():
            if document != self.document:
                raise PureRefError("Close the connector's PureRef window before changing boards.")
            self.ready()
            return
        if any(matches_document(caption, document) for _, _, caption in windows()):
            raise PureRefError("Close the already open board, then use Open board here.")
        if ";" in document:
            raise PureRefError("PureRef CLI paths cannot contain semicolons.")
        self.document = document
        Path(document).parent.mkdir(parents=True, exist_ok=True)
        if not Path(document).exists():
            self.run_cli([f"save;{document}", "exit"])
        command = [executable, "-s", str(self.profile("PureRef.ini")), document]
        temporary = self.directory / "temp"
        temporary.mkdir(exist_ok=True)
        self.process = subprocess.Popen(command, env=dict(os.environ, TEMP=str(temporary), TMP=str(temporary)), stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        self.pid = self.process.pid

        def opened():
            self.hwnd = next((hwnd for hwnd, pid, caption in windows() if pid == self.pid and matches_document(caption, document)), None)
            return self.hwnd and Path(document).exists()

        wait_until(opened, "PureRef did not open the board.")
        record.write_text(json.dumps({"hwnd": self.hwnd, "pid": self.pid, "document": document}), encoding="utf-8")
        self.ready()

    def ready(self):
        if not self.alive() or not matches_document(title(self.hwnd), self.document):
            raise PureRefError("The connector's board was closed or changed.")
        if not self.updating and not user32.IsWindowEnabled(self.hwnd):
            raise PureRefError("Finish the open PureRef dialog.")
        if any(hwnd != self.hwnd and matches_document(caption, self.document) for hwnd, _, caption in windows()):
            raise PureRefError("Close the other window using this board.")

    def message(self, msg, a=0, b=0):
        result = ctypes.c_size_t()
        if not user32.SendMessageTimeoutW(self.hwnd, msg, a, b, 2, 2000, ctypes.byref(result)):
            raise PureRefError("PureRef is busy.")

    def key(self, vk, shift=False, control=True):
        thread = user32.GetWindowThreadProcessId(self.hwnd, None)
        current = kernel32.GetCurrentThreadId()
        # ComfyUI's worker thread may not have a Windows message queue yet.
        message = w.MSG()
        user32.PeekMessageW(ctypes.byref(message), None, 0, 0, 0)
        if not user32.AttachThreadInput(current, thread, True):
            raise PureRefError("Cannot save the PureRef window.")
        state = (ctypes.c_ubyte * 256)()
        user32.GetKeyboardState(state)
        previous = bytes(state)
        try:
            for modifier in MODIFIERS:
                state[modifier] = 0
            if control:
                state[VK_CONTROL] = state[0xa2] = 128
            if shift:
                state[VK_SHIFT] = state[0xa0] = 128
            user32.SetKeyboardState(state)
            if control:
                self.message(WM_KEYDOWN, VK_CONTROL, 1 | (user32.MapVirtualKeyW(VK_CONTROL, 0) << 16))
            if shift:
                self.message(WM_KEYDOWN, VK_SHIFT, 1 | (user32.MapVirtualKeyW(VK_SHIFT, 0) << 16))
            self.message(WM_KEYDOWN, vk, 1 | (user32.MapVirtualKeyW(vk, 0) << 16))
        finally:
            try:
                try:
                    self.message(WM_KEYUP, vk, 0xc0000001 | (user32.MapVirtualKeyW(vk, 0) << 16))
                except PureRefError:
                    pass
                for modifier in MODIFIERS:
                    state[modifier] = 0
                user32.SetKeyboardState(state)
                for modifier in ((VK_SHIFT,) if shift else ()) + ((VK_CONTROL,) if control else ()):
                    try:
                        self.message(WM_KEYUP, modifier, 0xc0000001 | (user32.MapVirtualKeyW(modifier, 0) << 16))
                    except PureRefError:
                        pass
            finally:
                user32.SetKeyboardState((ctypes.c_ubyte * 256).from_buffer_copy(previous))
                user32.AttachThreadInput(current, thread, False)

    def save(self):
        self.ready()
        path = Path(self.document)
        before = path.stat().st_mtime_ns
        self.key(VK_ESCAPE, control=False)
        self.key(ord("S"))
        retry_at = time.monotonic() + 1

        def saved():
            nonlocal retry_at
            try:
                # The first shortcut can finish a note edit or arrive during reload.
                if time.monotonic() >= retry_at and path.stat().st_mtime_ns == before:
                    self.ready()
                    self.key(ord("S"))
                    retry_at = time.monotonic() + 1
                    return False
                if "*" in title(self.hwnd):
                    return False
                if path.stat().st_mtime_ns == before or path.stat().st_size == 0:
                    return False
                verify_saved(path)
                return True
            except (PermissionError, FileNotFoundError):
                return False

        wait_until(saved, "PureRef did not finish saving.")

    def run_cli(self, commands):
        desktop_name = "PureRefCLI-" + uuid.uuid4().hex
        desktop = user32.CreateDesktopW(desktop_name, None, None, 0, 0x1ff, None)
        if not desktop:
            raise PureRefError("Cannot start the background PureRef update.")
        startup = subprocess.STARTUPINFO()
        startup.lpDesktop = desktop_name
        temporary = self.directory / "worker-temp"
        temporary.mkdir(exist_ok=True)
        arguments = [self.executable, "-s", str(self.profile("worker.ini"))]
        for command in commands:
            arguments += ["-c", command]
        try:
            result = subprocess.run(arguments, env=dict(os.environ, TEMP=str(temporary), TMP=str(temporary)), startupinfo=startup, capture_output=True, timeout=120)
            if result.returncode or b"[Critical]" in result.stdout + result.stderr:
                reason = f"exit code {result.returncode}"
                lines = [line.strip() for line in (result.stderr + result.stdout).decode(errors="replace").splitlines() if line.strip()]
                for index, line in enumerate(lines):
                    if line.startswith("[Critical]") and "Encountered errors while invoking" not in line:
                        reason = line.partition("]")[2].lstrip(": ")
                        if index + 1 < len(lines) and not lines[index + 1].startswith("["):
                            reason += " " + lines[index + 1]
                        break
                raise PureRefError(f"PureRef CLI update failed: {reason}")
        finally:
            user32.CloseDesktop(desktop)

    def add(self, files, positions):
        self.save()
        document = Path(self.document)
        if any(";" in str(path) for path in [document, *files]):
            raise PureRefError("PureRef CLI paths cannot contain semicolons.")
        descriptor, name = tempfile.mkstemp(prefix=".pureref-backup-", suffix=".pur", dir=document.parent)
        os.close(descriptor)
        backup = Path(name)
        stamp = document.stat().st_mtime_ns
        changed = False
        restored = True
        try:
            shutil.copyfile(document, backup)
            # Pause editing during the short save/update/reload transaction.
            self.updating = True
            user32.EnableWindow(self.hwnd, False)
            try:
                if not self.alive() or "*" in title(self.hwnd) or document.stat().st_mtime_ns != stamp:
                    raise PureRefError("The board changed during the update.")
                commands = ["load;" + str(document).replace(",", ",,")]
                for path, (x, y) in zip(files, positions):
                    commands.append(f"load;{str(path).replace(',', ',,')};{x};{y}")
                commands += [f"save;{document}", "exit"]
                changed = True
                self.run_cli(commands)
                verify_saved(document, files)
                self.key(ord("L"), shift=True)
                self.save()
                verify_saved(document, files)
            except Exception:
                if changed:
                    # Keep the backup if restoring fails so the scene can be recovered by hand.
                    restored = False
                    shutil.copyfile(backup, document)
                    restored = True
                raise
            finally:
                if user32.IsWindow(self.hwnd):
                    user32.EnableWindow(self.hwnd, True)
                self.updating = False
        finally:
            if restored:
                backup.unlink(missing_ok=True)

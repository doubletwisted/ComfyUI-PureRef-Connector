import hashlib
import importlib.util
import os
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import Mock, patch


spec = importlib.util.spec_from_file_location("pureref_windows_test", Path(__file__).resolve().parents[1] / "windows.py")
native = importlib.util.module_from_spec(spec)
spec.loader.exec_module(native)


class NativeTests(unittest.TestCase):
    def setUp(self):
        self.board = native.Board.__new__(native.Board)
        self.board.hwnd = 1
        self.user32 = Mock()
        self.user32.GetWindowThreadProcessId.return_value = 99
        self.user32.MapVirtualKeyW.return_value = 0
        for name, value in (("u", self.user32), ("k", Mock())):
            replacement = patch.object(native, name, value)
            replacement.start()
            self.addCleanup(replacement.stop)

    def test_keyboard_timeouts_always_restore_state_and_detach_input(self):
        original = bytearray(256)
        original[18] = 128

        def read_state(state):
            state[:] = original

        self.user32.GetKeyboardState.side_effect = read_state
        states = []
        self.user32.SetKeyboardState.side_effect = lambda state: states.append(bytes(state))
        for failed in ((256, 17), (256, ord("S")), (257, ord("S")), (257, 17)):
            with self.subTest(failed=failed):
                def message(msg, key, value):
                    if (msg, key) == failed:
                        raise native.PureRefError("key timeout")

                self.board.message = Mock(side_effect=message)
                if failed[0] == 256:
                    with self.assertRaisesRegex(native.PureRefError, "key timeout"):
                        self.board.key(ord("S"))
                else:
                    self.board.key(ord("S"))
                self.assertEqual(states[-1], bytes(original))
                self.assertFalse(self.user32.AttachThreadInput.call_args.args[-1])
                self.assertTrue(any(call.args[:2] == (257, 17) for call in self.board.message.call_args_list))

    def test_cli_failure_is_detected_even_when_pureref_returns_zero(self):
        with tempfile.TemporaryDirectory() as temporary:
            self.board.directory = Path(temporary)
            self.board.executable = "PureRef.exe"
            self.board.profile = Mock(return_value=Path(temporary) / "worker.ini")
            failure = subprocess.CompletedProcess([], 0, b"", b"[Critical]: Encountered errors while invoking method 'save':\r\n[Critical]: Could not open file.\r\n\tAccess is denied.")
            with patch.object(native.subprocess, "run", return_value=failure):
                with self.assertRaisesRegex(native.PureRefError, "CLI update failed: Could not open file. Access is denied."):
                    self.board.run_cli(["exit"])
            self.user32.CloseDesktop.assert_called_once()

    def test_keyboard_does_not_send_shortcuts_when_input_cannot_be_attached(self):
        self.user32.AttachThreadInput.return_value = False
        self.board.message = Mock()
        with self.assertRaisesRegex(native.PureRefError, "Cannot save"):
            self.board.key(ord("S"))
        self.user32.PeekMessageW.assert_called_once()
        self.board.message.assert_not_called()

    def test_save_waits_for_a_new_readable_file(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "board.pur"
            path.write_bytes(b"old scene")
            before = path.stat().st_mtime_ns
            self.board.document = str(path)
            self.board.ready = Mock()
            self.board.key = Mock()
            attempts = 0
            original_open = Path.open

            def open_scene(target, *args, **kwargs):
                nonlocal attempts
                if target == path and args == ("rb",):
                    attempts += 1
                    if attempts == 1:
                        raise PermissionError("PureRef is still writing")
                return original_open(target, *args, **kwargs)

            def complete_save(ready, message):
                self.assertFalse(ready())
                path.write_bytes(b"saved scene")
                os.utime(path, ns=(before + 10000000, before + 10000000))
                self.assertFalse(ready())
                self.assertTrue(ready())

            with patch.object(Path, "open", new=open_scene), patch.object(native, "title", return_value="board"), patch.object(native, "wait_until", side_effect=complete_save):
                self.board.save()

    def test_cli_positions_and_conflict_protection(self):
        for conflict in (False, True):
            with self.subTest(conflict=conflict), tempfile.TemporaryDirectory() as temporary:
                root = Path(temporary)
                document = root / "board.pur"
                document.write_bytes(b"existing images and notes")
                self.board.document = str(document)
                self.board.save = Mock()
                self.board.ready = Mock()
                self.board.key = Mock()
                self.board.alive = Mock(return_value=True)
                files = [root / f"image-{i}.png" for i in range(7)]
                for i, file in enumerate(files):
                    file.write_bytes(f"original PNG workflow {i}".encode())

                def cli(commands):
                    self.assertEqual([command.rsplit(";", 2)[1:] for command in commands[1:-2]], [["128", "64"], ["448", "64"], ["768", "64"], ["1088", "64"], ["1408", "64"], ["1728", "64"], ["128", "256"]])
                    staged = Path(commands[0][5:])
                    self.assertEqual(staged.read_bytes(), b"existing images and notes")
                    staged.write_bytes(staged.read_bytes() + b"".join(hashlib.md5(file.read_bytes()).hexdigest().encode() for file in files))

                self.board.run_cli = Mock(side_effect=cli)
                with patch.object(native, "title", return_value="*board" if conflict else "board"):
                    if conflict:
                        with self.assertRaisesRegex(native.PureRefError, "changed during"):
                            self.board.add(files, [(128 + i % 6 * 320, 64 + i // 6 * 192) for i in range(7)])
                        self.assertEqual(document.read_bytes(), b"existing images and notes")
                        self.board.key.assert_not_called()
                    else:
                        self.board.add(files, [(128 + i % 6 * 320, 64 + i // 6 * 192) for i in range(7)])
                        self.assertTrue(document.read_bytes().startswith(b"existing images and notes"))
                        self.board.key.assert_called_once_with(ord("L"), shift=True)
                self.assertEqual(list(root.glob(".pureref-*")), [])

    def test_failed_cli_or_reload_restores_the_saved_scene_and_enables_editing(self):
        for failed_step in ("cli", "empty", "reload"):
            with self.subTest(failed_step=failed_step), tempfile.TemporaryDirectory() as temporary:
                document = Path(temporary) / "board.pur"
                original = b"saved references and notes"
                document.write_bytes(original)
                self.board.document = str(document)
                self.board.save = Mock()
                self.board.alive = Mock(return_value=True)
                self.board.key = Mock(side_effect=native.PureRefError("reload failed") if failed_step == "reload" else None)

                def cli(commands):
                    document.write_bytes(b"" if failed_step == "empty" else b"partial update")
                    if failed_step == "cli":
                        raise native.PureRefError("CLI failed")

                self.board.run_cli = Mock(side_effect=cli)
                with patch.object(native, "title", return_value="board"):
                    with self.assertRaises((native.PureRefError, ValueError)):
                        self.board.add([], [])
                self.assertEqual(document.read_bytes(), original)
                self.assertFalse(self.board.updating)
                self.user32.EnableWindow.assert_called_with(1, True)
                self.assertEqual(list(document.parent.glob(".pureref-backup-*")), [])

import asyncio
import importlib.util
import json
from pathlib import Path
import sys
import tempfile
import types
import unittest
from unittest.mock import AsyncMock, Mock, patch

from aiohttp import web


class SaveImage:
    def __init__(self, output):
        self.output_dir = str(output)
        self.type = "output"

    def save_images(self, images, filename_prefix="ComfyUI", prompt=None, extra_pnginfo=None):
        path = Path(self.output_dir) / (filename_prefix + ".png")
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"original PNG")
        return {"ui": {"images": [{"filename": path.name, "subfolder": "", "type": self.type}]}, "result": (images,)}


class PreviewImage(SaveImage):
    pass


class CaptureTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.output = self.root / "output"
        self.temp = self.root / "temp"
        folder_paths = types.SimpleNamespace(get_system_user_directory=lambda _: str(self.root / "settings"), get_temp_directory=lambda: str(self.temp))
        server = types.SimpleNamespace(PromptServer=types.SimpleNamespace(instance=types.SimpleNamespace(send_sync=Mock(), routes=web.RouteTableDef())))
        package = types.ModuleType("capture_test_plugin")
        package.__path__ = [str(Path(__file__).resolve().parents[1])]
        native = types.SimpleNamespace(Board=Mock(), PureRefError=RuntimeError)
        self.modules = patch.dict(sys.modules, {"folder_paths": folder_paths, "nodes": types.SimpleNamespace(SaveImage=SaveImage), "server": server, "capture_test_plugin": package, "capture_test_plugin.windows": native})
        self.modules.start()
        spec = importlib.util.spec_from_file_location("capture_test_plugin.connector", Path(package.__path__[0]) / "connector.py")
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        self.connector_module = module
        self.controller = module.Connector()
        self.controller.enabled = True
        self.controller.settings.update(document=str(self.root / "board.pur"), mode="pureref_only")
        self.controller.board = Mock()
        self.controller.launch = Mock()
        self.owner = SaveImage(self.output)
        self.images = types.SimpleNamespace(shape=(1, 256, 256, 3))

    def tearDown(self):
        self.modules.stop()
        self.temporary.cleanup()

    def save(self, owner=None):
        return self.controller.capture(owner or self.owner, self.images, "test", {"node": 1}, {"workflow": {}})

    def test_success_keeps_preview_and_node_contract(self):
        result = self.save()
        self.assertEqual(result["ui"]["images"][0]["type"], "temp")
        self.assertIs(result["result"][0], self.images)
        self.assertFalse((self.output / "test.png").exists())
        self.assertEqual(self.owner.output_dir, str(self.output))
        self.assertEqual(self.owner.type, "output")

    def test_pure_only_failure_saves_to_normal_folder(self):
        self.controller.board.add.side_effect = OSError("Board is unavailable")
        result = self.save()
        self.assertEqual(result["ui"]["images"][0]["type"], "output")
        self.assertTrue((self.output / "test.png").exists())
        self.assertIs(result["result"][0], self.images)

    def test_both_mode_imports_the_normal_output_without_changing_the_result(self):
        self.controller.settings["mode"] = "both"
        result = self.save()
        self.assertEqual(result["ui"]["images"][0]["type"], "output")
        self.assertTrue((self.output / "test.png").exists())
        self.assertFalse((self.temp / "test.png").exists())
        self.assertEqual(self.controller.board.add.call_args.args[0], [self.output / "test.png"])
        self.assertEqual(self.controller.board.add.call_args.args[1], [(128, 128)])

    def test_both_mode_failure_does_not_save_a_second_copy(self):
        self.controller.settings["mode"] = "both"
        self.controller.original_save = Mock(wraps=self.controller.original_save)
        self.controller.board.add.side_effect = OSError("Board is unavailable")
        result = self.save()
        self.assertEqual(result["ui"]["images"][0]["type"], "output")
        self.controller.original_save.assert_called_once()

    def test_installed_hook_delegates_and_open_returns_native_errors_as_json(self):
        directory = Path(__file__).resolve().parents[1]
        spec = importlib.util.spec_from_file_location("capture_test_plugin", directory / "__init__.py", submodule_search_locations=[str(directory)])
        module = importlib.util.module_from_spec(spec)
        original = SaveImage.save_images
        with patch.dict(sys.modules, {"capture_test_plugin.connector": self.connector_module}), patch.object(SaveImage, "save_images", original):
            spec.loader.exec_module(module)
            self.assertIsNot(SaveImage.save_images, original)
            result = self.owner.save_images(self.images, "hook", {"node": 1}, {"workflow": {}})
            self.assertIs(result["result"][0], self.images)
            self.assertTrue((self.output / "hook.png").exists())
            module.connector.launch = Mock(side_effect=OSError("Cannot read PureRef scene"))
            request = types.SimpleNamespace(remote="127.0.0.1", json=AsyncMock(return_value={}))
            response = asyncio.run(module.open_board(request))
            self.assertEqual(response.status, 400)
            self.assertEqual(json.loads(response.body), {"error": "Cannot read PureRef scene"})

    def test_temporary_save_failure_falls_back_and_restores_owner(self):
        original = self.controller.original_save

        def save(owner, *args):
            if owner.type == "temp":
                raise PermissionError("Temporary directory is unavailable")
            return original(owner, *args)

        self.controller.original_save = save
        result = self.save()
        self.controller.launch.assert_not_called()
        self.assertEqual(result["ui"]["images"][0]["type"], "output")
        self.assertEqual(self.owner.output_dir, str(self.output))
        self.assertEqual(self.owner.type, "output")

    def test_failed_update_keeps_reserved_slots_to_avoid_overlap(self):
        self.controller.board.add.side_effect = RuntimeError("CLI update failed")
        result = self.save()
        self.assertEqual(result["ui"]["images"][0]["type"], "output")
        records = list(self.controller.directory.glob("*.json"))
        self.assertEqual(len(records), 1)
        self.assertEqual(json.loads(records[0].read_text())["next_x"], 320)
        self.controller.board.add.side_effect = None
        self.save()
        self.assertEqual(self.controller.board.add.call_args.args[1], [(448, 128)])

    def test_saved_settings_are_validated_like_updates(self):
        defaults = self.connector_module.DEFAULTS
        self.controller.config_path.write_text(json.dumps({"mode": "pureref_only", "enabled": True}))
        self.assertEqual(self.connector_module.Connector().settings, dict(defaults, mode="pureref_only"))
        self.controller.config_path.write_text(json.dumps({"mode": "pureref_only", "images_per_row": "6"}))
        self.assertEqual(self.connector_module.Connector().settings, defaults)
        with self.assertRaisesRegex(ValueError, "positive whole number"):
            self.controller.update({"images_per_row": 0})

    def test_separate_generations_fill_six_columns_before_starting_a_row(self):
        positions = []
        for _ in range(7):
            self.save()
            positions.extend(self.controller.board.add.call_args.args[1])
        self.assertEqual(positions, [(128, 128), (448, 128), (768, 128), (1088, 128), (1408, 128), (1728, 128), (128, 448)])

    def test_mixed_sizes_use_the_tallest_image_for_the_next_row(self):
        self.controller.settings["images_per_row"] = 2
        self.images.shape = (1, 512, 256, 3)
        self.save()
        self.images.shape = (1, 128, 128, 3)
        self.save()
        self.assertEqual(self.controller.board.add.call_args.args[1], [(384, 64)])
        self.save()
        self.assertEqual(self.controller.board.add.call_args.args[1], [(64, 640)])

    def test_read_only_layout_state_falls_back_before_importing(self):
        with patch.object(Path, "write_text", side_effect=PermissionError("Settings directory is read-only")):
            result = self.save()
        self.controller.board.add.assert_not_called()
        self.assertEqual(result["ui"]["images"][0]["type"], "output")

    def test_preview_subclass_is_not_intercepted(self):
        preview = PreviewImage(self.temp)
        preview.type = "temp"
        result = self.save(preview)
        self.controller.launch.assert_not_called()
        self.assertEqual(result["ui"]["images"][0]["type"], "temp")

    def test_disabled_uses_unchanged_save_path(self):
        self.controller.enabled = False
        self.save()
        self.controller.launch.assert_not_called()
        self.assertTrue((self.output / "test.png").exists())

    def test_pause_on_close_uses_normal_saving(self):
        self.controller.settings["when_closed"] = "pause"
        self.controller.was_open = True
        self.controller.board.alive.return_value = False
        self.save()
        self.assertFalse(self.controller.enabled)
        self.controller.launch.assert_not_called()
        self.assertTrue((self.output / "test.png").exists())

    def test_restart_does_not_restore_session_enable(self):
        self.controller.validate_executable = Mock()
        self.controller.update({"enabled": True})
        values = json.loads(self.controller.config_path.read_text())
        self.assertNotIn("enabled", values)
        self.assertFalse(values["enable_on_startup"])


if __name__ == "__main__":
    unittest.main()

import hashlib
import json
import logging
import os
from pathlib import Path
import threading

import folder_paths
import nodes
from server import PromptServer

from .windows import Board


DEFAULTS = {
    "document": "", "executable": r"C:\Program Files\PureRef\PureRef.exe",
    "mode": "both", "when_closed": "reopen", "enable_on_startup": False, "images_per_row": 6,
}


def clean(settings, values):
    if not isinstance(values, dict):
        raise ValueError("Expected connector settings.")
    candidate = dict(settings)
    candidate.update({key: values[key] for key in candidate if key in values})
    for key in ("document", "executable"):
        if not isinstance(candidate[key], str):
            raise ValueError(f"Invalid {key}.")
        candidate[key] = candidate[key].strip()
    if candidate["mode"] not in ("both", "pureref_only") or candidate["when_closed"] not in ("reopen", "pause"):
        raise ValueError("Invalid save mode or window behavior.")
    if type(candidate["enable_on_startup"]) is not bool:
        raise ValueError("Invalid enable setting.")
    if type(candidate["images_per_row"]) is not int or candidate["images_per_row"] < 1:
        raise ValueError("Images per row must be a positive whole number.")
    if candidate["document"]:
        path = Path(candidate["document"])
        if not path.is_absolute() or path.suffix.lower() != ".pur":
            raise ValueError("Choose an absolute path ending in .pur.")
        if str(path).startswith("\\\\"):
            raise ValueError("Choose a board on a local drive.")
        candidate["document"] = os.path.abspath(path)
    return candidate


class Connector:
    def __init__(self):
        self.directory = Path(folder_paths.get_system_user_directory("pureref_connector"))
        self.directory.mkdir(parents=True, exist_ok=True)
        self.config_path = self.directory / "settings.json"
        self.lock = threading.RLock()
        self.settings = dict(DEFAULTS)
        if self.config_path.exists():
            try:
                self.settings = clean(DEFAULTS, json.loads(self.config_path.read_text(encoding="utf-8")))
            except (OSError, ValueError):
                logging.warning("PureRef: settings could not be loaded; using defaults.")
        self.enabled = self.settings["enable_on_startup"]
        self.error = ""
        self.last_warning = ""
        self.status = "Ready" if self.enabled else "Disabled"
        self.board = None
        self.was_open = False
        self.original_save = nodes.SaveImage.save_images

    def snapshot(self):
        return dict(self.settings, enabled=self.enabled, status=self.status, error=self.error)

    def notify(self, warning=False):
        PromptServer.instance.send_sync("pureref_status", dict(self.snapshot(), warning=warning))

    def update(self, values):
        with self.lock:
            candidate = clean(self.settings, values)
            enabled = values.get("enabled", self.enabled)
            if type(enabled) is not bool:
                raise ValueError("Invalid enable setting.")
            if enabled and not candidate["document"]:
                raise ValueError("Choose a PureRef document first.")
            if enabled:
                self.validate_executable(candidate["executable"])
            if self.board is not None and self.board.alive() and candidate["document"] != self.settings["document"]:
                raise ValueError("Close the connector's PureRef window before changing boards.")
            self.config_path.write_text(json.dumps(candidate, indent=2), encoding="utf-8")
            self.settings = candidate
            if enabled and not self.enabled:
                self.was_open = False
            self.enabled = enabled
            self.error = ""
            self.last_warning = ""
            self.status = "Ready" if enabled else "Disabled"
            self.notify()
            return self.snapshot()

    @staticmethod
    def validate_executable(executable):
        path = Path(executable)
        if not path.is_absolute() or path.name.lower() != "pureref.exe" or not path.is_file() or str(path).startswith("\\\\"):
            raise ValueError("Choose the installed PureRef.exe on a local drive.")

    def launch(self):
        self.validate_executable(self.settings["executable"])
        if not self.settings["document"]:
            raise ValueError("Choose a PureRef document first.")
        if self.board is None:
            self.board = Board(self.directory / "native")
        if not Path(self.settings["document"]).exists():
            self.slots().unlink(missing_ok=True)
        self.board.open(self.settings["executable"], self.settings["document"])
        self.was_open = True

    def open_board(self, values):
        with self.lock:
            self.update(values)
            self.launch()
            self.status = "Board open" if self.enabled else "Board open · connector disabled"
            self.notify()
            return self.snapshot()

    def slots(self):
        digest = hashlib.sha256(self.settings["document"].casefold().encode("utf-8")).hexdigest()[:20]
        return self.directory / (digest + ".json")

    def reserve(self, count, width, height):
        path = self.slots()
        layout = {"next_row": 0, "next_x": 0, "column": 0, "row_height": 0}
        if path.exists():
            layout.update(json.loads(path.read_text(encoding="utf-8")))
        positions = []
        for _ in range(count):
            if layout["column"] >= self.settings["images_per_row"]:
                layout["next_row"] += layout["row_height"] + 64
                layout["next_x"] = layout["column"] = layout["row_height"] = 0
            positions.append((layout["next_x"] + width // 2, layout["next_row"] + height // 2))
            layout["next_x"] += width + 64
            layout["column"] += 1
            layout["row_height"] = max(layout["row_height"], height)
        # Replace atomically so an interrupted write cannot leave an unreadable slot file.
        temporary = path.with_suffix(".tmp")
        temporary.write_text(json.dumps(layout), encoding="utf-8")
        os.replace(temporary, path)
        return positions

    def failure(self, message):
        self.error = f"{message.rstrip('.')}. Images were saved normally."
        self.status = "Saved to normal output folder"
        warning = self.error != self.last_warning
        if warning:
            logging.warning("PureRef: %s", self.error)
            self.last_warning = self.error
        self.notify(warning=warning)

    def capture(self, owner, images, filename_prefix, prompt, extra_pnginfo):
        with self.lock:
            if not self.enabled or type(owner) is not nodes.SaveImage:
                return self.original_save(owner, images, filename_prefix, prompt, extra_pnginfo)
            if self.was_open and self.board is not None and not self.board.alive() and self.settings["when_closed"] == "pause":
                self.enabled = False
                self.status = "Paused · PureRef window closed"
                self.notify()
                return self.original_save(owner, images, filename_prefix, prompt, extra_pnginfo)
            pure_only = self.settings["mode"] == "pureref_only"
            directory, output_type = owner.output_dir, owner.type
            try:
                if pure_only:
                    owner.output_dir = folder_paths.get_temp_directory()
                    owner.type = "temp"
                result = self.original_save(owner, images, filename_prefix, prompt, extra_pnginfo)
                files = [Path(owner.output_dir) / item["subfolder"] / item["filename"] for item in result["ui"]["images"]]
            except OSError:
                if not pure_only:
                    raise
                owner.output_dir, owner.type = directory, output_type
                result = self.original_save(owner, images, filename_prefix, prompt, extra_pnginfo)
                self.failure("Cannot create temporary PureRef PNGs.")
                return result
            finally:
                owner.output_dir, owner.type = directory, output_type
            try:
                self.launch()
                positions = self.reserve(len(files), int(images.shape[2]), int(images.shape[1]))
                self.board.add(files, positions)
                self.error = ""
                self.last_warning = ""
                self.status = f"Saved {len(files)} image{'s' if len(files) != 1 else ''} to PureRef"
                self.notify()
                return result
            except Exception as exc:
                self.failure(str(exc))
                if pure_only:
                    return self.original_save(owner, images, filename_prefix, prompt, extra_pnginfo)
                return result

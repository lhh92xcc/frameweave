from __future__ import annotations

import json
import os
from pathlib import Path

from studio.models import RoutingSettings
from studio.store import digest


class SettingsStore:
    def __init__(self, root: Path):
        self.path = root / "settings.json"

    def get(self):
        return (
            RoutingSettings.model_validate_json(self.path.read_text())
            if self.path.exists()
            else RoutingSettings()
        )

    @staticmethod
    def public(settings):
        data = settings.model_dump()
        for route in [*data["text"], *data["image"], *data["video"], data["audio"]]:
            route["configured"] = bool(route.pop("api_key"))
            route.pop("clear_key", None)
        return data

    def save(self, value: RoutingSettings):
        previous = self.get().model_dump()
        data = value.model_dump()
        for kind in ["text", "image", "video", "audio"]:
            entries = [data[kind]] if kind == "audio" else data[kind]
            old_entries = [previous[kind]] if kind == "audio" else previous[kind]
            for route in entries:
                if not route["api_key"] and not route.pop("clear_key", False):
                    old = next(
                        (
                            r
                            for r in old_entries
                            if (r["transport"], r["provider"], r["base_url"])
                            == (route["transport"], route["provider"], route["base_url"])
                        ),
                        {},
                    )
                    route["api_key"] = old.get("api_key")
        checked = RoutingSettings.model_validate(data)
        temp = self.path.with_suffix(".tmp")
        fd = os.open(temp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w") as f:
            json.dump(checked.model_dump(), f, ensure_ascii=False, indent=2)
        os.replace(temp, self.path)
        return self.public(checked)

    def fingerprint(self):
        return digest(self.get().model_dump())

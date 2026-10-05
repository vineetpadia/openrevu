"""Tool chest: reusable markup presets saved as JSON."""
from __future__ import annotations

import json
import os

DEFAULT_PATH = os.path.join(os.path.expanduser("~"), ".config", "openrevu", "toolchest.json")


class ToolChest:
    def __init__(self, path: str = DEFAULT_PATH):
        self.path = path
        self.items: dict[str, dict] = {}
        if os.path.exists(path):
            with open(path) as f:
                self.items = json.load(f)

    def add(self, name: str, markup) -> None:
        self.items[name] = markup.to_dict()
        self.save()

    def remove(self, name: str) -> None:
        del self.items[name]
        self.save()

    def place(self, doc, name: str, pno: int, at) -> object:
        """Place the saved markup so that its top-left lands on `at`."""
        d = self.items[name]
        x0, y0 = d["rect"][0], d["rect"][1]
        return doc.add_from_dict(pno, d, offset=(at[0] - x0, at[1] - y0))

    def save(self) -> None:
        os.makedirs(os.path.dirname(self.path), exist_ok=True)
        with open(self.path, "w") as f:
            json.dump(self.items, f, indent=1)

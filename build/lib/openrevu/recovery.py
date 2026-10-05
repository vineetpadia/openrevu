"""Autosave and crash recovery.

While a document has unsaved changes, OpenRevu keeps a snapshot of it in the recovery folder.
A snapshot is removed when the document is saved or closed. After a crash the snapshots stay,
and the next start offers to restore them. A snapshot of a password-protected document is encrypted."""
from __future__ import annotations

import json
import os
import sys
import time
import uuid


def recovery_dir() -> str:
    """The folder for snapshots. OPENREVU_RECOVERY_DIR overrides it (used by the tests)."""
    d = os.environ.get("OPENREVU_RECOVERY_DIR")
    if not d:
        if sys.platform.startswith("win"):
            base = os.environ.get("LOCALAPPDATA") or os.path.expanduser("~")
            d = os.path.join(base, "OpenRevu", "recovery")
        elif sys.platform == "darwin":
            d = os.path.expanduser("~/Library/Application Support/OpenRevu/recovery")
        else:
            d = os.path.join(os.environ.get("XDG_DATA_HOME") or os.path.expanduser("~/.local/share"), "openrevu", "recovery")
    os.makedirs(d, exist_ok=True)
    return d


def new_id() -> str:
    return uuid.uuid4().hex


def _paths(doc_id: str):
    d = recovery_dir()
    return os.path.join(d, doc_id + ".pdf"), os.path.join(d, doc_id + ".json")


def pid_alive(pid: int) -> bool:
    """True if a process with this id is running."""
    if pid <= 0:
        return False
    if sys.platform.startswith("win"):
        import ctypes
        h = ctypes.windll.kernel32.OpenProcess(0x1000, False, pid)   # PROCESS_QUERY_LIMITED_INFORMATION
        if h:
            ctypes.windll.kernel32.CloseHandle(h)
            return True
        return False
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True   # the process exists but belongs to someone else
    return True


def write(doc_id: str, data: bytes, original_path: str | None, title: str, encrypted: bool) -> None:
    """Write a snapshot. The files are replaced in one step, so a crash while writing never leaves half a file."""
    pdf, meta = _paths(doc_id)
    for path, payload, mode in ((pdf, data, "wb"), (meta, json.dumps({
            "id": doc_id, "pid": os.getpid(), "path": original_path, "title": title,
            "time": time.time(), "encrypted": encrypted}), "w")):
        tmp = path + ".tmp"
        with open(tmp, mode, **({"encoding": "utf-8"} if mode == "w" else {})) as f:
            f.write(payload)
        os.replace(tmp, path)


def remove(doc_id: str) -> None:
    for p in _paths(doc_id):
        try:
            os.remove(p)
        except FileNotFoundError:
            pass


def entries() -> list[dict]:
    """Snapshots left by sessions that are no longer running, newest first. Each has the keys of the
    metadata file plus "file" (the snapshot PDF). Damaged entries are removed."""
    out = []
    d = recovery_dir()
    for name in os.listdir(d):
        if not name.endswith(".json"):
            continue
        meta_path = os.path.join(d, name)
        try:
            with open(meta_path, encoding="utf-8") as f:
                m = json.load(f)
            pdf = os.path.join(d, m["id"] + ".pdf")
            if not os.path.exists(pdf):
                raise ValueError("missing snapshot")
        except (OSError, ValueError, KeyError):
            try:
                os.remove(meta_path)
            except OSError:
                pass
            continue
        if m.get("pid") != os.getpid() and pid_alive(int(m.get("pid", 0))):
            continue                       # another running OpenRevu owns this one
        if m.get("pid") == os.getpid():
            continue                       # our own snapshot
        m["file"] = pdf
        out.append(m)
    for name in os.listdir(d):             # leftovers of an interrupted write
        if name.endswith(".tmp"):
            try:
                os.remove(os.path.join(d, name))
            except OSError:
                pass
    return sorted(out, key=lambda m: -m.get("time", 0))

import json
import os

import fitz
import pytest

from openrevu import recovery
from openrevu.core import Document

pytest.importorskip("PyQt5")
from PyQt5 import QtWidgets

from openrevu.gui import Main
from tests.test_sheets import SET_V1, make_set


@pytest.fixture(scope="module")
def app():
    return QtWidgets.QApplication.instance() or QtWidgets.QApplication([])


@pytest.fixture
def pdf(tmp_path):
    return make_set(tmp_path / "work.pdf", SET_V1)


def files(d):
    return sorted(os.listdir(d)) if os.path.isdir(d) else []


def choose(monkeypatch, label):
    """Make the recovery question answer with the button called `label`."""
    def exec_(self):
        for b in self.buttons():
            if b.text() == label:
                self._clicked = b
        return 0
    monkeypatch.setattr(QtWidgets.QMessageBox, "exec_", exec_)
    monkeypatch.setattr(QtWidgets.QMessageBox, "clickedButton", lambda self: getattr(self, "_clicked", None))


def crash(w):
    """Leave without cleanup, as a crash does: no close_tab, no removal of snapshots."""
    w.autosave_timer.stop()


def as_dead_session(isolated):
    """Mark every snapshot as written by a process that no longer exists."""
    for name in os.listdir(isolated):
        if name.endswith(".json"):
            p = os.path.join(isolated, name)
            m = json.load(open(p)); m["pid"] = 999999991; json.dump(m, open(p, "w"))


def test_unmodified_documents_are_not_snapshotted_and_modified_ones_are(app, pdf, isolated_recovery_dir):
    w = Main(pdf); app.processEvents()
    assert w.autosave_now() == 0 and files(isolated_recovery_dir) == []
    w.doc.add_rect(0, fitz.Rect(10, 10, 60, 60)); w._after_change()
    assert w.autosave_now() == 1 and len(files(isolated_recovery_dir)) == 2           # one .pdf and one .json
    assert w.autosave_now() == 0                                                       # nothing changed since
    w.doc.add_rect(0, fitz.Rect(70, 70, 90, 90)); w._after_change()
    assert w.autosave_now() == 1
    snap = [f for f in files(isolated_recovery_dir) if f.endswith(".pdf")][0]
    assert len(Document(os.path.join(isolated_recovery_dir, snap)).markups()) == 2     # the latest state
    assert not [f for f in files(isolated_recovery_dir) if f.endswith(".tmp")]         # writes are completed


def test_saving_or_discarding_removes_the_snapshot(app, pdf, tmp_path, isolated_recovery_dir, monkeypatch):
    w = Main(pdf)
    w.doc.add_rect(0, fitz.Rect(10, 10, 60, 60)); w._after_change(); w.autosave_now()
    assert len(files(isolated_recovery_dir)) == 2
    w.save()
    assert files(isolated_recovery_dir) == []
    w.doc.add_rect(0, fitz.Rect(70, 70, 90, 90)); w._after_change(); w.autosave_now()
    assert len(files(isolated_recovery_dir)) == 2
    monkeypatch.setattr(QtWidgets.QMessageBox, "question", staticmethod(lambda *a, **k: QtWidgets.QMessageBox.Discard))
    w.close_tab(0)
    assert files(isolated_recovery_dir) == []
    w2 = Main(pdf); w2.doc.add_rect(0, fitz.Rect(1, 1, 9, 9)); w2._after_change(); w2.autosave_now()
    monkeypatch.setattr(QtWidgets.QFileDialog, "getSaveFileName", staticmethod(lambda *a, **k: (str(tmp_path / "copy.pdf"), "")))
    w2.save_as()
    assert files(isolated_recovery_dir) == [] and os.path.exists(tmp_path / "copy.pdf")


def test_crash_then_restart_restores_the_unsaved_work(app, pdf, tmp_path, isolated_recovery_dir, monkeypatch):
    w = Main(pdf)
    w.doc.add_rect(0, fitz.Rect(10, 10, 60, 60)); w.doc.add_length(0, [(0, 100), (100, 100)]); w.doc.set_scale(__import__("openrevu.core", fromlist=["Scale"]).Scale("m", 0.1))
    w._after_change(); w.autosave_now(); crash(w)
    original_bytes = open(pdf, "rb").read()
    as_dead_session(isolated_recovery_dir)
    w2 = Main(); choose(monkeypatch, "Recover")
    assert w2.offer_recovery() == 1
    assert w2.tabs.count() == 1 and w2.tabs.tabText(0) == "*work.pdf (recovered)"
    d = w2.doc
    assert d.path is None and d.modified and len(d.markups()) == 2 and d.scale.unit == "m"       # the work and the scale are back
    assert files(isolated_recovery_dir) == []                                                    # taken over by the new session
    assert open(pdf, "rb").read() == original_bytes                                              # the original file is untouched
    shown = {}
    monkeypatch.setattr(QtWidgets.QFileDialog, "getSaveFileName", staticmethod(lambda p, t, start, *a, **k: (shown.update(start=start) or str(tmp_path / "restored.pdf"), "")))
    w2.save()
    assert shown["start"] == pdf and len(Document(str(tmp_path / "restored.pdf")).markups()) == 2   # the dialog starts at the original name


def test_discard_and_ask_later(app, pdf, isolated_recovery_dir, monkeypatch):
    w = Main(pdf); w.doc.add_rect(0, fitz.Rect(10, 10, 60, 60)); w._after_change(); w.autosave_now(); crash(w)
    as_dead_session(isolated_recovery_dir)
    w2 = Main(); choose(monkeypatch, "Ask me later")
    assert w2.offer_recovery() == 0 and len(files(isolated_recovery_dir)) == 2 and w2.tabs.count() == 0     # kept for next time
    choose(monkeypatch, "Discard")
    assert w2.offer_recovery() == 0 and files(isolated_recovery_dir) == []
    assert w2.offer_recovery() == 0                                                                        # nothing left to offer


def test_a_live_session_and_our_own_snapshots_are_not_offered(app, pdf, isolated_recovery_dir):
    w = Main(pdf); w.doc.add_rect(0, fitz.Rect(10, 10, 60, 60)); w._after_change(); w.autosave_now()
    assert recovery.entries() == []                                                    # our own process
    for name in os.listdir(isolated_recovery_dir):
        if name.endswith(".json"):
            p = os.path.join(isolated_recovery_dir, name)
            m = json.load(open(p)); m["pid"] = os.getppid(); json.dump(m, open(p, "w"))     # another live process
    assert recovery.entries() == [] and recovery.pid_alive(os.getppid()) and not recovery.pid_alive(999999991)
    assert not recovery.pid_alive(0) and not recovery.pid_alive(-5)


def test_protected_documents_are_never_written_in_plain(app, pdf, tmp_path, isolated_recovery_dir, monkeypatch):
    enc = str(tmp_path / "e.pdf"); Document(pdf).save_encrypted(enc, "secret")
    w = Main()
    w.open(enc, "secret")
    w.doc.add_rect(0, fitz.Rect(10, 10, 60, 60)); w._after_change(); w.autosave_now(); crash(w)
    snap = [f for f in files(isolated_recovery_dir) if f.endswith(".pdf")][0]
    assert fitz.open(os.path.join(isolated_recovery_dir, snap)).needs_pass                 # encrypted on disk
    assert json.load(open(os.path.join(isolated_recovery_dir, [f for f in files(isolated_recovery_dir) if f.endswith(".json")][0])))["encrypted"]
    as_dead_session(isolated_recovery_dir)
    w2 = Main(); choose(monkeypatch, "Recover")
    monkeypatch.setattr(QtWidgets.QInputDialog, "getText", staticmethod(lambda *a, **k: ("secret", True)))
    assert w2.offer_recovery() == 1 and len(w2.doc.markups()) == 1


def test_wrong_password_skips_that_document_only(app, pdf, tmp_path, isolated_recovery_dir, monkeypatch):
    enc = str(tmp_path / "e.pdf"); Document(pdf).save_encrypted(enc, "secret")
    w = Main(); w.open(enc, "secret"); w.doc.add_rect(0, fitz.Rect(10, 10, 60, 60)); w._after_change()
    w.open(pdf); w.doc.add_rect(0, fitz.Rect(5, 5, 20, 20)); w._after_change(); w.autosave_now(); crash(w)
    as_dead_session(isolated_recovery_dir)
    w2 = Main(); choose(monkeypatch, "Recover")
    monkeypatch.setattr(QtWidgets.QInputDialog, "getText", staticmethod(lambda *a, **k: ("", False)))   # the user cancels the password
    assert w2.offer_recovery() == 1 and w2.tabs.count() == 1


def test_damaged_snapshots_are_cleaned_up_not_offered(app, isolated_recovery_dir, monkeypatch):
    d = recovery.recovery_dir()
    open(os.path.join(d, "a.json"), "w").write("{not json")
    json.dump({"id": "b", "pid": 999999991, "title": "x", "time": 1}, open(os.path.join(d, "b.json"), "w"))     # no pdf
    json.dump({"id": "c", "pid": 999999991, "title": "bad", "time": 2, "path": None}, open(os.path.join(d, "c.json"), "w"))
    open(os.path.join(d, "c.pdf"), "wb").write(b"%PDF-1.4 this is not a real pdf")
    open(os.path.join(d, "stale.pdf.tmp"), "wb").write(b"half")
    ents = recovery.entries()
    assert [e["id"] for e in ents] == ["c"] and "a.json" not in files(d) and "b.json" not in files(d) and "stale.pdf.tmp" not in files(d)
    w = Main(); choose(monkeypatch, "Recover")
    warned = []
    monkeypatch.setattr(QtWidgets.QMessageBox, "warning", staticmethod(lambda *a, **k: warned.append(a[2])))
    assert w.offer_recovery() == 0 and warned and "could not be recovered" in warned[0] and files(d) == []


def test_a_full_disk_does_not_interrupt_work(app, pdf, isolated_recovery_dir, monkeypatch):
    w = Main(pdf); w.doc.add_rect(0, fitz.Rect(10, 10, 60, 60)); w._after_change()
    monkeypatch.setattr(recovery, "write", lambda *a, **k: (_ for _ in ()).throw(OSError("No space left on device")))
    assert w.autosave_now() == 0 and "Autosave failed" in w.statusBar().currentMessage()
    monkeypatch.undo()
    assert w.autosave_now() == 1


def test_timer_runs_autosave(app, pdf, isolated_recovery_dir, monkeypatch):
    monkeypatch.setenv("OPENREVU_AUTOSAVE_SECONDS", "0.05")
    w = Main(pdf); w.doc.add_rect(0, fitz.Rect(10, 10, 60, 60)); w._after_change()
    import time
    t0 = time.time()
    while time.time() - t0 < 3 and not files(isolated_recovery_dir):
        app.processEvents(); time.sleep(0.02)
    assert len(files(isolated_recovery_dir)) == 2

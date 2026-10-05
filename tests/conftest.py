import os

# Tests must never read or write the real user preferences (recent files, window layout).
os.environ["OPENREVU_NO_SETTINGS"] = "1"
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")


import pytest


@pytest.fixture(autouse=True)
def no_unexpected_dialogs(monkeypatch):
    """A modal dialog would block a headless test forever. Make unexpected ones fail at once.
    Tests that expect a dialog patch it themselves (their patch replaces this one)."""
    try:
        from PyQt5 import QtWidgets as W
    except ImportError:
        return

    def boom(name):
        def f(*a, **k):
            raise AssertionError(f"unexpected dialog: {name} {a[1:3] if len(a) > 2 else ''}")
        return staticmethod(f)

    for cls, names in ((W.QMessageBox, ("warning", "critical", "information", "question")),
                       (W.QInputDialog, ("getText", "getInt", "getDouble", "getItem", "getMultiLineText")),
                       (W.QFileDialog, ("getOpenFileName", "getOpenFileNames", "getSaveFileName", "getExistingDirectory"))):
        for n in names:
            monkeypatch.setattr(cls, n, boom(f"{cls.__name__}.{n}"))


@pytest.fixture(autouse=True)
def fail_on_unhandled_exceptions():
    """An exception in a Qt event handler would abort the whole test run. Record it and fail only that test."""
    import sys
    errors = []
    old = sys.excepthook
    sys.excepthook = lambda t, v, tb: errors.append("".join(__import__("traceback").format_exception(t, v, tb)))
    yield
    sys.excepthook = old
    assert not errors, "unhandled exception in an event handler:\n" + errors[0]

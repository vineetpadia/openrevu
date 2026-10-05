import functools


def mutates(fn):
    """Checkpoint for undo before a top-level mutating Document method."""
    @functools.wraps(fn)
    def wrapper(self, *a, **kw):
        outer = self._depth == 0
        if outer:
            saved = (list(self._undo), self._redo, self.modified)
            self.checkpoint()
        self._depth += 1
        try:
            return fn(self, *a, **kw)
        except Exception:
            if outer:  # failed operation: restore undo/redo/dirty state exactly as before
                self._undo[:], self._redo, self.modified = saved[0], saved[1], saved[2]
            raise
        finally:
            self._depth -= 1
    return wrapper

import functools


def mutates(fn):
    """Checkpoint for undo before a top-level mutating Document method."""
    @functools.wraps(fn)
    def wrapper(self, *a, **kw):
        outer = self._depth == 0
        if outer:
            saved = (list(self._undo), self._redo, self.modified)
            self.checkpoint()
            snapshot = self._undo[-1]          # the state before this operation. Nested calls may push more snapshots.
        self._depth += 1
        try:
            return fn(self, *a, **kw)
        except Exception:
            if outer:
                # A failed operation may have changed the document before it failed (a page, an object count, or just
                # a value inside an object). Going back to the snapshot taken before it started is cheap and always
                # correct. Markup objects fetched earlier are stale after this: fetch them again.
                self._reopen(snapshot)
                self._undo[:], self._redo, self.modified = saved[0], saved[1], saved[2]
            raise
        finally:
            self._depth -= 1
    return wrapper

import functools


def mutates(fn):
    """Checkpoint for undo before a top-level mutating Document method."""
    @functools.wraps(fn)
    def wrapper(self, *a, **kw):
        outer = self._depth == 0
        if outer:
            saved = (list(self._undo), self._redo, self.modified)
            self.checkpoint()
            shape = (len(self.doc), self.doc.xref_length())
        self._depth += 1
        try:
            return fn(self, *a, **kw)
        except Exception:
            if outer:
                # failed operation: if it already changed the page or object count, go back to the snapshot taken
                # before it started, so a half-applied operation never stays in the document
                if (len(self.doc), self.doc.xref_length()) != shape:
                    self._reopen(self._undo.pop())
                self._undo[:], self._redo, self.modified = saved[0], saved[1], saved[2]
            raise
        finally:
            self._depth -= 1
    return wrapper

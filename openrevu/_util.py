import functools


def mutates(fn):
    """Checkpoint for undo before a top-level mutating Document method."""
    @functools.wraps(fn)
    def wrapper(self, *a, **kw):
        outer = self._depth == 0
        if outer:
            self.checkpoint()
        self._depth += 1
        try:
            return fn(self, *a, **kw)
        finally:
            self._depth -= 1
    return wrapper

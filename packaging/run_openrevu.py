"""Entry point for the packaged application (PyInstaller)."""
import multiprocessing

from openrevu.gui import main

if __name__ == "__main__":
    multiprocessing.freeze_support()
    raise SystemExit(main())

"""Check that the version is the same everywhere. Usage: python packaging/check_version.py [vX.Y.Z]
Exit code 0 if pyproject.toml and openrevu/__init__.py agree (and with the tag, if one is given)."""
import pathlib
import re
import sys

root = pathlib.Path(__file__).resolve().parent.parent


def versions():
    py = re.search(r'^version\s*=\s*"([^"]+)"', (root / "pyproject.toml").read_text(), re.M).group(1)
    init = re.search(r'__version__\s*=\s*"([^"]+)"', (root / "openrevu" / "__init__.py").read_text()).group(1)
    return py, init


def main(argv):
    py, init = versions()
    problems = []
    if py != init:
        problems.append(f"pyproject.toml says {py} but openrevu/__init__.py says {init}")
    if len(argv) > 1 and argv[1] and argv[1].lstrip("v") != py:      # an empty tag means "no tag" (a manual run)
        problems.append(f"the tag {argv[1]} does not match the version {py}")
    for p in problems:
        print("version check failed:", p)
    if not problems:
        print(f"version {py}: ok")
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))

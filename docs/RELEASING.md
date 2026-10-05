# Making a release

1. Change the version in **both** `pyproject.toml` and `openrevu/__init__.py`.
   Run `python packaging/check_version.py` to check that they agree.
2. Run the tests: `pytest`. Make sure the CI run on GitHub is green.
3. Commit and push.
4. Make a tag and push it: `git tag v0.4.0 && git push origin v0.4.0`.
5. The workflow `release` builds the program for Linux, Windows, and macOS, runs a self-test on each build,
   and attaches the files to a new GitHub release. The tag must match the version, or the build stops.

To test the build without making a release, open the **Actions** tab, pick **release**, and press **Run workflow**.
The files stay as artifacts of that run.

## What is in the packaged program

The program includes Python, Qt, PyMuPDF, numpy, and scipy. It does **not** include these external programs.
Install them if you want the matching function:

| Function | Program |
|---|---|
| OCR | Tesseract |
| PDF/A export | Ghostscript |
| PDF/A check | veraPDF |

The macOS and Windows programs are not code-signed. The system can show a warning when you start them the first time.

## Publishing to PyPI (not set up)

The workflow builds the Python package (`dist/*.whl` and `.tar.gz`) but does not upload it.
To upload it, create the project on pypi.org, add this repository as a *trusted publisher*, and add a job that uses
`pypa/gh-action-pypi-publish`. This needs your PyPI account, so it is left to you.

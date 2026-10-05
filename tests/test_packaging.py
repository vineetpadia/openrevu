import pathlib
import pytest

tomllib = pytest.importorskip("tomllib")  # Python >= 3.11

import openrevu

ROOT = pathlib.Path(__file__).resolve().parent.parent


def test_pyproject_is_valid_and_versions_agree():
    meta = tomllib.loads((ROOT / "pyproject.toml").read_text())
    proj = meta["project"]
    assert proj["name"] == "openrevu" and proj["version"] == openrevu.__version__
    assert {"pymupdf>=1.24", "PyQt5>=5.15"} <= set(proj["dependencies"])
    assert set(proj["scripts"]) == {"openrevu", "openrevu-cli"}
    assert {"sign", "fill", "test"} <= set(proj["optional-dependencies"])
    assert "build-system" in meta

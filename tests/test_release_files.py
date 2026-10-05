import pathlib
import subprocess
import sys

import pytest

ROOT = pathlib.Path(__file__).resolve().parent.parent


def run_check(*args):
    return subprocess.run([sys.executable, str(ROOT / "packaging" / "check_version.py"), *args], capture_output=True, text=True)


def test_versions_agree_and_tag_mismatch_is_caught():
    import openrevu
    ok = run_check()
    assert ok.returncode == 0 and openrevu.__version__ in ok.stdout
    assert run_check(f"v{openrevu.__version__}").returncode == 0
    assert run_check("").returncode == 0                       # a manual run has no tag
    bad = run_check("v99.0.0")
    assert bad.returncode == 1 and "does not match" in bad.stdout


def test_release_workflow_is_valid_and_complete():
    yaml = pytest.importorskip("yaml")
    wf = yaml.safe_load((ROOT / ".github" / "workflows" / "release.yml").read_text())
    triggers = wf.get("on") or wf.get(True)                      # YAML reads the key "on" as True
    assert "v*" in triggers["push"]["tags"] and "workflow_dispatch" in triggers
    build = wf["jobs"]["build"]
    assert set(build["strategy"]["matrix"]["os"]) == {"ubuntu-latest", "windows-latest", "macos-latest"}
    steps = " ".join(str(s.get("run", "")) for s in build["steps"])
    assert "pyinstaller" in steps and "--selftest" in steps and "check_version" in steps
    rel = wf["jobs"]["release"]
    assert rel["if"] == "github.ref_type == 'tag'" and rel["permissions"] == {"contents": "write"}
    assert set(rel["needs"]) == {"build", "python-package"}
    assert "permissions" not in build                              # the build jobs cannot write to the repository


def test_spec_and_entry_point_exist_and_include_the_icons():
    spec = (ROOT / "packaging" / "openrevu.spec").read_text()
    compile(spec, "openrevu.spec", "exec")
    assert "icons_svg" in spec and "platforms" in spec and "LICENSE" in spec
    assert "main()" in (ROOT / "packaging" / "run_openrevu.py").read_text()
    assert (ROOT / "LICENSE").exists() and (ROOT / "THIRD_PARTY_NOTICES.md").exists()


def test_version_and_selftest_flags_work_without_a_window(tmp_path):
    import os
    env = dict(os.environ, QT_QPA_PLATFORM="offscreen", OPENREVU_NO_SETTINGS="1")
    v = subprocess.run([sys.executable, "-m", "openrevu", "--version"], capture_output=True, text=True, env=env, cwd=ROOT)
    assert v.returncode == 0 and v.stdout.strip().startswith("OpenRevu ")
    t = subprocess.run([sys.executable, "-m", "openrevu", "--selftest"], capture_output=True, text=True, env=env, cwd=ROOT, timeout=120)
    assert t.returncode == 0 and "self-test passed: window, markup, save, reopen" in t.stdout

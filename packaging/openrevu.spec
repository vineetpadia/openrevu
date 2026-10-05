# PyInstaller spec: one folder with the application. Build with:  pyinstaller packaging/openrevu.spec
import os

import glob
import PyQt5

root = os.path.abspath(os.path.join(SPECPATH, ".."))

# PyInstaller's Qt hook does not always find the Qt plugins (the display plugin is essential: without it the
# program cannot open a window). Collect them explicitly, so the result is the same on every machine.
qt_plugins = os.path.join(os.path.dirname(PyQt5.__file__), "Qt5", "plugins")
plugin_binaries = []
for sub in ("platforms", "imageformats", "iconengines", "styles", "platformthemes", "xcbglintegrations"):
    for f in glob.glob(os.path.join(qt_plugins, sub, "*")):
        plugin_binaries.append((f, os.path.join("PyQt5", "Qt5", "plugins", sub)))
if not any("platforms" in dst for _src, dst in plugin_binaries):
    raise SystemExit("No Qt platform plugin found in " + qt_plugins + ". The packaged program would not start.")

a = Analysis(
    [os.path.join(SPECPATH, "run_openrevu.py")],
    pathex=[root],
    binaries=plugin_binaries,
    datas=[(os.path.join(root, "openrevu", "icons_svg"), os.path.join("openrevu", "icons_svg")),
           (os.path.join(root, "LICENSE"), "."), (os.path.join(root, "THIRD_PARTY_NOTICES.md"), ".")],
    hiddenimports=["PyQt5.QtSvg", "PyQt5.QtPrintSupport", "scipy.ndimage"],
    excludes=["tkinter", "matplotlib", "IPython", "pytest"],
)
pyz = PYZ(a.pure)
exe = EXE(pyz, a.scripts, [], exclude_binaries=True, name="OpenRevu", console=False)
coll = COLLECT(exe, a.binaries, a.datas, name="OpenRevu")
app = BUNDLE(coll, name="OpenRevu.app", bundle_identifier="org.openrevu.OpenRevu") if os.sys.platform == "darwin" else None

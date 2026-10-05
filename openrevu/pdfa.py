"""PDF/A export (archival PDF). Conversion uses Ghostscript; checking uses veraPDF (both optional, external).

OpenRevu never claims that a file is PDF/A on its own: it reports what veraPDF says about the output."""
from __future__ import annotations

import os
import shutil
import subprocess
import tempfile
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field

LEVELS = {"1b": 1, "2b": 2, "3b": 3}

def _def_ps() -> str:
    """The PostScript file that adds the PDF/A output intent. The colour profile is written inline, as a hex string:
    Ghostscript's safe mode does not let a PostScript file read other files, and its own copy of a profile is in
    different places (or missing) on different systems."""
    from .srgb import srgb_profile
    return ("%!\n"
            "[/_objdef {icc_PDFA} /type /stream /OBJ pdfmark\n"
            "[{icc_PDFA} << /N 3 >> /PUT pdfmark\n"
            f"[{{icc_PDFA}} <{srgb_profile().hex()}> /PUT pdfmark\n"
            "[/_objdef {OutputIntent_PDFA} /type /dict /OBJ pdfmark\n"
            "[{OutputIntent_PDFA} << /Type /OutputIntent /S /GTS_PDFA1 /DestOutputProfile {icc_PDFA} "
            "/OutputConditionIdentifier (sRGB) /Info (sRGB) >> /PUT pdfmark\n"
            "[{Catalog} << /OutputIntents [ {OutputIntent_PDFA} ] >> /PUT pdfmark\n")


@dataclass
class PdfaReport:
    level: str
    validated: bool          # True if veraPDF ran
    compliant: bool | None   # None when not validated
    failed_rules: int = 0
    messages: list = field(default_factory=list)
    warnings: list = field(default_factory=list)   # things the conversion changed that the user should know

    def summary(self) -> str:
        if not self.validated:
            text = f"Converted for PDF/A-{self.level}. Not validated: veraPDF is not installed."
        elif self.compliant:
            text = f"veraPDF: compliant with PDF/A-{self.level}."
        else:
            text = f"veraPDF: NOT compliant with PDF/A-{self.level} ({self.failed_rules} rule(s) failed)."
        return text + "".join(f"\nWarning: {w}" for w in self.warnings)


def ghostscript() -> str | None:
    for name in ("gs", "gswin64c", "gswin32c"):
        p = shutil.which(name)
        if p:
            return p
    return None


def verapdf() -> str | None:
    return shutil.which("verapdf")


def available() -> bool:
    return ghostscript() is not None


def validate(path: str, level: str = "2b", timeout: int = 300) -> PdfaReport:
    """Check a file with veraPDF. Raises RuntimeError if veraPDF is not installed."""
    if level not in LEVELS:
        raise ValueError(f"unknown PDF/A level {level!r}; use one of {sorted(LEVELS)}")
    exe = verapdf()
    if exe is None:
        raise RuntimeError("veraPDF is not installed (https://verapdf.org). It is needed to check PDF/A files.")
    r = subprocess.run([exe, "--flavour", level, path], capture_output=True, text=True, timeout=timeout)
    try:
        root = ET.fromstring(r.stdout)
    except ET.ParseError as e:
        raise RuntimeError(f"could not read the veraPDF output: {r.stderr.strip()[:200] or e}") from e
    rep = next((e for e in root.iter() if e.tag.endswith("validationReport")), None)
    if rep is None:
        raise RuntimeError("veraPDF gave no validation report (is the file a PDF?)")
    det = next((e for e in rep.iter() if e.tag.endswith("details")), None)
    msgs = [(e.findtext("{*}description") or "").strip() for e in rep.iter() if e.tag.endswith("rule")]
    return PdfaReport(level, True, rep.get("isCompliant") == "true",
                      int(det.get("failedRules", 0)) if det is not None else 0, [m for m in msgs if m][:20])


def convert(src: str, dst: str, level: str = "2b", check: bool = True, timeout: int = 600) -> PdfaReport:
    """Convert a PDF file to PDF/A with Ghostscript, then check it with veraPDF when it is installed."""
    if level not in LEVELS:
        raise ValueError(f"unknown PDF/A level {level!r}; use one of {sorted(LEVELS)}")
    gs = ghostscript()
    if gs is None:
        raise RuntimeError("PDF/A export needs Ghostscript (https://ghostscript.com) on your PATH.")
    if os.path.abspath(src) == os.path.abspath(dst):
        raise ValueError("the output file must differ from the input file")
    with tempfile.TemporaryDirectory() as tmp:
        ps = os.path.join(tmp, "pdfa_def.ps")
        with open(ps, "w", encoding="ascii") as f:
            f.write(_def_ps())
        out = os.path.join(tmp, "out.pdf")
        cmd = [gs, "-q", "-dBATCH", "-dNOPAUSE", "-dNOOUTERSAVE", "-sDEVICE=pdfwrite", f"-dPDFA={LEVELS[level]}",
               "-dPDFACompatibilityPolicy=1", "-sColorConversionStrategy=RGB", "-sProcessColorModel=DeviceRGB",
               "-dEmbedAllFonts=true", f"-sOutputFile={out}", ps, src]
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
        if r.returncode != 0 or not os.path.exists(out):
            # Ghostscript writes the cause to stdout and a short summary to stderr: show both
            detail = " ".join(x.strip() for x in (r.stdout, r.stderr) if x.strip())
            raise RuntimeError(f"Ghostscript failed (exit code {r.returncode}): {detail[:700]}")
        shutil.move(out, dst)
    if check and verapdf():
        return validate(dst, level)
    return PdfaReport(level, False, None)

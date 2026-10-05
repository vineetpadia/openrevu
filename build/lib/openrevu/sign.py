"""Cryptographic PDF signatures (optional: pip install openrevu[sign])."""
from __future__ import annotations


def _need():
    try:
        from pyhanko.sign import signers  # noqa: F401
    except ImportError as e:  # pragma: no cover
        raise RuntimeError("digital signatures need pyhanko: pip install pyhanko") from e


def sign_pdf(in_path, out_path, p12_path, passphrase, reason="", location="", field="Signature1",
             page=0, box=(40, 40, 240, 100)):
    """Add a visible PKCS#12-based signature field and sign. The input must be saved (not in memory)."""
    _need()
    from pyhanko.pdf_utils.incremental_writer import IncrementalPdfFileWriter
    from pyhanko.sign import fields, signers

    signer = signers.SimpleSigner.load_pkcs12(p12_path, passphrase=passphrase.encode())
    if signer is None:
        raise ValueError("could not load the PKCS#12 file (wrong passphrase?)")
    with open(in_path, "rb") as f:
        w = IncrementalPdfFileWriter(f)
        fields.append_signature_field(w, fields.SigFieldSpec(field, on_page=page, box=box))
        meta = signers.PdfSignatureMetadata(field_name=field, reason=reason or None, location=location or None)
        with open(out_path, "wb") as out:
            signers.sign_pdf(w, meta, signer=signer, output=out)
    return out_path


def list_signatures(path):
    """[(field_name, signer_common_name, intact, covers_whole_file)] — integrity only, no trust validation."""
    _need()
    from pyhanko.pdf_utils.reader import PdfFileReader
    from pyhanko.sign.validation import validate_pdf_signature
    from pyhanko_certvalidator import ValidationContext

    out = []
    with open(path, "rb") as f:
        r = PdfFileReader(f)
        for s in r.embedded_signatures:
            st = validate_pdf_signature(s, ValidationContext(allow_fetching=False))
            out.append((s.field_name, s.signer_cert.subject.native.get("common_name", ""), st.intact, st.coverage.name == "ENTIRE_FILE"))
    return out


def verify_signatures(path, trust_roots=()):
    """Full verification. trust_roots: PEM/DER certificate files you trust (e.g. your CA or the signer's own cert).

    Returns a list of dicts: field, signer, intact (bytes unmodified since signing), valid (cryptographically
    correct), trusted (chains to one of trust_roots), whole_file (nothing appended after signing), summary,
    verdict_ok (all of the above — use this, not `intact` or `trusted` alone)."""
    _need()
    from pyhanko.keys import load_cert_from_pemder
    from pyhanko.pdf_utils.reader import PdfFileReader
    from pyhanko.sign.validation import validate_pdf_signature
    from pyhanko_certvalidator import ValidationContext

    roots = [load_cert_from_pemder(p) for p in trust_roots]
    out = []
    with open(path, "rb") as f:
        for s in PdfFileReader(f).embedded_signatures:
            vc = ValidationContext(trust_roots=roots, allow_fetching=False)
            st = validate_pdf_signature(s, vc)
            whole = st.coverage.name == "ENTIRE_FILE" and st.modification_level.name == "NONE" \
                if hasattr(st, "modification_level") and st.modification_level is not None else st.coverage.name == "ENTIRE_FILE"
            out.append({
                "field": s.field_name,
                "signer": s.signer_cert.subject.native.get("common_name", ""),
                "intact": bool(st.intact), "valid": bool(st.valid), "trusted": bool(st.trusted),
                "whole_file": whole, "summary": st.summary(),
                # the only flag to rely on: signed bytes intact, cryptographically valid, chains to a root you
                # supplied, AND nothing (e.g. an incremental edit) was added after signing
                "verdict_ok": bool(st.intact and st.valid and st.trusted and whole),
            })
    return out

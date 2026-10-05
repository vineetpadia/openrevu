"""A small sRGB colour profile (ICC version 2), made by this program.

A PDF/A file needs an output intent with a colour profile. Ghostscript's own copy of a profile is in different
places (or missing) on different systems, so OpenRevu writes its own. It uses the sRGB primaries (adapted to D50)
and the sRGB transfer curve."""
from __future__ import annotations

import struct

# sRGB colorants, adapted to the D50 white point (the values of the ICC sRGB profiles)
_R = (0.4360747, 0.2225045, 0.0139322)
_G = (0.3850649, 0.7168786, 0.0971045)
_B = (0.1430804, 0.0606169, 0.7141733)
_D50 = (0.9642, 1.0, 0.8249)


def _s15(v: float) -> bytes:
    return struct.pack(">i", round(v * 65536))


def _xyz_tag(xyz) -> bytes:
    return b"XYZ \0\0\0\0" + b"".join(_s15(v) for v in xyz)


def _curve_tag(n: int = 1024) -> bytes:
    def eotf(x):                                      # the sRGB curve: encoded value -> linear light
        return x / 12.92 if x <= 0.04045 else ((x + 0.055) / 1.055) ** 2.4
    vals = [round(eotf(i / (n - 1)) * 65535) for i in range(n)]
    return b"curv\0\0\0\0" + struct.pack(">I", n) + struct.pack(f">{n}H", *vals)


def _text_tag(text: str) -> bytes:
    return b"text\0\0\0\0" + text.encode("ascii") + b"\0"


def _desc_tag(text: str) -> bytes:
    a = text.encode("ascii") + b"\0"
    return (b"desc\0\0\0\0" + struct.pack(">I", len(a)) + a + struct.pack(">II", 0, 0) + struct.pack(">HB", 0, 0) + b"\0" * 67)


def srgb_profile() -> bytes:
    """The bytes of the profile."""
    curve = _curve_tag()
    tags = [(b"desc", _desc_tag("sRGB (OpenRevu)")), (b"cprt", _text_tag("Public domain")), (b"wtpt", _xyz_tag(_D50)),
            (b"rXYZ", _xyz_tag(_R)), (b"gXYZ", _xyz_tag(_G)), (b"bXYZ", _xyz_tag(_B)),
            (b"rTRC", curve), (b"gTRC", None), (b"bTRC", None)]   # the three curves share one block of data
    table_len = 4 + 12 * len(tags)
    offset = 128 + table_len
    body, entries, curve_off = b"", [], None
    for sig, data in tags:
        if data is None:
            entries.append((sig, curve_off, len(curve)))
            continue
        if sig == b"rTRC":
            curve_off = offset + len(body)
        entries.append((sig, offset + len(body), len(data)))
        body += data + b"\0" * (-len(data) % 4)
    size = 128 + table_len + len(body)
    header = (struct.pack(">I", size) + b"\0\0\0\0" + struct.pack(">I", 0x02100000) + b"mntrRGB XYZ "
              + struct.pack(">6H", 2000, 1, 1, 0, 0, 0) + b"acsp" + b"\0" * 4 + b"\0" * 4 + b"\0" * 4 + b"\0" * 4
              + b"\0" * 8 + struct.pack(">I", 0) + b"".join(_s15(v) for v in _D50) + b"\0" * 4 + b"\0" * 16 + b"\0" * 28)
    assert len(header) == 128, len(header)
    table = struct.pack(">I", len(tags)) + b"".join(struct.pack(">4sII", s, o, n) for s, o, n in entries)
    return header + table + body

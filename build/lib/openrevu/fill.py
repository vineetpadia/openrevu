"""Dynamic-Fill-style region detection: click inside a room, get its boundary as a polygon.

Needs numpy and scipy (pip install openrevu[fill])."""
from __future__ import annotations

import math

# Moore neighbourhood, clockwise starting west (x, y with y pointing down)
_DIRS = [(-1, 0), (-1, -1), (0, -1), (1, -1), (1, 0), (1, 1), (0, 1), (-1, 1)]


def trace_boundary(mask):
    """Ordered outer boundary pixels (x, y) of the first connected blob in a boolean array."""
    h, w = mask.shape
    ys, xs = mask.nonzero()
    if len(xs) == 0:
        return []
    k = min(range(len(ys)), key=lambda i: (ys[i], xs[i]))  # topmost, then leftmost pixel
    start = (int(xs[k]), int(ys[k]))
    inside = lambda x, y: 0 <= x < w and 0 <= y < h and bool(mask[y, x])  # noqa: E731
    if len(xs) == 1:
        return [start]
    cur, back = start, 0  # we entered `start` from the west (background)
    out = [start]
    for _ in range(4 * len(xs) + 16):
        for i in range(1, 9):
            d = (back + i) % 8
            nx, ny = cur[0] + _DIRS[d][0], cur[1] + _DIRS[d][1]
            if inside(nx, ny):
                # new backtrack direction: from the neighbour back towards the previous (background) cell
                prev = (back + i - 1) % 8
                px, py = cur[0] + _DIRS[prev][0], cur[1] + _DIRS[prev][1]
                back = _DIRS.index((px - nx, py - ny)) if (px - nx, py - ny) in _DIRS else (d + 4) % 8
                cur = (nx, ny)
                break
        else:
            return out  # isolated pixel
        if cur == start:  # closed the loop (can stop early on 1-pixel-wide pinches; fine for room outlines)
            return out
        out.append(cur)
    return out


def simplify(points, tol):
    """Douglas–Peucker for a closed ring."""
    if len(points) < 4:
        return list(points)

    def seg(pts):
        a, b = pts[0], pts[-1]
        dx, dy = b[0] - a[0], b[1] - a[1]
        n = math.hypot(dx, dy) or 1e-9
        best, bi = -1.0, 0
        for i in range(1, len(pts) - 1):
            d = abs(dy * (pts[i][0] - a[0]) - dx * (pts[i][1] - a[1])) / n
            if d > best:
                best, bi = d, i
        if best > tol:
            return seg(pts[:bi + 1])[:-1] + seg(pts[bi:])
        return [a, b]

    far = max(range(len(points)), key=lambda i: math.dist(points[i], points[0]))
    ring = list(points)
    return seg(ring[:far + 1])[:-1] + seg(ring[far:] + [ring[0]])[:-1]


def offset_ring(ring, d):
    """Move every vertex of a closed ring outward by d (mitred). Boundary tracing runs through pixel *centres*;
    the true edge of the region is half a pixel further out."""
    n = len(ring)

    def unit(a, b):
        dx, dy = b[0] - a[0], b[1] - a[1]
        L = math.hypot(dx, dy) or 1.0
        return dx / L, dy / L

    def build(sign):
        out = []
        for i in range(n):
            p, c, q = ring[i - 1], ring[i], ring[(i + 1) % n]
            e1, e2 = unit(p, c), unit(c, q)
            n1, n2 = (e1[1], -e1[0]), (e2[1], -e2[0])
            m = (n1[0] + n2[0], n1[1] + n2[1])
            L = math.hypot(*m)
            m = n1 if L < 1e-9 else (m[0] / L, m[1] / L)
            k = d * sign / max(0.5, m[0] * n1[0] + m[1] * n1[1])
            out.append((c[0] + m[0] * k, c[1] + m[1] * k))
        return out

    def area(r):
        return abs(sum(x1 * y2 - x2 * y1 for (x1, y1), (x2, y2) in zip(r, r[1:] + r[:1]))) / 2

    a = build(1)
    return a if area(a) >= area(ring) else build(-1)


def find_region(free, seed, min_hole=30, tol=1.5):
    """free: boolean array (True = open space). Returns (outer_polygon, [hole_polygons]) in pixel coords."""
    import numpy as np
    from scipy import ndimage

    x, y = seed
    h, w = free.shape
    if not (0 <= x < w and 0 <= y < h) or not free[y, x]:
        raise ValueError("click inside an open (white) area, not on a line")
    lab, _ = ndimage.label(free)
    region = lab == lab[y, x]
    if region[0, :].any() or region[-1, :].any() or region[:, 0].any() or region[:, -1].any():
        raise ValueError("region is not enclosed (gap in the boundary or open to the page edge)")
    filled = ndimage.binary_fill_holes(region)
    outer = offset_ring(simplify(trace_boundary(filled), tol), 0.5)
    holes = []
    hl, n = ndimage.label(filled & ~region)
    if n:
        sizes = np.bincount(hl.ravel(), minlength=n + 1)
        slices = ndimage.find_objects(hl)  # bounding boxes: avoids a full-image pass per hole
        for i in range(1, n + 1):
            if sizes[i] < min_hole:
                continue
            sl = slices[i - 1]
            blob = np.pad(hl[sl] == i, 1)  # padded so tracing works on the cropped blob
            oy, ox = sl[0].start - 1, sl[1].start - 1
            ring = simplify(trace_boundary(blob), tol)
            if len(ring) < 3:  # too small for the simplifier: use the bounding box of the blob
                ys, xs = blob.nonzero()
                x0, x1, y0, y1 = int(xs.min()), int(xs.max()), int(ys.min()), int(ys.max())
                ring = [(x0, y0), (x1, y0), (x1, y1), (x0, y1)]
            if len(ring) >= 3:
                holes.append([(x + ox, y + oy) for x, y in offset_ring(ring, 0.5)])
    if len(outer) < 3:
        raise ValueError("region too small")
    return outer, holes

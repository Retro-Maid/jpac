"""Boundary geometry for the static map, packed for site/geo.js.

Two kinds of payload, split into chunks so the page fetches only what it needs:

* ``exact`` — for every 3次 cell a boundary passes through (between
  municipalities, and the coastline), the land of each municipality clipped to
  that cell, **unsimplified**. The page settles a click there by point-in-polygon
  instead of by the cell, which is what turns "this cell straddles two
  prefectures" into one answer. Chunked by 2次メッシュ (~10km square): one
  click fetches one small chunk.
* ``line`` / ``coarse`` — simplified outlines for drawing, chunked by 1次メッシュ
  (``coarse``: one national file). Display only; never used to decide anything,
  because simplification moves a line by up to its tolerance.

Encoding: every integer is an unsigned LEB128 varint; coordinates are
zig-zag varint deltas in units of 1e-6 degree (about 10 cm), relative to the
chunk's south-west corner. The census boundary is itself accurate to metres,
so that quantisation is the only change to the publisher's vertices in the
``exact`` payload and cannot move a point across a boundary that the data
places to better than 10 cm. Measured: int32 deltas made the exact payload
118 MB; varints bring a typical vertex from 8 bytes to 2-3.

Pure Python on purpose: importable by the tests without the ``geo`` extra. The
geometry work that feeds it lives in tools/build_site_geo.py.
"""

from __future__ import annotations

import base64
from collections.abc import Iterable, Sequence

SCALE = 1_000_000           # 1e-6 degree per unit
Point = tuple[float, float]


def chunk_origin(key: str) -> tuple[float, float]:
    """South-west corner (lng, lat) of a chunk.

    ``PPUU`` is a 1次メッシュ, ``PPUUQV`` a 2次メッシュ, ``all`` the national file.
    """
    if key == "all":
        return 122.0, 20.0
    lng = int(key[2:4]) + 100.0
    lat = int(key[:2]) / 1.5
    if len(key) == 6:
        lat += int(key[4]) / 12
        lng += int(key[5]) / 8
    return lng, lat


def _uv(n: int, out: bytearray) -> None:
    """Unsigned LEB128."""
    while True:
        b = n & 0x7F
        n >>= 7
        if n:
            out.append(b | 0x80)
        else:
            out.append(b)
            return


def _sv(v: int, out: bytearray) -> None:
    """Zig-zag signed varint."""
    _uv(v * 2 if v >= 0 else -v * 2 - 1, out)


def _path(pts: Sequence[Point], ox: float, oy: float, closed: bool) -> bytes | None:
    """One quantised path. Rings are stored open; the reader closes them."""
    q: list[tuple[int, int]] = []
    for x, y in pts:
        p = (round((x - ox) * SCALE), round((y - oy) * SCALE))
        if not q or p != q[-1]:
            q.append(p)
    if closed and len(q) > 1 and q[0] == q[-1]:
        q.pop()
    if len(q) < (3 if closed else 2):
        return None
    out = bytearray()
    _uv(len(q), out)
    px = py = 0
    for x, y in q:
        _sv(x - px, out)
        _sv(y - py, out)
        px, py = x, y
    return bytes(out)


def pack_exact(
    key: str,
    cells: Iterable[tuple[int, Sequence[tuple[int, Sequence[Sequence[Point]]]]]],
) -> bytes:
    """cell count; per cell: mesh code, piece count; per piece: unit, ring
    count, rings. All varints.

    A piece's rings are evaluated even-odd, so holes need no flag. A cell whose
    pieces all vanish under quantisation is dropped rather than written empty:
    an empty cell would read as "decided: no municipality".
    """
    ox, oy = chunk_origin(key)
    body = bytearray()
    n = 0
    for code, pieces in cells:
        packed = []
        for unit, rings in pieces:
            rs = [r for r in (_path(ring, ox, oy, True) for ring in rings) if r]
            if rs:
                head = bytearray()
                _uv(unit, head)
                _uv(len(rs), head)
                packed.append(bytes(head) + b"".join(rs))
        if not packed:
            continue
        _uv(code, body)
        _uv(len(packed), body)
        body += b"".join(packed)
        n += 1
    head = bytearray()
    _uv(n, head)
    return bytes(head) + bytes(body)


def pack_lines(key: str, lines: Iterable[tuple[int, Sequence[Point]]]) -> bytes:
    """line count; per line: unit and one open path. All varints."""
    ox, oy = chunk_origin(key)
    body = bytearray()
    n = 0
    for unit, pts in lines:
        p = _path(pts, ox, oy, False)
        if p:
            _uv(unit, body)
            body += p
            n += 1
    head = bytearray()
    _uv(n, head)
    return bytes(head) + bytes(body)


def js_chunk(kind: str, key: str, payload: bytes) -> str:
    """A chunk as a script, so the page can load it from file:// too."""
    return f'MeshGeo.put("{kind}","{key}","{base64.b64encode(payload).decode("ascii")}");\n'

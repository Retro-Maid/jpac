"""Word 97 (.doc) text extraction for MIC 市外局番の一覧.

MIC publishes the numbering-area table only as a legacy binary Word document
and a PDF. Nothing in the standard Python stack
reads Word 97, and PDF table extraction would be both heavier and less
reliable, so this module reconstructs the text directly from the OLE2 streams
using ``olefile`` (justification: docs/ARCHITECTURE.md §5).

The document stores text in the ``WordDocument`` stream, addressed through a
piece table held in the ``1Table`` stream. Each piece is either UTF-16LE or
CP1252-compressed, flagged by bit 30 of its file offset.

Verified against the live 2026-03-01 file, which yields a clean tab-delimited
table. A pinned fixture test fails loudly if the format ever changes.
"""

from __future__ import annotations

import struct
from pathlib import Path

import olefile

from ..errors import SourceFetchFailed

# FIB field offsets within the WordDocument stream.
_FIB_FC_MIN = 0x0018
_FIB_CCP_TEXT = 0x004C
_FIB_FC_CLX = 0x01A2
_FIB_LCB_CLX = 0x01A6

_PRC_MARKER = 1
_PIECE_TABLE_MARKER = 2
_FC_COMPRESSED_BIT = 0x40000000
_FC_ADDRESS_MASK = 0x3FFFFFFF

# Word 97 marks both a cell end and a row end with the same character, so a
# row boundary is the pair CELL+CELL.
CELL = chr(7)
SEP = CELL + CELL
NUL = chr(0)
PARA = chr(13)


def extract_doc_text(path: Path) -> str:
    """Return the document's plain text, tabs and paragraph marks preserved."""
    if not olefile.isOleFile(str(path)):
        raise SourceFetchFailed(
            "not an OLE2 compound document (publisher may have changed format)",
            path=str(path),
        )

    with olefile.OleFileIO(str(path)) as ole:
        names = {"/".join(entry) for entry in ole.listdir()}
        if "WordDocument" not in names:
            raise SourceFetchFailed("no WordDocument stream", path=str(path))
        table_name = "1Table" if "1Table" in names else "0Table"
        if table_name not in names:
            raise SourceFetchFailed("no piece-table stream", path=str(path))

        word = ole.openstream("WordDocument").read()
        table = ole.openstream(table_name).read()

    fc_clx = struct.unpack_from("<i", word, _FIB_FC_CLX)[0]
    lcb_clx = struct.unpack_from("<i", word, _FIB_LCB_CLX)[0]
    if lcb_clx <= 0 or fc_clx < 0 or fc_clx + lcb_clx > len(table):
        raise SourceFetchFailed("piece table out of range", path=str(path))

    clx = table[fc_clx : fc_clx + lcb_clx]

    # Skip any leading property-modifier (Prc) entries.
    pos = 0
    while pos < len(clx) and clx[pos] == _PRC_MARKER:
        size = struct.unpack_from("<H", clx, pos + 1)[0]
        pos += 3 + size
    if pos >= len(clx) or clx[pos] != _PIECE_TABLE_MARKER:
        raise SourceFetchFailed("piece table marker not found", path=str(path))

    lcb_plcfpcd = struct.unpack_from("<I", clx, pos + 1)[0]
    plc = clx[pos + 5 : pos + 5 + lcb_plcfpcd]

    # PLC layout: (n+1) 4-byte CPs followed by n 8-byte piece descriptors.
    n_pieces = (len(plc) - 4) // 12
    if n_pieces <= 0:
        raise SourceFetchFailed("empty piece table", path=str(path))

    cps = [struct.unpack_from("<i", plc, 4 * i)[0] for i in range(n_pieces + 1)]
    pcd_base = 4 * (n_pieces + 1)

    # Character positions must be monotonic and start at zero; anything else
    # means the piece table is not what this parser assumes.
    if cps[0] != 0 or any(b < a for a, b in zip(cps, cps[1:], strict=False)):
        raise SourceFetchFailed(
            "piece-table character positions are not monotonic", path=str(path)
        )

    # ccpText counts the main document only; the piece table additionally covers
    # headers, footnotes and the like, so it legitimately runs longer. It must
    # never run *short* — that would mean the main text is truncated.
    ccp_text = struct.unpack_from("<i", word, _FIB_CCP_TEXT)[0]
    if ccp_text > 0 and cps[-1] < ccp_text:
        raise SourceFetchFailed(
            "piece table does not cover the main document text",
            path=str(path), covered=cps[-1], expected=ccp_text,
        )

    parts: list[str] = []
    for i in range(n_pieces):
        fc = struct.unpack_from("<I", plc, pcd_base + 8 * i + 2)[0]
        length = cps[i + 1] - cps[i]
        if length <= 0:
            continue
        if fc & _FC_COMPRESSED_BIT:
            offset = (fc & _FC_ADDRESS_MASK) // 2
            end = offset + length
            encoding = "cp1252"
        else:
            offset = fc
            end = offset + length * 2
            encoding = "utf-16-le"

        # A piece pointing outside the stream would otherwise be silently
        # truncated to whatever bytes happen to be there.
        if offset < 0 or end > len(word):
            raise SourceFetchFailed(
                "piece extends beyond the WordDocument stream",
                path=str(path), piece=i, offset=offset, end=end, stream=len(word),
            )
        try:
            # Strict: `errors="replace"` would turn a changed encoding into
            # U+FFFD inside real place names and let the build continue.
            parts.append(word[offset:end].decode(encoding))
        except UnicodeDecodeError as exc:
            raise SourceFetchFailed(
                "could not decode a text piece; the document encoding may have "
                "changed",
                path=str(path), piece=i, encoding=encoding, error=str(exc),
            ) from exc

    text = "".join(parts)
    if "\ufffd" in text:
        raise SourceFetchFailed(
            "extracted text contains replacement characters", path=str(path)
        )
    # Main document only; headers and footnotes are not part of the table.
    return text[:ccp_text] if ccp_text > 0 else text


def doc_text_to_rows(text: str, expected_cells: int | None = None) -> list[list[str]]:
    """Split extracted Word text into table rows.

    Word 97 uses ``\\x07`` for both the cell mark and the row mark, so a row
    boundary appears as the pair ``\\x07\\x07`` (last cell mark + row mark).
    Splitting on that pair recovers the rows exactly; verified against the live
    file, which yields 583 four-cell rows.

    ``expected_cells`` filters out the document's preamble and trailer, which
    contain paragraph text rather than table cells.
    """
    parsed: list[list[str]] = []
    for segment in text.split(SEP):
        if CELL not in segment:
            continue
        cells = [c.strip().strip(NUL) for c in segment.split(CELL)]
        # The first table row is preceded by the document title and heading,
        # separated by paragraph marks; keep only the trailing cell content.
        cells[0] = cells[0].rsplit(PARA, 1)[-1].strip()
        if any(cells):
            parsed.append(cells)

    if expected_cells is None:
        return parsed

    # The document has non-table preamble and trailer whose cell counts
    # legitimately differ. What must never be tolerated is a wrong-shaped row
    # *inside* the table, because dropping one would remove telephone coverage
    # with no schema-drift signal at all. So the table region is bounded by the
    # first and last correctly-shaped rows, and anything malformed inside it is
    # a hard failure.
    good = [i for i, c in enumerate(parsed) if len(c) == expected_cells]
    if not good:
        raise SourceFetchFailed(
            "no rows with the expected cell count; the table structure changed",
            expected_cells=expected_cells, segments=len(parsed),
        )
    first, last = good[0], good[-1]
    malformed = [
        parsed[i] for i in range(first, last + 1) if len(parsed[i]) != expected_cells
    ]
    if malformed:
        raise SourceFetchFailed(
            "rows inside the Word table do not have the expected cell count; the "
            "publisher may have changed the table structure",
            expected_cells=expected_cells, malformed=len(malformed),
            sample=[c[:2] for c in malformed[:3]],
        )
    return parsed[first : last + 1]

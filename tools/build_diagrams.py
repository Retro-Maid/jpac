"""Render docs/diagrams/*.mmd to committed SVGs, and detect drift.

The README embeds images rather than mermaid code fences, because mermaid only
renders on GitHub itself — in an editor preview, a mirror, or a packaged copy of
the docs the reader sees the source text and no diagram.

That buys portability at the cost of a second copy of the truth, which is
exactly the kind of thing that goes stale. So the ``.mmd`` file is the source,
the ``.svg`` is generated from it, and ``manifest.json`` records the SHA-256 of
the source each SVG was built from. ``--check`` compares that against the
sources on disk, so a diagram edited in one place and not the other cannot reach
a release.

The check deliberately does **not** re-render and diff the SVG. Mermaid's edge
routing uses randomness — two renders of the same source differ in their curve
control points — so a byte comparison is always "out of date" and a gate that is
always red is a gate nobody reads. Hashing the input answers the question the
gate actually asks: was this SVG built from the source that is here now?

GitHub's own mermaid runs with ``securityLevel: strict``, which turns off HTML
labels. The same setting is used here so a diagram cannot look right in one
place and wrong in the other: ``<b>`` renders as literal ``&lt;b&gt;`` under
that setting, which is how it was caught in the first place.

The SVGs are given an explicit white background. They are viewed on whatever
ground the reader's theme paints, and this palette is dark ink on light fills,
which would be unreadable composited over a dark page.

Usage:
    py -3.12 tools/build_diagrams.py           # regenerate SVGs and the manifest
    py -3.12 tools/build_diagrams.py --check   # fail if any SVG is out of date
                                               # (same as: jpac verify diagrams)
"""

from __future__ import annotations

import sys as _sys

# These reports print Japanese and typographic dashes. A Windows console is
# cp932 by default, where an unencodable character raises UnicodeEncodeError
# and kills the run halfway through. Degrade the character, not the report.
if hasattr(_sys.stdout, "reconfigure") and (_sys.stdout.encoding or "").lower() not in (
    "utf-8", "utf8"
):
    _sys.stdout.reconfigure(errors="replace")
    if hasattr(_sys.stderr, "reconfigure"):
        _sys.stderr.reconfigure(errors="replace")


import hashlib
import json
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DIAGRAMS = ROOT / "docs" / "diagrams"
MANIFEST = DIAGRAMS / "manifest.json"
MERMAID_CLI = "@mermaid-js/mermaid-cli@11"

# Mirrors how GitHub renders mermaid, so a committed SVG and a GitHub-rendered
# fence of the same source cannot disagree.
CONFIG = {"securityLevel": "strict", "flowchart": {"htmlLabels": False}}


def source_hash(path: Path) -> str:
    # Read as text and normalise newlines: a checkout with different line
    # endings must not look like an edited diagram.
    body = path.read_text(encoding="utf-8").replace("\r\n", "\n")
    return hashlib.sha256(body.encode("utf-8")).hexdigest()


def _npx() -> str | None:
    return shutil.which("npx") or shutil.which("npx.cmd")


def render(src: Path, out: Path, npx: str) -> None:
    with tempfile.TemporaryDirectory() as tmp:
        cfg = Path(tmp) / "mermaid.json"
        cfg.write_text(json.dumps(CONFIG), encoding="utf-8")
        result = subprocess.run(
            [npx, "-y", "-p", MERMAID_CLI, "mmdc",
             "-i", str(src), "-o", str(out), "-c", str(cfg), "-b", "white"],
            capture_output=True, text=True, encoding="utf-8", errors="replace",
        )
    if result.returncode != 0 or not out.exists():
        raise SystemExit(
            f"mermaid failed for {src.name}:\n{result.stdout}\n{result.stderr}"
        )
    svg = out.read_text(encoding="utf-8")
    if "&lt;b&gt;" in svg or "&lt;i&gt;" in svg:
        raise SystemExit(
            f"{src.name}: HTML emphasis tags are not interpreted under GitHub's "
            "securityLevel=strict and would show as literal text. Use <br/> for "
            "line breaks and drop <b>/<i>."
        )


def check() -> int:
    sources = sorted(DIAGRAMS.glob("*.mmd"))
    recorded = (
        json.loads(MANIFEST.read_text(encoding="utf-8")) if MANIFEST.exists() else {}
    )
    problems = []
    for src in sources:
        svg = src.with_suffix(".svg")
        if not svg.exists():
            problems.append(f"{svg.name} is missing")
            continue
        want = recorded.get(src.name)
        if want is None:
            problems.append(f"{src.name} is absent from manifest.json")
        elif want != source_hash(src):
            problems.append(f"{svg.name} was built from an older {src.name}")
    for name in sorted(set(recorded) - {s.name for s in sources}):
        problems.append(f"manifest.json still lists {name}, which no longer exists")

    if problems:
        print("Diagrams are out of date. Run: py -3.12 tools/build_diagrams.py")
        for p in problems:
            print("  !", p)
        return 1
    print(f"{len(sources)} diagrams are up to date")
    return 0


def build() -> int:
    npx = _npx()
    if npx is None:
        print("ERROR: npx not found; mermaid-cli needs Node.js")
        return 1
    sources = sorted(DIAGRAMS.glob("*.mmd"))
    if not sources:
        print(f"no .mmd sources in {DIAGRAMS}")
        return 1
    manifest = {}
    for src in sources:
        target = src.with_suffix(".svg")
        render(src, target, npx)
        manifest[src.name] = source_hash(src)
        print(f"  {src.name} -> {target.name}  {target.stat().st_size:,} bytes")
    MANIFEST.write_text(
        json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    print(f"rendered {len(manifest)} diagrams")
    return 0


if __name__ == "__main__":
    raise SystemExit(check() if "--check" in sys.argv else build())

"use strict";
/*
 * メッシュ → 市区町村 の表引きと、標準地域メッシュのコード計算。DOM も Leaflet も使わない
 * 純粋な部分で、ページ（app.js）と tests/test_site_lookup.py（node 経由）が同じものを使う。
 *
 * The binary format is written by jp_address_crosswalk.build.mesh.pack_lookup;
 * that docstring is the specification, and the test runs the two against each
 * other so neither can drift alone.
 */
(function (root) {
  const UNKNOWN = 0xffff;

  // ---------------------------------------------------------------- codes
  // JIS X 0410: a 3次 cell is 30" of latitude by 45" of longitude (1/120° by
  // 1/80°). 4次・5次・6次 halve it three more times (1/2・1/4・1/8 地域メッシュ),
  // each numbered 1 (SW) 2 (SE) 3 (NW) 4 (NE). A 6次 cell is 1/960° by 1/640°,
  // about 125 m.
  function rowColOf(lat, lng) {
    return [Math.floor(lat * 120), Math.floor(lng * 80)];
  }

  function codeOf(row, col) {
    const p = Math.floor(row / 80), q = Math.floor((row % 80) / 10), r = row % 10;
    const u = Math.floor(col / 80) - 100, v = Math.floor((col % 80) / 10), w = col % 10;
    return p * 1e6 + u * 1e4 + q * 1e3 + v * 100 + r * 10 + w;
  }

  function rowColOfCode(code) {
    const s = String(code).padStart(8, "0");
    const p = +s.slice(0, 2), u = +s.slice(2, 4);
    const q = +s[4], v = +s[5], r = +s[6], w = +s[7];
    if (q > 7 || v > 7) return null;
    return [p * 80 + q * 10 + r, (u + 100) * 80 + v * 10 + w];
  }

  // 6次 row/column: 8 per 3次 cell in each direction.
  function rowCol6Of(lat, lng) {
    return [Math.floor(lat * 960), Math.floor(lng * 640)];
  }

  // The 11-digit 6次 code (a string: it is an identifier, and 11 digits).
  function code6Of(r6, c6) {
    const base = String(codeOf(Math.floor(r6 / 8), Math.floor(c6 / 8))).padStart(8, "0");
    const sr = r6 % 8, sc = c6 % 8;
    let s = base;
    for (const bit of [4, 2, 1]) s += 1 + ((sc & bit) ? 1 : 0) + ((sr & bit) ? 2 : 0);
    return s;
  }

  // Any 3次〜6次 code (8-11 digits) -> its south-west 6次 row/col and its size
  // in 6次 cells (8, 4, 2, 1). null if it is not a valid code.
  function parseMeshCode(s) {
    if (!/^\d{8,11}$/.test(s)) return null;
    const rc = rowColOfCode(+s.slice(0, 8));
    if (!rc) return null;
    let r6 = rc[0] * 8, c6 = rc[1] * 8, span = 8;
    for (const ch of s.slice(8)) {
      const d = +ch;
      if (d < 1 || d > 4) return null;
      span /= 2;
      if (d === 2 || d === 4) c6 += span;
      if (d === 3 || d === 4) r6 += span;
    }
    return { r6, c6, span, level: s.length - 5 };
  }

  // --------------------------------------------------------------- table
  function varints(buf) {
    const b = new Uint8Array(buf);
    let o = 0;
    const uv = () => {
      let n = 0, mul = 1, x;
      do { x = b[o++]; n += (x & 0x7f) * mul; mul *= 128; } while (x & 0x80);
      return n;
    };
    return { uv, done: () => o === b.length };
  }

  function create(uniformBuf, mixedBuf) {
    // Runs of consecutive codes sharing one answer (see pack_lookup).
    let rd = varints(uniformBuf);
    const nu = rd.uv();
    const uStart = new Uint32Array(nu), uLen = new Uint32Array(nu), uIdx = new Uint16Array(nu);
    let end = 0, cells = 0;
    for (let i = 0; i < nu; i++) {
      uStart[i] = end + rd.uv(); uLen[i] = rd.uv(); uIdx[i] = rd.uv();
      end = uStart[i] + uLen[i]; cells += uLen[i];
    }
    if (!rd.done()) throw new Error("mesh_uniform.bin の末尾が壊れています");

    rd = varints(mixedBuf);
    const nm = rd.uv();
    const mStart = new Uint32Array(nm), mLen = new Uint32Array(nm), mIds = new Array(nm);
    end = 0;
    let mixedCells = 0;
    for (let i = 0; i < nm; i++) {
      mStart[i] = end + rd.uv(); mLen[i] = rd.uv();
      const k = rd.uv(), ids = new Array(k);
      for (let j = 0; j < k; j++) ids[j] = rd.uv();
      mIds[i] = ids;
      end = mStart[i] + mLen[i]; mixedCells += mLen[i];
    }
    if (!rd.done()) throw new Error("mesh_mixed.bin の末尾が壊れています");

    // Index of the run that could hold `code`, or -1.
    function find(starts, lens, code) {
      let lo = 0, hi = starts.length - 1, at = -1;
      while (lo <= hi) {
        const mid = (lo + hi) >>> 1;
        if (starts[mid] <= code) { at = mid; lo = mid + 1; } else hi = mid - 1;
      }
      return at >= 0 && code < starts[at] + lens[at] ? at : -1;
    }

    // Four outcomes, never rounded into one another:
    //   contains  — the whole cell lies in one municipality
    //   ambiguous — the cell straddles a boundary; every candidate is kept
    //   unknown   — land, but the boundary data names no municipality
    //   none      — not land in the boundary data. Never snapped to the
    //               nearest municipality (docs/POLICY.md §4).
    function lookup(code) {
      const u = find(uStart, uLen, code);
      if (u >= 0) return { kind: "contains", ids: [uIdx[u]] };
      const m = find(mStart, mLen, code);
      if (m < 0) return { kind: "none", ids: [] };
      const ids = mIds[m];
      if (ids.length === 1) return { kind: ids[0] === UNKNOWN ? "unknown" : "contains", ids: ids.slice() };
      return { kind: "ambiguous", ids: ids.slice() };
    }

    // Every cell a municipality reaches, uniform or shared.
    function cellsOf(idx) {
      const out = [];
      for (let i = 0; i < nu; i++) if (uIdx[i] === idx) for (let k = 0; k < uLen[i]; k++) out.push(uStart[i] + k);
      for (let i = 0; i < nm; i++) if (mIds[i].includes(idx)) for (let k = 0; k < mLen[i]; k++) out.push(mStart[i] + k);
      return out;
    }

    return { n: cells, mixedCount: mixedCells, runs: nu + nm, lookup, cellsOf };
  }

  root.MeshLookup = { UNKNOWN, rowColOf, codeOf, rowColOfCode, rowCol6Of, code6Of, parseMeshCode, create };
})(typeof globalThis !== "undefined" ? globalThis : this);

"use strict";
/*
 * 境界ジオメトリの読み込みと点in面. DOM を触るのは load() だけ。
 *
 * The format is written by jp_address_crosswalk.build.geo_pack; that module's
 * docstrings are the specification, and tests/test_geo_pack.py runs the two
 * against each other.
 *
 * Chunks are scripts that call MeshGeo.put(...), not files fetched with
 * fetch(): a page opened from file:// may load scripts but not fetch.
 */
(function (root) {
  const SCALE = 1e6;
  const store = new Map();     // "kind/key" -> decoded chunk (null = none exists)
  const waiters = new Map();   // "kind/key" -> [resolve]
  let meta = null;
  let chunkSets = {};

  // PPUU = 1次メッシュ, PPUUQV = 2次メッシュ, "all" = the national file.
  function origin(key) {
    if (key === "all") return [122, 20];
    let lng = +key.slice(2, 4) + 100, lat = +key.slice(0, 2) / 1.5;
    if (key.length === 6) { lat += +key[4] / 12; lng += +key[5] / 8; }
    return [lng, lat];
  }

  function b64ToBytes(s) {
    const bin = atob(s);
    const out = new Uint8Array(bin.length);
    for (let i = 0; i < bin.length; i++) out[i] = bin.charCodeAt(i);
    return out;
  }

  // Varint cursor over one chunk (unsigned LEB128; signed values zig-zag).
  // Arithmetic rather than bit operators past 28 bits, so codes up to 2^53 stay exact.
  function reader(bytes) {
    let o = 0;
    const uv = () => {
      let n = 0, mul = 1, b;
      do { b = bytes[o++]; n += (b & 0x7f) * mul; mul *= 128; } while (b & 0x80);
      return n;
    };
    const sv = () => { const z = uv(); return z % 2 ? -(z + 1) / 2 : z / 2; };
    return { uv, sv, done: () => o === bytes.length };
  }

  function readPath(rd, ox, oy) {
    const n = rd.uv();
    const xy = new Float64Array(n * 2);
    let x = 0, y = 0;
    for (let i = 0; i < n; i++) {
      x += rd.sv();
      y += rd.sv();
      xy[2 * i] = ox + x / SCALE;
      xy[2 * i + 1] = oy + y / SCALE;
    }
    return xy;
  }

  // Map<mesh code, [{unit, rings: Float64Array[] (x,y interleaved, open)}]>
  function decodeExact(key, bytes) {
    const [ox, oy] = origin(key);
    const rd = reader(bytes);
    const n = rd.uv();
    const cells = new Map();
    for (let c = 0; c < n; c++) {
      const code = rd.uv();
      const np = rd.uv();
      const pieces = [];
      for (let p = 0; p < np; p++) {
        const unit = rd.uv();
        const nr = rd.uv();
        const rings = [];
        for (let r = 0; r < nr; r++) rings.push(readPath(rd, ox, oy));
        pieces.push({ unit, rings });
      }
      cells.set(code, pieces);
    }
    if (!rd.done()) throw new Error(`exact/${key}: trailing bytes`);
    return cells;
  }

  // [{unit, xy: Float64Array}]
  function decodeLines(key, bytes) {
    const [ox, oy] = origin(key);
    const rd = reader(bytes);
    const n = rd.uv();
    const lines = new Array(n);
    for (let i = 0; i < n; i++) {
      const unit = rd.uv();
      lines[i] = { unit, xy: readPath(rd, ox, oy) };
    }
    if (!rd.done()) throw new Error(`line/${key}: trailing bytes`);
    return lines;
  }

  // Even-odd over every ring of one piece, half-open on y so that a vertex
  // shared by two edges is counted once (same rule as build/spatial.py).
  function inside(rings, x, y) {
    let isIn = false;
    for (const xy of rings) {
      const n = xy.length / 2;
      let x1 = xy[2 * n - 2], y1 = xy[2 * n - 1];
      for (let i = 0; i < n; i++) {
        const x2 = xy[2 * i], y2 = xy[2 * i + 1];
        if ((y1 > y) !== (y2 > y) && x < x1 + (y - y1) * (x2 - x1) / (y2 - y1)) isIn = !isIn;
        x1 = x2; y1 = y2;
      }
    }
    return isIn;
  }

  function settle(id, val) {
    store.set(id, val);
    for (const f of waiters.get(id) || []) f(val);
    waiters.delete(id);
  }

  function put(kind, key, b64) {
    const bytes = b64ToBytes(b64);
    settle(`${kind}/${key}`, kind === "exact" ? decodeExact(key, bytes) : decodeLines(key, bytes));
  }

  function setMeta(m) {
    meta = m;
    chunkSets = {};
    for (const [kind, keys] of Object.entries(m.chunks || {})) chunkSets[kind] = new Set(keys);
  }

  function exists(kind, key) {
    return !!(chunkSets[kind] && chunkSets[kind].has(key));
  }

  // Resolves to the decoded chunk, or null when no chunk exists for that key
  // (open sea: nothing to decide, nothing to draw).
  function load(kind, key, base = "data/geo/") {
    const id = `${kind}/${key}`;
    if (store.has(id)) return Promise.resolve(store.get(id));
    if (!exists(kind, key)) return Promise.resolve(null);
    return new Promise((resolve) => {
      if (waiters.has(id)) { waiters.get(id).push(resolve); return; }
      waiters.set(id, [resolve]);
      const s = document.createElement("script");
      s.src = `${base}${kind}/${key}.js`;
      s.async = true;
      s.onerror = () => settle(id, null);
      document.head.appendChild(s);
    });
  }

  const cached = (kind, key) => store.get(`${kind}/${key}`);

  root.MeshGeo = {
    SCALE, origin, b64ToBytes, decodeExact, decodeLines, inside,
    put, units: setMeta, meta: () => meta, exists, load, cached,
  };
})(typeof globalThis !== "undefined" ? globalThis : this);

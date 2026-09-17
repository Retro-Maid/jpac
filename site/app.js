"use strict";
/*
 * メッシュ市区町村マップ — the page layer.
 *
 * Containment is decided from data built offline: the 3次 cell table
 * (lookup.js) and, where a boundary crosses a cell, that cell's unsimplified
 * boundary geometry (geo.js, docs/POLICY.md §3.2). This file draws: the map,
 * the mesh overlay, the boundary outlines, the panels.
 *
 * The overlay is 6次メッシュ (~125 m) based: from SIXTH_MIN_ZOOM each 3次 cell a
 * boundary crosses is split into its 64 6次 cells and each is coloured by what
 * its own ground holds. Below that zoom a 6次 cell is under a pixel, so the 3次
 * cell is drawn instead.
 *
 * Cheapness is a design constraint, not an afterthought:
 *  - grid layers repaint only once the map is idle (not on every zoom frame);
 *  - selecting a municipality repaints one thin layer (the red outline), not
 *    the mesh or the base outlines;
 *  - boundary lines are bucketed per chunk, so a tile touches only its lines;
 *  - 6次 colours are computed once per 3次 cell and cached; a cell no boundary
 *    crosses is one fill, not 64.
 *
 * What it will not do is claim more than the data holds: an address resolves
 * to its municipality and no further, and a point on a boundary gets every
 * candidate, not a guess.
 */

const { UNKNOWN, rowColOf, codeOf, rowColOfCode, rowCol6Of, code6Of, parseMeshCode } = MeshLookup;
const OVERLAY_MIN_ZOOM = 8;
const SIXTH_MIN_ZOOM = 13;      // 6次 cells (~125 m) are ~6 px here
const FINE_MIN_ZOOM = 10;       // detailed outlines from here, national file below
// Opened by double-clicking index.html. The data still loads (it is a
// <script>, not a fetch), but OpenStreetMap refuses tile requests that carry no
// Referer, and file:// pages send none — every tile would read "Access blocked".
const FROM_FILE = location.protocol === "file:";
const isPhone = () => matchMedia("(max-width: 640px)").matches;
// Paint grid layers when the map comes to rest, keep one ring of tiles around.
const LAZY_GRID = { updateWhenIdle: true, updateWhenZooming: false, keepBuffer: 1 };

// Material Icons (Apache License 2.0), inlined so the page needs no icon font
// and still draws its chrome offline.
const ICONS = {
  menu: "M3 18h18v-2H3v2zm0-5h18v-2H3v2zm0-7v2h18V6H3z",
  search: "M15.5 14h-.79l-.28-.27A6.471 6.471 0 0 0 16 9.5 6.5 6.5 0 1 0 9.5 16c1.61 0 3.09-.59 4.23-1.57l.27.28v.79l5 4.99L20.49 19l-4.99-5zm-6 0C7.01 14 5 11.99 5 9.5S7.01 5 9.5 5 14 7.01 14 9.5 11.99 14 9.5 14z",
  close: "M19 6.41 17.59 5 12 10.59 6.41 5 5 6.41 10.59 12 5 17.59 6.41 19 12 13.41 17.59 19 19 17.59 13.41 12z",
  place: "M12 2C8.13 2 5 5.13 5 9c0 5.25 7 13 7 13s7-7.75 7-13c0-3.87-3.13-7-7-7zm0 9.5a2.5 2.5 0 0 1 0-5 2.5 2.5 0 0 1 0 5z",
  share: "M18 16.08c-.76 0-1.44.3-1.96.77L8.91 12.7c.05-.23.09-.46.09-.7s-.04-.47-.09-.7l7.05-4.11c.54.5 1.25.81 2.04.81 1.66 0 3-1.34 3-3s-1.34-3-3-3-3 1.34-3 3c0 .24.04.47.09.7L8.04 9.81C7.5 9.31 6.79 9 6 9c-1.66 0-3 1.34-3 3s1.34 3 3 3c.79 0 1.5-.31 2.04-.81l7.12 4.16c-.05.21-.08.43-.08.65 0 1.61 1.31 2.92 2.92 2.92s2.92-1.31 2.92-2.92-1.31-2.92-2.92-2.92z",
  copy: "M16 1H4c-1.1 0-2 .9-2 2v14h2V3h12V1zm3 4H8c-1.1 0-2 .9-2 2v14c0 1.1.9 2 2 2h11c1.1 0 2-.9 2-2V7c0-1.1-.9-2-2-2zm0 16H8V7h11v14z",
  grid_on: "M20 2H4c-1.1 0-2 .9-2 2v16c0 1.1.9 2 2 2h16c1.1 0 2-.9 2-2V4c0-1.1-.9-2-2-2zM8 20H4v-4h4v4zm0-6H4v-4h4v4zm0-6H4V4h4v4zm6 12h-4v-4h4v4zm0-6h-4v-4h4v4zm0-6h-4V4h4v4zm6 12h-4v-4h4v4zm0-6h-4v-4h4v4zm0-6h-4V4h4v4z",
  my_location: "M12 8c-2.21 0-4 1.79-4 4s1.79 4 4 4 4-1.79 4-4-1.79-4-4-4zm8.94 3A8.994 8.994 0 0 0 13 3.06V1h-2v2.06A8.994 8.994 0 0 0 3.06 11H1v2h2.06A8.994 8.994 0 0 0 11 20.94V23h2v-2.06A8.994 8.994 0 0 0 20.94 13H23v-2h-2.06zM12 19c-3.870 0-7-3.13-7-7s3.13-7 7-7 7 3.13 7 7-3.13 7-7 7z",
  info: "M11 7h2v2h-2zm0 4h2v6h-2zm1-9C6.48 2 2 6.48 2 12s4.48 10 10 10 10-4.48 10-10S17.52 2 12 2zm0 18c-4.41 0-8-3.590-8-8s3.59-8 8-8 8 3.59 8 8-3.59 8-8 8z",
  chevron_left: "M15.41 7.41 14 6l-6 6 6 6 1.41-1.41L10.83 12z",
  chevron_right: "M10 6 8.59 7.41 13.17 12l-4.58 4.59L10 18l6-6z",
  add: "M19 13h-6v6h-2v-6H5v-2h6V5h2v6h6v2z",
  remove: "M19 13H5v-2h14v2z",
  arrow_back: "M20 11H7.83l5.59-5.59L12 4l-8 8 8 8 1.41-1.41L7.83 13H20v-2z",
  city: "M15 11V5l-3-3-3 3v2H3v14h18V11h-6zm-8 8H5v-2h2v2zm0-4H5v-2h2v2zm0-4H5V9h2v2zm6 8h-2v-2h2v2zm0-4h-2v-2h2v2zm0-4h-2V9h2v2zm0-4h-2V5h2v2zm6 12h-2v-2h2v2zm0-4h-2v-2h2v2z",
  tag: "M20 10V8h-4V4h-2v4h-4V4H8v4H4v2h4v4H4v2h4v4h2v-4h4v4h2v-4h4v-4h-4v-4h4zm-6 4h-4v-4h4v4z",
  train: "M12 2c-4 0-8 .5-8 4v9.5C4 17.43 5.57 19 7.5 19L6 20.5v.5h12v-.5L16.5 19c1.93 0 3.5-1.57 3.5-3.5V6c0-3.5-3.58-4-8-4zM7.5 17c-.83 0-1.5-.67-1.5-1.5S6.67 14 7.5 14s1.5.67 1.5 1.5S8.33 17 7.5 17zm3.5-7H6V6h5v4zm2 0V6h5v4h-5zm3.5 7c-.83 0-1.5-.67-1.5-1.5s.67-1.5 1.5-1.5 1.5.67 1.5 1.5-.67 1.5-1.5 1.5z",
  mail: "M20 4H4c-1.1 0-1.99.9-1.99 2L2 18c0 1.1.9 2 2 2h16c1.1 0 2-.9 2-2V6c0-1.1-.9-2-2-2zm0 4-8 5-8-5V6l8 5 8-5v2z",
  call: "M6.62 10.79c1.44 2.83 3.76 5.14 6.59 6.59l2.2-2.2c.27-.27.67-.36 1.02-.24 1.12.37 2.33.57 3.57.57.55 0 1 .45 1 1V20c0 .55-.45 1-1 1-9.39 0-17-7.61-17-17 0-.55.45-1 1-1h3.5c.55 0 1 .45 1 1 0 1.25.2 2.45.57 3.57.11.35.03.74-.25 1.02l-2.2 2.2z",
  bus: "M4 16c0 .88.39 1.67 1 2.22V20c0 .55.45 1 1 1h1c.55 0 1-.45 1-1v-1h8v1c0 .55.45 1 1 1h1c.55 0 1-.45 1-1v-1.78c.61-.55 1-1.34 1-2.22V6c0-3.5-3.58-4-8-4s-8 .5-8 4v10zm3.5 1c-.83 0-1.5-.67-1.5-1.5S6.67 14 7.5 14s1.5.67 1.5 1.5S8.33 17 7.5 17zm9 0c-.83 0-1.5-.67-1.5-1.5s.67-1.5 1.5-1.5 1.5.67 1.5 1.5-.67 1.5-1.5 1.5zM18 11H6V6h12v5z",
  fit: "M3 5v4h2V5h4V3H5c-1.1 0-2 .9-2 2zm2 10H3v4c0 1.1.9 2 2 2h4v-2H5v-4zm14 4h-4v2h4c1.1 0 2-.9 2-2v-4h-2v4zm0-16h-4v2h4v4h2V5c0-1.1-.9-2-2-2z",
  code: "M9.4 16.6 4.8 12l4.6-4.6L8 6l-6 6 6 6 1.4-1.4zm5.2 0 4.6-4.6-4.6-4.6L16 6l6 6-6 6-1.4-1.4z",
};
const icon = (name, cls = "") =>
  `<svg class="ic ${cls}" viewBox="0 0 24 24" aria-hidden="true"><path d="${ICONS[name]}"/></svg>`;
document.querySelectorAll("[data-icon]").forEach((el) => { el.innerHTML = icon(el.dataset.icon); });

const $ = (id) => document.getElementById(id);
const esc = (s) => String(s).replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));

const S = {
  table: null,
  munis: [],
  meta: null,
  overlay: true,
  view: null,       // what the side panel shows: {type:"pin",lat,lng} | {type:"m",idx} | {type:"results"} | {type:"about"}
  back: null,       // re-renders the previous panel, when there is one
  units: null,      // boundary units (data/geo/units.js), each with its municipality indices
  outlined: -1,     // municipality drawn in red on the highlight layer
  seq: 0,           // latest point request; an older one that resolves late is dropped
  jpac: null,       // data/jpac_data.js: 駅・郵便番号・市外局番（無ければその節を出さない）
  stationsByLg: null,
  stationsByMesh: null,
};

function push(map, key, value) {
  let list = map.get(key);
  if (!list) { list = []; map.set(key, list); }
  list.push(value);
}

// ------------------------------------------------------------------ data
function b64ToBuffer(s) {
  const bin = atob(s);
  const out = new Uint8Array(bin.length);
  for (let i = 0; i < bin.length; i++) out[i] = bin.charCodeAt(i);
  return out.buffer;
}

// data/mesh_data.js (tools/build_mesh_table.py) sets window.MESH_DATA. A
// <script> rather than fetch() is what lets the page work from file:// too.
function load() {
  const d = window.MESH_DATA;
  if (!d) throw new Error("data/mesh_data.js を読み込めませんでした");
  if (d.meta.unknown_index !== UNKNOWN) throw new Error("unknown_index がこのページの想定と異なります");
  S.table = MeshLookup.create(b64ToBuffer(d.uniform), b64ToBuffer(d.mixed));
  S.munis = d.meta.municipalities;
  S.meta = d.meta;
  // Boundary units speak lg_code, the key every jpac table joins on; map them
  // onto this page's municipality indices. A unit with no current code (the
  // nameless 13199 area) is the UNKNOWN candidate, as in the cell table.
  const gm = window.MeshGeo && MeshGeo.meta();
  if (gm) {
    const byLg = new Map(S.munis.map((m, i) => [m.lg_code, i]));
    S.units = gm.units.map((u) => ({
      ...u,
      ids: u.lg.length ? u.lg.map((lg) => (byLg.has(lg) ? byLg.get(lg) : UNKNOWN)) : [UNKNOWN],
    }));
  }
  // jpac が既に持っている対応。鍵は lg_code で、町字には降りない（POLICY.md §4）。
  // 駅は代表点も持つので、3次メッシュごとにも引けるようにしておく。
  const jd = window.JPAC_DATA;
  if (jd) {
    S.jpac = jd;
    S.stationsByLg = new Map();
    S.stationsByMesh = new Map();
    for (const st of jd.stations) {
      for (const lg of st.lg) push(S.stationsByLg, lg, st);
      const [r6, c6] = rowCol6Of(st.y, st.x);
      push(S.stationsByMesh, codeOf(Math.floor(r6 / 8), Math.floor(c6 / 8)), st);
    }
    // バス停は27万件あるので点は持たせていない。どのチャンクが存在するかだけ渡し、
    // 見えている範囲のぶんを都度読む（site/bus.js）。
    if (window.JPAC_BUS) JPAC_BUS.meshes(jd.busMeshes);
  }
}

// ---------------------------------------------------------------- naming
const PREF_RE = /^(北海道|東京都|京都府|大阪府|.{2,3}県)/;
function splitName(full) {
  const m = full.match(PREF_RE);
  return m ? { pref: m[1], local: full.slice(m[1].length) } : { pref: "", local: full };
}
const muniName = (i) => (i === UNKNOWN ? "所属未定地" : S.munis[i].name);

const fix = (v, d = 6) => v.toFixed(d);
function dms(lat, lng) {
  const part = (v, pos, neg) => {
    const a = Math.abs(v);
    let d = Math.floor(a), m = Math.floor((a - d) * 60);
    let s = Math.round(((a - d) * 60 - m) * 600) / 10;
    if (s >= 60) { s = 0; m += 1; }
    if (m >= 60) { m = 0; d += 1; }
    return `${d}°${String(m).padStart(2, "0")}'${s.toFixed(1).padStart(4, "0")}"${v >= 0 ? pos : neg}`;
  };
  return `${part(lat, "N", "S")} ${part(lng, "E", "W")}`;
}

const inJapan = (lat, lng) => lat >= 20 && lat < 46 && lng >= 122 && lng < 154;
function resolve(lat, lng) {
  if (!inJapan(lat, lng)) return { lat, lng, kind: "out", ids: [] };
  const [r6, c6] = rowCol6Of(lat, lng);
  const row = Math.floor(r6 / 8), col = Math.floor(c6 / 8);
  const code = codeOf(row, col);
  return { lat, lng, row, col, code, r6, c6, code6: code6Of(r6, c6), ...S.table.lookup(code) };
}

// Settle a point. The cell table answers when a whole 3次 cell lies in one
// municipality. Where a boundary or the coast crosses the cell, the cell's
// unsimplified geometry (data/geo/exact) decides the point itself.
async function decide(lat, lng) {
  const p = resolve(lat, lng);
  if (p.kind === "out" || !S.units) return { ...p, how: "cell", geo: false };
  const chunk = await MeshGeo.load("exact", String(p.code).padStart(8, "0").slice(0, 6));
  const pieces = chunk ? chunk.get(p.code) : null;
  if (!pieces) return { ...p, how: "cell", geo: true };
  const units = unitsAt(pieces, lng, lat);
  const ids = idsOfUnits(units);
  const kind = !ids.length ? "none" : ids.length > 1 ? "ambiguous" : ids[0] === UNKNOWN ? "unknown" : "contains";
  return { ...p, kind, ids, units, how: "point", geo: true, cellIds: p.ids };
}
function unitsAt(pieces, x, y) {
  const out = [];
  for (const pc of pieces) if (MeshGeo.inside(pc.rings, x, y) && !out.includes(pc.unit)) out.push(pc.unit);
  return out;
}
function idsOfUnits(units) {
  const ids = [];
  for (const u of units) for (const i of S.units[u].ids) if (!ids.includes(i)) ids.push(i);
  return ids.sort((a, b) => a - b);
}

const KIND_LABEL = {
  contains: "市区町村を特定",
  ambiguous: "候補が複数",
  unknown: "所属未定地",
  none: "判定なし",
  out: "範囲外",
};
const SPLIT_NOTE = "2020年の境界データでは旧浜松市北区として1つの区域です。2024年の区再編で中央区と浜名区に分かれましたが、その区界がデータに無いため、どちらかは決められません。";
function noteFor(p) {
  if (p.kind === "out") return "日本の標準地域メッシュの範囲外です。";
  if (p.kind === "unknown") return "陸地ですが、境界データ上で市区町村名が空の区域です。";
  if (p.how === "point") {
    if (p.kind === "contains") return "この地点の市区町村を、境界線（e-Stat 国勢調査の境界・2020年）で判定しました。";
    if (p.kind === "none") return "陸地の境界に含まれない地点です（海、または県境で境界データに隙間がある場所）。最寄りの市区町村に寄せることはしません。";
    if (p.units.length > 1) return "県境で境界データが重なっている地点です（発行元は県境を接合していません）。どちらかに決めず、両方を表示しています。";
    return SPLIT_NOTE;
  }
  if (p.kind === "contains") return "この地点を含む3次メッシュ（約1km四方）は、全体がこの市区町村に含まれます。";
  // With boundary data, a boundary cell is always decided by point; a
  // cell-level "several" can only be a unit that itself has two successors.
  if (p.kind === "ambiguous") return p.geo ? SPLIT_NOTE : "このメッシュは市区町村の境界をまたいでいます。クリックした点がどちらに属するかは決めず、候補をすべて表示しています。";
  return "境界データに含まれない地点です（海など）。最寄りの市区町村に寄せることはしません。";
}
const howText = (p) => (p.how === "point" ? "地点で判定（境界線）" : "メッシュで判定（3次メッシュ全体が同じ結果）");

// The one-line "address" of a point, as far as this data can say.
function addressLine(p) {
  switch (p.kind) {
    case "contains": return muniName(p.ids[0]);
    case "ambiguous": return p.ids.map(muniName).join(" または ");
    case "unknown": return "所属未定地（市区町村名なし）";
    case "none": return "市区町村の判定なし";
    default: return "日本の範囲外";
  }
}
function shortLine(p) {
  if (p.kind === "contains" && p.ids[0] !== UNKNOWN) return splitName(muniName(p.ids[0])).local;
  if (p.kind === "ambiguous") return p.how === "point" && p.units.length > 1 ? "県境付近" : "市区町村の候補が複数";
  return KIND_LABEL[p.kind];
}

// ------------------------------------------------------------------- map
const map = L.map("map", { zoomControl: false, minZoom: 4, maxZoom: 18, preferCanvas: true }).setView([36.5, 137.8], 5);
map.attributionControl.setPrefix('<a href="https://leafletjs.com" target="_blank" rel="noopener">Leaflet</a>');
if (!FROM_FILE) {
  L.tileLayer("https://tile.openstreetmap.org/{z}/{x}/{y}.png", {
    maxZoom: 18,
    keepBuffer: 1,
    attribution: '© <a href="https://www.openstreetmap.org/copyright" target="_blank" rel="noopener">OpenStreetMap</a> contributors ｜ 境界：e-Stat（加工）',
  }).addTo(map);
} else {
  map.attributionControl.addAttribution("境界：e-Stat（加工）");
}
L.control.scale({ imperial: false, position: "bottomright", maxWidth: 100 }).addTo(map);

// Web Mercator, as Leaflet's EPSG:3857 with 256 px tiles.
const mercX = (lng, z) => (lng + 180) / 360 * 256 * 2 ** z;
const mercY = (lat, z) => {
  const s = Math.sin(lat * Math.PI / 180);
  return (0.5 - Math.log((1 + s) / (1 - s)) / (4 * Math.PI)) * 256 * 2 ** z;
};
function tileBox(coords, size) {
  const ox = coords.x * size.x, oy = coords.y * size.y;
  const nw = map.unproject([ox, oy], coords.z), se = map.unproject([ox + size.x, oy + size.y], coords.z);
  return { ox, oy, w: nw.lng, e: se.lng, s: se.lat, n: nw.lat };
}
function canvasTile(size) {
  const t = document.createElement("canvas");
  t.width = size.x;
  t.height = size.y;
  return t;
}

// ------------------------------------------------------- mesh overlay
// Golden-angle hues: municipality indices are sorted by code, so neighbours
// usually have adjacent indices, and adjacent indices land far apart in hue.
const hue = (i) => (i * 137.508) % 360;
const FILL_BOUNDARY = "rgba(40,40,40,.28)";
const FILL_UNKNOWN = "rgba(120,60,160,.45)";
const fillCache = new Map();
function fillOne(i) {
  if (i === UNKNOWN) return FILL_UNKNOWN;
  let f = fillCache.get(i);
  if (!f) { f = `hsla(${hue(i)},70%,48%,.34)`; fillCache.set(i, f); }
  return f;
}
function fillFor(res) {
  switch (res.kind) {
    case "none": return null;
    case "unknown": return FILL_UNKNOWN;
    case "ambiguous": return FILL_BOUNDARY;
    default: return fillOne(res.ids[0]);
  }
}

// 6次 colours of one 3次 cell a boundary crosses, computed once. Each 6次 cell
// is tested at its centre and its four corners: all the same set of
// municipalities -> that set; a different non-empty set at any corner -> the
// cell itself straddles a boundary. Corners are shared between neighbours, so
// a 9x9 lattice plus 64 centres covers the whole 3次 cell.
const MIXED = -1;
const SETS = [[]];                 // set id -> sorted municipality indices ([] = no land)
const setIds = new Map([["", 0]]);
function setId(ids) {
  const k = ids.join(",");
  let id = setIds.get(k);
  if (id === undefined) { id = SETS.length; SETS.push(ids); setIds.set(k, id); }
  return id;
}
const subCache = new Map();
function subCells(code, row, col, pieces) {
  let out = subCache.get(code);
  if (out) return out;
  const at = (fy, fx) => setId(idsOfUnits(unitsAt(pieces, (col + fx) / 80, (row + fy) / 120)));
  const corner = new Int32Array(81);
  for (let a = 0; a <= 8; a++) for (let b = 0; b <= 8; b++) corner[a * 9 + b] = at(a / 8, b / 8);
  out = new Int32Array(64);
  for (let a = 0; a < 8; a++) {
    for (let b = 0; b < 8; b++) {
      const c = at((a + 0.5) / 8, (b + 0.5) / 8);
      let v = c;
      for (const k of [a * 9 + b, a * 9 + b + 1, (a + 1) * 9 + b, (a + 1) * 9 + b + 1]) {
        if (corner[k] !== c && corner[k] !== 0 && c !== 0) { v = MIXED; break; }
      }
      out[a * 8 + b] = v;
    }
  }
  if (subCache.size > 20000) subCache.clear();
  subCache.set(code, out);
  return out;
}
function fillForSet(id) {
  if (id === MIXED) return FILL_BOUNDARY;
  const ids = SETS[id];
  if (!ids.length) return null;
  return ids.length > 1 ? FILL_BOUNDARY : fillOne(ids[0]);
}

const MeshLayer = L.GridLayer.extend({
  createTile(coords, done) {
    const size = this.getTileSize();
    const tile = canvasTile(size);
    const z = coords.z;
    if (!S.table || z < OVERLAY_MIN_ZOOM) { setTimeout(() => done(null, tile), 0); return tile; }
    const b = tileBox(coords, size);
    const r0 = Math.floor(b.s * 120), r1 = Math.floor(b.n * 120);
    const c0 = Math.floor(b.w * 80), c1 = Math.floor(b.e * 80);
    const sixth = z >= SIXTH_MIN_ZOOM && !!S.units;
    const keys = [];
    if (sixth) {
      for (let r = r0; r <= r1; r++) for (let c = c0; c <= c1; c++) {
        const k = String(codeOf(r, c)).padStart(8, "0").slice(0, 6);
        if (!keys.includes(k) && MeshGeo.exists("exact", k)) keys.push(k);
      }
    }
    Promise.all(keys.map((k) => MeshGeo.load("exact", k))).then(() => {
      const ctx = tile.getContext("2d");
      const n = sixth ? 8 : 1;          // sub-cells per 3次 cell edge
      // Mercator: x depends only on longitude and y only on latitude.
      const xs = [], ys = [];
      for (let c = c0 * n; c <= (c1 + 1) * n; c++) xs.push(mercX(c / (80 * n), z) - b.ox);
      for (let r = r0 * n; r <= (r1 + 1) * n; r++) ys.push(mercY(r / (120 * n), z) - b.oy);
      // Rectangles are collected per colour and filled once per colour, and a
      // row of equal 6次 cells is one rectangle: switching fillStyle per cell
      // was a top self-time in the profile.
      const byColor = new Map();
      const add = (f, ri, ci, h, w) => {
        let list = byColor.get(f);
        if (!list) { list = []; byColor.set(f, list); }
        list.push(ri, ci, h, w);
      };
      for (let r = r0; r <= r1; r++) {
        for (let c = c0; c <= c1; c++) {
          const code = codeOf(r, c);
          const res = S.table.lookup(code);
          const ri = (r - r0) * n, ci = (c - c0) * n;
          const chunk = sixth ? MeshGeo.cached("exact", String(code).padStart(8, "0").slice(0, 6)) : null;
          const pieces = chunk ? chunk.get(code) : null;
          if (!pieces) {
            const f = fillFor(res);
            if (f) add(f, ri, ci, n, n);
            continue;
          }
          const sub = subCells(code, r, c, pieces);
          for (let a = 0; a < 8; a++) {
            let bb = 0;
            while (bb < 8) {
              const v = sub[a * 8 + bb];
              let e2 = bb + 1;
              while (e2 < 8 && sub[a * 8 + e2] === v) e2++;
              const f = fillForSet(v);
              if (f) add(f, ri + a, ci + bb, 1, e2 - bb);
              bb = e2;
            }
          }
        }
      }
      for (const [f, list] of byColor) {
        ctx.fillStyle = f;
        ctx.beginPath();
        for (let k = 0; k < list.length; k += 4) {
          const ri = list[k], ci = list[k + 1], h = list[k + 2], w = list[k + 3];
          const top = ys[ri + h] ?? ys[ys.length - 1];
          ctx.rect(xs[ci], top, xs[ci + w] - xs[ci], ys[ri] - top);
        }
        ctx.fill();
      }
      const line = (arr, horiz, style, width) => {
        ctx.strokeStyle = style;
        ctx.lineWidth = width;
        ctx.beginPath();
        for (const v of arr) {
          const p = Math.round(v) + 0.5;
          if (horiz) { ctx.moveTo(0, p); ctx.lineTo(size.x, p); } else { ctx.moveTo(p, 0); ctx.lineTo(p, size.y); }
        }
        ctx.stroke();
      };
      if (sixth && z >= 15) {                       // 6次 grid
        line(xs, false, "rgba(60,60,60,.14)", 1);
        line(ys, true, "rgba(60,60,60,.14)", 1);
      }
      if (z >= 12) {                                 // 3次 grid
        line(xs.filter((_, i) => i % n === 0), false, "rgba(60,60,60,.3)", 1);
        line(ys.filter((_, i) => i % n === 0), true, "rgba(60,60,60,.3)", 1);
      }
      done(null, tile);
    });
    return tile;
  },
});
const mesh = new MeshLayer({ zIndex: 2, ...LAZY_GRID });

// ------------------------------------------------------ boundary outlines
// Display only: simplified outlines, drawn per map tile from the chunks the
// tile needs. Lines are bucketed per chunk on first use, so a tile iterates
// only the lines near it rather than every line of the chunk.
// Per chunk, once: a flat bounding-box array (4 numbers per line) and each
// line's points projected to Web Mercator at zoom 0. A tile then scans boxes in
// a typed array and scales projected points by 2^z — no trig per point per
// tile, no per-tile allocation. (Measured in a CPU profile of a normal session:
// a Map-of-buckets index and per-point projection were the two largest
// self-times, and the index's allocations fed the garbage collector.)
function prepare(lines) {
  if (lines.bb) return lines;
  const bb = new Float64Array(lines.length * 4);
  for (let i = 0; i < lines.length; i++) {
    const xy = lines[i].xy, p = new Float64Array(xy.length);
    let x0 = Infinity, y0 = Infinity, x1 = -Infinity, y1 = -Infinity;
    for (let k = 0; k < xy.length; k += 2) {
      const x = xy[k], y = xy[k + 1];
      if (x < x0) x0 = x; if (x > x1) x1 = x;
      if (y < y0) y0 = y; if (y > y1) y1 = y;
      p[k] = mercX(x, 0);
      p[k + 1] = mercY(y, 0);
    }
    lines[i].p = p;
    bb[4 * i] = x0; bb[4 * i + 1] = y0; bb[4 * i + 2] = x1; bb[4 * i + 3] = y1;
  }
  lines.bb = bb;
  return lines;
}
function lineKeysIn(w, s, e, n) {
  const out = [];
  for (let pp = Math.floor(s * 1.5); pp <= Math.floor(n * 1.5); pp++) {
    for (let uu = Math.floor(w) - 100; uu <= Math.floor(e) - 100; uu++) {
      const k = `${String(pp).padStart(2, "0")}${String(uu).padStart(2, "0")}`;
      if (MeshGeo.exists("line", k)) out.push(k);
    }
  }
  return out;
}
// One outline layer; `pick` chooses which lines, `style` how they look.
const OutlineLayer = L.GridLayer.extend({
  createTile(coords, done) {
    const size = this.getTileSize();
    const tile = canvasTile(size);
    const pick = this.options.pick();
    if (!S.units || !pick) { setTimeout(() => done(null, tile), 0); return tile; }
    const z = coords.z, b = tileBox(coords, size);
    const pw = (b.e - b.w) * 0.02, ph = (b.n - b.s) * 0.02;
    const w = b.w - pw, e = b.e + pw, s = b.s - ph, n = b.n + ph;
    const fine = z >= FINE_MIN_ZOOM;
    const wants = fine ? lineKeysIn(w, s, e, n).map((k) => MeshGeo.load("line", k)) : [MeshGeo.load("coarse", "all")];
    Promise.all(wants).then((chunks) => {
      const ctx = tile.getContext("2d");
      const scale = 2 ** z;
      ctx.beginPath();
      for (const raw of chunks) {
        if (!raw) continue;
        const lines = prepare(raw), bb = lines.bb;
        for (let i = 0; i < lines.length; i++) {
          if (bb[4 * i + 2] < w || bb[4 * i] > e || bb[4 * i + 3] < s || bb[4 * i + 1] > n) continue;
          const l = lines[i];
          if (!pick(l)) continue;
          const p = l.p, last = p.length - 2;
          let px = 0, py = 0;
          for (let k = 0; k <= last; k += 2) {
            const x = p[k] * scale - b.ox, y = p[k + 1] * scale - b.oy;
            if (k === 0) ctx.moveTo(x, y);
            else if (k !== last && Math.abs(x - px) + Math.abs(y - py) < 0.7) continue;
            else ctx.lineTo(x, y);
            px = x; py = y;
          }
        }
      }
      const st = this.options.style(z, fine);
      ctx.strokeStyle = st.color;
      ctx.lineWidth = st.width;
      ctx.setLineDash(st.dash);
      ctx.stroke();
      done(null, tile);
    });
    return tile;
  },
});
const boundary = new OutlineLayer({
  zIndex: 3, ...LAZY_GRID,
  pick: () => () => true,
  style: (z, fine) => ({
    color: fine ? "rgba(60,64,67,.85)" : "rgba(95,99,104,.6)",
    width: fine ? (z >= 13 ? 1.6 : 1.3) : 0.8,
    dash: z >= 12 ? [5, 3] : [],
  }),
});
// The municipality being looked at, in red, on its own thin layer: selecting
// one repaints this and nothing else.
const highlight = new OutlineLayer({
  zIndex: 4, ...LAZY_GRID,
  pick: () => (S.outlined < 0 ? null : (l) => S.units[l.unit].ids.includes(S.outlined)),
  style: () => ({ color: "#d93025", width: 2.4, dash: [6, 4] }),
});
function setOutlined(idx) {
  if (S.outlined === idx) return;
  S.outlined = idx;
  refreshStations();          // 選択中の市区町村の駅は、ズームが浅くても出す
  if (idx < 0) { map.removeLayer(highlight); return; }
  if (!S.units) return;
  if (map.hasLayer(highlight)) highlight.redraw(); else highlight.addTo(map);
}

// ------------------------------------------------------------- 駅のピン
// 駅の所属市区町村は jpac が境界で判定済み（bridge_station_municipality）。地図に出す
// のはその代表点で、クリック地点の判定には一切使わない。選択中の市区町村の駅は、
// ズームが浅くても出す —— 「この市区町村の駅」を一覧から辿れるようにするため。
const STATION_MIN_ZOOM = 12;
const stationLayer = L.layerGroup();
function stationsOfSelected() {
  if (S.outlined < 0 || !S.stationsByLg) return [];
  const m = S.munis[S.outlined];
  return m ? (S.stationsByLg.get(m.lg_code) || []) : [];
}
function refreshStations() {
  if (!S.jpac) return;
  const z = map.getZoom();
  const selected = stationsOfSelected();
  if (z < STATION_MIN_ZOOM && !selected.length) {
    if (map.hasLayer(stationLayer)) map.removeLayer(stationLayer);
    return;
  }
  stationLayer.clearLayers();
  if (!map.hasLayer(stationLayer)) stationLayer.addTo(map);
  const bounds = map.getBounds();
  const pool = z >= STATION_MIN_ZOOM ? S.jpac.stations : selected;
  const mine = new Set(selected.map((s) => s.id));
  let drawn = 0;
  for (const st of pool) {
    if (!bounds.contains([st.y, st.x])) continue;
    if (++drawn > 500) break;                 // a tile-dense view stays responsive
    const highlighted = mine.has(st.id);
    L.circleMarker([st.y, st.x], {
      radius: highlighted ? 6 : 5,
      color: highlighted ? "#d93025" : "#1967d2",
      weight: 2, fillColor: "#fff", fillOpacity: 1,
    })
      .bindTooltip(`${esc(st.n)}<span style="color:#70757a">（${esc(st.o)}）</span>`,
                   { direction: "top", opacity: 1 })
      .on("click", () => openStation(st))
      .addTo(stationLayer);
  }
}
map.on("moveend", refreshStations);

// ----------------------------------------------------------- バス停のピン
// 駅より1段深いズームから。27万件あるので、見えている2次メッシュのチャンクだけ読む。
const BUS_MIN_ZOOM = 14;
const busLayer = L.layerGroup();
let busToken = 0;
async function refreshBusStops() {
  if (!S.jpac || !window.JPAC_BUS) return;
  if (map.getZoom() < BUS_MIN_ZOOM) {
    if (map.hasLayer(busLayer)) map.removeLayer(busLayer);
    return;
  }
  const token = ++busToken;
  const b = map.getBounds();
  const chunks = await Promise.all(
    JPAC_BUS.meshesIn(b.getSouth(), b.getWest(), b.getNorth(), b.getEast())
      .map((k) => JPAC_BUS.load(k))
  );
  // 読んでいる間に地図が動いていたら捨てる。古い範囲を描き足さないため。
  if (token !== busToken) return;
  busLayer.clearLayers();
  if (!map.hasLayer(busLayer)) busLayer.addTo(map);
  let drawn = 0;
  for (const stops of chunks) {
    if (!stops || drawn > 800) break;
    for (const [name, operators, x, y] of stops) {
      if (!b.contains([y, x])) continue;
      if (++drawn > 800) break;        // 密集地でも操作が重くならない上限
      L.circleMarker([y, x], {
        radius: 3, color: "#188038", weight: 1.5, fillColor: "#fff", fillOpacity: 1,
      })
        // 同じ地点に複数の事業者が立っていることがある。ピンは1つにまとめ、
        // 事業者は全部出す。
        .bindTooltip(
          `${esc(name)}<span style="color:#70757a">（${operators.map(esc).join("・")}）</span>`,
          { direction: "top", opacity: 1 })
        .addTo(busLayer);
    }
  }
}
map.on("moveend", refreshBusStops);

// --------------------------------------------------------------- markers
const pinSvg = (w, h, fill, stroke, dot) =>
  `<svg viewBox="0 0 27 43" width="${w}" height="${h}"><path d="M13.5 1C6.6 1 1 6.6 1 13.5 1 23.2 13.5 42 13.5 42S26 23.2 26 13.5C26 6.6 20.4 1 13.5 1z" fill="${fill}" stroke="${stroke}" stroke-width="1.2"/><circle cx="13.5" cy="13.5" r="4.6" fill="${dot}"/></svg>`;
const pin = L.marker([0, 0], {
  icon: L.divIcon({ className: "pin", html: pinSvg(27, 43, "#ea4335", "#b31412", "#b31412"), iconSize: [27, 43], iconAnchor: [13.5, 42] }),
  interactive: false, keyboard: false, zIndexOffset: 1000,
});
const dropPin = L.marker([0, 0], {
  icon: L.divIcon({ className: "drop-pin", html: pinSvg(18, 29, "#70757a", "#4d5156", "#fff"), iconSize: [18, 29], iconAnchor: [9, 28] }),
  interactive: false, keyboard: false,
});
const cellRect = L.rectangle([[0, 0], [0, 0]], { color: "#1a73e8", weight: 1.5, fill: false, interactive: false });
const me = L.marker([0, 0], {
  icon: L.divIcon({ className: "me", html: '<div class="me-dot"></div>', iconSize: [18, 18], iconAnchor: [9, 9] }),
  interactive: false, keyboard: false,
});
const meAccuracy = L.circle([0, 0], { radius: 1, color: "#1a73e8", weight: 0, fillOpacity: .12, interactive: false });
let outline = null;

function clearOutline() {
  if (outline) { map.removeLayer(outline); outline = null; }
}

// Fallback when boundary data is absent: the municipality's extent at mesh
// resolution — every cell edge not shared with another of its cells, with
// collinear edges merged so the dashes run continuously.
function drawOutline(cells) {
  clearOutline();
  const K = 100000;
  const set = new Set();
  for (const code of cells) { const [r, c] = rowColOfCode(code); set.add(r * K + c); }
  const H = new Map(), V = new Map();
  const push = (m, k, v) => { if (!m.has(k)) m.set(k, []); m.get(k).push(v); };
  let r0 = Infinity, r1 = -Infinity, c0 = Infinity, c1 = -Infinity;
  for (const k of set) {
    const r = Math.floor(k / K), c = k % K;
    r0 = Math.min(r0, r); r1 = Math.max(r1, r); c0 = Math.min(c0, c); c1 = Math.max(c1, c);
    if (!set.has(k + K)) push(H, r + 1, c);
    if (!set.has(k - K)) push(H, r, c);
    if (!set.has(k + 1)) push(V, c + 1, r);
    if (!set.has(k - 1)) push(V, c, r);
  }
  const runs = (vals) => {
    vals.sort((a, b) => a - b);
    const out = [];
    let s = vals[0], p = vals[0];
    for (let i = 1; i <= vals.length; i++) {
      if (vals[i] === p + 1) { p = vals[i]; continue; }
      out.push([s, p + 1]);
      s = p = vals[i];
    }
    return out;
  };
  const segs = [];
  for (const [line, vals] of H) for (const [a, b] of runs(vals)) segs.push([[line / 120, a / 80], [line / 120, b / 80]]);
  for (const [line, vals] of V) for (const [a, b] of runs(vals)) segs.push([[a / 120, line / 80], [b / 120, line / 80]]);
  outline = L.polyline(segs, { color: "#d93025", weight: 2, dashArray: "6 5", interactive: false }).addTo(map);
  return L.latLngBounds([r0 / 120, c0 / 80], [(r1 + 1) / 120, (c1 + 1) / 80]);
}

// Keep a point clear of the side panel and search box when framing it.
function framePadding() {
  if (isPhone()) return { paddingTopLeft: [16, 72], paddingBottomRight: [16, innerHeight * 0.58] };
  const open = document.body.classList.contains("panel-open") && !document.body.classList.contains("panel-collapsed");
  return { paddingTopLeft: [open ? 408 + 24 : 24, 72], paddingBottomRight: [72, 40] };
}

// ----------------------------------------------------------------- panel
function openPanel(html) {
  $("panelBody").innerHTML = html;
  $("panel").hidden = false;
  $("panel").scrollTop = 0;
  document.body.classList.add("panel-open");
  document.body.classList.remove("panel-collapsed");
  $("collapse").hidden = false;
  $("collapse").innerHTML = icon("chevron_left");
  $("clearBtn").hidden = false;
  hideCard();
}

function closePanel() {
  $("panel").hidden = true;
  $("collapse").hidden = true;
  document.body.classList.remove("panel-open", "panel-collapsed");
  $("clearBtn").hidden = !$("q").value;
  S.view = null;
  S.back = null;
  map.removeLayer(pin);
  map.removeLayer(cellRect);
  clearOutline();
  setOutlined(-1);
  updateHash();
}

function toggleCollapse() {
  const c = document.body.classList.toggle("panel-collapsed");
  $("collapse").innerHTML = icon(c ? "chevron_right" : "chevron_left");
  $("collapse").setAttribute("aria-label", c ? "サイドパネルを展開" : "サイドパネルを折りたたむ");
}

function header({ title, sub = "", chip = null, back = false }) {
  return `<div class="ph">
    ${back && S.back ? `<div class="ph-back"><button class="sb-btn" data-act="back" aria-label="戻る">${icon("arrow_back")}</button></div>` : ""}
    <h1 class="ph-title">${esc(title)}</h1>
    ${sub ? `<div class="ph-sub">${esc(sub)}</div>` : ""}
    ${chip ? `<span class="chip kind-${chip.kind}">${esc(chip.label)}</span>` : ""}
  </div>`;
}

function actions(list) {
  return `<div class="actions">${list.map((a, n) =>
    `<button class="act${n === 0 ? " primary" : ""}" data-act="${a.act}"${a.disabled ? " disabled" : ""}>
       <span class="act-ic">${icon(a.icon)}</span><span>${esc(a.label)}</span></button>`).join("")}</div>`;
}

// One info row. `copy` makes the row copy its text; `act` makes it navigate.
function row({ ic, text, sub = "", copy = null, act = null, i = null, note = false }) {
  const attrs = [
    copy !== null ? `data-copy="${esc(copy)}"` : "",
    act ? `data-act="${act}"` : "",
    i !== null ? `data-i="${i}"` : "",
  ].join(" ");
  const trail = copy !== null ? `<span class="row-trail">${icon("copy")}</span>`
    : act ? `<span class="row-trail">${icon("chevron_right")}</span>` : "";
  const cls = `row${copy === null && !act ? " static" : ""}${note ? " note" : ""}`;
  return `<button class="${cls}" ${attrs}>${icon(ic)}<div class="row-text"><div>${text}</div>${sub ? `<div class="row-sub">${sub}</div>` : ""}</div>${trail}</button>`;
}

function footer() {
  return `<div class="pfoot">市区町村は e-Stat 国勢調査の境界（2020年）をもとに判定しています。
    <button class="link" data-act="about">このデータについて</button></div>`;
}

// --- 駅・郵便番号・市外局番（data/jpac_data.js）
// どれも jpac が市区町村単位で持っているもの。町字には降りない（POLICY.md §4）。
const SHOW_MAX = 12;
// 呼ぶ側が市区町村を1つに決めたときだけ出す。候補が複数のままの地点で両方の
// 郵便番号・市外局番を並べても、どちらのものか言えない（POLICY.md §4）。
function jpacSections(lg, meshCode) {
  if (!S.jpac || !lg) return "";
  const rows = [];
  const inMesh = meshCode ? (S.stationsByMesh.get(meshCode) || []) : [];
  const inLg = S.stationsByLg.get(lg) || [];

  if (inMesh.length) {
    rows.push(row({
      ic: "train", text: `このメッシュ内の駅 ${inMesh.length} 件`,
      sub: "クリックした3次メッシュ（約1km四方）の中にある駅",
    }));
    for (const st of inMesh.slice(0, SHOW_MAX)) {
      rows.push(row({ ic: "train", text: esc(st.n), sub: esc(st.o), act: "station", i: st.id }));
    }
  }
  if (inLg.length) {
    rows.push(row({
      ic: "train", text: `この市区町村の駅 ${inLg.length} 件`,
      sub: inMesh.length ? "上のメッシュ内の駅を含む" : "地図を拡大すると駅のピンが出ます",
    }));
    // メッシュの節に出したものは繰り返さない。「ほか N 件」は実際に出した行から数える。
    const rest = inLg.filter((st) => !inMesh.some((m) => m.id === st.id));
    const shown = rest.slice(0, inMesh.length ? 6 : SHOW_MAX);
    for (const st of shown) {
      rows.push(row({ ic: "train", text: esc(st.n), sub: esc(st.o), act: "station", i: st.id }));
    }
    if (rest.length > shown.length) {
      rows.push(row({ ic: "train", text: `ほか ${rest.length - shown.length} 件`, note: true }));
    }
  }

  // 路線。座標を持たないので市区町村単位の対応そのもの（POLICY.md §3.2）。
  const lineIds = (S.jpac.linesByLg || {})[lg] || [];
  if (lineIds.length) {
    const names = lineIds.map((i) => S.jpac.lineNames[i]);
    const shown = names.slice(0, SHOW_MAX)
      .map(([n, o]) => `${esc(n)}<span style="color:#70757a">（${esc(o)}）</span>`)
      .join("、");
    rows.push(row({
      ic: "train", text: `この市区町村を通る路線 ${names.length} 件`,
      sub: shown + (names.length > SHOW_MAX ? ` ほか ${names.length - SHOW_MAX} 件` : ""),
    }));
  }

  const buses = (S.jpac.busCounts || {})[lg] || 0;
  if (buses) {
    rows.push(row({
      ic: "bus", text: `この市区町村のバス停 ${buses.toLocaleString()} 件`,
      sub: `ズーム${BUS_MIN_ZOOM}以上で地図に出ます`,
    }));
  }

  const postal = (S.jpac.postal || {})[lg] || [];
  if (postal.length) {
    const shown = postal.slice(0, SHOW_MAX)
      .map(([code, kind]) => (kind ? `${code}<span style="color:#70757a">（${esc(kind === "no_listing" ? "以下に掲載がない場合" : kind)}）</span>` : code))
      .join("、");
    rows.push(row({
      ic: "mail", text: shown + (postal.length > SHOW_MAX ? ` ほか ${postal.length - SHOW_MAX} 件` : ""),
      sub: `この市区町村の郵便番号 ${postal.length} 件（町ごとの対応は下の「この地点の住所」では扱いません）`,
      copy: postal.map((p) => p[0]).join(","),
    }));
  }

  const tel = (S.jpac.telephone || {})[lg] || [];
  for (const t of tel) {
    rows.push(row({
      ic: "call", text: esc(t.a),
      sub: t.c === "partial"
        ? `番号区画 ${esc(t.z)}。この市区町村の一部のみ${t.t ? "：" + esc(String(t.t).slice(0, 60)) + (String(t.t).length > 60 ? "…" : "") : ""}`
        : `番号区画 ${esc(t.z)}。市区町村全域`,
      copy: t.a,
    }));
  }
  if (!rows.length) return "";
  return `<div class="rows">${rows.join("")}</div>`;
}

// --- a station
function openStation(st) {
  const lgs = st.lg.map((lg) => S.munis.findIndex((m) => m.lg_code === lg)).filter((i) => i >= 0);
  const prev = S.view;
  S.view = { type: "station", id: st.id };
  S.back = prev ? () => restoreView(prev) : null;
  map.removeLayer(dropPin);
  pin.setLatLng([st.y, st.x]).addTo(map);
  map.removeLayer(cellRect);
  const [r6, c6] = rowCol6Of(st.y, st.x);
  const rows = [
    row({ ic: "train", text: esc(st.o), sub: "事業者（N02 の表記のまま）" }),
    ...(((S.jpac && S.jpac.linesByStation) || {})[st.id] || []).map((i) => {
      const [name, operator] = S.jpac.lineNames[i];
      return row({ ic: "train", text: esc(name), sub: `${esc(operator)}・路線` });
    }),
    ...lgs.map((i) => row({ ic: "place", text: esc(S.munis[i].name), sub: "市区町村", act: "muni", i })),
    row({ ic: "my_location", text: `${fix(st.y)}, ${fix(st.x)}`, sub: "駅の代表点（N02 のポリラインから算出）", copy: `${fix(st.y)}, ${fix(st.x)}` }),
    row({ ic: "grid_on", text: code6Of(r6, c6), sub: "6次メッシュコード（約125m四方）", copy: code6Of(r6, c6) }),
    row({ ic: "info", note: true, text: st.lg.length > 1
      ? "この駅は複数の市区町村の境界にかかっています。候補をすべて表示しています。"
      : "駅の位置は路線の形から求めた代表点で、駅舎や出入口の位置ではありません。" }),
  ];
  openPanel(header({ title: st.n, sub: lgs.length ? S.munis[lgs[0]].name : "市区町村は未確定", chip: { kind: "muni", label: "駅" }, back: true })
    + actions([
      { act: "fit-station", icon: "fit", label: "地図で見る" },
      { act: "copy-station", icon: "copy", label: "名前をコピー" },
      { act: "share", icon: "share", label: "共有" },
    ])
    + `<div class="rows">${rows.join("")}</div>` + footer());
  S.station = st;
  map.setView([st.y, st.x], Math.max(map.getZoom(), 15));
  keepVisible([st.y, st.x]);
  updateHash();
}

// --- a point (dropped pin, searched coordinates, searched mesh code)
async function openPoint(lat, lng, { zoom = null, fromSearch = false } = {}) {
  const token = ++S.seq;
  const p = await decide(lat, lng);
  if (token !== S.seq) return;       // a newer click or search superseded this one
  S.view = { type: "pin", lat, lng };
  S.back = null;
  map.removeLayer(dropPin);
  pin.setLatLng([lat, lng]).addTo(map);
  if (p.kind !== "out") cellRect.setBounds([[p.r6 / 960, p.c6 / 640], [(p.r6 + 1) / 960, (p.c6 + 1) / 640]]).addTo(map);
  else map.removeLayer(cellRect);
  clearOutline();
  setOutlined(-1);

  const rows = [];
  for (const i of p.ids) {
    if (i === UNKNOWN) rows.push(row({ ic: "place", text: "所属未定地", sub: "境界データで市区町村名が空の区域" }));
    else rows.push(row({ ic: "place", text: esc(S.munis[i].name), sub: p.kind === "ambiguous" ? "候補" : "市区町村", act: "muni", i }));
  }
  if (!p.ids.length) rows.push(row({ ic: "place", text: esc(addressLine(p)), note: true }));
  rows.push(row({ ic: "my_location", text: `${fix(lat)}, ${fix(lng)}`, sub: "緯度・経度（10進）", copy: `${fix(lat)}, ${fix(lng)}` }));
  if (p.kind !== "out") {
    const c3 = String(p.code).padStart(8, "0");
    rows.push(row({ ic: "grid_on", text: p.code6, sub: `6次メッシュコード（約125m四方）・3次 ${c3}（約1km）`, copy: p.code6 }));
  }
  for (const i of p.ids) {
    if (i !== UNKNOWN) rows.push(row({ ic: "tag", text: S.munis[i].lg_code, sub: `全国地方公共団体コード（${esc(splitName(S.munis[i].name).local)}）`, copy: S.munis[i].lg_code }));
  }
  if (p.kind !== "out") rows.push(row({ ic: p.how === "point" ? "my_location" : "grid_on", text: howText(p), sub: "判定方法" }));
  rows.push(row({ ic: "info", text: esc(noteFor(p)), note: true }));

  const one = p.kind === "contains" && p.ids[0] !== UNKNOWN;
  openPanel(
    header({ title: dms(lat, lng), sub: addressLine(p), chip: { kind: p.kind, label: KIND_LABEL[p.kind] } })
    + actions([
      { act: one ? "muni-first" : "noop", icon: "city", label: "市区町村", disabled: !one },
      { act: "copy-coords", icon: "copy", label: "座標をコピー" },
      { act: "share", icon: "share", label: "共有" },
    ])
    + `<div class="rows">${rows.join("")}</div>`
    + (one ? jpacSections(S.munis[p.ids[0]].lg_code, p.code) : "")
    + footer());
  S.pointIds = p.ids;
  $("q").value = fromSearch ? $("q").value : `${fix(lat)}, ${fix(lng)}`;
  if (zoom !== null) map.setView([lat, lng], Math.max(map.getZoom(), zoom));
  else if (!map.getBounds().contains([lat, lng])) map.panTo([lat, lng]);
  keepVisible([lat, lng]);
  updateHash();
}

function keepVisible(latlng) {
  const pad = framePadding();
  map.panInside(latlng, { paddingTopLeft: pad.paddingTopLeft, paddingBottomRight: pad.paddingBottomRight });
}

// --- a municipality
function openMunicipality(idx, { rest = "", fit = true, back = null } = {}) {
  const m = S.munis[idx];
  const { pref, local } = splitName(m.name);
  S.view = { type: "m", idx };
  S.back = back;
  map.removeLayer(pin);
  map.removeLayer(cellRect);
  map.removeLayer(dropPin);
  const cells = S.table.cellsOf(idx);
  const border = cells.filter((c) => S.table.lookup(c).kind !== "contains").length;
  let bounds;
  const mine = S.units ? S.units.filter((u) => u.ids.includes(idx)) : [];
  if (mine.length) {
    // The real outline comes from the highlight layer; the frame from its units.
    clearOutline();
    bounds = L.latLngBounds([]);
    for (const u of mine) bounds.extend([[u.bbox[1], u.bbox[0]], [u.bbox[3], u.bbox[2]]]);
    setOutlined(idx);
  } else {
    setOutlined(-1);
    bounds = drawOutline(cells);
  }
  const sharesSplit = mine.some((u) => u.ids.length > 1);
  const rows = [
    row({ ic: "city", text: esc(m.name), sub: "市区町村", copy: m.name }),
    row({ ic: "tag", text: m.lg_code, sub: "全国地方公共団体コード", copy: m.lg_code }),
    row({ ic: "grid_on", text: `${cells.length.toLocaleString()} メッシュ`, sub: `3次メッシュ（約1km）の数。うち境界をまたぐもの ${border.toLocaleString()}` }),
  ];
  if (rest) rows.push(row({ ic: "info", text: `「${esc(rest)}」（町字・番地）はこのデータでは扱っていません。市区町村までを表示しています。`, note: true }));
  rows.push(row({
    ic: "info", note: true,
    text: mine.length
      ? "赤い点線は e-Stat 国勢調査の境界（2020年）を表示用に簡略化したものです。行政区域そのものではありません。"
      : "赤い点線はメッシュ（約1km）単位の範囲で、実際の境界線ではありません。",
  }));
  if (sharesSplit) rows.push(row({ ic: "info", note: true, text: "旧浜松市北区の範囲は、中央区と浜名区のどちらにも含めて表示しています（2020年の境界データに現在の区界が無いため）。" }));
  openPanel(
    header({ title: local, sub: pref, chip: { kind: "muni", label: "市区町村" }, back: true })
    + actions([
      { act: "fit", icon: "fit", label: "全体を表示" },
      { act: "copy-code", icon: "copy", label: "コードをコピー" },
      { act: "share", icon: "share", label: "共有" },
    ])
    + `<div class="rows">${rows.join("")}</div>`
    + jpacSections(m.lg_code, null)
    + footer());
  S.bounds = bounds;
  $("q").value = m.name + (rest || "");
  if (fit) fitMunicipality();
  updateHash();
}

function fitMunicipality() {
  if (!S.bounds) return;
  map.fitBounds(S.bounds, { ...framePadding(), maxZoom: 14 });
}

// --- search results / nothing found / about
function openResults(q, list) {
  S.view = { type: "results" };
  S.back = null;
  clearOutline();
  setOutlined(-1);
  map.removeLayer(pin);
  map.removeLayer(cellRect);
  const items = list.slice(0, 50).map((i) => {
    const { pref, local } = splitName(S.munis[i].name);
    return `<button class="res" data-act="pick" data-i="${i}"><span class="res-ic">${icon("city")}</span>
      <span><div class="res-title">${esc(local)}</div><div class="res-sub">${esc(pref)}・市区町村・${S.munis[i].lg_code}</div></span></button>`;
  }).join("");
  openPanel(`<div class="res-head">「${esc(q)}」の検索結果 ${list.length} 件${list.length > 50 ? "（先頭 50 件）" : ""}</div>${items}`);
  S.lastResults = { q, list };
}

function openNotFound(q, why = "") {
  S.view = { type: "results" };
  S.back = null;
  openPanel(header({ title: `「${q}」は見つかりませんでした` })
    + `<div class="empty"><p>${esc(why || "一致する市区町村がありません。")}</p>
      <p class="hint">市区町村名（例：浜松市中央区）、住所（例：東京都千代田区丸の内1丁目）、
      緯度経度（例：35.6812, 139.7671）、メッシュコード（3次〜6次、例：53394611 / 53394611432）で検索できます。</p></div>`);
}

function openAbout() {
  const prev = S.view;
  const restore = prev ? () => restoreView(prev) : null;
  S.view = { type: "about" };
  S.back = restore;
  const node = $("aboutTpl").content.cloneNode(true);
  node.querySelector('[data-slot="caveats"]').innerHTML = S.meta.caveats.map((c) => `<li>${esc(c)}</li>`).join("");
  node.querySelector('[data-slot="attribution"]').textContent = S.meta.attribution;
  node.querySelector('[data-slot="generated"]').textContent = S.meta.generated;
  // 駅・郵便番号・市外局番を出しているなら、その出典も併せて出す（DATA_LICENSE.md）。
  if (S.jpac) {
    for (const el of node.querySelectorAll('[data-slot="jpac-attribution"], [data-slot="jpac-sources"]')) {
      el.hidden = false;
    }
  }
  const wrap = document.createElement("div");
  wrap.appendChild(node);
  openPanel(header({ title: "このマップについて", back: true }) + wrap.innerHTML);
}

function restoreView(v) {
  if (v.type === "pin") openPoint(v.lat, v.lng);
  else if (v.type === "m") openMunicipality(v.idx, { fit: false });
  else if (v.type === "results" && S.lastResults) openResults(S.lastResults.q, S.lastResults.list);
  else if (v.type === "station" && S.jpac) {
    const st = S.jpac.stations.find((s) => s.id === v.id);
    if (st) openStation(st);
  }
}

$("panelBody").addEventListener("click", (e) => {
  const el = e.target.closest("[data-act], [data-copy]");
  if (!el || el.disabled) return;
  if (el.dataset.copy !== undefined && !el.dataset.act) { copyText(el.dataset.copy); return; }
  const i = el.dataset.i !== undefined ? +el.dataset.i : null;
  switch (el.dataset.act) {
    case "back": if (S.back) S.back(); break;
    case "muni": {
      // restoreView handles every view type; a station panel has no lat/lng.
      const v = S.view;
      openMunicipality(i, { back: () => restoreView(v) });
      break;
    }
    case "muni-first": {
      const v = S.view;
      openMunicipality(S.pointIds[0], { back: () => openPoint(v.lat, v.lng) });
      break;
    }
    case "pick": {
      const r = S.lastResults;
      openMunicipality(i, { back: () => openResults(r.q, r.list) });
      break;
    }
    case "station": {
      const st = S.jpac && S.jpac.stations.find((s) => s.id === el.dataset.i);
      if (st) openStation(st);
      break;
    }
    case "fit-station":
      if (S.station) map.setView([S.station.y, S.station.x], Math.max(map.getZoom(), 16));
      break;
    case "copy-station": if (S.station) copyText(S.station.n); break;
    case "copy-coords": copyText(`${fix(S.view.lat)}, ${fix(S.view.lng)}`); break;
    case "copy-code": copyText(S.munis[S.view.idx].lg_code); break;
    case "fit": fitMunicipality(); break;
    case "share": updateHash(); copyText(location.href, "リンクをコピーしました"); break;
    case "about": openAbout(); break;
    default: break;
  }
});
$("collapse").addEventListener("click", toggleCollapse);
$("sheetHandle").addEventListener("click", toggleCollapse);

// ------------------------------------------------------------------ card
function showCard(p) {
  $("card").innerHTML = `<button class="card-main" type="button">
      <div class="card-title">${esc(shortLine(p))}</div>
      <div class="card-sub">${esc(addressLine(p))}</div>
      <div class="card-coords">${fix(p.lat, 5)}, ${fix(p.lng, 5)}</div>
    </button>
    <button class="sb-btn card-close" type="button" aria-label="閉じる">${icon("close")}</button>`;
  $("card").hidden = false;
  document.body.classList.add("card-open");
  S.card = p;
}
function hideCard() {
  $("card").hidden = true;
  document.body.classList.remove("card-open");
  map.removeLayer(dropPin);
  S.card = null;
}
$("card").addEventListener("click", (e) => {
  if (e.target.closest(".card-close")) { hideCard(); return; }
  if (e.target.closest(".card-main") && S.card) openPoint(S.card.lat, S.card.lng);
});

async function onMapClick(latlng) {
  hideMenus();
  if (!S.table) return;
  const token = ++S.seq;
  dropPin.setLatLng(latlng).addTo(map);
  const p = await decide(latlng.lat, latlng.lng);
  if (token !== S.seq) return;
  showCard(p);
}

// A double-click zooms; only a single click drops a pin.
let clickTimer = null;
map.on("click", (e) => {
  if (clickTimer) return;
  clickTimer = setTimeout(() => { clickTimer = null; onMapClick(e.latlng); }, 250);
});
map.on("dblclick", () => { clearTimeout(clickTimer); clickTimer = null; });

// ------------------------------------------------------------ context menu
function showMenu(pt, latlng) {
  const m = $("ctxmenu");
  const coords = `${fix(latlng.lat, 5)}, ${fix(latlng.lng, 5)}`;
  m.innerHTML = `<button class="coords" data-m="copy">${coords}</button><hr>
    <button data-m="here">この場所について</button>
    <button data-m="zoom">ここを拡大</button>
    <button data-m="mesh">メッシュの色分けを${S.overlay ? "オフ" : "オン"}</button>`;
  m.hidden = false;
  const w = m.offsetWidth, h = m.offsetHeight;
  m.style.left = `${Math.min(pt.x, innerWidth - w - 8)}px`;
  m.style.top = `${Math.min(pt.y, innerHeight - h - 8)}px`;
  m.onclick = (e) => {
    const b = e.target.closest("[data-m]");
    if (!b) return;
    hideMenus();
    if (b.dataset.m === "copy") copyText(`${fix(latlng.lat)}, ${fix(latlng.lng)}`);
    if (b.dataset.m === "here") openPoint(latlng.lat, latlng.lng);
    if (b.dataset.m === "zoom") map.setView(latlng, Math.min(map.getZoom() + 2, 18));
    if (b.dataset.m === "mesh") setOverlay(!S.overlay);
  };
}
map.on("contextmenu", (e) => { hideSuggest(); showMenu(e.containerPoint, e.latlng); });
function hideMenus() { $("ctxmenu").hidden = true; hideSuggest(); }
map.on("movestart", () => { $("ctxmenu").hidden = true; });

// ---------------------------------------------------------------- search
const norm = (s) => s.normalize("NFKC").replace(/\s+/g, "").replace(/ヶ/g, "ケ").replace(/ヵ/g, "カ");

function parseCoords(q) {
  const m = q.match(/^(-?\d{1,3}(?:\.\d+)?)\s*[,，、\s]\s*(-?\d{1,3}(?:\.\d+)?)$/);
  if (!m) return null;
  let a = +m[1], b = +m[2];
  if (Math.abs(a) > 90 && Math.abs(b) <= 90) [a, b] = [b, a];   // "lng, lat" too
  if (Math.abs(a) > 90 || Math.abs(b) > 180) return null;
  return [a, b];
}
// A 3次〜6次 mesh code -> its centre and a zoom that shows it.
const MESH_ZOOM = { 3: 14, 4: 15, 5: 16, 6: 17 };
const MESH_NAME = { 3: "3次メッシュ（約1km）", 4: "4次メッシュ（約500m）", 5: "5次メッシュ（約250m）", 6: "6次メッシュ（約125m）" };
function parseMesh(q) {
  const m = parseMeshCode(q);
  if (!m) return null;
  return { lat: (m.r6 + m.span / 2) / 960, lng: (m.c6 + m.span / 2) / 640, zoom: MESH_ZOOM[m.level], level: m.level };
}

// An address: the longest municipality name the query starts with, with or
// without its prefecture. Ties (府中市 in Tokyo and in Hiroshima) are all kept.
function addressMatches(q) {
  const nq = norm(q);
  let best = [], len = 0;
  S.munis.forEach((m, i) => {
    const full = norm(m.name);
    for (const cand of [full, splitName(full).local]) {
      if (!cand || !nq.startsWith(cand)) continue;
      if (cand.length > len) { best = [i]; len = cand.length; }
      else if (cand.length === len && !best.includes(i)) best.push(i);
    }
  });
  return { list: best, rest: len ? q.normalize("NFKC").replace(/\s+/g, "").slice(len) : "" };
}

function nameMatches(q) {
  const nq = norm(q);
  if (!nq) return [];
  const starts = [], within = [];
  S.munis.forEach((m, i) => {
    const full = norm(m.name), local = splitName(full).local;
    if (local.startsWith(nq) || m.lg_code.startsWith(nq)) starts.push(i);
    else if (full.includes(nq)) within.push(i);
  });
  return starts.concat(within);
}

function runSearch(raw) {
  const q = raw.normalize("NFKC").trim();
  hideSuggest();
  if (!q || !S.table) return;
  $("q").blur();
  const c = parseCoords(q);
  if (c) { openPoint(c[0], c[1], { zoom: 15, fromSearch: true }); return; }
  if (/^\d{8,11}$/.test(q)) {
    const m = parseMesh(q);
    if (!m) { openNotFound(q, "メッシュコードとして正しくありません（3次の5・6桁目は0〜7、4次以降の各桁は1〜4）。"); return; }
    openPoint(m.lat, m.lng, { zoom: m.zoom, fromSearch: true });
    return;
  }
  const am = addressMatches(q);
  if (am.list.length === 1) { openMunicipality(am.list[0], { rest: am.rest }); return; }
  if (am.list.length > 1) { openResults(q, am.list); return; }
  const list = nameMatches(q);
  if (list.length === 1) openMunicipality(list[0]);
  else if (list.length) openResults(q, list);
  else openNotFound(q);
}

// --- autocomplete
const SG = { items: [], active: -1 };
function bold(text, q) {
  const k = text.indexOf(q);
  return k < 0 ? esc(text) : `${esc(text.slice(0, k))}<b>${esc(q)}</b>${esc(text.slice(k + q.length))}`;
}
function buildSuggestions(raw) {
  const q = raw.normalize("NFKC").trim();
  const out = [];
  if (!q || !S.table) return out;
  const c = parseCoords(q);
  if (c) out.push({ ic: "my_location", main: esc(`${c[0]}, ${c[1]}`), sub: "緯度・経度の地点を表示", run: () => openPoint(c[0], c[1], { zoom: 15, fromSearch: true }) });
  const m = /^\d{8,11}$/.test(q) ? parseMesh(q) : null;
  if (m) out.push({ ic: "grid_on", main: esc(q), sub: MESH_NAME[m.level], run: () => openPoint(m.lat, m.lng, { zoom: m.zoom, fromSearch: true }) });
  const am = addressMatches(q);
  if (am.rest) {
    for (const i of am.list.slice(0, 3)) {
      out.push({ ic: "place", main: `${esc(S.munis[i].name)}<span style="color:#80868b">${esc(am.rest)}</span>`, sub: "住所 → 市区町村まで表示", run: () => openMunicipality(i, { rest: am.rest }) });
    }
  }
  const nq = q.replace(/\s+/g, "");
  for (const i of nameMatches(q).slice(0, 8 - out.length)) {
    if (am.rest && am.list.includes(i)) continue;
    const { pref, local } = splitName(S.munis[i].name);
    out.push({ ic: "place", main: bold(local, nq), sub: `${pref}・${S.munis[i].lg_code}`, run: () => { $("q").value = S.munis[i].name; openMunicipality(i); } });
  }
  return out;
}
function renderSuggest() {
  const ul = $("suggest");
  if (!SG.items.length) { hideSuggest(); return; }
  ul.innerHTML = SG.items.map((s, n) =>
    `<li role="option" data-n="${n}" class="${n === SG.active ? "active" : ""}" aria-selected="${n === SG.active}">
      ${icon(s.ic)}<div class="sg-text"><div class="sg-main">${s.main}</div><div class="sg-sub">${esc(s.sub)}</div></div></li>`).join("");
  ul.hidden = false;
  $("searchbox").classList.add("open");
  $("q").setAttribute("aria-expanded", "true");
}
function hideSuggest() {
  $("suggest").hidden = true;
  $("searchbox").classList.remove("open");
  $("q").setAttribute("aria-expanded", "false");
  SG.active = -1;
}
$("q").addEventListener("input", () => {
  $("clearBtn").hidden = !$("q").value && $("panel").hidden;
  SG.items = buildSuggestions($("q").value);
  SG.active = -1;
  renderSuggest();
});
$("q").addEventListener("focus", () => { if ($("q").value) { SG.items = buildSuggestions($("q").value); renderSuggest(); } });
$("q").addEventListener("keydown", (e) => {
  if (e.key === "ArrowDown" || e.key === "ArrowUp") {
    if (!SG.items.length) return;
    e.preventDefault();
    const n = SG.items.length;
    SG.active = e.key === "ArrowDown" ? (SG.active + 1) % n : (SG.active - 1 + n) % n;
    renderSuggest();
  } else if (e.key === "Enter") {
    e.preventDefault();
    if (SG.active >= 0) { const s = SG.items[SG.active]; hideSuggest(); $("q").blur(); s.run(); }
    else runSearch($("q").value);
  } else if (e.key === "Escape") {
    hideSuggest();
  }
});
$("suggest").addEventListener("mousedown", (e) => {
  const li = e.target.closest("li[data-n]");
  if (!li) return;
  e.preventDefault();
  const s = SG.items[+li.dataset.n];
  hideSuggest();
  $("q").blur();
  s.run();
});
$("searchBtn").addEventListener("click", () => runSearch($("q").value));
$("clearBtn").addEventListener("click", () => {
  $("q").value = "";
  hideSuggest();
  closePanel();
  $("clearBtn").hidden = true;
});
document.addEventListener("click", (e) => {
  if (!e.target.closest("#searchbox")) hideSuggest();
  if (!e.target.closest("#ctxmenu")) $("ctxmenu").hidden = true;
});
document.addEventListener("keydown", (e) => {
  if (e.key !== "Escape") return;
  $("ctxmenu").hidden = true;
  if (!$("drawer").hidden) { closeDrawer(); return; }
  if (!$("card").hidden) hideCard();
});

// -------------------------------------------------------------- controls
$("zin").addEventListener("click", () => map.zoomIn());
$("zout").addEventListener("click", () => map.zoomOut());
$("locateBtn").addEventListener("click", () => {
  if (!navigator.geolocation) { toast("このブラウザは位置情報に対応していません"); return; }
  $("locateBtn").classList.add("active");
  navigator.geolocation.getCurrentPosition(
    (pos) => {
      const ll = [pos.coords.latitude, pos.coords.longitude];
      me.setLatLng(ll).addTo(map);
      meAccuracy.setLatLng(ll).setRadius(pos.coords.accuracy || 1).addTo(map);
      openPoint(ll[0], ll[1], { zoom: 15 });
    },
    (err) => { $("locateBtn").classList.remove("active"); toast(`現在地を取得できませんでした（${err.message}）`); },
    { enableHighAccuracy: true, timeout: 10000 });
});

function setOverlay(on) {
  S.overlay = on;
  if (on) mesh.addTo(map); else map.removeLayer(mesh);
  $("layersBtn").classList.toggle("on", on);
  $("layersBtn").setAttribute("aria-pressed", String(on));
  $("drMeshSwitch").classList.toggle("on", on);
  $("legend").hidden = !on;
  updateLegendHint();
}
$("layersBtn").addEventListener("click", () => setOverlay(!S.overlay));
function updateLegendHint() {
  const z = map.getZoom(), el = $("lgHint");
  if (z < OVERLAY_MIN_ZOOM) { el.textContent = "拡大すると色分けを表示"; el.hidden = false; }
  else if (z < SIXTH_MIN_ZOOM && S.units) { el.textContent = "3次（約1km）・拡大で6次（約125m）"; el.hidden = false; }
  else if (S.units) { el.textContent = "6次メッシュ（約125m）"; el.hidden = false; }
  else el.hidden = true;
}
map.on("zoomend", updateLegendHint);

// The layers thumbnail: a few cells in the overlay's own colours.
(function drawThumb() {
  const cv = $("layersBtn").querySelector("canvas");
  const ctx = cv.getContext("2d");
  ctx.fillStyle = "#e9e5dc";
  ctx.fillRect(0, 0, cv.width, cv.height);
  const n = 6, s = cv.width / n;
  const plan = [0, 0, 0, 1, 1, 1, 0, 0, 9, 1, 1, 1, 0, 9, 2, 2, 1, 1, 9, 2, 2, 2, 9, 1, 2, 2, 2, 2, 3, 3, 2, 2, 2, 3, 3, 3];
  plan.forEach((k, j) => {
    ctx.fillStyle = k === 9 ? "rgba(40,40,40,.45)" : `hsla(${hue(k * 7 + 3)},70%,48%,.55)`;
    ctx.fillRect((j % n) * s, Math.floor(j / n) * s, s, s);
  });
  ctx.strokeStyle = "rgba(255,255,255,.6)";
  for (let k = 1; k < n; k++) {
    ctx.beginPath(); ctx.moveTo(k * s, 0); ctx.lineTo(k * s, cv.height); ctx.stroke();
    ctx.beginPath(); ctx.moveTo(0, k * s); ctx.lineTo(cv.width, k * s); ctx.stroke();
  }
})();

// ----------------------------------------------------------------- drawer
function openDrawer() { $("drawer").hidden = false; $("scrim").hidden = false; }
function closeDrawer() { $("drawer").hidden = true; $("scrim").hidden = true; }
$("menuBtn").addEventListener("click", openDrawer);
$("drawerClose").addEventListener("click", closeDrawer);
$("scrim").addEventListener("click", closeDrawer);
$("drMesh").addEventListener("click", () => setOverlay(!S.overlay));
$("drAbout").addEventListener("click", () => { closeDrawer(); openAbout(); });

// ----------------------------------------------------------- copy, toast
let toastTimer = null;
function toast(msg) {
  const el = $("snackbar");
  el.textContent = msg;
  el.hidden = false;
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => { el.hidden = true; }, 3000);
}
function copyText(text, msg = "クリップボードにコピーしました") {
  const fallback = () => {
    const ta = document.createElement("textarea");
    ta.value = text;
    ta.style.position = "fixed";
    ta.style.opacity = "0";
    document.body.appendChild(ta);
    ta.select();
    try { document.execCommand("copy"); toast(msg); } catch { toast("コピーできませんでした"); }
    ta.remove();
  };
  if (navigator.clipboard && window.isSecureContext) navigator.clipboard.writeText(text).then(() => toast(msg), fallback);
  else fallback();
}

// ------------------------------------------------------------------- url
// #@lat,lng,zoomz[&pin=lat,lng | &m=lg_code] — the view, and what is open.
function updateHash() {
  const c = map.getCenter();
  let h = `@${c.lat.toFixed(6)},${c.lng.toFixed(6)},${map.getZoom()}z`;
  if (S.view && S.view.type === "pin") h += `&pin=${fix(S.view.lat)},${fix(S.view.lng)}`;
  else if (S.view && S.view.type === "m") h += `&m=${S.munis[S.view.idx].lg_code}`;
  else if (S.view && S.view.type === "station") h += `&st=${encodeURIComponent(S.view.id)}`;
  history.replaceState(null, "", `#${h}`);
}
map.on("moveend", () => { if (S.table) updateHash(); });

function fromHash() {
  const h = decodeURIComponent(location.hash.slice(1));
  if (!h) return;
  const view = h.match(/@(-?\d+(?:\.\d+)?),(-?\d+(?:\.\d+)?),(\d+(?:\.\d+)?)z/);
  if (view) map.setView([+view[1], +view[2]], +view[3], { animate: false });
  const pinM = h.match(/(?:^|&)pin=(-?\d+(?:\.\d+)?),(-?\d+(?:\.\d+)?)/);
  const legacy = h.match(/^(-?\d+(?:\.\d+)?),(-?\d+(?:\.\d+)?)$/);
  const mM = h.match(/(?:^|&)m=(\d{6})/);
  const stM = h.match(/(?:^|&)st=([^&]+)/);
  if (pinM || legacy) {
    const [, a, b] = pinM || legacy;
    openPoint(+a, +b, { zoom: view ? null : 15 });
  } else if (stM && S.jpac) {
    const st = S.jpac.stations.find((s) => s.id === stM[1]);
    if (st) openStation(st);
  } else if (mM) {
    const i = S.munis.findIndex((x) => x.lg_code === mM[1]);
    if (i >= 0) openMunicipality(i, { fit: !view });
  }
}

// ------------------------------------------------------------------ boot
$("noticeClose").addEventListener("click", () => { $("notice").hidden = true; });
try {
  load();
  if (FROM_FILE) $("notice").hidden = false;
  if (S.units) boundary.addTo(map);
  setOverlay(true);           // the mesh is drawn over the map from the start
  fromHash();
} catch (err) {
  $("notice").hidden = false;
  $("notice").querySelector("div").textContent = `データを読み込めませんでした：${err.message}`;
}

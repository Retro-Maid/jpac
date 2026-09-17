"use strict";
/*
 * バス停の点の読み込み。DOM を触るのは load() だけ。
 *
 * 形式は tools/build_site_data.py が書く: 2次メッシュごとに1ファイルで、中身は
 * [[名称, [事業者, ...], 経度, 緯度], ...] の配列。境界チャンク（geo.js）と違って
 * varint も base64 も使っていない —— あちらは座標が数十万点あって差分圧縮が効くが、
 * こちらは1チャンク中央値 2.1 KB で、素のままのほうが読む側も速く、何より
 * ファイルを開いた人が中身を読める。
 *
 * 事業者が配列なのは、同じ地点・同じ名称に複数の事業者が立っているため（実測で
 * 60,472 行、最大12件が1点）。表示のためにまとめてあるだけで、事業者は捨てていない。
 *
 * geo.js と同じく、チャンクは fetch ではなく <script> で読む: file:// で開いた
 * ページは script は読めても fetch はできない。
 *
 * 27万件を全部持たせない代わりに「見えている範囲だけ」読む。間引きはしない ——
 * 欠けた集合は「ここにあるバス停」という問いに、完全に見えるまま誤答する
 * （docs/POLICY.md §3.2）。
 */
(function (root) {
  const store = new Map();     // key -> [[name, operator, x, y], ...]（null = 無い）
  const waiters = new Map();   // key -> [resolve]
  let known = null;            // 存在する鍵の集合。null = まだ知らされていない

  function settle(key, value) {
    store.set(key, value);
    for (const f of waiters.get(key) || []) f(value);
    waiters.delete(key);
  }

  // チャンク側が呼ぶ。
  function put(key, stops) {
    settle(key, stops);
  }

  function meshes(keys) {
    known = new Set(keys || []);
  }

  // 鍵の存在を知らないうちは「無い」と答える。存在しないチャンクを要求して
  // 404 を並べるより、出ないほうがましである。
  function exists(key) {
    return !!(known && known.has(key));
  }

  // 緯度経度 → 2次メッシュ。build_site_data.py と同じ式であること。
  function meshOf(lat, lng) {
    const p = Math.floor(lat * 1.5), u = Math.floor(lng) - 100;
    const q = Math.floor((lat * 1.5 - p) * 8), v = Math.floor((lng - Math.floor(lng)) * 8);
    return String(p).padStart(2, "0") + String(u).padStart(2, "0") + q + v;
  }

  // 表示範囲にかかる2次メッシュの鍵。1次 = 緯度 2/3°・経度 1°、2次はその 1/8。
  function meshesIn(south, west, north, east) {
    const out = [];
    const latStep = 1 / 12, lngStep = 1 / 8;
    const y0 = Math.floor(south * 12) / 12, x0 = Math.floor(west * 8) / 8;
    for (let y = y0; y <= north; y += latStep) {
      for (let x = x0; x <= east; x += lngStep) {
        const key = meshOf(y + latStep / 2, x + lngStep / 2);
        if (out.indexOf(key) < 0) out.push(key);
      }
    }
    return out;
  }

  // 解決値はチャンクの配列、またはそのメッシュにチャンクが無ければ null。
  function load(key, base = "data/bus/") {
    if (store.has(key)) return Promise.resolve(store.get(key));
    if (!exists(key)) return Promise.resolve(null);
    return new Promise((resolve) => {
      if (waiters.has(key)) { waiters.get(key).push(resolve); return; }
      waiters.set(key, [resolve]);
      const s = document.createElement("script");
      s.src = `${base}${key}.js`;
      s.async = true;
      s.onerror = () => settle(key, null);
      document.head.appendChild(s);
    });
  }

  const cached = (key) => store.get(key);

  root.JPAC_BUS = { put, meshes, exists, meshOf, meshesIn, load, cached };
})(typeof globalThis !== "undefined" ? globalThis : this);

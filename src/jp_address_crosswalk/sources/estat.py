"""e-Stat 統計GIS 小地域（町丁・字等）境界データ adapter.

What this source is for. It is the only redistributable public dataset that
carries a JIS municipality code on a polygon, which makes it the bridge between
geometry-only sources (N02 stations) and jpac's existing keys: a point lands in
a 小地域 polygon, the polygon's ``PREF``+``CITY`` is a ``jis_city_code``, and from
there every jpac table is reachable by key. See ``docs/STATION_JOIN_PLAN.md``.

Two things this adapter deliberately does not do.

* **It does not read geometry.** The .shp is left alone; only the .dbf is
  parsed. Attribution to a municipality is what this stage produces, and the
  polygons are a tool for the later join, not a shipped artifact.
* **It does not ingest ``X_CODE`` / ``Y_CODE`` / ``AREA`` / ``PERIMETER``.** The
  definition sheet states 「属性情報については、JGD2000の値」 and measurement
  confirmed it holds for the JGD2011 product too — 99.03% of those coordinates
  are byte-identical to the JGD2000 edition while 0% of polygon vertices are
  (``docs/STATION_JOIN_PREFLIGHT.md`` §5.5.1). Shipping a JGD2000 coordinate
  beside JGD2011 geometry is exactly the silent datum mix this project treats as
  a defect. ``AREA``/``PERIMETER`` are excluded because the publisher states they
  do not agree with official 国土地理院 figures.

The DBF itself is read by ``payload.read_dbf_member``; the reasoning for parsing
that format rather than depending on a library is recorded there.
"""

from __future__ import annotations

import polars as pl

from ..errors import SourceFetchFailed
from ..logging_setup import get_logger, stage_context
from ..payload import FetchResult, read_dbf_member
from ..snapshot import SchemaInfo, SourceSnapshot, make_snapshot_id, utcnow
from .base import BaseSource

log = get_logger(__name__)

# Kept, and why. The three 記号 columns and HCODE are not decoration: they are how
# the publisher marks the cases that would otherwise silently corrupt a spatial
# join — a 町字 split across several polygons, a hole cut by another
# municipality's exclave, and a water-surface area that is not land at all.
COLUMNS = [
    "KEY_CODE",      # PREF+KEYCODE2 — the 小地域 identifier
    "PREF",          # 2-digit 都道府県コード
    "CITY",          # 3-digit 市区町村コード; PREF+CITY is the JIS 5-digit code
    "S_AREA",        # 町丁・字等番号
    "PREF_NAME",
    "CITY_NAME",
    "S_NAME",
    "HCODE",         # 8101 町丁・字等 / 8154 水面調査区
    "KIGO_E",        # 重複境域フラグ: one 町字 cut into several polygons
    "AREA_MAX_F",    # 'M' on the largest of those
    "KIGO_D",        # 'D' 飛び地 / 'D1' 抜け地 (a hole belonging elsewhere)
    "N_KEN",         # 抜け地の元都道府県
    "N_CITY",        # 抜け地の元市区町村
    "KIGO_I",        # 'I' 島
]

RENAME = {
    "KEY_CODE": "key_code",
    "PREF": "pref_code",
    "CITY": "city_code",
    "S_AREA": "s_area",
    "PREF_NAME": "pref_name_raw",
    "CITY_NAME": "city_name_raw",
    "S_NAME": "s_name_raw",
    "HCODE": "hcode",
    "KIGO_E": "kigo_e",
    "AREA_MAX_F": "area_max_f",
    "KIGO_D": "kigo_d",
    "N_KEN": "n_ken",
    "N_CITY": "n_city",
    "KIGO_I": "kigo_i",
}

HCODE_TOWN = "8101"
HCODE_WATER = "8154"

PREFECTURES = [f"{i:02d}" for i in range(1, 48)]
ENCODING = "cp932"




class EstatBoundarySource(BaseSource):
    name = "estat_boundary"
    provider = "総務省統計局"
    required = False

    def inspect(self, fetched: dict[str, FetchResult]) -> dict[str, SchemaInfo]:
        info: dict[str, SchemaInfo] = {}
        with stage_context(self.name, "inspect"):
            for key, fr in fetched.items():
                names, rows = read_dbf_member(fr.path, encoding=ENCODING)
                info[key] = SchemaInfo(
                    columns=names, column_count=len(names), encoding=ENCODING,
                    delimiter="", has_header=True, sheet_names=[], container="zip",
                    members=[], row_count=len(rows),
                )
        return info

    def parse(self, fetched: dict[str, FetchResult]) -> dict[str, pl.DataFrame]:
        frames: list[pl.DataFrame] = []
        with stage_context(self.name, "parse"):
            for key in sorted(fetched):
                names, rows = read_dbf_member(fetched[key].path, encoding=ENCODING)
                missing = [c for c in COLUMNS if c not in names]
                if missing:
                    raise SourceFetchFailed(
                        "boundary DBF is missing expected columns",
                        key=key, missing=missing, observed=names,
                    )
                index = {c: names.index(c) for c in COLUMNS}
                frames.append(
                    pl.DataFrame(
                        [{c: r[index[c]] for c in COLUMNS} for r in rows],
                        schema={c: pl.Utf8 for c in COLUMNS},
                    )
                )
        if not frames:
            raise SourceFetchFailed("no e-Stat boundary rows parsed")
        df = pl.concat(frames).rename(RENAME).with_columns(
            # The join key jpac already speaks. Built here rather than at use
            # time so nothing downstream has to remember that it is two columns.
            (pl.col("pref_code") + pl.col("city_code")).alias("jis_city_code")
        ).sort(["jis_city_code", "key_code"])
        town = df.filter(pl.col("hcode") == HCODE_TOWN)
        log.info(
            "parsed e-Stat small-area boundaries",
            rows=df.height,
            town=town.height,
            water=df.filter(pl.col("hcode") == HCODE_WATER).height,
            municipalities=town["jis_city_code"].n_unique(),
            exclave=df.filter(pl.col("kigo_d") == "D").height,
            enclave=df.filter(pl.col("kigo_d") == "D1").height,
            island=df.filter(pl.col("kigo_i") == "I").height,
            duplicated_boundary=df.filter(pl.col("kigo_e") != "").height,
        )
        return {"estat_small_area": df}

    def build_snapshots(self, discovery, fetched, schemas, row_counts):
        snaps = []
        for res in discovery.resources:
            fr = fetched[res.key]
            snaps.append(
                SourceSnapshot(
                    source_snapshot_id=make_snapshot_id(res.dataset_name, fr.sha256),
                    provider=self.provider, dataset_name=res.dataset_name,
                    source_page_url=discovery.source_page_url, download_url=res.url,
                    license_name=discovery.license_name, license_url=discovery.license_url,
                    license_text_sha256=discovery.license_text_sha256,
                    source_version=res.version, published_at=res.published_at,
                    edition_origin=res.edition_origin,
                    downloaded_at=utcnow(), etag=fr.etag, last_modified=fr.last_modified,
                    sha256=fr.sha256, file_size=fr.size,
                    row_count=row_counts.get(res.key),
                    schema_fingerprint=schemas[res.key].fingerprint(),
                    resolved_via=res.resolved_via,
                )
            )
        self._snapshots = snaps
        return snaps

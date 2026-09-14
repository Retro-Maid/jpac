"""Reconcile e-Stat 小地域 boundaries against jpac's municipality set.

The product of this stage is not a match rate. It is a **classification in which
every code on both sides lands somewhere named**, so that a difference between
the two datasets is either explained or visibly not explained. A residual of
"unexplained" is the number that matters; a high match rate with unclassified
leftovers is worse than a lower one without them (docs/POLICY.md §4, §5).

Three of the five classes are differences that are *correct*, and treating them
as errors is the easiest mistake here:

* ``designated_city_parent`` — e-Stat carries 政令指定都市 at ward granularity
  because the 省令 says so: 「指定都市における調査区の設定は、当該指定都市の区の
  区域を区分して…行う」(国勢調査の調査区の設定の基準等に関する省令 第二条). The 20
  parent-city codes therefore have no polygon, by law rather than by accident.
* ``not_surveyed`` — the 国勢調査 is not conducted in the northern territories,
  so those six villages have no boundary.
* ``unassigned_area`` — a polygon whose 市区町村名 is blank: the data itself
  declines to assign it.

Only ``jpac_only`` / ``estat_only`` are genuine version skew, and those are
resolved against ``overrides/municipality_lineage.yml`` rather than guessed.
"""

from __future__ import annotations

import polars as pl

from ..logging_setup import get_logger, stage_context

log = get_logger(__name__)

HCODE_TOWN = "8101"

# 国勢調査 is not conducted here, so the absence of a boundary is the correct
# state and not a defect to chase. 北方領土6村 (根室振興局).
NOT_SURVEYED = {"01695", "01696", "01697", "01698", "01699", "01700"}

STATUSES = (
    "matched",
    "designated_city_parent",
    "not_surveyed",
    "unassigned_area",
    "superseded",       # e-Stat side: a code the lineage file carries forward
    "successor",        # jpac side: the code it was carried forward to
    "split_no_single_successor",
    "jpac_only",        # genuinely unexplained
    "estat_only",       # genuinely unexplained
)

# The two statuses that mean "nobody has accounted for this yet". Everything
# else is a difference with a recorded reason, and the count of these is the
# number a reviewer should read first.
UNEXPLAINED = ("jpac_only", "estat_only")


def _designated_city_parents(municipality: pl.DataFrame) -> set[str]:
    """jpac codes that are the parent of a 政令指定都市, derived not assumed.

    Deliberately not "codes ending in 00": measured against the real data, 17
    codes end in 00 of which two (倶知安町 01400, 蘂取村 01700) are ordinary
    municipalities, while 川崎 14130 / 相模原 14150 / 浜松 22130 / 堺 27140 are
    parents that do not. The reliable signal is having ward children.
    """
    current = municipality.filter(pl.col("is_current") == 1)
    wards = current.filter(
        pl.col("ward").is_not_null() & (pl.col("ward") != "")
    )
    parents = wards.select(["pref", "city"]).unique()
    return set(
        current.join(parents, on=["pref", "city"], how="semi")
        .filter(pl.col("ward").is_null() | (pl.col("ward") == ""))["jis_city_code"]
        .to_list()
    )


def reconcile(
    small_area: pl.DataFrame,
    municipality: pl.DataFrame,
    lineage: dict[str, str] | None = None,
    unlisted: dict[str, str] | None = None,
) -> tuple[pl.DataFrame, pl.DataFrame]:
    """Label every small-area row, and every jpac municipality without one.

    Returns ``(small_area_labelled, municipality_reconciliation)``. The second
    frame is what a reader checks: one row per code that is on exactly one side,
    with the reason it is.

    ``lineage`` and ``unlisted`` both come from
    ``overrides/municipality_lineage.yml`` and are read in both directions: the
    e-Stat side needs old→new to say a code was superseded, and the jpac side
    needs new→old to say a code is the successor. Reading it only one way leaves
    the other side reported as unexplained, which is how this function was wrong
    on first run.
    """
    with stage_context("estat", "reconcile"):
        lineage = lineage or {}
        unlisted = unlisted or {}
        # Both files speak 6-digit lg_code; e-Stat speaks the 5-digit JIS code.
        lineage_5 = {k[:5]: v[:5] for k, v in lineage.items()}
        unlisted_5 = {k[:5]: v for k, v in unlisted.items()}
        successors: dict[str, list[str]] = {}
        for old, new in sorted(lineage_5.items()):
            successors.setdefault(new, []).append(old)

        jpac_codes = set(municipality.filter(pl.col("is_current") == 1)["jis_city_code"])
        town = small_area.filter(pl.col("hcode") == HCODE_TOWN)
        estat_codes = set(town["jis_city_code"])
        parents = _designated_city_parents(municipality)
        # A code whose polygons carry no municipality name is the data declining
        # to assign it, not a mismatch to chase.
        unassigned = set(
            small_area.filter(pl.col("city_name_raw") == "")["jis_city_code"]
        )

        labelled = small_area.with_columns(
            pl.when(pl.col("city_name_raw") == "")
            .then(pl.lit("unassigned_area"))
            .when(pl.col("jis_city_code").is_in(list(jpac_codes)))
            .then(pl.lit("matched"))
            .when(pl.col("jis_city_code").is_in(list(lineage_5)))
            .then(pl.lit("superseded"))
            .when(pl.col("jis_city_code").is_in(list(unlisted_5)))
            .then(pl.lit("split_no_single_successor"))
            .otherwise(pl.lit("estat_only"))
            .alias("reconcile_status")
        )

        rows: list[dict] = []
        for code in sorted(jpac_codes - estat_codes):
            if code in parents:
                status, note = "designated_city_parent", (
                    "e-Stat は政令指定都市を区単位で持つ（省令第二条）。親市に境界が"
                    "無いのが正しい"
                )
            elif code in NOT_SURVEYED:
                status, note = "not_surveyed", "国勢調査が実施されていないため境界データが存在しない"
            elif code in successors:
                status, note = "successor", (
                    f"廃置分合の承継先。承継元 {', '.join(successors[code])} "
                    "（overrides/municipality_lineage.yml）"
                )
            else:
                status = "jpac_only"
                note = "e-Stat の断面（2020年国勢調査）に存在しない。版ズレの可能性。要調査"
            rows.append({"jis_city_code": code, "side": "jpac_only", "reconcile_status": status,
                         "note": note, "polygons": 0})

        counts = dict(town.group_by("jis_city_code").len().iter_rows())
        for code in sorted(estat_codes - jpac_codes):
            if code in unassigned:
                status, note = "unassigned_area", (
                    "市区町村名が空。データ上、帰属が確定していない区域"
                )
            elif code in lineage_5:
                status, note = "superseded", (
                    f"廃置分合により {lineage_5[code]} へ承継"
                    "（overrides/municipality_lineage.yml）"
                )
            elif code in unlisted_5:
                status, note = "split_no_single_successor", (
                    f"承継先が一意でないため lineage に載せていない: {unlisted_5[code][:120]}"
                )
            else:
                status = "estat_only"
                note = "jpac の現行市区町村に存在しない。版ズレの可能性。要調査"
            rows.append({"jis_city_code": code, "side": "estat_only",
                         "reconcile_status": status, "note": note,
                         "polygons": counts.get(code, 0)})

        reconciliation = pl.DataFrame(
            rows,
            schema={"jis_city_code": pl.Utf8, "side": pl.Utf8, "reconcile_status": pl.Utf8,
                    "note": pl.Utf8, "polygons": pl.Int64},
        ).sort(["side", "jis_city_code"])

        unexplained = [
            r["jis_city_code"] for r in rows if r["reconcile_status"] in UNEXPLAINED
        ]
        log.info(
            "reconciled e-Stat boundaries against jpac municipalities",
            jpac=len(jpac_codes), estat=len(estat_codes),
            matched=len(jpac_codes & estat_codes),
            jpac_only=len(jpac_codes - estat_codes),
            estat_only=len(estat_codes - jpac_codes),
            unexplained=len(unexplained),
            unexplained_codes=unexplained[:20],
        )
        return labelled, reconciliation

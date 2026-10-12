"""e-Stat 廃置分合等情報 → 市区町村コードの系譜（build/municipality_change.py）.

行は合成したもので、発行元の書式（「名称(5桁コード)」の並び、全角括弧、改行を含む
改正事由）だけを写している。ここで確かめるのは、読めるものだけを辺にし、読めない
ものを推測で埋めないことである。
"""

from __future__ import annotations

from types import SimpleNamespace

import polars as pl
import pytest

from jp_address_crosswalk.build.municipality_change import (
    COLUMNS,
    build_change_tables,
    check_attested_lineage,
    check_digit,
    read_change_csv,
)

COLS = list(COLUMNS.values())


def row(pattern: str, code: str, date: str, reason: str, name: str = "") -> dict:
    r = dict.fromkeys(COLS, "")
    r.update(event_pattern_raw=pattern, area_code_raw=code, effective_date_raw=date,
             reason_raw=reason, municipality_raw=name)
    return r


def frame(rows: list[dict]) -> pl.DataFrame:
    return pl.DataFrame(rows, schema=dict.fromkeys(COLS, pl.Utf8))


def build(rows, created=(), abolished=()):
    """発行元では、新設・廃止に挙がるコードは本体にも自分の行を持つ。それを補う."""
    have = {(r["effective_date_raw"], r["area_code_raw"]) for r in rows}
    filler = [
        row("", f["area_code_raw"], f["effective_date_raw"], "")
        for f in [*created, *abolished]
        if (f["effective_date_raw"], f["area_code_raw"]) not in have
    ]
    return build_change_tables(
        frame([*rows, *filler]), frame(list(created)), frame(list(abolished))
    )


def edges_of(tables) -> set[tuple]:
    e = tables["municipality_lineage_edge"]
    return {
        (r["old_jis_code"], r["new_jis_code"], r["event_type"], r["candidate_count"])
        for r in e.iter_rows(named=True)
    }


class TestCheckDigit:
    @pytest.mark.parametrize("jis5, lg", [
        ("13101", "131016"),  # 千代田区
        ("01100", "011002"),  # 札幌市
        ("22138", "221384"),  # 浜松市中央区
        ("22135", "221350"),  # 浜松市北区。r = 1 で 11 - r = 10、下1桁の 0
    ])
    def test_known_codes(self, jis5, lg):
        assert check_digit(jis5) == lg


class TestParsing:
    def test_absorption_is_one_candidate_each(self):
        r = row("編入合併", "14422", "2006-03-20",
                "津久井町(14422)、相模湖町(14423)が相模原市(14209)に編入")
        gone = [row("編入合併", c, "2006-03-20", "") for c in ("14422", "14423")]
        assert edges_of(build([r], abolished=gone)) == {
            ("14422", "14209", "absorb", 1), ("14423", "14209", "absorb", 1),
        }

    def test_new_merger_with_a_new_code(self):
        r = row("新設合併（コード追加あり）", "40403", "2006-02-11",
                "宮田町(40403)、若宮町(40404)が合併し、宮若市(40226)を新設")
        gone = [row("", c, "2006-02-11", "") for c in ("40403", "40404")]
        assert edges_of(build([r], abolished=gone)) == {
            ("40403", "40226", "merge", 1), ("40404", "40226", "merge", 1),
        }

    def test_reused_code_comes_from_the_created_flag_not_the_name(self):
        reason = "砺波市(16208)、庄川町(16405)が合併し、砺波市を新設"
        r = row("新設合併（コード追加なし）", "16208", "2004-11-01", reason)
        created = [row("", "16208", "2004-11-01", "")]
        gone = [row("", "16405", "2004-11-01", "")]
        assert edges_of(build([r], created=created, abolished=gone)) == {
            ("16208", "16208", "merge_reuse_code", 1),
            ("16405", "16208", "merge_reuse_code", 1),
        }

    def test_reused_code_without_the_flag_is_unresolved(self):
        """同名の旧市があっても、新設フラグが無ければ承継先を決めない."""
        r = row("新設合併（コード追加なし）", "16208", "2004-11-01",
                "砺波市(16208)、庄川町(16405)が合併し、砺波市を新設")
        t = build([r])
        e = t["municipality_lineage_edge"]
        assert e["new_lg_code"].is_null().all()
        assert t["municipality_change_raw"]["parse_status"][0] == "unresolved"

    def test_reused_code_whose_flag_disagrees_with_the_name_is_unresolved(self):
        r = row("新設合併（コード追加なし）", "16208", "2004-11-01",
                "砺波市(16208)、庄川町(16405)が合併し、砺波市を新設")
        created = [row("", "16405", "2004-11-01", "")]   # 名称と食い違うコードに新設フラグ
        e = build([r], created=created)["municipality_lineage_edge"]
        assert e["new_lg_code"].is_null().all()

    def test_designated_city_spreads_the_old_city_over_its_wards(self):
        reason = ("川崎市(14202)の川崎市(14130)への政令指定都市施行\r\n"
                  "川崎区(14131)、幸区(14132)の新設")
        r = row("政令指定都市施行", "14130", "1972-04-01", reason)
        gone = [row("", "14202", "1972-04-01", "")]
        assert edges_of(build([r], abolished=gone)) == {
            ("14202", "14130", "designated_city", 3),
            ("14202", "14131", "designated_city_ward", 3),
            ("14202", "14132", "designated_city_ward", 3),
        }

    def test_ward_reorganisation_with_full_width_text_and_a_split_ward(self):
        """浜松 2024: 全角括弧・全角空白・注記。分割された北区は候補2件."""
        reason = (
            "浜松市（22130）の区の再編（7区から3区へ再編）　"
            "中区（22131）、北区（22135）（三方原地区／初生町、三方原町）を中央区（22138）に再編"
            "　北区（22135）（三方原地区以外）、浜北区（22136）を浜名区（22139）に再編"
        )
        r = row("区の再編", "22130", "2024-01-01", reason)
        gone = [row("", c, "2024-01-01", "") for c in ("22131", "22135", "22136")]
        t = build([r], abolished=gone)
        assert edges_of(t) == {
            ("22131", "22138", "ward_reorg", 1),
            ("22135", "22138", "ward_reorg", 2),
            ("22135", "22139", "ward_reorg", 2),
            ("22136", "22139", "ward_reorg", 1),
        }
        e = t["municipality_lineage_edge"].filter(pl.col("old_jis_code") == "22135")
        assert set(e["area_qualifier_raw"]) == {"三方原地区/初生町、三方原町", "三方原地区以外"}

    def test_separation_keeps_the_old_code_as_a_candidate(self):
        """分離元は存続する。A→B だけにすると候補1件の対応を捏造する."""
        r = row("分離", "14116", "1986-11-03",
                "戸塚区(14110)から分離し、栄区(14115)、泉区(14116)を新設")
        edges = edges_of(build([r]))   # 14110 は廃止されていない
        assert edges == {
            ("14110", "14110", "continues", 3),
            ("14110", "14115", "separation", 3),
            ("14110", "14116", "separation", 3),
        }

    def test_same_code_rename_and_rename_with_reading(self):
        rows = [
            row("名称変更", "29321", "2006-01-01", "都祁村(29321)が都祁町(29321)に名称変更"),
            row("名称変更", "34325", "1982-04-01",
                "佐伯町（さえきちょう、34325）が佐伯町（さいきちょう、34325）に名称変更"),
        ]
        assert edges_of(build(rows)) == {
            ("29321", "29321", "rename", 1), ("34325", "34325", "rename", 1),
        }

    def test_status_change_with_a_name_change(self):
        r = row("市制施行", "13222", "1970-10-01",
                "久留米町(13345)が久留米市に市制施行し、東久留米市(13222)に名称変更")
        gone = [row("", "13345", "1970-10-01", "")]
        assert edges_of(build([r], abolished=gone)) == {
            ("13345", "13222", "status_change", 1),
        }

    def test_identical_rows_are_both_kept(self):
        """発行元には全列が同じ行がある。行のハッシュで1つに潰さない."""
        r = row("名称変更", "29321", "2006-01-01", "都祁村(29321)が都祁町(29321)に名称変更")
        t = build([r, dict(r)])
        raw = t["municipality_change_raw"]
        assert raw.height == 2
        assert raw["row_sha256"].n_unique() == 1
        e = t["municipality_lineage_edge"]
        assert e.height == 1 and e["source_row_ordinals"][0] == "1;2"

    def test_repeated_lines_are_one_claim_and_county_lines_make_no_edge(self):
        line = "夢前町(28422)が姫路市(28201)に編入"
        r = row("郡の廃止", "28420", "2006-03-27", f"{line}\r\n{line}\r\n飾磨郡(28420)の廃止")
        gone = [row("", "28422", "2006-03-27", "")]
        t = build([r], abolished=gone)
        assert edges_of(t) == {("28422", "28201", "absorb", 1)}
        assert t["municipality_change_raw"]["parse_status"][0] == "parsed"

    def test_raw_text_is_kept_byte_for_byte(self):
        reason = "浜松市（22130）の区の再編　天竜区（22137）を天竜区（22140）に再編（区名、区域の変更なし）"
        raw = build([row("区の再編", "22140", "2024-01-01", reason)])["municipality_change_raw"]
        assert raw["reason_raw"][0] == reason

    def test_partial_absorption_by_大字_is_not_guessed(self):
        """上九一色村: 大字ごとに2つの市町へ。機械的には解けないので辺を作らない."""
        r = row("編入合併", "19341", "2006-03-01",
                "上九一色村(19341)大字梯及び古関が甲府市(19201)に編入し、"
                "大字精進、本栖及び富士ヶ嶺が富士河口湖町(19430)に編入")
        t = build([r])
        assert t["municipality_lineage_edge"].height == 0
        raw = t["municipality_change_raw"]
        assert raw["parse_status"][0] == "unresolved"
        assert raw["unresolved_reason"][0]

    def test_county_change_pairs_only_one_to_one(self):
        one = row("郡の区域変更", "46303", "1973-04-01",
                  "大島郡三島村(46521)が鹿児島郡三島村(46303)に郡の区域変更")
        many = row("郡の区域変更", "17461", "2005-03-01",
                   "鳳至郡穴水町(17421)、門前町(17422)が鳳珠郡穴水町(17461)、門前町(17462)に郡の区域変更")
        gone = [row("", "46521", "1973-04-01", "")]
        t = build([one, many], abolished=gone)
        assert edges_of(t) == {("46521", "46303", "county_change", 1)}
        assert t["municipality_change_raw"]["parse_status"].to_list()[:2] == ["parsed", "unresolved"]


class TestAttestedLineageGate:
    EDGES = build(
        [
            row("区の再編", "22138", "2024-01-01",
                "浜松市(22130)の区の再編 中区(22131)、北区(22135)(三方原地区)を中央区(22138)に再編"
                " 北区(22135)(三方原地区以外)を浜名区(22139)に再編"),
        ],
        abolished=[row("", c, "2024-01-01", "") for c in ("22131", "22135")],
    )["municipality_lineage_edge"]

    def test_matching_transition_and_successors_pass(self):
        doc = {
            "transitions": [{"old_lg_code": "221317", "new_lg_code": "221384",
                             "effective_date": "2024-01-01"}],
            "unlisted": [{"lg_code": "221350", "successors": ["221384", "221392"]}],
        }
        assert check_attested_lineage(self.EDGES, doc) == []

    def test_transition_absent_from_the_official_data_fails(self):
        doc = {"transitions": [{"old_lg_code": "221317", "new_lg_code": "221392",
                                "effective_date": "2024-01-01"}]}
        assert check_attested_lineage(self.EDGES, doc)

    def test_transition_on_the_wrong_date_fails(self):
        doc = {"transitions": [{"old_lg_code": "221317", "new_lg_code": "221384",
                                "effective_date": "2023-01-01"}]}
        assert check_attested_lineage(self.EDGES, doc)

    def test_attesting_a_split_as_one_to_one_fails(self):
        doc = {"transitions": [{"old_lg_code": "221350", "new_lg_code": "221384",
                                "effective_date": "2024-01-01"}]}
        problems = check_attested_lineage(self.EDGES, doc)
        assert problems and "2" in problems[0]["problem"]

    def test_successors_that_differ_fail(self):
        doc = {"unlisted": [{"lg_code": "221350", "successors": ["221384"]}]}
        assert check_attested_lineage(self.EDGES, doc)


def test_header_check_rejects_a_changed_csv():
    with pytest.raises(ValueError):
        read_change_csv("事由パターン,標準地域コード\n編入合併,14422\n".encode())


def test_source_refuses_to_guess_without_all_three_files(tmp_path):
    """新設・廃止の2本が無いと承継先と存続が分からない。推測で埋めずに止める."""
    from jp_address_crosswalk.errors import SourceFetchFailed
    from jp_address_crosswalk.sources.estat_municipality_change import (
        EstatMunicipalityChangeSource,
    )

    header = ",".join(f'"{c}"' for c in COLUMNS)
    body = '"編入合併","","","14422","","","","","","2006-03-20","津久井町(14422)が相模原市(14209)に編入","",""'
    text = f"{header}\n{body}\n"
    path = tmp_path / "changes.csv"
    path.write_text(text, encoding="utf-8")
    src = EstatMunicipalityChangeSource({})
    with pytest.raises(SourceFetchFailed):
        src.parse({"changes": SimpleNamespace(path=path)})

    for key in ("changes_created", "changes_abolished"):
        (tmp_path / f"{key}.csv").write_text(text, encoding="utf-8")
    tables = src.parse({
        k: SimpleNamespace(path=tmp_path / f"{k}.csv")
        for k in ("changes", "changes_created", "changes_abolished")
    })
    assert tables["municipality_change_raw"].height == 1
    assert tables["municipality_change_raw"]["hs_flag"][0] == "SH"


def test_filter_files_from_another_day_are_refused():
    """新設・廃止の CSV に本体に無い行があれば、3本の取得時点が揃っていない."""
    r = row("編入合併", "14422", "2006-03-20", "津久井町(14422)が相模原市(14209)に編入")
    stale = [row("", "99999", "2030-01-01", "")]
    with pytest.raises(ValueError, match="本体に無い"):
        build_change_tables(frame([r]), frame([]), frame(stale))


def test_edge_id_is_deterministic_and_unique():
    r = row("分離", "14116", "1986-11-03",
            "戸塚区(14110)から分離し、栄区(14115)、泉区(14116)を新設")
    a = build([r])["municipality_lineage_edge"]
    b = build([r])["municipality_lineage_edge"]
    assert a["edge_id"].to_list() == b["edge_id"].to_list()
    assert a["edge_id"].n_unique() == a.height


class TestPipelineGlue:
    """``pipeline._build_lineage_tables``: 検査結果は非公開の表に置き、配布物に出さない."""

    TABLES = build(
        [row("区の再編", "22138", "2024-01-01",
             "浜松市(22130)の区の再編 中区(22131)を中央区(22138)に再編")],
        abolished=[row("", "22131", "2024-01-01", "")],
    )

    def run(self, tmp_path, yml: str, parsed=True):
        from jp_address_crosswalk import pipeline

        (tmp_path / "municipality_lineage.yml").write_text(yml, encoding="utf-8")
        paths = SimpleNamespace(overrides=tmp_path)
        outcome = SimpleNamespace(
            parsed={"estat_municipality_change": self.TABLES} if parsed else {}
        )
        return pipeline._build_lineage_tables(paths, outcome, lambda *a: "snap_test")

    def test_clean_attestation_passes(self, tmp_path):
        out = self.run(tmp_path, 'transitions:\n  - old_lg_code: "221317"\n'
                                 '    new_lg_code: "221384"\n    effective_date: 2024-01-01\n')
        assert out["_lineage_gate"].height == 0
        assert (out["municipality_lineage_edge"]["source_snapshot_id"] == "snap_test").all()
        public = {k for k in out if not k.startswith("_")}
        assert public == {"municipality_change_raw", "municipality_lineage_edge"}

    def test_contradicting_attestation_fails(self, tmp_path):
        out = self.run(tmp_path, 'transitions:\n  - old_lg_code: "221317"\n'
                                 '    new_lg_code: "221392"\n    effective_date: 2024-01-01\n')
        assert out["_lineage_gate"].height == 1

    def test_absent_payload_produces_nothing(self, tmp_path):
        assert self.run(tmp_path, "transitions: []\n", parsed=False) == {}

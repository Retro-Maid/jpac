"""e-Stat 廃置分合等情報 → 市区町村コードの系譜（docs/MUNICIPALITY_LINEAGE_PREFLIGHT.md）.

発行元の改正事由は「名称(5桁コード)」を並べた定型文で、旧→新の対応はコードで書かれて
いる。ここではそれを**文型ごとの規則で厳密に読み**、読めたものだけを辺にする。

守っていること:

- **名称で結ばない。** 改正事由のコードが読めない節は辺にせず、行を ``unresolved`` に
  する。下読みでは、節の区切りを緩く読んだために 19201→19430 という誤った辺が
  出ていた。
- **承継先を名称で選ばない。** 「新設合併（コード追加なし）」は新設側をコードなしで
  書く（「…が合併し、喜多方市を新設」）。引き継いだコードは、その日に**新設フラグ**が
  付いているものであって、同名の旧市町村ではない。名称は照合に使うだけで、食い違えば
  承継先は NULL にする。
- **旧コードが存続する場合は存続の辺を作る。** 「A区から分離し、B区を新設」から A→B
  だけを作ると、候補1件の一意な対応を捏造することになる。旧コードがその日の**廃止
  フラグ**に無ければ A→A（``continues``）を足し、候補数に数える。
- **並び順で対応させない。** 郡の区域変更（「X郡A町(…)、B町(…)がY郡A町(…)、B町(…)に
  郡の区域変更」）は旧と新を位置で対にしないと読めないので、辺にせず ``unresolved``
  にする。
- **原文は触らない。** NFKC は解析用の作業コピーにだけ掛け、``*_raw`` 列は発行元の
  バイト列のまま（改行・全角括弧を含む）残す。
"""

from __future__ import annotations

import hashlib
import re
import unicodedata

import polars as pl

# e-Stat の CSV の見出し（2026-09-27 取得）と、jpac の列名。
COLUMNS: dict[str, str] = {
    "事由パターン": "event_pattern_raw",
    "支庁・振興局等内郡": "subprefecture_county_raw",
    "支庁・振興局等内郡（ふりがな）": "subprefecture_county_kana_raw",
    "標準地域コード": "area_code_raw",
    "都道府県": "pref_raw",
    "政令市･郡･支庁･振興局等": "parent_raw",
    "政令市･郡･支庁･振興局等（ふりがな）": "parent_kana_raw",
    "市区町村": "municipality_raw",
    "市区町村（ふりがな）": "municipality_kana_raw",
    "廃置分合等施行年月日": "effective_date_raw",
    "改正事由": "reason_raw",
    "告示年月日": "notice_date_raw",
    "告示番号": "notice_number_raw",
}

_NAME = r"[^\s、,()]+?"
# 「佐伯町(さえきちょう、34325)」のように読みを添えた書き方もある。
_CODE = r"\((?:[^()、]+、)?\d{5}\)"          # 他の正規表現に埋め込む版（捕捉しない）
_ITEM = rf"{_NAME}{_CODE}(?:\([^()]*\))?"
_ITEM_RE = re.compile(rf"({_NAME})\((?:[^()、]+、)?(\d{{5}})\)(?:\(([^()]*)\))?")
# 並びの区切りは「、」か「と」（「北区(27101)と大淀区(27112)が統合し」）。閉じ括弧の直後に
# 限るので、注記の中の「、」（「(三方原地区/初生町、三方原町…)」）では切らない。
_SEP = re.compile(r"(?<=\))(?:、|と)")
# 支庁・振興局は市区町村ではない。コードを持つが、系譜の辺にはしない。
_NON_MUNICIPAL = re.compile(r"(支庁|振興局|郡)$")


_RULES: list[tuple[str, re.Pattern[str]]] = [
    ("C_absorb", re.compile(r"^(?P<old>.+)が(?P<new>[^、]+)に編入$")),
    ("C_merge", re.compile(r"^(?P<old>.+)が(?:合併|統合)し、(?P<new>[^、]+)を新設$")),
    ("C_designated", re.compile(
        rf"^(?P<old>{_ITEM})の(?P<new>{_ITEM})への政令指定都市(?:施行|移行)$")),
    ("C_created", re.compile(r"^(?P<new>.+)の新設$")),
    ("C_split", re.compile(r"^(?P<old>[^、]+)を分割し、(?P<new>.+)を新設$")),
    ("C_separate", re.compile(r"^(?P<old>.+)から分離し、(?P<new>.+)を新設$")),
    # 旧側・新側の「、」は読みの中にも現れる（「佐伯町(さえきちょう、34325)」）。1つであることは
    # 文型ではなく、下の _items と _SINGLE で確かめる。
    ("C_status_rename", re.compile(
        r"^(?P<old>.+?)が[^()]+?(?:\([^()]*\))?に(?:市制|町制)施行し、(?P<new>.+)に名称変更$")),
    ("C_rename_status", re.compile(
        r"^(?P<old>.+?)が[^()]+?(?:\([^()]*\))?に名称変更し、(?P<new>.+)に(?:市制|町制)施行$")),
    ("C_rename", re.compile(r"^(?P<old>.+?)が(?P<new>.+)に名称変更$")),
    ("C_status", re.compile(r"^(?P<old>.+?)が(?P<new>.+)に(?:市制|町制)施行$")),
    ("C_reorg_header", re.compile(rf"^{_ITEM}の区の再編(?:\([^()]*\))?$")),
    ("C_reorg", re.compile(
        rf"^(?P<old>.+)を(?P<new>{_ITEM})に再編(?:\((?P<q>[^()]*)\))?$")),
    ("C_county_change", re.compile(r"^(?P<old>.+)が(?P<new>.+)に(?:郡の)?区域変更$")),
    ("C_abolished", re.compile(rf"^{_ITEM}の廃止$")),
    ("C_city_type", re.compile(rf"^{_ITEM}が.*(?:中核市|特例市|指定都市|施行時特例市).*$")),
]

# 旧側がちょうど1つでなければならない文型。
_SINGLE = {"C_designated", "C_split", "C_status_rename", "C_rename_status", "C_rename",
           "C_status"}

EDGE_TYPES = {
    "C_absorb": "absorb",
    "C_merge": "merge",
    "C_designated": "designated_city",
    "C_split": "split",
    "C_separate": "separation",
    "C_status_rename": "status_change",
    "C_rename_status": "status_change",
    "C_county_change": "county_change",
    "C_rename": "rename",
    "C_status": "status_change",
    "C_reorg": "ward_reorg",
}


def check_digit(jis5: str) -> str:
    """5桁の標準地域コードに検査数字を付けて6桁の全国地方公共団体コードにする.

    総務省の仕様: 各桁に 6,5,4,3,2 を掛けた和を 11 で割った余り r について
    11 - r の下1桁（r=0 → 1、r=1 → 0）。
    """
    total = sum(int(d) * w for d, w in zip(jis5, (6, 5, 4, 3, 2), strict=True))
    return jis5 + str((11 - total % 11) % 10)


def _items(text: str) -> list[tuple[str, str, str | None]] | None:
    """「名称(コード)(注記)?」の並びとして**厳密に**読めたときだけ返す."""
    out = []
    for chunk in _SEP.split(text):
        m = _ITEM_RE.fullmatch(chunk)
        if m is None:
            return None
        out.append((m.group(1), m.group(2), m.group(3)))
    return out


def _lines(reason_raw: str) -> list[str]:
    """作業用の節のリスト。NFKC のうえ改行と空白で分割し、同じ節の重複は1つにする.

    区の再編は「浜松市(22130)の区の再編(…) 中区(22131)、…を中央区(22138)に再編 北区…」と
    1行に複数の節を空白（全角空白。NFKC で半角になる）で続ける。名称にも注記にも空白は
    現れないので、空白で切る。空白を含む節が仮にあっても、断片は文型に合わず未解決に
    なるだけで、誤った辺にはならない。

    「郡の廃止」の行は、同じ合併の文を2〜3回繰り返したうえで「…郡の廃止」を続ける
    ことがある。繰り返しは同じ主張なので1つにする（順序は保つ）。
    """
    text = unicodedata.normalize("NFKC", reason_raw or "")
    out: list[str] = []
    for clause in text.split():
        if clause not in out:
            out.append(clause)
    return out


def parse_reason(
    reason_raw: str,
    effective_date: str,
    created: set[tuple[str, str]],
) -> tuple[list[dict], str | None]:
    """1行の改正事由から辺を作る。``(edges, unresolved_reason)``.

    ``created`` はその日に新設フラグが付いた ``(施行日, 5桁コード)`` の集合で、
    「コード追加なし」の承継先の根拠になる。
    """
    edges: list[dict] = []
    designated_old: str | None = None
    reasons: list[str] = []

    for clause in _lines(reason_raw):
        hit = next(((rid, m) for rid, rx in _RULES if (m := rx.match(clause))), None)
        if hit is None:
            reasons.append(f"文型に合わない節: {clause}")
            continue
        rule, m = hit
        gd = m.groupdict()

        if rule in ("C_reorg_header", "C_abolished", "C_city_type"):
            continue
        if rule == "C_county_change":
            # 旧と新が2つ以上並ぶと、位置で対にしないと読めない。並び順で対応させる
            # ことはしない。1対1なら位置は関係しない。
            o, n = _items(gd["old"]), _items(gd["new"])
            if o is None or n is None or len(o) != 1 or len(n) != 1:
                reasons.append(f"郡の区域変更は旧と新を位置でしか対にできない: {clause}")
                continue
            edges.append(_edge(effective_date, o[0][1], n[0][1], "county_change",
                               None, rule))
            continue

        old = _items(gd["old"]) if gd.get("old") is not None else []
        if old is None:
            reasons.append(f"旧側を「名称(コード)」の並びとして読めない: {clause}")
            continue
        if any(_NON_MUNICIPAL.search(n) for n, _, _ in old):
            continue  # 支庁・振興局・郡の名称変更など。市区町村の系譜ではない

        if rule == "C_created":
            new = _items(gd["new"])
            if new is None:
                reasons.append(f"新設側を読めない: {clause}")
                continue
            if designated_old is not None:
                # 政令市施行と同じ行の区の新設: 旧市の区域が各区に分かれる。
                edges.extend(
                    _edge(effective_date, designated_old, code, "designated_city_ward",
                          "区域の一部", "C_designated_wards")
                    for name, code, _ in new if not _NON_MUNICIPAL.search(name)
                )
            # 単独の区の新設（政令市への編入など）は旧コードを書かないので辺にしない。
            continue

        if rule == "C_merge" and _items(gd["new"]) is None:
            # 新設合併（コード追加なし）。新設側は名称だけで書かれる。
            successor, why = _reused_code(old, gd["new"], effective_date, created)
            if why:
                reasons.append(why)
            edges.extend(
                _edge(effective_date, code, successor, "merge_reuse_code",
                      None if successor else "承継先のコードが確定しない", rule)
                for _, code, _ in old
            )
            continue

        new = _items(gd["new"])
        if new is None:
            reasons.append(f"新側を「名称(コード)」の並びとして読めない: {clause}")
            continue
        if rule not in ("C_split", "C_separate") and len(new) != 1:
            reasons.append(f"新側が1つでない: {clause}")
            continue
        if rule in _SINGLE and len(old) != 1:
            reasons.append(f"旧側が1つでない: {clause}")
            continue
        if rule == "C_designated":
            designated_old = old[0][1]

        qualifier = gd.get("q")
        for _, old_code, old_q in old:
            for _, new_code, _ in new:
                edges.append(_edge(
                    effective_date, old_code, new_code, EDGE_TYPES[rule],
                    old_q or qualifier
                    or ("区域の一部" if rule in ("C_split", "C_separate") else None),
                    rule,
                ))

    return edges, ("; ".join(reasons) if reasons else None)


def _reused_code(
    old: list[tuple[str, str, str | None]],
    new_name: str,
    effective_date: str,
    created: set[tuple[str, str]],
) -> tuple[str | None, str | None]:
    """引き継いだコード。根拠は新設フラグで、名称は照合に使うだけ."""
    flagged = [code for _, code, _ in old if (effective_date, code) in created]
    if len(flagged) != 1:
        return None, (
            f"コード追加なしの新設で、新設フラグの付いた旧コードが {len(flagged)} 件: "
            f"{new_name}"
        )
    code = flagged[0]
    name = next(n for n, c, _ in old if c == code)
    if name != new_name:
        return None, f"新設フラグ {code}（{name}）と新設側の名称 {new_name} が食い違う"
    return code, None


def _edge(date: str, old5: str, new5: str | None, event: str,
          qualifier: str | None, rule: str) -> dict:
    return {
        "effective_date": date, "old_jis_code": old5, "new_jis_code": new5,
        "event_type": event, "area_qualifier_raw": qualifier, "parse_rule_id": rule,
    }


def read_change_csv(data: bytes) -> pl.DataFrame:
    """発行元の CSV を全列文字列で読み、見出しを検査して jpac の列名にする."""
    df = pl.read_csv(data, infer_schema_length=0, encoding="utf8")
    missing = [c for c in COLUMNS if c not in df.columns]
    if missing:
        raise ValueError(f"e-Stat 廃置分合等情報の見出しが足りない: {missing}")
    return df.select(list(COLUMNS)).rename(COLUMNS)


def build_change_tables(
    main: pl.DataFrame, created_rows: pl.DataFrame, abolished_rows: pl.DataFrame
) -> dict[str, pl.DataFrame]:
    """``municipality_change_raw`` と ``municipality_lineage_edge``.

    3本とも ``read_change_csv`` を通したもの。新設・廃止の2本は、同じ検索条件に
    新設／廃止の絞り込みを足して取り直した CSV で、行の中身は本体と同じ形である。
    """
    cols = list(COLUMNS.values())
    created_full = {tuple(r) for r in created_rows.select(cols).iter_rows()}
    abolished_full = {tuple(r) for r in abolished_rows.select(cols).iter_rows()}
    created = {
        (r["effective_date_raw"], r["area_code_raw"])
        for r in created_rows.iter_rows(named=True)
    }
    abolished = {
        (r["effective_date_raw"], r["area_code_raw"])
        for r in abolished_rows.iter_rows(named=True)
    }

    raw_rows: list[dict] = []
    edges: list[dict] = []
    for ordinal, row in enumerate(main.select(cols).iter_rows(), start=1):
        rec = dict(zip(cols, row, strict=True))
        flag = ("S" if row in created_full else "") + ("H" if row in abolished_full else "")
        row_edges, unresolved = parse_reason(
            rec["reason_raw"], rec["effective_date_raw"], created
        )
        for e in row_edges:
            e["row_ordinal"] = ordinal
        edges.extend(row_edges)
        raw_rows.append({
            "row_ordinal": ordinal,
            **rec,
            # 全列が同じ行が発行元のデータに2組ある。ハッシュは行の同定に使わない。
            "row_sha256": hashlib.sha256("\x1f".join(
                v if v is not None else "\x00" for v in row).encode("utf-8")).hexdigest(),
            "hs_flag": flag or None,
            "parse_status": "unresolved" if unresolved else "parsed",
            "unresolved_reason": unresolved,
        })

    # 存続の辺。旧コードがその日に廃止されていなければ、旧コードは残っている。
    survivors = {
        (e["effective_date"], e["old_jis_code"])
        for e in edges
        if e["new_jis_code"] and e["new_jis_code"] != e["old_jis_code"]
        and (e["effective_date"], e["old_jis_code"]) not in abolished
    }
    for e in list(edges):
        key = (e["effective_date"], e["old_jis_code"])
        if key in survivors:
            edges.append({**_edge(key[0], key[1], key[1], "continues", None,
                                  "C_continues"), "row_ordinal": e["row_ordinal"]})

    edge_schema = {
        "effective_date": pl.Utf8, "old_jis_code": pl.Utf8, "new_jis_code": pl.Utf8,
        "event_type": pl.Utf8, "area_qualifier_raw": pl.Utf8,
        "parse_rule_id": pl.Utf8, "row_ordinal": pl.Int64,
    }
    e = pl.DataFrame(edges, schema=edge_schema)
    keys = ["effective_date", "old_jis_code", "new_jis_code", "event_type",
            "area_qualifier_raw"]
    e = (
        e.group_by(keys, maintain_order=True)
        .agg(
            pl.col("parse_rule_id").unique().sort().str.join(";"),
            pl.col("row_ordinal").unique().sort().cast(pl.Utf8).str.join(";")
            .alias("source_row_ordinals"),
        )
    )
    counts = (
        e.filter(pl.col("new_jis_code").is_not_null())
        .group_by(["effective_date", "old_jis_code"])
        .agg(pl.col("new_jis_code").n_unique().alias("candidate_count"))
    )
    e = (
        e.join(counts, on=["effective_date", "old_jis_code"], how="left")
        .with_columns(
            pl.col("candidate_count").fill_null(0).cast(pl.Int64),
            pl.col("old_jis_code").map_elements(check_digit, return_dtype=pl.Utf8)
            .alias("old_lg_code"),
            pl.col("new_jis_code").map_elements(check_digit, return_dtype=pl.Utf8)
            .alias("new_lg_code"),
        )
        .with_columns((pl.col("candidate_count") == 1).alias("is_unique_match"))
        .sort(["effective_date", "old_jis_code", "new_jis_code", "event_type"],
              nulls_last=True)
    )
    raw = pl.DataFrame(raw_rows, schema={
        "row_ordinal": pl.Int64, **{c: pl.Utf8 for c in cols},
        "row_sha256": pl.Utf8, "hs_flag": pl.Utf8, "parse_status": pl.Utf8,
        "unresolved_reason": pl.Utf8,
    })
    return {"municipality_change_raw": raw, "municipality_lineage_edge": e}


def check_attested_lineage(edges: pl.DataFrame, lineage_doc: dict) -> list[dict]:
    """人の署名した ``municipality_lineage.yml`` を公式の辺で検査する.

    - ``transitions`` の各行は、``(old, new, 施行日)`` がちょうど1本の辺に一致し、
      その辺の候補数が1であること
    - ``unlisted`` の ``successors`` は、その旧コードから出る辺の行き先（存続を除く）の
      集合と一致すること

    食い違いは問題として返す。人の署名と公式データが矛盾しているので、どちらかが
    誤っている。どちらが正しいかはここでは決めない。
    """
    problems: list[dict] = []
    for t in lineage_doc.get("transitions") or []:
        old, new = str(t["old_lg_code"]), str(t["new_lg_code"])
        date = str(t.get("effective_date"))
        hit = edges.filter(
            (pl.col("old_lg_code") == old) & (pl.col("new_lg_code") == new)
            & (pl.col("effective_date") == date)
        )
        if hit.height != 1:
            problems.append({"entry": f"transitions {old}->{new} {date}",
                             "problem": f"公式の辺が {hit.height} 本"})
        elif hit["candidate_count"][0] != 1:
            problems.append({"entry": f"transitions {old}->{new} {date}",
                             "problem": f"公式では承継先が {hit['candidate_count'][0]} 件"})
    for u in lineage_doc.get("unlisted") or []:
        if "successors" not in u:
            continue
        code = str(u["lg_code"])
        official = set(
            edges.filter(
                (pl.col("old_lg_code") == code) & pl.col("new_lg_code").is_not_null()
                & (pl.col("new_lg_code") != code)
            )["new_lg_code"].to_list()
        )
        attested = {str(s) for s in u["successors"]}
        if official != attested:
            problems.append({
                "entry": f"unlisted {code}",
                "problem": f"承継先が一致しない: 署名 {sorted(attested)} / 公式 {sorted(official)}",
            })
    return problems

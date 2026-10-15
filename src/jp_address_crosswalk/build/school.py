"""文部科学省 学校コード → 町字・市区町村 (docs/POLICY.md §3.5).

学校コードは文部科学省が全国の学校に1つずつ振ったコードで、学校種・都道府県・設置区分・
学校番号・検査数字の13桁からなる（「学校コードの取り扱いについて」別添1）。一覧は
所在地を**文字列**で持ち、座標を持たない。だから結び付けは位置ではなく所在地の文字列で
行う —— 駅・バス停のブリッジとはそこが違い、町字（``address_id``）まで届く。

照合の規則は3つで、どれも「間違って結ぶより、結ばずに残す」側に倒してある
（docs/POLICY.md §4）。

* **市区町村は名前の最長一致。** 学校の都道府県番号の中だけで探す。同じ名前の候補が
  2つあれば決めない。
* **町字は、町字名の直後が番地で終わるときだけ結ぶ。** 「小野田２２番地」が名前の短い
  別の町字「小野」に結ばれるのを防ぐため（2026-10-15 の試算で実際に起きた）。
  ``4-6-27`` の先頭の数字は、その町字に「4丁目」があれば丁目として読む。
* **正規化は緩めない。** ヶ/ケ、「字」の省略、京都の通り名は結ばずに市区町村どまりに
  なる。``normalize_conservative`` の方針（docs/MATCHING_RULES.md §8）をそのまま使う。

郵便番号は照合に使わない。結んだあとで、町字の郵便番号（``bridge_address_postal_code``）
と学校の郵便番号が合うかを ``postal_check`` に記録するだけである。合わない学校の多くは
大学などの事業所個別番号で、誤りではない。
"""

from __future__ import annotations

import io
import re

import polars as pl

from ..errors import SourceSchemaChanged
from ..logging_setup import get_logger, stage_context
from ..normalize import normalize_conservative

log = get_logger(__name__)

# 一覧 CSV の表頭。発行元の順のまま。違えば読まずに止める。
COLUMNS = (
    "学校コード", "学校種", "都道府県番号", "設置区分", "本分校", "学校名", "学校所在地",
    "郵便番号", "属性情報設定年月日", "属性情報廃止年月日", "旧学校調査番号", "移行後の学校コード",
)
ENCODING = "cp932"

# 学校種(2) 都道府県番号(2) 設置区分(1) 学校番号(7、先頭は0以外) 検査数字(1)
_CODE_RE = re.compile(r"^([A-H][12])(\d{2})([123])([1-9]\d{6})(\d)$")
# 「A1(幼稚園)」の形。コードと名前を分けて持つ。
_LABELLED_RE = re.compile(r"^([0-9A-Z]+)\((.+)\)$")
_DATE_RE = re.compile(r"^(\d{4})/(\d{1,2})/(\d{1,2})$")
# 移行後の学校コードは1つとは限らない。発行元は 、 ､ , 改行 で区切っている。
_SUCCESSOR_SPLIT_RE = re.compile(r"[、､,，\s]+")

BRANCH_ABOLISHED = "9"

RELATION_LOCATED = "located_in"
RELATION_AMBIGUOUS = "ambiguous"
RELATION_MUNICIPALITY = "municipality_only"
RELATION_UNRESOLVED = "unresolved"

MATCH_TOWN = "address_text_prefix"
MATCH_MUNICIPALITY = "municipality_text_prefix"
MATCH_UNRESOLVED = "unresolved"

POSTAL_AGREES = "agrees"
POSTAL_DIFFERS = "differs"
POSTAL_NO_TOWN_POSTAL = "town_has_no_postal_code"

# 規則で決まる信頼度。確率ではない（docs/MATCHING_RULES.md と同じ意味）。
CONFIDENCE = {
    (RELATION_LOCATED, POSTAL_AGREES): 0.98,
    (RELATION_LOCATED, None): 0.90,
    RELATION_AMBIGUOUS: 0.50,
    RELATION_MUNICIPALITY: 0.90,
    RELATION_UNRESOLVED: 0.0,
}

# 町字名のあとに来てよいもの。数字（番地・丁目の数字）か、番地を表す語か、小字の「字」。
_TOWN_BOUNDARY_RE = re.compile(r"^($|\d|番地|番|大字|字|地内|地先|無番地)")
_LEADING_NUMBER_RE = re.compile(r"^(\d+)(?:-|丁目|$)")
_OAZA_PREFIX_RE = re.compile(r"^大?字")
_LEADING_TAG_RE = re.compile(r"^\([^)]*\)")


# ------------------------------------------------------------------ the list

def check_digit(code: str) -> int:
    """「学校コードにおける検査数字の計算方法について」（別添1 別紙）.

    先頭の英字を2桁の数字（A=01 … H=08）に換え、上の桁から1と2を交互に掛ける。積が
    10を超えれば十の位と一の位を足し、和を10で割った余りの補数（10なら0）を取る。
    """
    digits = f"{'ABCDEFGH'.index(code[0]) + 1:02d}" + code[1:12]
    total = 0
    for i, ch in enumerate(digits):
        product = int(ch) * (1 if i % 2 == 0 else 2)
        total += product // 10 + product % 10
    return (10 - total % 10) % 10


def read_school_csv(data: bytes) -> pl.DataFrame:
    """一覧 CSV を文字列のまま読む。表頭が違えば止める。"""
    text = data.decode(ENCODING)  # 読めない字があれば UnicodeDecodeError で止める
    df = pl.read_csv(io.StringIO(text), infer_schema=False)
    if tuple(df.columns) != COLUMNS:
        raise SourceSchemaChanged(
            "学校コード一覧の表頭が想定と違う", expected=list(COLUMNS), observed=df.columns
        )
    return df


def _split_label(value: str, column: str) -> tuple[str, str]:
    m = _LABELLED_RE.match(value or "")
    if not m:
        raise SourceSchemaChanged(f"{column} が「コード(名前)」の形でない", value=value)
    return m.group(1), m.group(2)


def _iso_date(value: str | None) -> str | None:
    if value is None:
        return None
    m = _DATE_RE.match(value)
    if not m:
        raise SourceSchemaChanged("日付が YYYY/M/D の形でない", value=value)
    y, mo, d = (int(g) for g in m.groups())
    return f"{y:04d}-{mo:02d}-{d:02d}"


def build_school_table(raw: pl.DataFrame) -> pl.DataFrame:
    """1校1行。発行元の値を、コードと名前に分けたうえでそのまま持つ。"""
    rows = []
    for r in raw.iter_rows(named=True):
        code = r["学校コード"]
        type_code, type_name = _split_label(r["学校種"], "学校種")
        pref_code, _pref_name = _split_label(r["都道府県番号"], "都道府県番号")
        founder_code, founder_name = _split_label(r["設置区分"], "設置区分")
        branch_code, branch_name = _split_label(r["本分校"], "本分校")
        abolished = _iso_date(r["属性情報廃止年月日"])
        m = _CODE_RE.match(code or "")
        rows.append({
            "school_code": code,
            "school_type_code": type_code,
            "school_type_name": type_name,
            "pref_code": pref_code,
            "founder_code": founder_code,
            "founder_name": founder_name,
            "branch_code": branch_code,
            "branch_name": branch_name,
            "school_name_raw": r["学校名"],
            "address_raw": r["学校所在地"],
            # 郵便番号の表に無い番号（事業所個別番号など）や桁の欠けもあるので、
            # postal_code という名前にしない。その名前は postal_code_entity への
            # 外部キーになる（export/writers.py の _FK_BY_COLUMN）。
            "postal_code_raw": r["郵便番号"],
            "attribute_set_date": _iso_date(r["属性情報設定年月日"]),
            "attribute_abolished_date": abolished,
            "old_survey_number_raw": r["旧学校調査番号"],
            "successor_school_codes_raw": r["移行後の学校コード"],
            "is_active": branch_code != BRANCH_ABOLISHED and abolished is None,
            # 学校種などが変わった学校は、元の種別で計算した検査数字を持ち続けている
            # （令和7年確定版で163校）。発行元の値なので直さず、合うかどうかだけ持つ。
            "check_digit_valid": bool(m) and check_digit(code) == int(code[12]),
        })
    return pl.DataFrame(rows, schema=SCHOOL_SCHEMA).sort("school_code")


SCHOOL_SCHEMA = {
    "school_code": pl.Utf8,
    "school_type_code": pl.Utf8,
    "school_type_name": pl.Utf8,
    "pref_code": pl.Utf8,
    "founder_code": pl.Utf8,
    "founder_name": pl.Utf8,
    "branch_code": pl.Utf8,
    "branch_name": pl.Utf8,
    "school_name_raw": pl.Utf8,
    "address_raw": pl.Utf8,
    "postal_code_raw": pl.Utf8,
    "attribute_set_date": pl.Utf8,
    "attribute_abolished_date": pl.Utf8,
    "old_survey_number_raw": pl.Utf8,
    "successor_school_codes_raw": pl.Utf8,
    "is_active": pl.Boolean,
    "check_digit_valid": pl.Boolean,
}


def build_succession(schools: pl.DataFrame) -> pl.DataFrame:
    """廃止した学校 → 移行後の学校。1つの学校が複数に移ることがある（統合・分離）。

    移行先が一覧に無いこともある（令和7年確定版で 11件）。行は残し、
    ``successor_listed`` で分かるようにする。
    """
    listed = set(schools["school_code"].to_list())
    rows = []
    for code, raw in schools.select("school_code", "successor_school_codes_raw").iter_rows():
        if not raw:
            continue
        for successor in dict.fromkeys(s for s in _SUCCESSOR_SPLIT_RE.split(raw) if s):
            rows.append({
                "school_code": code,
                "successor_school_code": successor,
                "successor_listed": successor in listed,
            })
    return pl.DataFrame(rows, schema=SUCCESSION_SCHEMA).sort(
        ["school_code", "successor_school_code"]
    )


SUCCESSION_SCHEMA = {
    "school_code": pl.Utf8,
    "successor_school_code": pl.Utf8,
    "successor_listed": pl.Boolean,
}


def consistency_problems(schools: pl.DataFrame, succession: pl.DataFrame) -> list[str]:
    """リリースを止める食い違い。検査数字の不一致はここに入れない（上の注記）。"""
    problems: list[str] = []
    dup = schools.group_by("school_code").len().filter(pl.col("len") > 1)
    problems += [f"{c}: 学校コードが重複している" for c in dup["school_code"].to_list()]
    for r in schools.iter_rows(named=True):
        m = _CODE_RE.match(r["school_code"] or "")
        if not m:
            problems.append(f"{r['school_code']}: 学校コードが13桁の構成に合わない")
            continue
        parts = (r["school_type_code"], r["pref_code"], r["founder_code"])
        if m.group(1, 2, 3) != parts:
            problems.append(
                f"{r['school_code']}: コードの学校種・都道府県・設置区分が列の値 {parts} と違う"
            )
    bad = succession.filter(~pl.col("successor_school_code").str.contains(_CODE_RE.pattern))
    problems += [
        f"{a}: 移行後の学校コード {b} が13桁の構成に合わない"
        for a, b, _ in bad.iter_rows()
    ]
    return problems


# ------------------------------------------------------------------ matching

def _normalize_address(value: str | None) -> tuple[str, bool]:
    """(正規化した所在地, 複数の所在地が書かれていたか)."""
    if not value:
        return "", False
    lines = [s for s in value.splitlines() if s.strip()]
    text = normalize_conservative(lines[0] if lines else "")
    # 「（前期）長野県…」のような見出しは所在地ではない。
    text = _LEADING_TAG_RE.sub("", text)
    return text, len(lines) > 1


def _municipality_index(municipality: pl.DataFrame) -> tuple[dict, dict, dict]:
    """都道府県コードごとの (名前 → lg_code の集合)、都道府県名、政令市の区。"""
    current = municipality.filter(pl.col("is_current") == 1)
    names: dict[str, dict[str, set[str]]] = {}
    pref_names: dict[str, set[str]] = {}
    wards: dict[str, set[str]] = {}
    rows = list(current.iter_rows(named=True))
    for r in rows:
        pc = r["lg_code"][:2]
        pref_names.setdefault(pc, set()).add(normalize_conservative(r["pref"]))
        city, ward, county = r["city"] or "", r["ward"] or "", r["county"] or ""
        if not city:
            continue
        for key in {county + city + ward, city + ward}:
            names.setdefault(pc, {}).setdefault(normalize_conservative(key), set()).add(
                r["lg_code"]
            )
    # 政令市の親（区が NULL の行）→ その区。所在地が区を書かずに市で止まる学校のため。
    parents = {
        (r["pref"], r["city"]): r["lg_code"] for r in rows if r["city"] and not r["ward"]
    }
    for r in rows:
        if r["ward"] and (r["pref"], r["city"]) in parents:
            wards.setdefault(parents[(r["pref"], r["city"])], set()).add(r["lg_code"])
    return names, pref_names, wards


def _town_index(address: pl.DataFrame) -> dict[str, dict[str, set[str]]]:
    """lg_code ごとの (正規化した町字名 → address_id の集合)。現行の町字だけ。

    先頭の「大字」「字」を落とした名前も同じ索引に入れる。落とした結果が別の町字と
    同じ名前になれば、その名前は候補が2つになり、結ばずに残る。
    """
    current = address.filter(pl.col("valid_to").is_null()) if "valid_to" in address.columns \
        else address
    towns: dict[str, dict[str, set[str]]] = {}
    for aid, lg, name in current.select(
        "address_id", "lg_code", "full_name_normalized"
    ).iter_rows():
        if not name:
            continue
        bucket = towns.setdefault(lg, {})
        bucket.setdefault(name, set()).add(aid)
        stripped = _OAZA_PREFIX_RE.sub("", name)
        if stripped and stripped != name:
            bucket.setdefault(stripped, set()).add(aid)
    return towns


def _match_town(rest: str, candidates: dict[str, set[str]]) -> tuple[str, set[str]] | None:
    """所在地の市区町村より後ろを、最長の町字名に結ぶ。"""
    best: tuple[int, str, set[str]] | None = None
    for text in dict.fromkeys((rest, _OAZA_PREFIX_RE.sub("", rest))):
        for i in range(len(text), 0, -1):
            head, tail = text[:i], text[i:]
            number = _LEADING_NUMBER_RE.match(tail)
            if number:
                chome = f"{head}{int(number.group(1))}丁目"
                if chome in candidates:
                    hit = (len(chome), chome, candidates[chome])
                    best = hit if best is None or hit[0] > best[0] else best
                    break
            if head in candidates and _TOWN_BOUNDARY_RE.match(tail):
                hit = (len(head), head, candidates[head])
                best = hit if best is None or hit[0] > best[0] else best
                break
    return (best[1], best[2]) if best else None


def build_school_bridge(
    schools: pl.DataFrame,
    address: pl.DataFrame,
    municipality: pl.DataFrame,
    address_postal: pl.DataFrame | None,
) -> pl.DataFrame:
    """学校1校につき、町字の候補ごとに1行。決まらない学校も行を残す。"""
    with stage_context("school", "match"):
        names, pref_names, wards = _municipality_index(municipality)
        towns = _town_index(address)
        postal_by_address: dict[str, set[str]] = {}
        if address_postal is not None:
            for aid, pc in address_postal.select("address_id", "postal_code").iter_rows():
                if aid and pc:
                    postal_by_address.setdefault(aid, set()).add(pc)

        rows: list[dict] = []
        for r in schools.select(
            "school_code", "pref_code", "address_raw", "postal_code_raw"
        ).iter_rows(named=True):
            text, multiple = _normalize_address(r["address_raw"])
            pc = r["pref_code"]
            for pref in sorted(pref_names.get(pc, ()), key=len, reverse=True):
                if text.startswith(pref):
                    text = text[len(pref):]
                    break
            notes = ["所在地が複数書かれている。1つ目で照合した"] if multiple else []

            lgs, rest = None, ""
            index = names.get(pc, {})
            for i in range(len(text), 0, -1):
                if text[:i] in index:
                    lgs, rest = index[text[:i]], text[i:]
                    break
            if not lgs:
                rows.append(_row(r, None, None, RELATION_UNRESOLVED, None, 0, None,
                                 notes + ["所在地から市区町村が決まらない"]))
                continue
            if len(lgs) > 1:
                rows.append(_row(r, None, None, RELATION_UNRESOLVED, None, 0, None,
                                 notes + [f"同じ名前の市区町村が複数ある: {','.join(sorted(lgs))}"]))
                continue
            lg = next(iter(lgs))

            # 政令市の親で止まったときは、その区の町字をまとめて探す。
            searched = sorted(wards.get(lg, ())) or [lg]
            candidates: dict[str, set[str]] = {}
            owner: dict[str, str] = {}
            for code in searched:
                for name, ids in towns.get(code, {}).items():
                    candidates.setdefault(name, set()).update(ids)
                    for aid in ids:
                        owner[aid] = code
            hit = _match_town(rest, candidates)
            if hit is None:
                rows.append(_row(r, None, lg, RELATION_MUNICIPALITY, None, 0, None,
                                 notes + ["町字まで決まらない（表記の違い・町字の無い地域など）"]))
                continue

            matched_name, ids = hit
            if len(ids) > 1:
                for aid in sorted(ids):
                    rows.append(_row(r, aid, owner[aid], RELATION_AMBIGUOUS, matched_name,
                                     len(ids), _postal_check(r, aid, postal_by_address),
                                     notes + ["同じ名前の町字が複数ある。候補を残す（POLICY.md §4）"]))
                continue
            aid = next(iter(ids))
            check = _postal_check(r, aid, postal_by_address)
            if check == POSTAL_DIFFERS:
                notes.append("学校の郵便番号が町字の郵便番号と違う（事業所個別番号のことが多い）")
            elif check == POSTAL_NO_TOWN_POSTAL:
                notes.append("町字に郵便番号の対応が無く、郵便番号で確かめられない")
            elif check is None:
                notes.append("学校の郵便番号が7桁でなく、確かめられない")
            rows.append(_row(r, aid, owner[aid], RELATION_LOCATED, matched_name, 1, check, notes))

        bridge = pl.DataFrame(rows, schema=BRIDGE_SCHEMA).sort(
            ["school_code", "address_id"], nulls_last=True
        )
        per_school = bridge.unique("school_code")
        log.info(
            "built school -> address bridge",
            schools=schools.height,
            rows=bridge.height,
            **{
                rel: per_school.filter(pl.col("relation_type") == rel).height
                for rel in (RELATION_LOCATED, RELATION_AMBIGUOUS,
                            RELATION_MUNICIPALITY, RELATION_UNRESOLVED)
            },
            auto=bridge.filter(pl.col("verification_status") == "auto").height,
        )
        return bridge


def _postal_check(r: dict, aid: str, postal_by_address: dict[str, set[str]]) -> str | None:
    code = r["postal_code_raw"]
    if not code or not re.fullmatch(r"\d{7}", code):
        return None
    known = postal_by_address.get(aid)
    if not known:
        return POSTAL_NO_TOWN_POSTAL
    return POSTAL_AGREES if code in known else POSTAL_DIFFERS


def _row(r: dict, address_id, lg_code, relation, matched_name, candidate_count,
         postal_check, notes: list[str]) -> dict:
    if relation == RELATION_LOCATED:
        confidence = CONFIDENCE[(relation, POSTAL_AGREES if postal_check == POSTAL_AGREES else None)]
    else:
        confidence = CONFIDENCE[relation]
    method = {
        RELATION_LOCATED: MATCH_TOWN, RELATION_AMBIGUOUS: MATCH_TOWN,
        RELATION_MUNICIPALITY: MATCH_MUNICIPALITY, RELATION_UNRESOLVED: MATCH_UNRESOLVED,
    }[relation]
    # 自動で確定するのは、町字が1つに決まり、学校の郵便番号がその町字の番号と合うときだけ。
    # 文字列の照合を、独立した郵便番号の対応が裏づけている。
    auto = (relation == RELATION_LOCATED and candidate_count == 1
            and postal_check == POSTAL_AGREES and not notes)
    return {
        "school_code": r["school_code"],
        "address_id": address_id,
        "lg_code": lg_code,
        "relation_type": relation,
        "match_method": method,
        "matched_town_name": matched_name,
        "confidence": confidence,
        "candidate_count": candidate_count,
        "is_unique_match": 1 if candidate_count == 1 else 0,
        "postal_check": postal_check,
        "verification_status": "auto" if auto else "review_required",
        "mismatch_note": "。".join(notes) if notes else None,
    }


BRIDGE_SCHEMA = {
    "school_code": pl.Utf8,
    "address_id": pl.Utf8,
    "lg_code": pl.Utf8,
    "relation_type": pl.Utf8,
    "match_method": pl.Utf8,
    "matched_town_name": pl.Utf8,
    "confidence": pl.Float64,
    "candidate_count": pl.Int64,
    "is_unique_match": pl.Int64,
    "postal_check": pl.Utf8,
    "verification_status": pl.Utf8,
    "mismatch_note": pl.Utf8,
}

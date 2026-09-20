"""境界データの JIS コードを、現行の市区町村に読み替える.

境界データ（e-Stat 令和2年国勢調査）は2020年時点の断面で、jpac の市区町村は現行の
断面である。両者がずれている箇所では、ポリゴンが持つ5桁 JIS コードを現行の
``lg_code`` が持っていない。実測では**すべて旧浜松市の7区**（22131〜22137）で、
2024-01-01 の区再編より前の断面である。

``overrides/municipality_lineage.yml`` には、その読み替えが人の署名つきで記録されて
いる。メッシュ表（``build/mesh.py``）と静的マップは以前からこれを読んでいたが、
駅・路線・バス停のブリッジは読んでいなかった（``docs/LIMITATIONS.md`` 項目15）。
そのため同じ区域が、メッシュでは解決され、ブリッジでは ``lg_code`` NULL のまま
残るという食い違いが起きていた。このモジュールはその読み替えを1か所に置く。

**分割は1つに決めない。** 旧北区（22135）は中央区と浜名区に分かれており、承継先が
一意でない。``mesh.py`` と同じく**全承継先を候補として残す**。どちらかを選ぶのは
``docs/POLICY.md`` §4 が欠陥と呼ぶ「誤った 1:1 の捏造」にあたる。

**読み替えても含有関係は含有関係のまま。** 旧区の区域は新区の区域の内側にあるので、
系譜を経て解決した一意な候補は ``auto`` のままでよい（``mesh.py`` の
``build_mesh_municipality`` が同じ判断をしている）。ただし「直接一致した」のではなく
「承継記録を経た」ことは注記に残す —— 経路を消すと、あとから誰かが
``municipality_lineage.yml`` を疑う手がかりが無くなる。
"""

from __future__ import annotations

# 承継先が複数ある旧コードは、どれも候補として残す。
Candidate = tuple[str | None, bool]

# 読み替えの経路は match_method に持たせる。注記ではなく列にするのは、出荷テーブルの
# CHECK が「auto なら注記は無い」を要求しているからである（export/writers.py の
# STATION_BRIDGE_CHECKS。「It still requires a single settled candidate and a clean
# note」）。1:1 の承継は含有関係として確定しているので auto のままにしたい。そこで
# 経路は列として残す —— こうすれば SQL で数えられ、注記を濁さずに済む。
#
# 綴りをここに置くのは、3つのビルダーが各自で文字列を持つと食い違うからである。
VIA_LINEAGE_SUFFIX = "_via_lineage"


def via_lineage_method(method: str) -> str:
    """``spatial_containment`` → ``spatial_containment_via_lineage``."""
    return method + VIA_LINEAGE_SUFFIX


def resolve_candidates(
    jis_code: str,
    lg_by_jis: dict[str, str],
    successors: dict[str, list[str]] | None = None,
) -> list[Candidate]:
    """5桁 JIS コード → ``(lg_code, 系譜を経たか)`` の候補一覧.

    順序は決定的（``lg_code`` 昇順）。ビルドのバイト再現性がこれに依存する。

    4つの場合がある:

    * 現行の市区町村が持っている → ``[(lg, False)]``
    * 現行に無く、系譜の承継先が1つ → ``[(lg, True)]``
    * 現行に無く、承継先が複数（旧浜松市北区） → 承継先の数だけ候補
    * 現行にも系譜にも無い → ``[(None, False)]``

    最後の場合に空リストを返さないのが要点である。行そのものを消すと「境界データに
    そんなコードは無かった」と読めてしまうが、実際には「コードはあるが現行の断面に
    対応が無い」であり、両者は別の主張である。
    """
    if jis_code in lg_by_jis:
        return [(lg_by_jis[jis_code], False)]
    targets = (successors or {}).get(jis_code)
    if targets:
        return [(lg, True) for lg in sorted(set(targets))]
    return [(None, False)]


def lineage_note(jis_code: str, lg_code: str) -> str:
    """**分割**を経て解決した行に付ける注記.

    1:1 の承継には注記を付けない —— 出荷テーブルの CHECK が「auto なら注記は無い」を
    要求しており、経路は ``match_method`` が持つ。分割は候補が複数あるので
    ``review_required`` になり、CHECK に抵触しないうえ、**人に伝えるべきことが実際に
    ある**：これは候補の1つでしかない。
    """
    return (
        f"境界データ側のコード {jis_code} は jpac の現行市区町村に無いが、"
        f"overrides/municipality_lineage.yml の承継記録により {lg_code} に読み替えた。"
        "承継先が複数あるため、これは候補の1つである（POLICY.md §4）"
    )

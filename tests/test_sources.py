"""Source adapters, tested offline against mocked HTTP and real fixtures.

Everything here runs without network access, so CI is deterministic and the
project stays testable on a machine that cannot reach the publishers
(docs/TEST_STRATEGY.md §1).
"""

from __future__ import annotations

import io
import zipfile
from pathlib import Path

import pytest

from jp_address_crosswalk.errors import SourceSchemaChanged, UnsafeArchive
from jp_address_crosswalk.payload import (
    ArchiveLimits,
    read_zip_member,
    safe_zip_members,
)
from jp_address_crosswalk.snapshot import (
    SchemaInfo,
    check_schema_drift,
    license_text_hash,
    normalize_member_name,
)

FIXTURES = Path(__file__).parent / "fixtures"


class TestArchiveSafety:
    @staticmethod
    def zip_bytes(members: dict[str, bytes]) -> bytes:
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
            for name, data in members.items():
                zf.writestr(name, data)
        return buf.getvalue()

    def test_normal_archive_passes(self, tmp_path):
        p = tmp_path / "a.zip"
        p.write_bytes(self.zip_bytes({"a.csv": b"x,y\n1,2\n"}))
        assert read_zip_member(p, "a.csv") == b"x,y\n1,2\n"

    @pytest.mark.parametrize("name", ["../evil.csv", "/abs.csv", "C:/win.csv", "a/../../b.csv"])
    def test_path_traversal_refused(self, tmp_path, name):
        p = tmp_path / "t.zip"
        p.write_bytes(self.zip_bytes({name: b"x"}))
        with zipfile.ZipFile(p) as zf, pytest.raises(UnsafeArchive):
            safe_zip_members(zf, ArchiveLimits())

    def test_zip_bomb_refused(self, tmp_path):
        p = tmp_path / "bomb.zip"
        p.write_bytes(self.zip_bytes({"big.csv": b"0" * 5_000_000}))
        with zipfile.ZipFile(p) as zf, pytest.raises(UnsafeArchive):
            safe_zip_members(zf, ArchiveLimits(max_compression_ratio=10))

    def test_too_many_members_refused(self, tmp_path):
        p = tmp_path / "many.zip"
        p.write_bytes(self.zip_bytes({f"f{i}.csv": b"x" for i in range(30)}))
        with zipfile.ZipFile(p) as zf, pytest.raises(UnsafeArchive):
            safe_zip_members(zf, ArchiveLimits(max_archive_members=10))


class TestMagicByteValidation:
    """An HTML error page saved under a .zip name must not reach a parser."""

    def test_magic_check_rejects_html(self):
        from jp_address_crosswalk.payload import MAGIC_BY_KIND

        html_head = b"<!doctype"
        assert not any(html_head.startswith(m) for m in MAGIC_BY_KIND["zip"])
        assert not any(html_head.startswith(m) for m in MAGIC_BY_KIND["doc"])


class TestSchemaDrift:
    BASE = SchemaInfo(
        columns=["a", "b", "c"], column_count=3, encoding="utf-8",
        container="zip", members=["01_2025.csv"],
    )

    def write_baseline(self, tmp_path: Path, info: SchemaInfo) -> Path:
        import yaml

        p = tmp_path / "expected.yml"
        p.write_text(yaml.safe_dump(info.as_dict(), allow_unicode=True, sort_keys=True),
                     encoding="utf-8")
        return p

    def test_unchanged_schema_passes(self, tmp_path):
        p = self.write_baseline(tmp_path, self.BASE)
        check_schema_drift("t", self.BASE, p)

    def test_added_column_blocks(self, tmp_path):
        p = self.write_baseline(tmp_path, self.BASE)
        changed = SchemaInfo(columns=["a", "b", "c", "d"], column_count=4,
                             encoding="utf-8", container="zip", members=["01_2025.csv"])
        with pytest.raises(SourceSchemaChanged):
            check_schema_drift("t", changed, p)

    def test_reordered_columns_block(self, tmp_path):
        p = self.write_baseline(tmp_path, self.BASE)
        changed = SchemaInfo(columns=["b", "a", "c"], column_count=3,
                             encoding="utf-8", container="zip", members=["01_2025.csv"])
        with pytest.raises(SourceSchemaChanged):
            check_schema_drift("t", changed, p)

    def test_encoding_change_blocks(self, tmp_path):
        p = self.write_baseline(tmp_path, self.BASE)
        changed = SchemaInfo(columns=["a", "b", "c"], column_count=3,
                             encoding="cp932", container="zip", members=["01_2025.csv"])
        with pytest.raises(SourceSchemaChanged):
            check_schema_drift("t", changed, p)

    def test_year_roll_in_member_name_does_not_block(self):
        """01_2025.csv -> 01_2026.csv is routine, not structural."""
        assert normalize_member_name("01000-19.0b/01_2025.csv") == normalize_member_name(
            "01000-19.0b/01_2026.csv"
        )


class TestLicenseHash:
    def test_markup_and_whitespace_ignored(self):
        a = "<html><body><p>利用規約  本文</p></body></html>"
        b = "<html>\n<body>\n  <p>利用規約 本文</p>\n</body>\n</html>"
        assert license_text_hash(a) == license_text_hash(b)

    def test_script_changes_ignored(self):
        a = "<script>var x=1</script><p>terms</p>"
        b = "<script>var x=2;analytics()</script><p>terms</p>"
        assert license_text_hash(a) == license_text_hash(b)

    def test_body_change_detected(self):
        a = "<p>再配布を許可します</p>"
        b = "<p>再配布を禁止します</p>"
        assert license_text_hash(a) != license_text_hash(b)


@pytest.mark.skipif(not (FIXTURES / "mic_shigai_list.tsv").exists(),
                    reason="fixtures not generated")
class TestWordDocExtraction:
    def test_fixture_rows_have_four_cells(self):
        rows = [
            line.split("\t")
            for line in (FIXTURES / "mic_shigai_list.tsv")
            .read_text(encoding="utf-8").splitlines()
        ]
        assert rows, "fixture is empty"
        assert all(len(r) == 4 for r in rows)

    def test_exclusion_clauses_survived_extraction(self):
        text = (FIXTURES / "mic_shigai_list.tsv").read_text(encoding="utf-8")
        assert "を除く" in text, "official exclusion wording must be preserved verbatim"

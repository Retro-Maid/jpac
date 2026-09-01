"""Licensing tests: the attribution a build emits, and how terms text is hashed.

Shipping the accepted payloads alongside the derived artifacts (see
`DATA_LICENSE.md`) makes attribution a two-form problem rather than a one-form
one. An unmodified publisher file must not be described as 加工して作成, and the
derived artifacts must not be presented as the publisher's own product. Both
forms are configuration, so these tests check the configuration rather than a
string baked into the writer.
"""

from __future__ import annotations

import codecs
from pathlib import Path

import pytest
import yaml

from jp_address_crosswalk.pipeline import Config, Paths, _attribution_section
from jp_address_crosswalk.snapshot import (
    DEFAULT_HTML_ENCODING,
    license_text_hash,
    license_text_hash_bytes,
    resolve_html_encoding,
)

ROOT = Path(__file__).parents[1]
SOURCES = yaml.safe_load((ROOT / "config" / "sources.yml").read_text(encoding="utf-8"))["sources"]

# 「…を加工して作成」 (ABR, MIC) and 「…をもとに○○作成」 (MLIT) are the two
# publisher-supplied ways of saying "this was processed". PDL 1.0 §1.1 lets each
# publisher's own example replace the default, so both spellings are legitimate
# and the test accepts either.
PROCESSED_MARKERS = ("加工して作成", "をもとに")


@pytest.mark.parametrize("name", sorted(SOURCES))
class TestAttributionConfig:
    def test_both_forms_are_present(self, name: str) -> None:
        attribution = SOURCES[name]["license"].get("attribution")
        assert attribution, f"{name} has no attribution block"
        for form in ("unmodified", "processed"):
            assert attribution.get(form, "").strip(), f"{name}.{form} is empty"

    def test_every_form_carries_a_url(self, name: str) -> None:
        for form, text in SOURCES[name]["license"]["attribution"].items():
            assert "https://" in text, f"{name}.{form} cites no URL"

    def test_unmodified_form_never_claims_processing(self, name: str) -> None:
        """The point of the second block: an untouched file is not 加工."""
        text = SOURCES[name]["license"]["attribution"]["unmodified"]
        for marker in PROCESSED_MARKERS:
            assert marker not in text, f"{name} describes raw payloads as processed"

    def test_processed_form_states_processing(self, name: str) -> None:
        text = SOURCES[name]["license"]["attribution"]["processed"]
        if SOURCES[name]["license"].get("statement"):
            # Japan Post asserts no copyright, so it imposes no modification
            # notice and the voluntary wording is deliberately identical.
            return
        assert any(m in text for m in PROCESSED_MARKERS), (
            f"{name} processed attribution does not say the data was processed"
        )

    def test_redistribution_conclusion_is_recorded(self, name: str) -> None:
        """A conclusion that only lives in prose cannot be checked by a build."""
        redistribution = SOURCES[name]["license"].get("redistribution")
        assert redistribution, f"{name} records no redistribution conclusion"
        assert redistribution["unmodified"] == "permitted"
        assert redistribution["modified"] == "permitted"


class TestNoticeAttributionSections:
    @staticmethod
    def _section(form: str) -> str:
        cfg = Config.load(Paths(ROOT))
        return "\n".join(_attribution_section(cfg, "## H", "intro", form))

    def test_the_two_sections_differ(self) -> None:
        assert self._section("unmodified") != self._section("processed")

    def test_unmodified_section_claims_no_processing(self) -> None:
        section = self._section("unmodified")
        for marker in PROCESSED_MARKERS:
            assert marker not in section

    def test_every_publisher_appears_in_both(self) -> None:
        for form in ("unmodified", "processed"):
            section = self._section(form)
            for provider in ("デジタル庁", "日本郵便株式会社", "国土交通省", "総務省"):
                assert provider in section, f"{provider} missing from the {form} block"

    def test_mlit_uses_the_publishers_own_dataset_name(self) -> None:
        """The official example says 位置参照情報ダウンロードサービス."""
        assert "位置参照情報ダウンロードサービス" in self._section("unmodified")


class TestLicenceTextEncoding:
    def test_bom_outranks_the_transport_header(self) -> None:
        raw = codecs.BOM_UTF8 + "<html>本文</html>".encode()
        assert resolve_html_encoding(raw, "text/html; charset=euc-jp") == "utf-8-sig"

    def test_transport_header_outranks_the_document(self) -> None:
        raw = b'<meta charset="euc-jp"><html>x</html>'
        assert resolve_html_encoding(raw, "text/html; charset=UTF-8") == "utf-8"

    def test_xml_prolog_is_read_when_the_header_is_silent(self) -> None:
        raw = '<?xml version="1.0" encoding="Shift_JIS"?><html>x</html>'.encode("cp932")
        assert resolve_html_encoding(raw, "text/html") == "cp932"

    def test_meta_charset_is_read_when_the_header_is_silent(self) -> None:
        raw = b'<html><head><meta charset="EUC-JP"></head></html>'
        assert resolve_html_encoding(raw, "text/html") == "euc_jp"

    def test_undeclared_pages_do_not_fall_back_to_latin_1(self) -> None:
        """The defect the committed baselines carry: ISO-8859-1 hashes mojibake."""
        assert resolve_html_encoding(b"<html>x</html>", "text/html") == DEFAULT_HTML_ENCODING
        assert DEFAULT_HTML_ENCODING != "iso-8859-1"

    def test_hash_over_bytes_matches_hash_over_decoded_text(self) -> None:
        text = "<html><body>利用規約 本文</body></html>"
        raw = text.encode("cp932")
        header = '<?xml version="1.0" encoding="Shift_JIS"?>'.encode("cp932")
        assert license_text_hash_bytes(header + raw, "text/html") == license_text_hash(
            (header + raw).decode("cp932")
        )

    def test_mis_decoding_produces_a_different_hash(self) -> None:
        """Why the recorded baselines are weak evidence of what the terms say."""
        raw = "<html>郵便番号データ</html>".encode()
        assert license_text_hash_bytes(raw, "text/html") != license_text_hash(
            raw.decode("iso-8859-1")
        )


class TestLicenceBaselines:
    """The mis-decoding finding must stay visible until it is acted on."""

    @pytest.mark.parametrize("name", sorted(SOURCES))
    def test_hashed_artifacts_record_the_correctly_decoded_value(self, name: str) -> None:
        for artifact in SOURCES[name]["license"].get("artifacts", []):
            if artifact.get("text_sha256") is None:
                continue
            assert artifact.get("text_sha256_decoded"), (
                f"{name}/{artifact['role']} has a baseline but no decoded counterpart; "
                "see docs/LICENSE_POLICY.md §4"
            )


class TestAcceptedPayloadEnumeration:
    @staticmethod
    def _raw(tmp_path: Path, manifest: str | None = None) -> Path:
        src = tmp_path / "abr"
        src.mkdir()
        (src / "town_master.zip").write_bytes(b"PK\x03\x04payload")
        if manifest is not None:
            (src / "_payload.yml").write_text(manifest, encoding="utf-8")
        return src

    def test_the_manifest_is_not_mistaken_for_a_payload(self, tmp_path: Path) -> None:
        from jp_address_crosswalk.payload import load_payload_manifest
        from jp_address_crosswalk.pipeline import _accepted_payloads

        src = self._raw(tmp_path, "license:\n  name: x\n")
        fetched = _accepted_payloads(src, "abr", load_payload_manifest(src))
        assert set(fetched) == {"town_master"}

    def test_no_manifest_records_no_local_absolute_path(self, tmp_path: Path) -> None:
        """A builder's filesystem is not provenance and must not be shipped."""
        from jp_address_crosswalk.payload import PayloadManifest
        from jp_address_crosswalk.pipeline import _accepted_payloads

        src = self._raw(tmp_path)
        url = _accepted_payloads(src, "abr", PayloadManifest())["town_master"].url
        assert url == "local-payload:raw/abr/town_master.zip"
        assert "file://" not in url
        assert str(tmp_path) not in url

    def test_manifest_supplies_the_real_download_url(self, tmp_path: Path) -> None:
        from jp_address_crosswalk.payload import load_payload_manifest
        from jp_address_crosswalk.pipeline import _accepted_payloads

        src = self._raw(
            tmp_path,
            "resources:\n"
            "  town_master:\n"
            "    download_url: https://data.address-br.digital.go.jp/mt_town.zip\n"
            "    etag: 'W/\"abc\"'\n",
        )
        got = _accepted_payloads(src, "abr", load_payload_manifest(src))["town_master"]
        assert got.url == "https://data.address-br.digital.go.jp/mt_town.zip"
        assert got.etag == 'W/"abc"'


class TestPayloadManifest:
    def test_absence_is_not_an_error(self, tmp_path: Path) -> None:
        from jp_address_crosswalk.payload import load_payload_manifest

        manifest = load_payload_manifest(tmp_path)
        assert manifest.license_text_sha256 is None
        assert manifest.resources == {}

    def test_top_level_hash_is_shorthand_for_primary_terms(self, tmp_path: Path) -> None:
        from jp_address_crosswalk.payload import load_payload_manifest

        (tmp_path / "_payload.yml").write_text(
            "license:\n  text_sha256: deadbeef\n", encoding="utf-8"
        )
        manifest = load_payload_manifest(tmp_path)
        assert manifest.observed_text_sha256("primary_terms") == "deadbeef"
        assert manifest.observed_text_sha256("policy_page") is None

    def test_per_role_hashes_are_read(self, tmp_path: Path) -> None:
        from jp_address_crosswalk.payload import load_payload_manifest

        (tmp_path / "_payload.yml").write_text(
            "license:\n"
            "  artifacts:\n"
            "    policy_page:\n"
            "      text_sha256: cafe\n",
            encoding="utf-8",
        )
        assert load_payload_manifest(tmp_path).observed_text_sha256("policy_page") == "cafe"


class TestLicenceArtifactRows:
    """The evidence a redistributed release carries about its own terms."""

    SPEC = SOURCES["abr"]
    BASELINE = SPEC["license"]["artifacts"][0]["text_sha256"]

    @staticmethod
    def _rows(manifest):
        from jp_address_crosswalk.pipeline import _license_artifacts

        return {
            r["role"]: r
            for r in _license_artifacts("abr", TestLicenceArtifactRows.SPEC, manifest, [])
        }

    def test_baselines_ship_even_when_nothing_was_observed(self) -> None:
        from jp_address_crosswalk.payload import PayloadManifest

        rows = self._rows(PayloadManifest())
        assert rows["primary_terms"]["baseline_sha256"] == self.BASELINE
        assert rows["primary_terms"]["text_sha256"] is None
        assert rows["primary_terms"]["review_decision"] == "not_observed"

    def test_an_ungated_artifact_is_recorded_as_such(self) -> None:
        """The advertised CC BY entry is evidence of a discrepancy, not a gate."""
        from jp_address_crosswalk.payload import PayloadManifest

        assert self._rows(PayloadManifest())["advertised_license"][
            "review_decision"
        ] == "not_gated"

    def test_a_matching_observation_clears_the_gate(self) -> None:
        from jp_address_crosswalk.payload import PayloadManifest

        rows = self._rows(PayloadManifest(license_text_sha256=self.BASELINE))
        assert rows["primary_terms"]["review_decision"] == "baseline_match"
        assert rows["primary_terms"]["text_sha256"] == self.BASELINE

    def test_a_changed_terms_page_stops_the_release(self) -> None:
        from jp_address_crosswalk.errors import LicenseReviewRequired
        from jp_address_crosswalk.payload import PayloadManifest

        with pytest.raises(LicenseReviewRequired):
            self._rows(PayloadManifest(license_text_sha256="0" * 64))

    def test_artifact_ids_are_deterministic(self) -> None:
        from jp_address_crosswalk.payload import PayloadManifest

        first = self._rows(PayloadManifest())
        second = self._rows(PayloadManifest())
        assert [r["artifact_id"] for r in first.values()] == [
            r["artifact_id"] for r in second.values()
        ]

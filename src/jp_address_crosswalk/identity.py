"""address_id minting and the identity ledger (docs/IDENTITY_MODEL.md).

The rule this module exists to enforce: ABR's ``machiaza_id`` is a lookup key,
not an identity. A published ``address_id`` must survive ABR code corrections
and municipal re-coding, and must never be silently carried onto the wrong
entity.

Every reuse rule is narrow on purpose. Where evidence is thin the code mints a
new id and files a review item, because a wrong identity carry-over is worse
than an extra id (docs/POLICY.md §4).
"""

from __future__ import annotations

import gzip
import hashlib
from dataclasses import dataclass, field
from pathlib import Path

import polars as pl

from .errors import IdentityCollision
from .logging_setup import get_logger

log = get_logger(__name__)

ID_SCHEME = "jpa1"
ID_LENGTH = 20  # 4-char scheme prefix + 16-char base32 payload
# Crockford base32: no I, L, O, U — unambiguous when read or transcribed.
_ALPHABET = "0123456789abcdefghjkmnpqrstvwxyz"

LEDGER_COLUMNS = [
    "address_id",
    "genesis_lg_code",
    "genesis_machiaza_id",
    "current_lg_code",
    "current_machiaza_id",
    "genesis_normalized_name",
    "current_normalized_name",
    "first_observed_snapshot_id",
    "last_observed_snapshot_id",
    "entity_status",
    "retired_at",
    "retire_reason",
]


def _b32(data: bytes) -> str:
    value = int.from_bytes(data, "big")
    out = []
    for _ in range(16):
        out.append(_ALPHABET[value & 31])
        value >>= 5
    return "".join(reversed(out))


def mint_address_id(
    genesis_lg_code: str, genesis_machiaza_id: str, generation: int = 0
) -> str:
    """Deterministic id from the *genesis* natural key.

    Deterministic so a genesis build with no prior state reproduces every id
    (spec §46); opaque so consumers cannot parse a municipality out of it and be
    wrong after a merger.

    ``generation`` exists because minting is deterministic: if ABR retires a key
    and later reuses it, hashing the same key would hand the new town the
    retired entity's id — the precise identity corruption rule I6 is meant to
    prevent. Each reuse of a previously-seen key increments the generation, so
    the new entity gets a genuinely new id while the old one keeps its own.
    """
    key = f"abr:town:{genesis_lg_code}:{genesis_machiaza_id}"
    if generation:
        key = f"{key}#g{generation}"
    digest = hashlib.blake2s(key.encode("utf-8"), digest_size=10).digest()
    return ID_SCHEME + _b32(digest)


@dataclass
class LedgerRow:
    address_id: str
    genesis_lg_code: str
    genesis_machiaza_id: str
    current_lg_code: str
    current_machiaza_id: str
    genesis_normalized_name: str
    current_normalized_name: str
    first_observed_snapshot_id: str
    last_observed_snapshot_id: str
    entity_status: str = "active"
    retired_at: str | None = None
    retire_reason: str | None = None


@dataclass
class ResolutionResult:
    rows: list[LedgerRow] = field(default_factory=list)
    rule_by_address_id: dict[str, str] = field(default_factory=dict)
    lineage: list[dict] = field(default_factory=list)
    review_required: list[dict] = field(default_factory=list)
    minted: int = 0
    reused: int = 0
    retired: int = 0

    def counts_by_rule(self) -> dict[str, int]:
        out: dict[str, int] = {}
        for rule in self.rule_by_address_id.values():
            out[rule] = out.get(rule, 0) + 1
        return out


class IdentityLedger:
    """Committed memory of every address_id ever minted."""

    def __init__(self, rows: list[LedgerRow] | None = None) -> None:
        self.rows: list[LedgerRow] = rows or []

    # --------------------------------------------------------------- I/O

    @classmethod
    def load(cls, path: Path) -> IdentityLedger:
        if not path.exists():
            log.info("no identity ledger found; this is a genesis build", path=str(path))
            return cls([])
        with gzip.open(path, "rb") as fh:
            data = fh.read()
        df = pl.read_csv(data, schema_overrides={c: pl.Utf8 for c in LEDGER_COLUMNS})
        rows = [LedgerRow(**{c: r.get(c) for c in LEDGER_COLUMNS}) for r in df.to_dicts()]
        log.info("identity ledger loaded", path=str(path), rows=len(rows))
        return cls(rows)

    def save(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        df = pl.DataFrame(
            [
                {c: getattr(r, c) for c in LEDGER_COLUMNS}
                for r in sorted(self.rows, key=lambda r: r.address_id)
            ],
            schema={c: pl.Utf8 for c in LEDGER_COLUMNS},
        )
        # mtime=0 and an empty embedded filename so the gzip container is
        # byte-identical across builds (spec §46). gzip.open() cannot set these.
        payload = df.write_csv().encode("utf-8")
        # Written to a temp file and renamed, so an interrupted build can never
        # leave the ledger truncated. It is the project's only memory of every
        # address_id ever minted; a torn write would lose that permanently.
        tmp = path.with_suffix(path.suffix + ".tmp")
        with tmp.open("wb") as raw, gzip.GzipFile(
            filename="", mode="wb", fileobj=raw, mtime=0
        ) as fh:
            fh.write(payload)
        tmp.replace(path)
        log.info("identity ledger written", path=str(path), rows=len(self.rows))

    # -------------------------------------------------------- resolution

    def resolve(
        self,
        towns: pl.DataFrame,
        snapshot_id: str,
        municipality_lineage: dict[str, str] | None = None,
    ) -> ResolutionResult:
        """Apply rules I1..I6 in fixed order (docs/IDENTITY_MODEL.md §4).

        ``towns`` needs ``lg_code``, ``machiaza_id`` and ``full_name_normalized``
        and must already be sorted, so the outcome does not depend on row order.

        ``municipality_lineage`` maps ``old_lg_code -> new_lg_code`` and comes
        from ``overrides/municipality_lineage.yml``. Without an entry, I3 does
        not fire: a municipality-level statement is not town-level evidence.
        """
        attested = municipality_lineage or {}

        active_by_key: dict[tuple[str, str], LedgerRow] = {}
        retired_by_key: dict[tuple[str, str], LedgerRow] = {}
        for row in self.rows:
            key = (row.current_lg_code, row.current_machiaza_id)
            if row.entity_status == "active":
                active_by_key[key] = row
            else:
                retired_by_key.setdefault(key, row)

        # Keys present in this build. A code correction is a disappearance /
        # appearance pair, so a candidate whose old key is still present cannot
        # be the same entity under a new code.
        incoming_keys = {
            (r["lg_code"], r["machiaza_id"]) for r in towns.iter_rows(named=True)
        }

        by_lg_and_name: dict[tuple[str, str], list[LedgerRow]] = {}
        by_mid_and_name: dict[tuple[str, str], list[LedgerRow]] = {}
        for row in self.rows:
            if row.entity_status != "active":
                continue
            by_lg_and_name.setdefault(
                (row.current_lg_code, row.current_normalized_name), []
            ).append(row)
            by_mid_and_name.setdefault(
                (row.current_machiaza_id, row.current_normalized_name), []
            ).append(row)

        result = ResolutionResult()
        claimed: set[str] = set()
        minted_keys: dict[str, tuple[str, str]] = {}
        out_rows: list[LedgerRow] = []
        # Every id the ledger has ever handed out, including retired ones. A new
        # mint must not land on any of them.
        all_known_ids: set[str] = {r.address_id for r in self.rows}

        for rec in towns.iter_rows(named=True):
            lg, mid, name = rec["lg_code"], rec["machiaza_id"], rec["full_name_normalized"]
            row, rule, lineage = self._resolve_one(
                lg, mid, name, active_by_key, retired_by_key,
                by_lg_and_name, by_mid_and_name, incoming_keys, attested,
                claimed, result,
            )

            if row is None:
                generation = 0
                address_id = mint_address_id(lg, mid, generation)
                # Walk generations past any id the ledger already knows, so a
                # reused natural key never inherits a retired entity's identity.
                while address_id in all_known_ids or address_id in claimed:
                    generation += 1
                    if generation > 64:
                        raise IdentityCollision(
                            "could not mint a free address_id for this key",
                            key=(lg, mid),
                        )
                    address_id = mint_address_id(lg, mid, generation)
                if generation:
                    log.info(
                        "natural key reused after retirement; minted a new identity "
                        "rather than reviving the old one",
                        lg_code=lg, machiaza_id=mid, generation=generation,
                        address_id=address_id,
                    )
                previous = minted_keys.get(address_id)
                if previous is not None and previous != (lg, mid):
                    raise IdentityCollision(
                        "two distinct genesis keys produced the same address_id",
                        address_id=address_id, key_a=previous, key_b=(lg, mid),
                    )
                all_known_ids.add(address_id)
                minted_keys[address_id] = (lg, mid)
                row = LedgerRow(
                    address_id=address_id,
                    genesis_lg_code=lg, genesis_machiaza_id=mid,
                    current_lg_code=lg, current_machiaza_id=mid,
                    genesis_normalized_name=name, current_normalized_name=name,
                    first_observed_snapshot_id=snapshot_id,
                    last_observed_snapshot_id=snapshot_id,
                )
                result.minted += 1
            else:
                if row.current_normalized_name != name:
                    result.lineage.append({
                        "old_address_id": row.address_id,
                        "new_address_id": row.address_id,
                        "relation_type": "renamed",
                        "evidence": f"{row.current_normalized_name} -> {name}",
                        "evidence_source": "abr_town_master",
                    })
                    if rule == "I1":
                        rule = "I4"
                row.current_lg_code = lg
                row.current_machiaza_id = mid
                row.current_normalized_name = name
                row.last_observed_snapshot_id = snapshot_id
                result.reused += 1
                if lineage:
                    result.lineage.append(lineage)

            claimed.add(row.address_id)
            out_rows.append(row)
            result.rule_by_address_id[row.address_id] = rule

        # Anything active in the ledger but absent from this build retires; it is
        # never deleted (docs/IDENTITY_MODEL.md §5).
        for row in self.rows:
            if row.address_id in claimed:
                continue
            if row.entity_status == "active":
                row.entity_status = "retired"
                row.retire_reason = "absent_from_source"
                result.retired += 1
                result.lineage.append({
                    "old_address_id": row.address_id,
                    "new_address_id": None,
                    "relation_type": "retired",
                    "evidence": "no longer present in ABR town master",
                    "evidence_source": "abr_town_master",
                })
            out_rows.append(row)

        result.rows = out_rows
        self.rows = out_rows
        log.info(
            "identity resolved",
            minted=result.minted, reused=result.reused, retired=result.retired,
            review_required=len(result.review_required),
            by_rule=result.counts_by_rule(),
        )
        return result

    @staticmethod
    def _resolve_one(
        lg, mid, name,
        active_by_key, retired_by_key, by_lg_and_name, by_mid_and_name,
        incoming_keys, attested, claimed, result,
    ) -> tuple[LedgerRow | None, str, dict | None]:
        # --- I1: exact key match against an ACTIVE row only.
        # Retired rows are excluded: if ABR reuses a key after abolition,
        # inheriting the old id would hand a new town someone else's identity.
        row = active_by_key.get((lg, mid))
        if row is not None and row.address_id not in claimed:
            return row, "I1", None

        # --- I6: the key matches a RETIRED row. Never automatic.
        retired = retired_by_key.get((lg, mid))
        if retired is not None:
            result.review_required.append({
                "reason": "I6_reinstatement_candidate",
                "lg_code": lg, "machiaza_id": mid, "name": name,
                "candidates": [retired.address_id],
                "note": "retired key reappeared; confirm continuity before reusing the id",
            })
            return None, "I6", None

        # --- I2: ABR corrected machiaza_id within the same municipality.
        # Requires the current name to match AND the candidate's old key to have
        # disappeared, which is what distinguishes a correction from a new town
        # that merely shares a name.
        candidates = [
            r for r in by_lg_and_name.get((lg, name), [])
            if r.address_id not in claimed
            and r.current_machiaza_id != mid
            and (r.current_lg_code, r.current_machiaza_id) not in incoming_keys
        ]
        if len(candidates) == 1:
            row = candidates[0]
            return row, "I2", {
                "old_address_id": row.address_id,
                "new_address_id": row.address_id,
                "relation_type": "code_corrected",
                "evidence": f"machiaza_id {row.current_machiaza_id} -> {mid}",
                "evidence_source": "abr_town_master",
            }
        if len(candidates) > 1:
            result.review_required.append({
                "reason": "I2_ambiguous", "lg_code": lg, "machiaza_id": mid,
                "name": name, "candidates": [c.address_id for c in candidates],
            })
            return None, "I5", None

        # --- I3: municipality re-coded. Only with an attested lg_code transition.
        candidates = [
            r for r in by_mid_and_name.get((mid, name), [])
            if r.address_id not in claimed
            and r.current_lg_code != lg
            and attested.get(r.current_lg_code) == lg
            and (r.current_lg_code, r.current_machiaza_id) not in incoming_keys
        ]
        if len(candidates) == 1:
            row = candidates[0]
            return row, "I3", {
                "old_address_id": row.address_id,
                "new_address_id": row.address_id,
                "relation_type": "municipality_recoded",
                "evidence": f"lg_code {row.current_lg_code} -> {lg} (attested)",
                "evidence_source": "manual_override",
            }
        if len(candidates) > 1:
            result.review_required.append({
                "reason": "I3_ambiguous", "lg_code": lg, "machiaza_id": mid,
                "name": name, "candidates": [c.address_id for c in candidates],
            })

        # Unattested municipality re-code: report it, but do not infer identity.
        unattested = [
            r for r in by_mid_and_name.get((mid, name), [])
            if r.address_id not in claimed and r.current_lg_code != lg
            and attested.get(r.current_lg_code) != lg
        ]
        if unattested:
            result.review_required.append({
                "reason": "I3_unattested", "lg_code": lg, "machiaza_id": mid,
                "name": name, "candidates": [c.address_id for c in unattested],
                "note": "same machiaza_id and name under a different lg_code; "
                        "add an entry to overrides/municipality_lineage.yml to carry the id",
            })

        return None, "I5", None

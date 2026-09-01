"""Named, fail-closed error codes.

Every one of these stops the build and prevents a release. They are exceptions
rather than return values so that no caller can forget to check.
"""

from __future__ import annotations


class JpacError(Exception):
    """Base class. ``code`` is what CI reports and what the docs name."""

    code = "JPAC_ERROR"

    def __init__(self, message: str, **context: object) -> None:
        super().__init__(message)
        self.message = message
        self.context = context

    def __str__(self) -> str:
        if not self.context:
            return f"[{self.code}] {self.message}"
        detail = ", ".join(f"{k}={v!r}" for k, v in sorted(self.context.items()))
        return f"[{self.code}] {self.message} ({detail})"


class SourceFetchFailed(JpacError):
    code = "SOURCE_FETCH_FAILED"


class SourceSchemaChanged(JpacError):
    code = "SOURCE_SCHEMA_CHANGED"


class LicenseReviewRequired(JpacError):
    code = "LICENSE_REVIEW_REQUIRED"


class RowCountAnomaly(JpacError):
    code = "ROW_COUNT_ANOMALY"


class DuplicateKeyAnomaly(JpacError):
    code = "DUPLICATE_KEY_ANOMALY"


class UnmatchedRateSpike(JpacError):
    code = "UNMATCHED_RATE_SPIKE"


class AmbiguousRateSpike(JpacError):
    code = "AMBIGUOUS_RATE_SPIKE"


class RequiredSourceMissing(JpacError):
    code = "REQUIRED_SOURCE_MISSING"


class ChecksumMismatch(JpacError):
    code = "CHECKSUM_MISMATCH"


class ValidationFailed(JpacError):
    code = "VALIDATION_FAILED"


class DataLossSuspected(JpacError):
    code = "DATA_LOSS_SUSPECTED"


class IdentityCollision(JpacError):
    """Two distinct genesis keys hashed to the same address_id.

    Never a warning: a collision silently merges two real places.
    """

    code = "IDENTITY_COLLISION"


class UnsafeArchive(JpacError):
    code = "UNSAFE_ARCHIVE"

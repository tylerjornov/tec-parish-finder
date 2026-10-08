"""Small shared data classes and constants."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Optional

# ---- statuses (these exact words appear in the CSV files) --------------------------------
CORRECT = "CORRECT"
DISCREPANCY = "DISCREPANCY"
PARTIAL = "PARTIAL"
JSON_ONLY = "JSON_ONLY"
SITE_ONLY = "SITE_ONLY"
BOTH_MISSING = "BOTH_MISSING"
UNCLEAR = "UNCLEAR"
NOT_CHECKABLE = "NOT_CHECKABLE"

ALL_STATUSES = [CORRECT, DISCREPANCY, PARTIAL, JSON_ONLY, SITE_ONLY, BOTH_MISSING, UNCLEAR, NOT_CHECKABLE]
REVIEW_STATUSES = {DISCREPANCY, PARTIAL, JSON_ONLY, SITE_ONLY, UNCLEAR}

# ---- source types -----------------------------------------------------------------------
OWN_SITE = "own site"
FACEBOOK = "Facebook"
ASSET_MAP = "asset map"
SOURCE_TYPES = (OWN_SITE, FACEBOOK, ASSET_MAP)


@dataclass
class Candidate:
    """One thing found on a page (by a rule or by the model).

    `field` is a real config key for model answers (e.g. 'sunday_services'). For rule-based finds it is
    one of the generic kinds: '@phone', '@email', '@livestream', '@address', '@website', '@contact_person'.
    """

    field: str
    value: str
    source_type: str = OWN_SITE
    source_url: str = ""
    method: str = "rule"            # 'rule' or 'llm'
    confidence: str = "high"        # 'high' or 'low'
    evidence: str = ""
    role: Optional[str] = None      # 'church' / 'rector' / None  (phones and emails)
    page_rank: int = 99             # lower = better kind of page for this field (contact page, services page...)
    stale: bool = False             # Facebook post older than the allowed age
    in_footer: bool = False
    extra: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return asdict(self)

    @staticmethod
    def from_dict(d: dict) -> "Candidate":
        known = {k: d[k] for k in Candidate.__dataclass_fields__ if k in d}
        return Candidate(**known)


@dataclass
class SiteValue:
    """The merged answer for one field of one record, ready to compare against the JSON."""

    values: list[str] = field(default_factory=list)       # all acceptable values (phones: every candidate)
    best: str = ""                                          # the single value shown as site_value
    confidence: str = "high"
    source_type: str = ""
    source_url: str = ""
    method: str = ""
    evidence: str = ""
    notes: list[str] = field(default_factory=list)          # e.g. "Facebook says: ..."
    unclear_reason: str = ""                                # non-empty -> the field is UNCLEAR
    alt_values: list[str] = field(default_factory=list)     # phones/emails the site gives for the OTHER role
    conflict: bool = False                                   # the site itself gives different answers
    unlocated: bool = False                                  # shared website: value is not from this record's own location page
    derived_from: list[str] = field(default_factory=list)

    @property
    def present(self) -> bool:
        return bool(self.values) or bool(self.best)

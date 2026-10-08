"""Reads config.yaml and style_example.json and turns them into plain Python objects.

Nothing about your particular JSON file is hard-coded anywhere else: field names, which URL column to
use, what counts as "missing", and which fields exist all come from here.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

import yaml

from .normalizers import DEFAULT_MISSING_MARKERS, is_missing, collapse_ws

FIELD_TYPES = {"phone", "email", "url", "address", "person", "services", "list", "text", "rite"}


class ConfigError(Exception):
    """Raised with a plain-language message when config.yaml needs fixing."""


# Built-in defaults: anything you leave out of config.yaml falls back to these.
DEFAULTS: dict[str, Any] = {
    "input_json": "parishes.json",
    "output_dir": "output",
    "id_key": "id",
    "name_key": "name",
    "website_key": "website",
    "model": "qwen2.5:3b",
    "ollama_url": "http://localhost:11434",
    "ollama_num_ctx": 6144,
    "ollama_timeout_seconds": 300,
    "max_pages_per_site": 40,
    "max_crawl_depth": 3,
    "delay_seconds": 1.0,
    "fetch_concurrency": 4,
    "respect_robots": True,
    "use_browser_fallback": True,
    "min_text_chars_before_browser": 250,
    "time_format": "12h",
    "max_chars_per_chunk": 6000,
    "window_chars": 1500,
    "max_llm_pages_per_group": 4,
    "cache_max_age_days": 14,
    "user_agent": "Mozilla/5.0 (compatible; ParishInfoVerifier/1.0; personal low-volume parish data check; respects robots.txt)",
    "missing_markers": list(DEFAULT_MISSING_MARKERS),
    "exclude_patterns": [],
    "priority_keywords": [],
    "skip_domains": ["instagram.com", "twitter.com", "x.com", "linkedin.com", "tiktok.com", "youtube.com",
                     "yelp.com", "maps.google.com", "goo.gl", "pinterest.com"],
    "livestream_hosts": ["youtube.com", "youtu.be", "facebook.com", "fb.watch", "zoom.us", "boxcast.com",
                         "online.church", "vimeo.com", "livestream.com", "restream.io", "resi.io",
                         "churchstreaming.tv", "subsplash.com"],
    "special_sources": {},
    "source_priority": ["own_site", "facebook", "asset_map"],
    "uncheckable_fields": ["id", "lat", "lon", "asa", "notes", "verification_status", "date_last_updated"],
    "date_updated_key": "date_last_updated",
    "fields": [],
    "derived_fields": {},
    "groups": {},
    "style_example_file": "style_example.json",
}

SPECIAL_DEFAULTS = {
    "facebook.com": {
        "mode": "best_effort", "max_pages": 5, "delay_seconds": 5,
        "llm_groups": ["services", "community", "identity"], "max_post_age_days": 365,
    },
    "episcopalassetmap.org": {
        "mode": "fetch", "delay_seconds": 2,
        "never_paths": [r"^/maps?(/|$|\?)", r"^/search", r"^/user", r"^/admin", r"^/node/[^/]+/edit"],
        "llm_groups": ["services", "community"],
    },
}


@dataclass
class FieldSpec:
    key: str
    type: str
    description: str = ""
    group: str = "misc"
    role: Optional[str] = None          # phones/emails: 'church' or 'rector'
    format: str = ""                    # one-line style hint shown to the model
    address_part: Optional[str] = None  # 'city' or 'state': copy that part of the address instead of asking the model
    default_day: Optional[str] = None   # services: day to assume when none is written ('sun' for sunday_services)
    url_kind: Optional[str] = None      # type url: 'website' or 'livestream'
    derived: bool = False
    checkable: bool = True


@dataclass
class Config:
    raw: dict
    base_dir: Path
    input_json: Path
    output_dir: Path
    id_key: str
    name_key: str
    website_key: str
    model: str
    ollama_url: str
    ollama_num_ctx: int
    ollama_timeout_seconds: int
    max_pages_per_site: int
    max_crawl_depth: int
    delay_seconds: float
    fetch_concurrency: int
    respect_robots: bool
    use_browser_fallback: bool
    min_text_chars_before_browser: int
    time_format: str
    max_chars_per_chunk: int
    window_chars: int
    max_llm_pages_per_group: int
    cache_max_age_days: int
    user_agent: str
    missing_markers: list
    exclude_regexes: list
    priority_keywords: list
    skip_domains: list
    livestream_hosts: list
    special_sources: dict
    source_priority: list
    uncheckable_fields: list
    date_updated_key: str
    fields: list
    derived_fields: dict
    groups: dict
    style_examples: dict = field(default_factory=dict)
    facebook_enabled: bool = True

    # ---- convenience --------------------------------------------------------------------
    def get_field(self, key: str) -> Optional[FieldSpec]:
        for f in self.fields:
            if f.key == key:
                return f
        return None

    def fields_by_group(self) -> dict[str, list[FieldSpec]]:
        out: dict[str, list[FieldSpec]] = {}
        for f in self.fields:
            if f.checkable and not f.derived:
                out.setdefault(f.group, []).append(f)
        return out

    @property
    def cache_dir(self) -> Path:
        return self.output_dir / "cache"

    def special(self, domain_key: str) -> dict:
        return self.special_sources.get(domain_key, {})

    def is_missing(self, value) -> bool:
        return is_missing(value, self.missing_markers)

    def priority_rank(self, source_type_key: str) -> int:
        """0 = best.  source_type_key is one of own_site / facebook / asset_map."""
        try:
            return self.source_priority.index(source_type_key)
        except ValueError:
            return len(self.source_priority)


def _deep_merge(base: dict, extra: dict) -> dict:
    out = dict(base)
    for k, v in (extra or {}).items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = _deep_merge(out[k], v)
        else:
            out[k] = v
    return out


def _default_role(key: str) -> str:
    return "rector" if re.search(r"rector|vicar|priest|clergy|pastor|dean", key, re.I) else "church"


def load_config(path: str | Path, overrides: Optional[dict] = None) -> Config:
    path = Path(path).expanduser()
    if not path.exists():
        raise ConfigError(f"I can't find the settings file: {path}\nMake sure config.yaml is in the same folder as scraper.py.")
    try:
        user = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except yaml.YAMLError as exc:
        raise ConfigError(
            "config.yaml has a typing mistake that I can't read. The most common causes are a missing colon, "
            "a missing quote mark, or lines indented with TAB instead of spaces.\n"
            f"Details: {exc}"
        )
    if not isinstance(user, dict):
        raise ConfigError("config.yaml should be a list of 'setting: value' lines, but it looks empty or wrong.")
    raw = dict(DEFAULTS)
    for k, v in user.items():
        raw[k] = v
    if overrides:
        raw.update({k: v for k, v in overrides.items() if v is not None})
    base_dir = path.resolve().parent

    # ---- special sources: merge user settings over built-in defaults ----
    special = _deep_merge(SPECIAL_DEFAULTS, user.get("special_sources") or {})

    # ---- fields ----
    fields_raw = raw.get("fields") or []
    if not fields_raw:
        raise ConfigError("config.yaml has no 'fields:' table, so I don't know what to check. See the comments in config.yaml.")
    fields: list[FieldSpec] = []
    seen = set()
    uncheck = set(raw.get("uncheckable_fields") or [])
    for i, row in enumerate(fields_raw, start=1):
        if not isinstance(row, dict) or "key" not in row:
            raise ConfigError(f"In config.yaml, fields entry #{i} needs a 'key:' line.")
        key = str(row["key"])
        ftype = str(row.get("type", "text")).strip().lower()
        if ftype not in FIELD_TYPES:
            raise ConfigError(
                f"In config.yaml, field '{key}' has type '{ftype}', which I don't know. "
                f"Allowed types: {', '.join(sorted(FIELD_TYPES))}."
            )
        if key in seen:
            raise ConfigError(f"In config.yaml, the field '{key}' is listed twice.")
        seen.add(key)
        spec = FieldSpec(
            key=key,
            type=ftype,
            description=collapse_ws(str(row.get("description", ""))),
            group=str(row.get("group", "misc")),
            role=row.get("role") or (_default_role(key) if ftype in ("phone", "email")
                                     else ("rector" if ftype == "person" and re.search(r"rector|vicar|priest|pastor|dean|clergy", key, re.I) else None)),
            format=collapse_ws(str(row.get("format", ""))),
            address_part=row.get("address_part"),
            default_day=row.get("default_day"),
            url_kind=row.get("url_kind"),
            derived=key in (raw.get("derived_fields") or {}),
            checkable=key not in uncheck,
        )
        if spec.type == "services" and spec.default_day is None and "sunday" in key.lower():
            spec.default_day = "sun"
        if spec.type == "url" and spec.url_kind is None:
            if key == raw["website_key"]:
                spec.url_kind = "website"
            elif re.search(r"stream|live", key, re.I):
                spec.url_kind = "livestream"
        fields.append(spec)
    # Make sure the website column is always checked even if the user forgot to list it.
    if raw["website_key"] not in seen:
        fields.append(FieldSpec(key=raw["website_key"], type="url", description="The church's website address",
                                group="contact", url_kind="website", checkable=raw["website_key"] not in uncheck))

    # ---- regexes ----
    regexes = []
    for pat in raw.get("exclude_patterns") or []:
        try:
            regexes.append(re.compile(pat, re.I))
        except re.error as exc:
            raise ConfigError(f"In config.yaml, exclude_patterns has a broken pattern: {pat!r} ({exc}).")

    tf = str(raw.get("time_format", "12h")).lower()
    if tf not in ("12h", "24h"):
        raise ConfigError("In config.yaml, time_format must be 12h or 24h.")

    for key in ("max_pages_per_site", "fetch_concurrency", "max_chars_per_chunk", "window_chars",
                "max_llm_pages_per_group", "ollama_num_ctx", "max_crawl_depth"):
        try:
            raw[key] = int(raw[key])
        except (TypeError, ValueError):
            raise ConfigError(f"In config.yaml, '{key}' must be a whole number (you have {raw[key]!r}).")
    raw["delay_seconds"] = float(raw["delay_seconds"])
    raw["fetch_concurrency"] = max(1, raw["fetch_concurrency"])
    raw["max_pages_per_site"] = max(1, raw["max_pages_per_site"])

    # Facebook can be switched off from config (mode: skip) or with --no-facebook.
    fb_mode = str((special.get("facebook.com") or {}).get("mode", "best_effort")).lower()

    cfg = Config(
        raw=raw,
        base_dir=base_dir,
        input_json=_resolve(base_dir, raw["input_json"]),
        output_dir=_resolve(base_dir, raw["output_dir"]),
        id_key=raw["id_key"],
        name_key=raw["name_key"],
        website_key=raw["website_key"],
        model=str(raw["model"]),
        ollama_url=str(raw["ollama_url"]).rstrip("/"),
        ollama_num_ctx=raw["ollama_num_ctx"],
        ollama_timeout_seconds=int(raw["ollama_timeout_seconds"]),
        max_pages_per_site=raw["max_pages_per_site"],
        max_crawl_depth=raw["max_crawl_depth"],
        delay_seconds=raw["delay_seconds"],
        fetch_concurrency=raw["fetch_concurrency"],
        respect_robots=bool(raw["respect_robots"]),
        use_browser_fallback=bool(raw["use_browser_fallback"]),
        min_text_chars_before_browser=int(raw["min_text_chars_before_browser"]),
        time_format=tf,
        max_chars_per_chunk=raw["max_chars_per_chunk"],
        window_chars=raw["window_chars"],
        max_llm_pages_per_group=raw["max_llm_pages_per_group"],
        cache_max_age_days=int(raw["cache_max_age_days"]),
        user_agent=str(raw["user_agent"]),
        missing_markers=list(raw["missing_markers"] if raw["missing_markers"] is not None else DEFAULT_MISSING_MARKERS),
        exclude_regexes=regexes,
        priority_keywords=[str(k).lower() for k in (raw.get("priority_keywords") or [])],
        skip_domains=[str(d).lower() for d in (raw.get("skip_domains") or [])],
        livestream_hosts=[str(d).lower() for d in (raw.get("livestream_hosts") or [])],
        special_sources=special,
        source_priority=[str(s) for s in raw["source_priority"]],
        uncheckable_fields=list(raw.get("uncheckable_fields") or []),
        date_updated_key=str(raw.get("date_updated_key") or ""),
        fields=fields,
        derived_fields=raw.get("derived_fields") or {},
        groups=raw.get("groups") or {},
        facebook_enabled=(fb_mode != "skip"),
    )
    cfg.style_examples = load_style_examples(_resolve(base_dir, raw["style_example_file"]))
    return cfg


def _resolve(base: Path, p: str) -> Path:
    pp = Path(str(p)).expanduser()
    return pp if pp.is_absolute() else (base / pp)


def load_style_examples(path: Path) -> dict[str, list[str]]:
    """style_example.json -> {field_key: [example, example...]} (gold-standard record first)."""
    out: dict[str, list[str]] = {}
    if not path.exists():
        return out
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return out
    gold = data.get("gold_record") or {}
    for k, v in gold.items():
        if isinstance(v, str) and v.strip():
            out.setdefault(k, []).append(v.strip())
    for k, vals in (data.get("supplemental") or {}).items():
        for v in vals if isinstance(vals, list) else [vals]:
            if isinstance(v, str) and v.strip():
                out.setdefault(k, []).append(v.strip())
    return out

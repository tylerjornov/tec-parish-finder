"""Extraction for ONE source (a church website, a Facebook page, or an Asset Map listing).

The result is a plain dictionary (so it can be saved in the progress database):
    status ('ok' | 'failed' | 'blocked'), reason, pages_crawled, cap_hit, candidates [...], issues [...], notes [...]
Every value found is kept as a Candidate together with where it came from and the exact evidence.
Merging candidates into one answer per record happens later (merge.py).
"""

from __future__ import annotations

import hashlib
import json
import logging
import threading
import time
from collections import defaultdict
from dataclasses import dataclass
from datetime import date
from typing import Optional

from . import models as M
from .assetmap import AssetMapListing, listing_candidates, listing_to_doc
from .cleaner import PageDoc
from .config import Config, FieldSpec
from .crawler import CrawlResult
from .facebook import FacebookCrawl, assess_recency, fb_rule_candidates
from .llm import (
    PROMPT_VERSION, GroupInfo, OllamaClient, build_chunk, disambiguate_contacts, get_group_info, keyword_hits,
    _keyword_regex, run_group_on_chunk,
)
from .models import Candidate
from .normalizers import collapse_ws, norm_for_quote_check
from .rules import extract_rule_candidates, page_rank
from .state import State

log = logging.getLogger("parishcheck")

LLM_TYPES = {"person", "services", "list", "text"}
# Bump this when the extraction/verification rules change, so saved results from older rules are redone.
# (Saved model answers are kept, so redoing is quick.)
EXTRACT_VERSION = "7"


@dataclass
class ExtractContext:
    cfg: Config
    client: Optional[OllamaClient]
    state: State
    today: date
    stop: threading.Event


def config_fingerprint(cfg: Config) -> str:
    """Changes whenever a setting that affects extraction changes, so stale results are redone."""
    blob = json.dumps({
        "v": PROMPT_VERSION, "x": EXTRACT_VERSION, "model": cfg.model, "max_pages": cfg.max_pages_per_site, "time": cfg.time_format,
        "chunk": cfg.max_chars_per_chunk, "window": cfg.window_chars, "llm_pages": cfg.max_llm_pages_per_group,
        "ctx": cfg.ollama_num_ctx,
        "fields": [(f.key, f.type, f.group, f.description, f.format, f.role, f.address_part, f.default_day, f.url_kind)
                   for f in cfg.fields],
        "groups": cfg.groups, "priority": cfg.priority_keywords,
        "special": {k: {kk: vv for kk, vv in v.items() if kk != "mode"} for k, v in cfg.special_sources.items()},
    }, sort_keys=True, default=str)
    return hashlib.sha1(blob.encode("utf-8")).hexdigest()


def llm_specs_by_group(cfg: Config, allowed_groups: Optional[list] = None) -> dict[str, list[FieldSpec]]:
    out: dict[str, list[FieldSpec]] = {}
    for f in cfg.fields:
        if not f.checkable or f.derived or f.address_part or f.type not in LLM_TYPES:
            continue
        if allowed_groups is not None and f.group not in allowed_groups:
            continue
        out.setdefault(f.group, []).append(f)
    return out


def new_result(kind: str, key: str, url: str) -> dict:
    return {
        "key": key, "kind": kind, "url": url, "status": "ok", "reason": "", "pages_crawled": 0, "cap_hit": False,
        "sitemap_used": False, "home_final_url": "", "candidates": [], "issues": [], "notes": [], "pdfs": [],
        "page_urls": [], "llm_calls": 0, "llm_cache_hits": 0, "elapsed": 0.0, "browser_pages": 0,
    }


# --------------------------------------------------------------------------------------
# The model part, shared by all three kinds of source
# --------------------------------------------------------------------------------------
def run_llm_groups(pages: list[PageDoc], source_type: str, ctx: ExtractContext, allowed_groups: Optional[list],
                   result: dict, page_notes: Optional[dict] = None) -> list[Candidate]:
    """For each field group: pick the most promising pages, send small text windows, verify the answers."""
    cfg = ctx.cfg
    if ctx.client is None:
        return []
    out: list[Candidate] = []
    by_group = llm_specs_by_group(cfg, allowed_groups)
    # Skip pages whose text duplicates an earlier page.
    unique, seen_hash = [], set()
    for doc in pages:
        h = hashlib.sha1(norm_for_quote_check(doc.text[:3000]).encode()).hexdigest()
        if h in seen_hash or len(doc.text) < 40:
            continue
        seen_hash.add(h)
        unique.append(doc)
    norms: dict[int, str] = {}

    def norm_of(doc: PageDoc) -> str:
        k = id(doc)
        if k not in norms:
            norms[k] = norm_for_quote_check(doc.text)
        return norms[k]

    for group, specs in by_group.items():
        info: GroupInfo = get_group_info(cfg, group, specs)
        rx = _keyword_regex(info.keywords)
        ranked = []
        for doc in unique:
            # Look for keywords in the page's own content, not in the menu/footer text that repeats on every page.
            hits = keyword_hits(doc.main_text or doc.text, rx)
            if hits:
                ranked.append((page_rank(doc.final_url or doc.url, doc.title, info.url_hints), -len(hits), id(doc), doc, hits))
        ranked.sort(key=lambda r: (r[0], r[1]))
        chosen = ranked[: info.max_pages or cfg.max_llm_pages_per_group]
        if not chosen:
            continue
        group_found: dict[str, bool] = {}
        seen_chunks: set[str] = set()
        for rank_value, _, _, doc, hits in chosen:
            if ctx.stop.is_set():
                raise KeyboardInterrupt()
            chunk = build_chunk(doc.text, hits, cfg.window_chars, cfg.max_chars_per_chunk)
            if not chunk:
                continue
            chunk_hash = hashlib.sha1(norm_for_quote_check(chunk).encode()).hexdigest()
            if chunk_hash in seen_chunks:        # exactly the same text as an earlier page: no need to ask again
                continue
            seen_chunks.add(chunk_hash)
            url = doc.final_url or doc.url
            log.info("    asking the model about '%s' on %s", group, _short(url))
            oc = run_group_on_chunk(
                ctx.client, cfg, info, specs, url=url, title=doc.title, chunk=chunk,
                page_text=doc.text, page_norm=norm_of(doc), source_type=source_type, page_rank_value=rank_value,
                today=ctx.today, cache_get=ctx.state.llm_get, cache_put=ctx.state.llm_put,
                extra_note=(page_notes or {}).get(id(doc), ""),
            )
            if oc.called_model:
                result["llm_calls"] += 1
            if oc.from_cache:
                result["llm_cache_hits"] += 1
            for field_key, kind, text in oc.issues:
                result["issues"].append([field_key, kind, text, url])
            for c in oc.candidates:
                out.append(c)
                if c.confidence == "high":
                    group_found[c.field] = True
            if info.early_stop and all(group_found.get(s.key) for s in specs):
                break
    return out


def _short(url: str) -> str:
    return url if len(url) <= 70 else url[:67] + "..."


# --------------------------------------------------------------------------------------
# Optional model help for look-alike phone numbers and emails
# --------------------------------------------------------------------------------------
def disambiguate_leftovers(cands: list[Candidate], ctx: ExtractContext, source_type: str) -> list[Candidate]:
    if ctx.client is None:
        return []
    extra: list[Candidate] = []
    for kind, label in (("@phone", "phone number"), ("@email", "email address")):
        groups: dict[str, list[Candidate]] = defaultdict(list)
        for c in cands:
            if c.field == kind:
                groups[c.value.lower()].append(c)
        if not groups:
            continue
        labelled_church = any(c.role == "church" for g in groups.values() for c in g)
        unlabelled = [g for g in groups.values() if all(c.role is None for c in g)]
        if labelled_church or len(unlabelled) < 2:
            continue
        unlabelled.sort(key=lambda g: -len({c.source_url for c in g}))
        top = unlabelled[:6]
        options = [(g[0].value, g[0].evidence) for g in top]
        try:
            res = disambiguate_contacts(ctx.client, label, options, ctx.today)
        except KeyboardInterrupt:
            raise
        except Exception as exc:
            log.debug("contact disambiguation failed: %s", exc)
            continue
        for role_key, role in (("church", "church"), ("rector", "rector")):
            idx = res.get(role_key)
            if idx is not None:
                g0 = top[idx][0]
                extra.append(Candidate(field=kind, value=g0.value, source_type=source_type, source_url=g0.source_url,
                                       method="llm", confidence="low", evidence=g0.evidence, role=role,
                                       page_rank=g0.page_rank, in_footer=g0.in_footer))
    return extra


# --------------------------------------------------------------------------------------
# Church's own website
# --------------------------------------------------------------------------------------
def extract_own_site(key: str, url: str, crawl: CrawlResult, ctx: ExtractContext) -> dict:
    started = time.monotonic()
    res = new_result(M.OWN_SITE, key, url)
    res["status"], res["reason"] = crawl.status, crawl.reason
    res["cap_hit"], res["sitemap_used"] = crawl.cap_hit, crawl.sitemap_used
    res["home_final_url"], res["pdfs"] = crawl.home_final_url, crawl.pdfs
    res["pages_crawled"] = len(crawl.pages)
    res["page_urls"] = [(p.final_url or p.url) for p in crawl.pages]
    res["browser_pages"] = crawl.used_browser_pages
    if crawl.errors:
        res["notes"].append(f"{len(crawl.errors)} page(s) could not be downloaded (first: {crawl.errors[0]})")
    if crawl.robots_blocked:
        res["notes"].append(f"{crawl.robots_blocked} page(s) skipped because robots.txt asks robots to stay away")
    if crawl.status != "ok":
        res["elapsed"] = time.monotonic() - started
        return res

    cands: list[Candidate] = []
    for doc in crawl.pages:
        cands.extend(extract_rule_candidates(doc, ctx.cfg.livestream_hosts, M.OWN_SITE))
    cands.append(Candidate(field="@website", value=crawl.home_final_url or url, source_type=M.OWN_SITE,
                           source_url=crawl.home_final_url or url, method="rule", confidence="high",
                           evidence=f"the site address resolves to {crawl.home_final_url or url}", page_rank=0))
    cands.extend(disambiguate_leftovers(cands, ctx, M.OWN_SITE))
    cands.extend(run_llm_groups(crawl.pages, M.OWN_SITE, ctx, None, res))
    res["candidates"] = [c.to_dict() for c in cands]
    res["elapsed"] = time.monotonic() - started
    return res


# --------------------------------------------------------------------------------------
# Episcopal Asset Map listing
# --------------------------------------------------------------------------------------
def extract_asset_map(key: str, url: str, listing: Optional[AssetMapListing], fetch_error: str,
                      ctx: ExtractContext) -> dict:
    started = time.monotonic()
    res = new_result(M.ASSET_MAP, key, url)
    if listing is None or not listing.ok:
        res["status"] = "failed"
        res["reason"] = f"the Asset Map page could not be read ({fetch_error or 'no listing found on the page'})"
        return res
    res["pages_crawled"] = 1
    res["page_urls"] = [url]
    cands, notes = listing_candidates(listing, url)
    res["notes"].extend(notes)
    doc = listing_to_doc(listing)
    groups = ctx.cfg.special("episcopalassetmap.org").get("llm_groups", ["services", "community"])
    cands.extend(run_llm_groups([doc], M.ASSET_MAP, ctx, groups, res))
    res["candidates"] = [c.to_dict() for c in cands]
    res["elapsed"] = time.monotonic() - started
    return res


# --------------------------------------------------------------------------------------
# Facebook
# --------------------------------------------------------------------------------------
_POST_NOTE = ("This text comes from a Facebook page. Posts may be old. Ignore any post that is clearly older than "
              "12 months. If you cannot tell how old a post is, use confidence \"low\".")


def extract_facebook(key: str, url: str, crawl: FacebookCrawl, ctx: ExtractContext) -> dict:
    started = time.monotonic()
    res = new_result(M.FACEBOOK, key, url)
    res["status"], res["reason"] = crawl.status, crawl.reason
    res["notes"].extend(crawl.notes)
    if crawl.status != "ok":
        return res
    res["pages_crawled"] = len(crawl.pages)
    res["page_urls"] = [(d.final_url or d.url) for d, _ in crawl.pages]
    cands: list[Candidate] = []
    docs, notes_by_doc = [], {}
    for doc, kind in crawl.pages:
        cands.extend(fb_rule_candidates(doc))
        docs.append(doc)
        if kind == "posts":
            notes_by_doc[id(doc)] = _POST_NOTE
    spec = ctx.cfg.special("facebook.com")
    groups = spec.get("llm_groups", ["services", "community", "identity"])
    max_age = int(spec.get("max_post_age_days", 365))
    llm_cands = run_llm_groups(docs, M.FACEBOOK, ctx, groups, res, page_notes=notes_by_doc)
    kinds = {(d.final_url or d.url): k for d, k in crawl.pages}
    texts = {(d.final_url or d.url): d.text for d, _ in crawl.pages}
    for c in llm_cands:
        if kinds.get(c.source_url) == "posts":
            window = _window_around(texts.get(c.source_url, ""), c.evidence)
            verdict = assess_recency(window, ctx.today, max_age)
            if verdict == "stale":
                c.stale = True
                c.confidence = "low"
            elif verdict == "unknown":
                c.confidence = "low"
        cands.append(c)
    res["candidates"] = [c.to_dict() for c in cands]
    res["elapsed"] = time.monotonic() - started
    return res


def _window_around(text: str, evidence: str, radius: int = 500) -> str:
    ev = collapse_ws(evidence)[:40]
    if ev:
        i = text.lower().find(ev.lower())
        if i >= 0:
            return text[max(0, i - radius): i + len(ev) + radius]
    return text[: 2 * radius]

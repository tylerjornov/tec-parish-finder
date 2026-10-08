"""The whole run, start to finish: load -> plan -> (dry run | crawl + extract) -> compare -> write files."""

from __future__ import annotations

import json
import logging
import sys
import threading
import time
from collections import Counter, deque
from concurrent.futures import ThreadPoolExecutor, wait, FIRST_COMPLETED
from dataclasses import dataclass, field
from datetime import date, datetime
from pathlib import Path
from typing import Optional

from . import models as M
from .assetmap import AssetMapListing, parse_listing
from .config import Config, ConfigError
from .crawler import CrawlResult, crawl_site
from .extract import (
    ExtractContext, config_fingerprint, extract_asset_map, extract_facebook, extract_own_site, llm_specs_by_group,
    new_result,
)
from .facebook import FB_NOTICE, WALL_REASON, crawl_facebook
from .fetcher import Fetcher, StopRequested
from .llm import (
    GroupInfo, OllamaClient, OllamaUnavailable, build_chunk, get_group_info, run_group_on_chunk, _keyword_regex,
    keyword_hits,
)
from .merge import RecordView, build_view
from .normalizers import collapse_ws, host_of, normalize_url
from .report import (
    Row, build_proposed, compare_record, summarize_record, write_pdfs, write_proposed, write_raw,
    write_report_csvs, write_summary_csv,
)
from .state import State
from .urls import (
    KIND_ASSET_MAP, KIND_FACEBOOK, KIND_INVALID, KIND_MISSING, KIND_OWN, KIND_UNSUPPORTED, classify_url,
    facebook_base_url, facebook_page_key,
)

log = logging.getLogger("parishcheck")


# --------------------------------------------------------------------------------------
# Logging
# --------------------------------------------------------------------------------------
def setup_logging(output_dir: Path) -> None:
    output_dir.mkdir(parents=True, exist_ok=True)
    root = logging.getLogger("parishcheck")
    root.setLevel(logging.DEBUG)
    for h in list(root.handlers):
        root.removeHandler(h)
    fh = logging.FileHandler(output_dir / "run.log", encoding="utf-8")
    fh.setLevel(logging.DEBUG)
    fh.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
    ch = logging.StreamHandler(sys.stdout)
    ch.setLevel(logging.INFO)
    ch.setFormatter(logging.Formatter("%(message)s"))
    root.addHandler(fh)
    root.addHandler(ch)
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)
    logging.getLogger("trafilatura").setLevel(logging.ERROR)
    logging.getLogger("htmldate").setLevel(logging.ERROR)
    root.propagate = False


def say(msg: str = "") -> None:
    log.info(msg)


# --------------------------------------------------------------------------------------
# Loading
# --------------------------------------------------------------------------------------
def load_records(cfg: Config) -> list[dict]:
    path = cfg.input_json
    if not path.exists():
        raise ConfigError(
            f"I can't find your parish file: {path}\n"
            "Check the 'input_json' line in config.yaml: it must point to your parishes.json file.")
    try:
        data = json.loads(path.read_text(encoding="utf-8-sig"))
    except json.JSONDecodeError as exc:
        raise ConfigError(f"Your parish file {path.name} is not valid JSON (line {exc.lineno}, column {exc.colno}): {exc.msg}")
    except UnicodeDecodeError:
        raise ConfigError(f"Your parish file {path.name} is not saved as UTF-8 text. Re-save it as UTF-8.")
    if not isinstance(data, list) or not all(isinstance(r, dict) for r in data):
        raise ConfigError(f"{path.name} should be a JSON array (a list) of parish objects, like [ {{...}}, {{...}} ].")
    return data


def record_id(cfg: Config, rec: dict, idx: int) -> str:
    v = rec.get(cfg.id_key)
    return str(v) if v not in (None, "") else f"row-{idx + 1}"


def record_name(cfg: Config, rec: dict) -> str:
    v = rec.get(cfg.name_key)
    return str(v) if v not in (None, "") else ""


# --------------------------------------------------------------------------------------
# Planning
# --------------------------------------------------------------------------------------
@dataclass
class Source:
    key: str
    kind: str                       # M.OWN_SITE / M.FACEBOOK / M.ASSET_MAP
    url: str
    record_idx: list = field(default_factory=list)
    discovered_from: str = ""
    page_key: str = ""
    disabled_reason: str = ""


@dataclass
class RecordPlan:
    idx: int
    rid: str
    name: str
    url: str
    url_kind: str
    source_keys: list = field(default_factory=list)
    pre_failures: list = field(default_factory=list)
    notes: list = field(default_factory=list)
    discovered_site: str = ""


@dataclass
class Plan:
    records: list = field(default_factory=list)                 # list[RecordPlan]
    sources: dict = field(default_factory=dict)                  # key -> Source (insertion order = crawl order)
    am_pages: dict = field(default_factory=dict)                 # key -> (AssetMapListing|None, error)


def select_indices(records: list[dict], cfg: Config, only: Optional[str], limit_sites: Optional[int]) -> list[int]:
    idxs = list(range(len(records)))
    if only:
        needle = only.casefold()
        idxs = [i for i in idxs if needle in record_id(cfg, records[i], i).casefold()
                or needle in record_name(cfg, records[i]).casefold()]
    if limit_sites:
        chosen, seen = [], set()
        for i in idxs:
            kind, key = _preclassify(cfg, records[i])
            tag = key or f"row-{i}"
            if tag not in seen and len(seen) >= limit_sites:
                continue
            seen.add(tag)
            chosen.append(i)
        idxs = chosen
    return idxs


def _preclassify(cfg: Config, rec: dict) -> tuple[str, str]:
    raw = rec.get(cfg.website_key)
    if cfg.is_missing(raw) or not isinstance(raw, str):
        return KIND_MISSING, ""
    kind = classify_url(raw, cfg.skip_domains)
    n = normalize_url(raw)
    if kind == KIND_OWN:
        return kind, "site:" + host_of(n)
    if kind == KIND_FACEBOOK:
        pk = facebook_page_key(n)
        return (kind, "fb:" + pk) if pk else (KIND_UNSUPPORTED, "")
    if kind == KIND_ASSET_MAP:
        return kind, "am:" + n
    return kind, ""


def build_plan(records: list[dict], idxs: list[int], cfg: Config, fetcher: Fetcher, stop: threading.Event,
               no_facebook: bool) -> Plan:
    plan = Plan()
    am_to_fetch: list[Source] = []
    for i in idxs:
        rec = records[i]
        rp = RecordPlan(i, record_id(cfg, rec, i), record_name(cfg, rec), _fmt_url(rec.get(cfg.website_key)), KIND_MISSING)
        kind, key = _preclassify(cfg, rec)
        rp.url_kind = kind
        raw = rec.get(cfg.website_key)
        url = normalize_url(raw) if isinstance(raw, str) else ""
        if kind == KIND_MISSING:
            rp.pre_failures.append(_failed(M.OWN_SITE, "", "there is no website address in the JSON for this record"))
        elif kind == KIND_INVALID:
            rp.pre_failures.append(_failed(M.OWN_SITE, str(raw), "the website address in the JSON is not a valid web address"))
        elif kind == KIND_UNSUPPORTED:
            rp.pre_failures.append(_failed(M.OWN_SITE, url or str(raw),
                                           "the address is a social-media or map page that this tool cannot read"))
        elif kind == KIND_OWN:
            src = plan.sources.setdefault(key, Source(key, M.OWN_SITE, url))
            src.record_idx.append(i)
            rp.source_keys.append(key)
        elif kind == KIND_FACEBOOK:
            src = plan.sources.setdefault(key, Source(key, M.FACEBOOK, url, page_key=key[3:]))
            if no_facebook or not cfg.facebook_enabled:
                src.disabled_reason = "Facebook is switched off (--no-facebook or mode: skip in config.yaml)"
            src.record_idx.append(i)
            rp.source_keys.append(key)
        elif kind == KIND_ASSET_MAP:
            src = plan.sources.setdefault(key, Source(key, M.ASSET_MAP, url))
            src.record_idx.append(i)
            rp.source_keys.append(key)
            if all(src.key != other.key for other in am_to_fetch):
                am_to_fetch.append(src)
        plan.records.append(rp)

    # ---- Asset Map pages: fetch now to find each church's own website ("discovered site") ----
    if am_to_fetch:
        say(f"Reading {len(am_to_fetch)} Episcopal Asset Map page(s) to find each church's own website...")
    for src in am_to_fetch:
        if stop.is_set():
            raise StopRequested()
        res = fetcher.get(src.url)
        listing, err = None, ""
        if res.ok and res.text:
            listing = parse_listing(res.text, src.url)
            if not listing.ok:
                err = "the page has no church listing on it"
        else:
            err = res.error or f"HTTP {res.status}"
        plan.am_pages[src.key] = (listing, err)
        if listing is not None and listing.ok and listing.website:
            kind = classify_url(listing.website, cfg.skip_domains)
            if kind == KIND_OWN:
                skey = "site:" + host_of(listing.website)
                site = plan.sources.setdefault(skey, Source(skey, M.OWN_SITE, listing.website,
                                                            discovered_from=src.url))
                for ri in src.record_idx:
                    if ri not in site.record_idx:
                        site.record_idx.append(ri)
                    for rp in plan.records:
                        if rp.idx == ri and skey not in rp.source_keys:
                            rp.source_keys.append(skey)
                            rp.discovered_site = listing.website
                            rp.notes.append(f"discovered site: {listing.website} (found on the Asset Map page)")
    return plan


def _fmt_url(v) -> str:
    return "" if v is None else str(v)


def _failed(kind: str, url: str, reason: str) -> dict:
    r = new_result("none", "none", url)
    r["status"], r["reason"] = "failed", reason
    return r


def shared_site_keys(plan: Plan) -> set[str]:
    return {k for k, s in plan.sources.items() if s.kind == M.OWN_SITE and len(set(s.record_idx)) >= 2}


# --------------------------------------------------------------------------------------
# Environment checks
# --------------------------------------------------------------------------------------
def check_ollama(cfg: Config, client: OllamaClient) -> None:
    ok, err = client.ping()
    if not ok:
        raise ConfigError(
            "I can't reach Ollama (the program that runs the AI model on your Mac).\n"
            "  1. Open the Ollama app (look for the llama icon in your menu bar), or run:  open -a Ollama\n"
            "  2. Wait a few seconds, then run this tool again.\n"
            f"(Technical detail: {err})")
    if not client.has_model():
        raise ConfigError(
            f"The AI model '{cfg.model}' is not downloaded yet.\n"
            f"Download it once by pasting this into Terminal:   ollama pull {cfg.model}\n"
            "Or pick a model you already have by changing 'model:' in config.yaml.\n"
            f"Models on this Mac right now: {', '.join(client.installed_models()) or '(none)'}")


# --------------------------------------------------------------------------------------
# Dry run
# --------------------------------------------------------------------------------------
_SAMPLE_PAGE = """Welcome to St. Example's Episcopal Church. Sunday Worship: 8:00 AM Holy Eucharist, Rite I (spoken).
10:30 AM Holy Eucharist, Rite II with choir and organ (livestreamed on YouTube). Wednesday: 12:00 noon Holy Eucharist
and 6:00 PM Evening Prayer. Childcare is offered during the 10:30 service. All are welcome. The Rev. Pat Sample, Rector.
Our parking lot is behind the church and the building is wheelchair accessible. Sunday School for ages 4-12 meets at 9:15 AM.
Adult Forum meets Sundays at 9:15 AM in the parish hall. Contact the parish office at (555) 010-0100."""


def time_sample_call(cfg: Config, client: OllamaClient, today: date) -> Optional[float]:
    """Run ONE small model call on a made-up sample page and report how many seconds it took."""
    groups = llm_specs_by_group(cfg)
    gname = "services" if "services" in groups else next(iter(groups), None)
    if gname is None:
        return None
    specs = groups[gname]
    info = get_group_info(cfg, gname, specs)
    from .normalizers import norm_for_quote_check
    try:
        client.warm_up()
    except OllamaUnavailable:
        return None
    started = time.monotonic()
    try:
        run_group_on_chunk(client, cfg, info, specs, url="sample", title="Sample", chunk=_SAMPLE_PAGE,
                           page_text=_SAMPLE_PAGE, page_norm=norm_for_quote_check(_SAMPLE_PAGE),
                           source_type=M.OWN_SITE, page_rank_value=0, today=today)
    except OllamaUnavailable:
        return None
    return time.monotonic() - started


def dry_run(cfg: Config, args) -> int:
    records = load_records(cfg)
    setup_logging(cfg.output_dir)
    idxs = select_indices(records, cfg, args.only, args.limit_sites)
    if not idxs:
        say("No records match your --only / --limit-sites choice, so there is nothing to do.")
        return 1
    today = date.today()
    stop = threading.Event()
    fetcher = Fetcher(cfg, stop)
    client = OllamaClient(cfg, stop)
    try:
        ollama_ok, _ = client.ping()
        say("")
        say("=== DRY RUN: nothing will be extracted; I only look at what a real run would do ===")
        say(f"Records in file: {len(records)}   selected: {len(idxs)}   model: {cfg.model}")
        if not (args.no_facebook or not cfg.facebook_enabled) and any(_preclassify(cfg, records[i])[0] == KIND_FACEBOOK for i in idxs):
            say("")
            say(FB_NOTICE)
        plan = build_plan(records, idxs, cfg, fetcher, stop, args.no_facebook)
        counts = Counter(rp.url_kind for rp in plan.records)
        say("")
        say("Website types: " + ", ".join(f"{k}: {v}" for k, v in sorted(counts.items())))
        say("")
        # ---- per-site plans ----
        site_pages: dict[str, int] = {}
        site_notes: dict[str, str] = {}
        max_pages = cfg.max_pages_per_site
        own = [s for s in plan.sources.values() if s.kind == M.OWN_SITE]
        def _plan_one(src):
            try:
                return crawl_site(src.url, cfg, fetcher, stop, max_pages=max_pages, plan_only=True)
            except StopRequested:
                return None
            except Exception as exc:
                bad = CrawlResult(start_url=src.url, status="failed", reason=f"unexpected problem ({type(exc).__name__}: {str(exc)[:80]})")
                return bad

        if own:
            say(f"Checking {len(own)} website(s): home page and sitemap only ({cfg.fetch_concurrency} at a time)...")
        pool = ThreadPoolExecutor(max_workers=cfg.fetch_concurrency, thread_name_prefix="plan")
        try:
            plans = list(pool.map(_plan_one, own))
        except KeyboardInterrupt:
            stop.set()
            raise
        finally:
            pool.shutdown(wait=True, cancel_futures=True)
        for n, (src, cr) in enumerate(zip(own, plans), start=1):
            if cr is None:
                raise KeyboardInterrupt()
            if cr.status != "ok":
                site_pages[src.key] = 0
                site_notes[src.key] = f"CANNOT OPEN: {cr.reason}"
            else:
                site_pages[src.key] = min(max_pages, cr.candidates_found + 1)
                site_notes[src.key] = (f"sitemap found, {cr.candidates_found + 1} pages available" if cr.sitemap_used
                                       else f"no sitemap; will follow links (home page shows {cr.candidates_found} links)")
        say("")
        say(f"{'#':>3}  {'id':<28} {'name':<34} {'source':<10} plan")
        for rp in plan.records:
            lines = []
            for k in rp.source_keys:
                s = plan.sources[k]
                if s.kind == M.OWN_SITE:
                    shared = " [shared website]" if k in shared_site_keys(plan) else ""
                    lines.append(f"own site {s.url} -> {site_pages.get(k, 0)} page(s) to crawl; {site_notes.get(k, '')}{shared}"
                                 + (" [discovered site]" if s.discovered_from else ""))
                elif s.kind == M.FACEBOOK:
                    lines.append("Facebook page (best effort, <=%s pages, %ss apart; a website listed on it is crawled too)%s" % (
                        cfg.special("facebook.com").get("max_pages", 5), cfg.special("facebook.com").get("delay_seconds", 5),
                        f"  SKIPPED: {s.disabled_reason}" if s.disabled_reason else ""))
                else:
                    listing, err = plan.am_pages.get(k, (None, ""))
                    lines.append("Asset Map page: " + (f"read OK; church website = {listing.website or 'not listed'}" if listing and listing.ok else f"could not read ({err})"))
            for pf in rp.pre_failures:
                lines.append("NO SOURCE: " + pf["reason"])
            say(f"{rp.idx + 1:>3}  {rp.rid[:28]:<28} {rp.name[:34]:<34} {rp.url_kind:<10} " + (lines[0] if lines else ""))
            for extra in lines[1:]:
                say(" " * 80 + extra)
        # ---- time estimate ----
        say("")
        groups = llm_specs_by_group(cfg)
        total_pages = sum(site_pages.values())
        calls = 0
        for k, pages in site_pages.items():
            calls += sum(min(cfg.max_llm_pages_per_group, max(1, round(pages * 0.6))) for _ in groups) if pages else 0
        n_fb = sum(1 for s in plan.sources.values() if s.kind == M.FACEBOOK and not s.disabled_reason)
        n_am = sum(1 for s in plan.sources.values() if s.kind == M.ASSET_MAP)
        calls += n_am * min(2, len(groups)) + n_fb * min(3, len(groups)) * 2
        fetch_seconds = (total_pages * (cfg.delay_seconds + 0.6)) / max(1, min(cfg.fetch_concurrency, max(1, len(own)))) + n_fb * 4 * 7
        t_call: Optional[float] = None
        if calls and ollama_ok and client.has_model():
            say("Timing the model on one sample page (this takes a few seconds)...")
            t_call = time_sample_call(cfg, client, today)
        elif calls and ollama_ok:
            say(f"(The model '{cfg.model}' is not downloaded yet, so I can't time it. Run:  ollama pull {cfg.model})")
        elif calls:
            say("(Ollama is not running, so I can't time the model. Start the Ollama app first.)")
        say("")
        say(f"Sites to crawl: {len(own)}    pages to download: about {total_pages}    Facebook pages: {n_fb}    Asset Map pages: {n_am}")
        say(f"Model calls (rough guess): about {calls}")
        if t_call:
            total = fetch_seconds * 0.5 + calls * t_call
            say(f"One model call took {t_call:.1f} s here.  ESTIMATED TOTAL RUN TIME: about {_hms(total)} "
                f"(a rough guess; downloading overlaps with the model's work).")
        elif calls:
            say(f"Downloading alone should take about {_hms(fetch_seconds * 0.5)}; the model time could not be estimated.")
        else:
            say("There is nothing to download or ask the model about.")
        say("")
        say(f"Dry-run details are also in {cfg.output_dir / 'run.log'}")
        return 0
    finally:
        fetcher.close()
        client.close()


def _hms(seconds: float) -> str:
    seconds = int(seconds)
    h, rem = divmod(seconds, 3600)
    m, s = divmod(rem, 60)
    if h:
        return f"{h} h {m} min"
    if m:
        return f"{m} min {s} s"
    return f"{s} s"


# --------------------------------------------------------------------------------------
# The real run
# --------------------------------------------------------------------------------------
def run(cfg: Config, args) -> int:
    records = load_records(cfg)
    setup_logging(cfg.output_dir)
    started = time.monotonic()
    today = date.today()
    idxs = select_indices(records, cfg, args.only, args.limit_sites)
    if not idxs:
        say("No records match your --only / --limit-sites choice, so there is nothing to do.")
        return 1
    say(f"Loaded {len(records)} parish record(s); checking {len(idxs)} of them. Model: {cfg.model}")
    stop = threading.Event()
    state = State(cfg.output_dir / "progress.sqlite", fresh=args.fresh)
    if args.fresh:
        _clear_cache(cfg)
        say("--fresh: old progress and cached pages were cleared; starting from scratch.")
    fetcher = Fetcher(cfg, stop)
    client = OllamaClient(cfg, stop)
    interrupted = False
    try:
        check_ollama(cfg, client)
        if not (args.no_facebook or not cfg.facebook_enabled) and any(_preclassify(cfg, records[i])[0] == KIND_FACEBOOK for i in idxs):
            say("")
            say(FB_NOTICE)
            say("")
        ctx = ExtractContext(cfg=cfg, client=client, state=state, today=today, stop=stop)
        plan = build_plan(records, idxs, cfg, fetcher, stop, args.no_facebook)
        fp = config_fingerprint(cfg)

        todo_total = len(plan.sources)
        say(f"{todo_total} source(s) to check ({sum(1 for s in plan.sources.values() if s.kind == M.OWN_SITE)} websites, "
            f"{sum(1 for s in plan.sources.values() if s.kind == M.FACEBOOK)} Facebook, "
            f"{sum(1 for s in plan.sources.values() if s.kind == M.ASSET_MAP)} Asset Map). Press Ctrl+C any time; progress is saved.")
        done_count = 0
        pdfs: dict[str, list[str]] = {}

        # ---- Asset Map listings (already downloaded while planning) ----
        for src in list(plan.sources.values()):
            if src.kind != M.ASSET_MAP:
                continue
            done_count += 1
            if state.get_source(src.key, fp) is not None:
                say(f"[{done_count}/{todo_total}] {src.key}: already done earlier (resuming)")
                continue
            say(f"[{done_count}/{todo_total}] Asset Map: {src.url}")
            listing, err = plan.am_pages.get(src.key, (None, "page was not fetched"))
            try:
                r = extract_asset_map(src.key, src.url, listing, err, ctx)
            except (OllamaUnavailable, KeyboardInterrupt):
                raise
            except Exception as exc:
                log.exception("Asset Map step crashed for %s", src.key)
                r = new_result(M.ASSET_MAP, src.key, src.url)
                r["status"], r["reason"] = "failed", f"unexpected problem ({type(exc).__name__}: {str(exc)[:100]})"
            state.put_source(src.key, fp, r)
            _report_source_line(r)

        # ---- Facebook pages next, one at a time: their About page may name the church's own website ----
        for src in list(plan.sources.values()):
            if src.kind != M.FACEBOOK:
                continue
            done_count += 1
            r = state.get_source(src.key, fp)
            if r is not None:
                say(f"[{done_count}/{todo_total}] {src.key}: already done earlier (resuming)")
            elif src.disabled_reason:
                r = new_result(M.FACEBOOK, src.key, src.url)
                r["status"], r["reason"] = "failed", src.disabled_reason
                state.put_source(src.key, fp, r)
            else:
                say(f"[{done_count}/{todo_total}] Facebook page {src.page_key} (slow on purpose: {cfg.special('facebook.com').get('delay_seconds', 5)} s between pages)")
                try:
                    crawl = crawl_facebook(src.page_key, cfg, fetcher, stop)
                    r = extract_facebook(src.key, src.url, crawl, ctx)
                except StopRequested:
                    raise KeyboardInterrupt()
                except (OllamaUnavailable, KeyboardInterrupt):
                    raise
                except Exception as exc:
                    log.exception("Facebook step crashed for %s", src.key)
                    r = new_result(M.FACEBOOK, src.key, src.url)
                    r["status"], r["reason"] = "failed", f"unexpected problem ({type(exc).__name__}: {str(exc)[:100]})"
                state.put_source(src.key, fp, r)
                _report_source_line(r)
            if _adopt_discovered_site(plan, cfg, src, r):
                todo_total = len(plan.sources)

        # ---- the websites: crawl in threads, extract here one at a time ----
        threaded: list[Source] = []
        for src in plan.sources.values():
            if src.kind != M.OWN_SITE:
                continue
            if state.get_source(src.key, fp) is not None:
                done_count += 1
                say(f"[{done_count}/{todo_total}] {src.key}: already done earlier (resuming)")
                continue
            threaded.append(src)

        # ---- websites and Facebook pages: crawl in threads, extract here one at a time ----
        pending = deque(threaded)
        inflight: dict = {}
        pool = ThreadPoolExecutor(max_workers=cfg.fetch_concurrency, thread_name_prefix="crawl")
        try:
            def submit_more() -> None:
                while pending and len(inflight) < cfg.fetch_concurrency + 1:
                    s = pending.popleft()
                    say(f"      starting {s.key} ...")
                    inflight[pool.submit(_crawl_job, s, cfg, fetcher, stop)] = s

            submit_more()
            while inflight:
                done, _ = wait(list(inflight), timeout=0.5, return_when=FIRST_COMPLETED)
                for fut in done:
                    src = inflight.pop(fut)
                    done_count += 1
                    nrec = len(set(src.record_idx))
                    say(f"[{done_count}/{todo_total}] {src.key}  ({nrec} record(s) use it)")
                    try:
                        crawl = fut.result()
                    except StopRequested:
                        raise KeyboardInterrupt()
                    except Exception as exc:         # a crashed crawl must not stop the run
                        log.exception("Crawl crashed for %s", src.key)
                        r = new_result(src.kind, src.key, src.url)
                        r["status"], r["reason"] = "failed", f"unexpected problem while reading it ({type(exc).__name__}: {str(exc)[:100]})"
                        state.put_source(src.key, fp, r)
                        continue
                    try:
                        r = extract_own_site(src.key, src.url, crawl, ctx)
                    except OllamaUnavailable:
                        raise
                    except KeyboardInterrupt:
                        raise
                    except Exception as exc:
                        log.exception("Extraction crashed for %s", src.key)
                        r = new_result(src.kind, src.key, src.url)
                        r["status"], r["reason"] = "failed", f"unexpected problem while extracting ({type(exc).__name__}: {str(exc)[:100]})"
                    state.put_source(src.key, fp, r)
                    _report_source_line(r)
                    del crawl
                submit_more()
        except KeyboardInterrupt:
            interrupted = True
            stop.set()
        finally:
            if pending or inflight:      # leaving early (Ctrl+C or an error): tell the crawl threads to wrap up
                stop.set()
            pool.shutdown(wait=True, cancel_futures=True)
        if interrupted:
            raise KeyboardInterrupt()

        # ---- compare every record to what was found ----
        say("")
        say("All sources are read. Comparing your JSON with what I found...")
        shared = shared_site_keys(plan)
        all_rows: list[Row] = []
        rows_by_index: dict[int, list[Row]] = {}
        summary: list[dict] = []
        raw_records = []
        for rp in plan.records:
            rec = records[rp.idx]
            results = [state.get_source(k, fp) for k in rp.source_keys]
            results = [r for r in results if r is not None] + list(rp.pre_failures)
            is_shared = any(k in shared for k in rp.source_keys)
            try:
                view = build_view(rec, cfg, results, shared=is_shared)
            except Exception as exc:                 # never let one record stop the report
                log.exception("Could not combine the results for %s", rp.rid)
                view = RecordView(all_failed_reason=f"internal problem while combining results ({type(exc).__name__}: {str(exc)[:100]}); see run.log")
            if rp.discovered_site:
                view.notes.append(f"discovered site: {rp.discovered_site}")
            rows = compare_record(rec, cfg, view, rp.rid, rp.name)
            rows_by_index[rp.idx] = rows
            all_rows.extend(rows)
            summary.append(summarize_record(rp.rid, rp.name, view, rows))
            raw_records.append({"id": rp.rid, "name": rp.name, "website": rp.url, "website_kind": rp.url_kind,
                                "sources": rp.source_keys, "notes": view.notes, "rows": [r.__dict__ for r in rows]})
        for k, s in plan.sources.items():
            r = state.get_source(k, fp)
            if r and r.get("pdfs"):
                pdfs[k.split(":", 1)[-1]] = r["pdfs"]

        p_report, p_review = write_report_csvs(cfg, all_rows)
        p_summary = write_summary_csv(cfg, summary)
        proposed, n_changed = build_proposed(records, rows_by_index, cfg, today, args.apply_discrepancies)
        p_prop = write_proposed(cfg, proposed)
        p_pdf = write_pdfs(cfg, pdfs)
        p_raw = write_raw(cfg, {
            "generated": datetime.now().isoformat(timespec="seconds"), "model": cfg.model,
            "records": raw_records,
            "sources": {k: state.get_source(k, fp) for k in plan.sources},
        })
        counts = Counter(r.status for r in all_rows)
        say("")
        say("==================== DONE ====================")
        say(f"Checked {len(plan.records)} record(s) in {_hms(time.monotonic() - started)}.")
        for st in M.ALL_STATUSES:
            if counts.get(st):
                say(f"  {st:<14} {counts[st]}")
        say(f"{n_changed} record(s) have proposed changes ({'including' if args.apply_discrepancies else 'filling only'} "
            f"{'discrepancies and ' if args.apply_discrepancies else ''}blank fields).")
        say(f"Files are in: {cfg.output_dir}")
        for p in (p_review, p_report, p_summary, p_prop, p_raw, p_pdf, cfg.output_dir / "run.log"):
            say(f"   - {p.name}")
        say("Start with needs_review.csv.")
        return 0
    except KeyboardInterrupt:
        stop.set()
        say("")
        say("Stopped by you (Ctrl+C). Your progress is saved. Run the same command again to pick up where it left off.")
        return 130
    except OllamaUnavailable as exc:
        say("")
        say(f"The AI model stopped responding: {exc}")
        say("Your progress is saved. Start the Ollama app (or run: open -a Ollama), then run this tool again to resume.")
        return 3
    finally:
        stop.set()
        try:
            fetcher.close()
        except Exception:
            pass
        client.unload()        # give the model's memory back to your Mac
        client.close()
        state.close()


def _crawl_job(src: Source, cfg: Config, fetcher: Fetcher, stop: threading.Event):
    return crawl_site(src.url, cfg, fetcher, stop)


def _adopt_discovered_site(plan: Plan, cfg: Config, src: Source, result: Optional[dict]) -> bool:
    """If a Facebook page lists the church's own website, crawl that website too ("discovered site")."""
    if not result or result.get("status") != "ok":
        return False
    for d in result.get("candidates", []):
        if d.get("field") != "@website" or not d.get("value"):
            continue
        if classify_url(d["value"], cfg.skip_domains) != KIND_OWN:
            continue
        skey = "site:" + host_of(d["value"])
        site = plan.sources.get(skey)
        if site is None:
            site = plan.sources[skey] = Source(skey, M.OWN_SITE, d["value"], discovered_from=src.url)
        for ri in src.record_idx:
            if ri not in site.record_idx:
                site.record_idx.append(ri)
            for rp in plan.records:
                if rp.idx == ri and skey not in rp.source_keys:
                    rp.source_keys.append(skey)
                    rp.discovered_site = d["value"]
                    rp.notes.append(f"discovered site: {d['value']} (found on the Facebook page)")
        return True
    return False


def _report_source_line(r: dict) -> None:
    if r["status"] == "ok":
        extra = " (hit the page limit)" if r.get("cap_hit") else ""
        say(f"      read {r['pages_crawled']} page(s){extra}; {len(r['candidates'])} item(s) found; "
            f"{r['llm_calls']} model call(s), {r['llm_cache_hits']} reused"
            + (f"; {sum(1 for i in r['issues'] if i[1] == 'rejected_unverified')} answer(s) rejected as unverifiable" if r["issues"] else ""))
    else:
        say(f"      could not use it: {r['reason']}")


def _clear_cache(cfg: Config) -> None:
    import shutil
    try:
        shutil.rmtree(cfg.cache_dir)
    except FileNotFoundError:
        pass
    except OSError as exc:
        log.warning("Could not clear the page cache: %s", exc)

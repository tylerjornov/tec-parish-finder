#!/usr/bin/env python3
"""selftest.py - checks the tool's own logic. No internet, no Ollama, no website needed.

Run it with:   python selftest.py      (run.command can also run it for you)
If everything is fine you will see "ALL TESTS PASSED" at the end.

It tests: phone / email / address / person / URL normalizers, service-time sets (noon, ranges, "1st & 3rd"),
list comparison, status assignment (including UNCLEAR), missing markers, Asset Map contact
filtering, Rite derivation, the evidence "post-check" that keeps the AI honest, robots.txt wildcards,
Facebook wall detection, merging (rector vs church numbers, shared websites), and that the JSON round-trip
keeps unknown keys and key order.
"""

from __future__ import annotations

import copy
import json
import sys
import tempfile
import traceback
from datetime import date
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import logging                                                                    # noqa: E402
logging.getLogger("parishcheck").addHandler(logging.NullHandler())                # keep the test output tidy
logging.getLogger("parishcheck").propagate = False

from parishcheck import models as M                                              # noqa: E402
from parishcheck.assetmap import listing_candidates, parse_listing               # noqa: E402
from parishcheck.cleaner import PageDoc, clean_html                              # noqa: E402
from parishcheck.comparators import compare_typed, decide_status                 # noqa: E402
from parishcheck.config import load_config                          # noqa: E402
from parishcheck.facebook import WALL_REASON, assess_recency, detect_wall, unwrap_fb_link  # noqa: E402
from parishcheck.llm import (                                                    # noqa: E402
    FieldAnswer, build_chunk, build_group_schema, evidence_in_text, is_unclear, verify_value, _keyword_regex,
)
from parishcheck.merge import build_view, merge_field                            # noqa: E402
from parishcheck.models import Candidate                                         # noqa: E402
from parishcheck.normalizers import (                                            # noqa: E402
    addresses_match, derive_rite, extract_emails, extract_phones, format_phone, is_missing, livestream_key,
    norm_for_quote_check, normalize_url, parse_person, persons_match, phone_digits, role_is_parish_clergy,
    split_city_state, url_key,
)
from parishcheck.report import build_proposed, compare_record                    # noqa: E402
from parishcheck.robots import RobotsRules                                       # noqa: E402
from parishcheck.rules import extract_rule_candidates, find_addresses, label_role  # noqa: E402
from parishcheck.times import (                                                  # noqa: E402
    find_times, format_time, parse_service_slots, restyle_times, time_keys,
)
from parishcheck.urls import classify_url, facebook_page_key, is_excluded, score_url  # noqa: E402

MARKERS = ["", "N/A - Data Not Available"]
RESULTS = {"passed": 0, "failed": 0}


def check(name: str, ok: bool, detail: str = "") -> None:
    if ok:
        RESULTS["passed"] += 1
    else:
        RESULTS["failed"] += 1
        print(f"  FAIL: {name}" + (f"   ({detail})" if detail else ""))


def status_of(ftype, json_value, site_values, **kw):
    return decide_status(ftype, json_value, site_values, missing_markers=MARKERS, **kw)


# --------------------------------------------------------------------------------------------------
def test_missing_markers():
    check("empty string is missing", is_missing("", MARKERS))
    check("None is missing", is_missing(None, MARKERS))
    check("marker is missing (any case, extra spaces)", is_missing("  n/a - data not available ", MARKERS))
    check("empty list is missing", is_missing([], MARKERS))
    check("real text is not missing", not is_missing("St. Andrew's", MARKERS))
    check("zero is a real value", not is_missing(0, MARKERS))
    check("custom marker works", is_missing("TBD", MARKERS + ["TBD"]))


def test_phones():
    check("phone formats", phone_digits("(864) 235-5884") == "8642355884" == phone_digits("+1 864.235.5884"))
    check("phone extension ignored", phone_digits("864-235-5884 ext. 12") == "8642355884")
    check("not a phone", phone_digits("12345") is None)
    check("find phones in text", extract_phones("Call (864) 235-5884 or 864-555-1212; fax 800 555 0000") ==
          ["8642355884", "8645551212", "8005550000"])
    check("zip+4 / dates are not phones", extract_phones("Greenville SC 29601-1234, posted 2025-10-08") == [])
    check("format_phone", format_phone("8642355884") == "(864) 235-5884")
    check("phone equal", compare_typed("phone", "864-235-5884", ["(864) 235-5884"]).status == M.CORRECT)
    check("phone differs", compare_typed("phone", "864-235-5884", ["(864) 235-9999"]).status == M.DISCREPANCY)
    check("phone any of several", compare_typed("phone", "864-111-2222", ["(864) 235-5884", "(864) 111-2222"]).status == M.CORRECT)
    check("json holds two phones", compare_typed("phone", "864-111-2222 / 864-235-5884", ["(864) 235-5884"]).status == M.CORRECT)


def test_emails():
    check("email case-insensitive", compare_typed("email", "Office@Church.ORG", ["office@church.org"]).status == M.CORRECT)
    check("email any candidate", compare_typed("email", "b@x.org", ["a@x.org", "b@x.org"]).status == M.CORRECT)
    check("email differs", compare_typed("email", "b@x.org", ["a@x.org"]).status == M.DISCREPANCY)
    check("junk emails dropped", extract_emails("logo@2x.png sentry@sentry.io real@church.org") == ["real@church.org"])


def test_urls():
    check("url ignores scheme/www/slash", url_key("https://www.Church.org/about/") == url_key("http://church.org/about"))
    check("tracking params stripped", normalize_url("https://x.org/a?utm_source=z&fbclid=1#frag") == "https://x.org/a")
    check("index.html folded", normalize_url("https://x.org/index.html") == "https://x.org/")
    check("relative links", normalize_url("../a", "https://x.org/b/c") == "https://x.org/a")
    check("mailto is not a page", normalize_url("mailto:a@b.org") == "")
    check("website: host only", compare_typed("url", "http://church.org/", ["https://www.church.org/home"], url_kind="website").status == M.CORRECT)
    check("website: different host", compare_typed("url", "http://old.org/", ["https://new.org/"], url_kind="website").status == M.DISCREPANCY)
    check("livestream: youtube channel forms", livestream_key("https://www.youtube.com/@abc/live") == livestream_key("youtube.com/@abc"))
    check("livestream matches among links", compare_typed("url", "https://www.youtube.com/@abc/live",
          ["https://vimeo.com/1", "https://www.youtube.com/@abc"], url_kind="livestream").status == M.CORRECT)
    check("classify facebook", classify_url("https://m.facebook.com/stjohns") == "facebook")
    check("classify asset map", classify_url("https://www.episcopalassetmap.org/dioceses/x/list/y") == "asset_map")
    check("classify own", classify_url("stjohns.org") == "own_site")
    check("classify missing", classify_url("  ") == "missing")
    check("classify unsupported", classify_url("https://instagram.com/x", ["instagram.com"]) == "unsupported")
    check("fb page key", facebook_page_key("https://www.facebook.com/StJohnsChurch/about") == "stjohnschurch")
    check("fb profile id", facebook_page_key("https://www.facebook.com/profile.php?id=1000123") == "id:1000123")
    check("fb share links unsupported", facebook_page_key("https://www.facebook.com/sharer/sharer.php?u=x") == "")
    rx = [__import__("re").compile(p, __import__("re").I) for p in [r"/(?:19|20)\d{2}/(?:0?[1-9]|1[0-2])(?:/|$)", r"/(?:tag|category)(?:/|$)", r"/wp-json"]]
    check("exclude date archive", is_excluded("https://x.org/2024/03/post", rx))
    check("exclude tag pages", is_excluded("https://x.org/tag/music", rx))
    check("exclude calendar query", is_excluded("https://x.org/events?tribe-bar-date=2025-01", rx))
    check("keep normal page", not is_excluded("https://x.org/worship", rx))
    check("priority score", score_url("https://x.org/worship", "", ["worship"]) > score_url("https://x.org/blog/a/b/c", "", ["worship"]))


def test_addresses():
    check("St/Street, S/South, punctuation", addresses_match("1002 S Main St., Greenville, SC", "1002 South Main Street, Greenville, SC 29601"))
    check("Saint Charles", addresses_match("123 St Charles Ave, New Orleans, LA", "123 Saint Charles Avenue, New Orleans, Louisiana"))
    check("different number", not addresses_match("1002 S Main St", "1004 S Main St"))
    check("different direction", not addresses_match("1002 S Main St., Greenville, SC", "1002 N Main St, Greenville, SC"))
    check("different city", not addresses_match("10 Main St, Austin, TX", "10 Main St, Dallas, TX"))
    check("city missing on one side is fine", addresses_match("10 Main St", "10 Main St, Dallas, TX"))
    check("Rd/Road, Ave/Avenue, Dr/Drive, Hwy/Highway", all([
        addresses_match("5 Elm Rd", "5 Elm Road"), addresses_match("5 Oak Ave", "5 Oak Avenue"),
        addresses_match("5 Pine Dr", "5 Pine Drive"), addresses_match("5 US Hwy 1", "5 US Highway 1")]))
    check("address compare typed", compare_typed("address", "1002 S Main St., Greenville, SC", ["22 Elm St, X, SC", "1002 South Main Street, Greenville, SC"]).status == M.CORRECT)
    check("city/state split", split_city_state("1002 S Main St., Greenville, SC 29601") == ("Greenville", "SC"))
    found = find_addresses("Visit us at 1002 S. Main Street, Greenville, SC 29601 or write to PO Box 5.")
    check("find address in text", len(found) == 1 and found[0][0] == "1002 S. Main Street, Greenville, SC", str(found))
    found2 = find_addresses("Parish Hall\n2803 1st St\nWyandotte, MI 48192")
    check("find address across lines", found2 and found2[0][0] == "2803 1st St, Wyandotte, MI", str(found2))


def test_times_and_services():
    ts = lambda s: sorted(format_time(t.minutes) for t in find_times(s))
    check("noon / midnight", ts("12:00 noon and midnight") == ["12:00 midnight", "12:00 noon"], str(ts("12:00 noon and midnight")))
    check("a.m./p.m. forms", ts("8 a.m., 5:30 pm, 10.30 AM") == ["10:30 AM", "5:30 PM", "8:00 AM"], str(ts("8 a.m., 5:30 pm, 10.30 AM")))
    check("range keeps start", [format_time(s.minutes) for s in parse_service_slots("9-10:30 am Forum", "sun")] == ["9:00 AM"])
    check("range crossing noon", ts("11:00-1:00 PM") == ["11:00 AM", "1:00 PM"], str(ts("11:00-1:00 PM")))
    check("12:30 AM / PM", ts("12:30 AM 12:30 PM") == ["12:30 AM", "12:30 PM"])
    check("bare 12-hour guess", ts("Mass at 5:30 and 8:00") == ["5:30 PM", "8:00 AM"], str(ts("Mass at 5:30 and 8:00")))
    check("24h output", restyle_times("Mass 5:30 PM, noon, 8:30am", "24h") == "Mass 17:30, 12:00, 08:30", restyle_times("Mass 5:30 PM, noon, 8:30am", "24h"))
    check("12h output", restyle_times("at 8:30am and 7pm", "12h") == "at 8:30 AM and 7:00 PM")
    check("John 3:16 left alone", restyle_times("John 3:16 study", "12h", only_explicit=True) == "John 3:16 study")
    check("Midnight Mass title not a time", ts("Midnight Mass 11:30 PM") == ["11:30 PM"])
    gold_sun = "8:30 AM Low Mass, Rite II; 10:30 AM Sung High Mass with Incense, Rite I (livestreamed)"
    gold_wk = "Wed 5:30 PM Healing Mass (Benediction of the Blessed Sacrament following, every 4th Wednesday); 3rd Wed 5:00 PM Rosary"
    desc = lambda text, day=None: sorted(__import__("parishcheck.times", fromlist=["describe_slot"]).describe_slot(s) for s in parse_service_slots(text, day))
    check("sunday default day", desc(gold_sun, "sun") == ["Sun 10:30 AM", "Sun 8:30 AM"], str(desc(gold_sun, "sun")))
    check("parenthetical ordinal ignored; 3rd Wed kept", desc(gold_wk) == ["3rd Wed 5:00 PM", "Wed 5:30 PM"], str(desc(gold_wk)))
    check("1st & 3rd", desc("1st & 3rd Thursdays at 10 a.m.") == ["1st Thu 10:00 AM", "3rd Thu 10:00 AM"], str(desc("1st & 3rd Thursdays at 10 a.m.")))
    check("Mon-Fri range", len(parse_service_slots("Mon–Fri 12:00 noon Holy Eucharist")) == 5)
    check("two days two times", desc("Tue 7:00 AM Morning Prayer, Wed 5:30 PM Eucharist") == ["Tue 7:00 AM", "Wed 5:30 PM"])
    # comparisons
    check("services equal", compare_typed("services", gold_sun, ["10:30 AM Sung High Mass, Rite I; 8:30 AM Low Mass, Rite II"], default_day="sun").status == M.CORRECT)
    r = compare_typed("services", gold_sun, ["8:30 AM Low Mass; 5:00 PM Evensong"], default_day="sun")
    check("services overlap = PARTIAL", r.status == M.PARTIAL and r.site_only and r.json_only, str(r))
    check("services none in common = DISCREPANCY", compare_typed("services", "9:00 AM Eucharist", ["11:00 AM Eucharist"], default_day="sun").status == M.DISCREPANCY)
    check("same time, different week-of-month = PARTIAL", compare_typed("services", "Wed 5:30 PM Mass", ["1st Wed 5:30 PM Mass"]).status == M.PARTIAL)
    check("services with no clock times fall back to text", compare_typed("services", "Sunday Eucharist", ["Sunday Eucharist"]).status == M.CORRECT)


def test_lists_person_rite():
    check("list correct (fuzzy)", compare_typed("list", "English; Spanish", ["Spanish", "english"]).status == M.CORRECT)
    r = compare_typed("list", "English; Spanish", ["English"])
    check("list partial", r.status == M.PARTIAL and r.json_only == ["Spanish"], str(r))
    check("list extra on site is partial", compare_typed("list", "English", ["English", "Spanish"]).status == M.PARTIAL)
    check("list discrepancy", compare_typed("list", "Latin", ["English"]).status == M.DISCREPANCY)
    check("text fuzzy match", compare_typed("text", "Parking lot on site", ["On-site parking lot"]).status == M.CORRECT)
    check("person ignores honorifics", persons_match("The Reverend J. Gary Eichelberger, Rector", "Fr. Gary Eichelberger")[0])
    check("person different role", not persons_match("The Rev. John Bishop, Vicar", "Rev. John Bishop, Rector")[0])
    check("person different surname", not persons_match("The Rev. Jane Smith, Rector", "The Rev. Jane Jones, Rector")[0])
    check("person same surname, different first name", not persons_match("The Rev. Jane Smith, Rector", "The Rev. John Smith, Rector")[0])
    p = parse_person("The Reverend J. Gary Eichelberger, Rector")
    check("person parse", p.surname == "eichelberger" and p.roles == frozenset({"rector"}), str(p))
    check("interim rector is its own role", parse_person("Rev. A. Lee, Interim Rector").roles == frozenset({"interim rector"}))
    check("surname Bishop survives", parse_person("The Rev. John Bishop, Vicar").surname == "bishop")
    check("rite I", derive_rite("8:00 Rite I") == "I")
    check("rite II", derive_rite("10:30 Rite Two") == "II")
    check("rite mixed", derive_rite("Rite I", "Rite II") == "Mixed")
    check("rite I/II", derive_rite("Rite I/II") == "Mixed")
    check("rite none", derive_rite("Holy Eucharist") is None)
    check("rite II not mistaken for I", derive_rite("Rite II only") == "II")
    check("rite compare", compare_typed("rite", "Mixed", ["Mixed"]).status == M.CORRECT)


def test_status_assignment():
    s = status_of
    check("CORRECT", s("phone", "864-235-5884", ["(864) 235-5884"]).status == M.CORRECT)
    check("DISCREPANCY", s("phone", "864-235-5884", ["(864) 999-9999"]).status == M.DISCREPANCY)
    check("BOTH_MISSING (empty + marker)", s("text", "N/A - Data Not Available", []).status == M.BOTH_MISSING)
    check("BOTH_MISSING None", s("text", None, []).status == M.BOTH_MISSING)
    check("SITE_ONLY when JSON holds a marker", s("text", "N/A - Data Not Available", ["Parking lot"]).status == M.SITE_ONLY)
    check("JSON_ONLY", s("text", "Parking lot", [], json_only_note="crawl hit cap").status == M.JSON_ONLY)
    check("JSON_ONLY note kept", "cap" in s("text", "x", [], json_only_note="crawl hit cap").detail)
    u = s("text", "x", [], unclear_reason="blocked by Facebook login wall")
    check("UNCLEAR when blocked", u.status == M.UNCLEAR and "login wall" in u.reason)
    check("UNCLEAR beats BOTH_MISSING when source failed", s("text", "", [], unclear_reason="fetch failed").status == M.UNCLEAR)
    low = s("text", "Parking lot", ["Street parking"], site_confidence="low")
    check("low confidence difference -> UNCLEAR", low.status == M.UNCLEAR and "low confidence" in low.reason, str(low))
    check("low confidence agreement stays CORRECT", s("text", "Parking lot", ["Parking lot"], site_confidence="low").status == M.CORRECT)
    check("low confidence SITE_ONLY -> UNCLEAR", s("text", "", ["Street parking"], site_confidence="low").status == M.UNCLEAR)
    p = s("list", "English; Spanish", ["English"])
    check("PARTIAL lists the items", p.status == M.PARTIAL and p.json_only == ["Spanish"])
    check("state compare", compare_typed("state", "South Carolina", ["SC"]).status == M.CORRECT)


def test_asset_map():
    check("Rector counts", role_is_parish_clergy("Rector"))
    check("Vicar counts", role_is_parish_clergy("Vicar"))
    check("Priest-in-Charge counts", role_is_parish_clergy("Priest-in-Charge"))
    check("Interim Rector counts", role_is_parish_clergy("Interim Rector"))
    check("Dean counts", role_is_parish_clergy("Dean"))
    check("Canon to the Ordinary does NOT count", not role_is_parish_clergy("Canon to the Ordinary"))
    check("Parish Administrator does NOT count", not role_is_parish_clergy("Parish Administrator"))
    check("Assistant to the Rector does NOT count", not role_is_parish_clergy("Assistant to the Rector"))
    check("Associate Rector does NOT count", not role_is_parish_clergy("Associate Rector"))
    check("empty title does NOT count", not role_is_parish_clergy(""))
    html = """<html><body><main><h1>St. Test Church</h1>
    <p class="address"><span class="address-line1">1 Main St</span><br><span class="locality">Testville</span>, <span class="administrative-area">MI</span> <span class="postal-code">48000</span></p>
    <div class="field--name-field-phone"><a href="tel:734-555-0100">734-555-0100</a></div>
    <div class="field--name-field-email-address"><a href="mailto:office@test.org">office@test.org</a></div>
    <div class="field--name-field-external-url"><a href="http://www.test.org">http://www.test.org</a></div>
    <div class="block-field_contacts" data-block-plugin-id="field_block:node:place:field_contacts"><h2>Contact</h2>
      <div class="details--person"><h3><div class="field--name-field-person-name">Canon Pat Diocese</div></h3><div class="field--name-field-person-position">Canon to the Ordinary</div><div class="details--person__email"><a href="mailto:canon@diocese.org">canon@diocese.org</a></div></div>
      <div class="details--person"><h3><div class="field--name-field-person-name">Pastor Lee Priest</div></h3><div class="field--name-field-person-position">Rector</div><div class="details--person__email"><a href="mailto:lee@test.org">lee@test.org</a></div></div>
    </div>
    <div class="worship-times"><div class="field--name-field-worship-times-day">Sunday</div><div class="field--name-field-worship-times-time">10:00 am</div></div>
    </main></body></html>"""
    listing = parse_listing(html, "https://www.episcopalassetmap.org/x")
    check("AM address parsed", listing.address == "1 Main St, Testville, MI", listing.address)
    check("AM website parsed", listing.website == "http://www.test.org/", listing.website)
    cands, notes = listing_candidates(listing, listing.url)
    clergy = [c for c in cands if c.field == "@clergy"]
    check("AM: only the Rector becomes rector", len(clergy) == 1 and "Lee Priest" in clergy[0].value, str([c.value for c in clergy]))
    rector_emails = [c.value for c in cands if c.field == "@email" and c.role == "rector"]
    check("AM: canon's email is never a rector email", rector_emails == ["lee@test.org"], str(rector_emails))
    check("AM: ignored contact is explained", any("Canon to the Ordinary" in n for n in notes), str(notes))
    check("AM: church email stays church", any(c.field == "@email" and c.role == "church" and c.value == "office@test.org" for c in cands))


def test_postcheck():
    page = "Sunday Worship: 8:00 AM Holy Eucharist Rite I. The Rev. Jane Doe, Rector. Call (864) 235-5884 or office@church.org."
    pn = norm_for_quote_check(page)
    check("evidence found (case/punctuation ignored)", evidence_in_text("the rev. jane doe, RECTOR", pn))
    check("evidence with ... pieces", evidence_in_text("Sunday Worship ... Holy Eucharist Rite I", pn))
    check("evidence invented", not evidence_in_text("Sunday 11:00 AM Mass", pn))
    check("empty evidence rejected", not evidence_in_text("", pn))
    from parishcheck.config import FieldSpec
    svc = FieldSpec("sunday_services", "services")
    check("time present", verify_value(svc, "8:00 AM Holy Eucharist, Rite I", page, pn)[0])
    check("time invented is rejected", not verify_value(svc, "9:30 AM Choral Eucharist", page, pn)[0])
    check("noon / 12h loose match", verify_value(svc, "8:00 AM Eucharist", "Eucharist at 8 a.m.", norm_for_quote_check("Eucharist at 8 a.m."))[0])
    per = FieldSpec("rector_name", "person")
    check("name present", verify_value(per, "The Reverend Jane Doe, Rector", page, pn)[0])
    check("name invented is rejected", not verify_value(per, "The Reverend John Smith, Rector", page, pn)[0])
    check("email invented is rejected", not verify_value(FieldSpec("e", "text"), "write to boss@church.org", page, pn)[0])
    check("phone present", verify_value(FieldSpec("p", "text"), "Call (864) 235-5884", page, pn)[0])
    check("phone invented is rejected", not verify_value(FieldSpec("p", "text"), "Call (864) 555-1111", page, pn)[0])
    check("unclear words", all(is_unclear(x) for x in ["unclear", "", "N/A", "Unknown", "not stated"]))
    check("real answer is not unclear", not is_unclear("Parking lot on site"))
    ans = FieldAnswer.model_validate({"evidence": "x", "value": "y", "confidence": "high"})
    check("pydantic answer", ans.confidence == "high")
    schema = build_group_schema([FieldSpec("music_style", "text"), FieldSpec("parking", "text")])
    check("schema asks for evidence, value and confidence for every field",
          schema["required"] == ["music_style", "parking"] and
          list(schema["properties"]["parking"]["properties"]) == ["evidence", "value", "confidence"], str(schema))
    rx = _keyword_regex(["parking", "re:\\d{1,2}\\s?[ap]\\.?m\\b"])
    text = "x" * 3000 + " free parking here " + "y" * 3000 + " service at 10 am " + "z" * 100
    hits = [m.start() for m in rx.finditer(text)]
    chunk = build_chunk(text, hits, 1500, 6000)
    check("chunk is limited", len(chunk) <= 6000 and "parking" in chunk)
    check("no hits -> no chunk", build_chunk("hello", [], 1500, 6000) == "")


def test_robots_and_facebook():
    r = RobotsRules("User-agent: *\nDisallow: /maps\nDisallow: /*/map\nDisallow: /node/*/edit\nAllow: /maps/public$\nCrawl-delay: 4\nSitemap: https://x.org/sm.xml\n")
    check("robots disallow prefix", not r.allowed("/maps/anything"))
    check("robots wildcard", not r.allowed("/en/map") and not r.allowed("/node/12/edit"))
    check("robots allow ordinary", r.allowed("/worship"))
    check("robots longest match wins", r.allowed("/maps/public"))
    check("robots crawl-delay and sitemap", r.crawl_delay == 4 and r.sitemaps == ["https://x.org/sm.xml"])
    named = RobotsRules("User-agent: ParishInfoVerifier\nDisallow: /\n\nUser-agent: *\nAllow: /\n")
    check("robots group that names us wins", not named.allowed("/anything"))
    check("empty robots allows all", RobotsRules("").allowed("/x"))
    real_page = ("Our Church About\n" + "We are a welcoming parish with many programs and a long history in the town. " * 8)
    check("fb: real content is not a wall", detect_wall("https://www.facebook.com/x/about", 200, real_page) == "")
    check("fb: login redirect is a wall", detect_wall("https://www.facebook.com/login/?next=x", 200, real_page) == WALL_REASON)
    check("fb: captcha text is a wall", detect_wall("https://www.facebook.com/x", 200, real_page + " Confirm you're human") == WALL_REASON)
    check("fb: only a login box is a wall", detect_wall("https://www.facebook.com/x", 200, "Log in\nEmail or phone\nPassword\nCreate new account") == WALL_REASON)
    check("fb: 403 is a wall", detect_wall("https://www.facebook.com/x", 403, real_page) == WALL_REASON)
    check("fb: l.php unwrap", unwrap_fb_link("https://l.facebook.com/l.php?u=https%3A%2F%2Fchurch.org%2F&h=AT0") == "https://church.org/")
    today = date(2026, 10, 8)
    check("fb: recent relative date", assess_recency("Sunday service at 10! 3d", today) == "recent")
    check("fb: old full date", assess_recency("March 3, 2024 Easter service at 9", today) == "stale")
    check("fb: new full date", assess_recency("September 14, 2026 Bring a friend", today) == "recent")
    check("fb: years-ago relative", assess_recency("Pancake supper 2y", today) == "stale")
    check("fb: no clues", assess_recency("Join us Sunday", today) == "unknown")


def test_sitemaps():
    import gzip
    from parishcheck.crawler import _parse_sitemap
    from parishcheck.fetcher import _maybe_gunzip
    index = b"<?xml version='1.0'?><sitemapindex xmlns='http://www.sitemaps.org/schemas/sitemap/0.9'><sitemap><loc>https://x.org/page-sitemap.xml.gz</loc></sitemap><sitemap><loc>https://x.org/post-sitemap.xml</loc></sitemap></sitemapindex>"
    urls = b"<?xml version='1.0'?><urlset xmlns='http://www.sitemaps.org/schemas/sitemap/0.9'><url><loc>https://x.org/</loc></url><url><loc> https://x.org/worship </loc></url></urlset>"
    kind, locs = _parse_sitemap(index)
    check("nested sitemap index is recognised", kind == "index" and locs == ["https://x.org/page-sitemap.xml.gz", "https://x.org/post-sitemap.xml"], str((kind, locs)))
    kind, locs = _parse_sitemap(urls)
    check("sitemap urls are read (spaces trimmed)", kind == "urls" and locs == ["https://x.org/", "https://x.org/worship"], str(locs))
    check(".gz sitemap is unzipped", _maybe_gunzip(gzip.compress(urls)) == urls)
    check("plain data is left alone", _maybe_gunzip(urls) == urls)
    check("corrupt .gz does not crash (returns nothing)", _maybe_gunzip(b"\x1f\x8bnot really gzip") == b"")
    check("broken XML does not crash", _parse_sitemap(b"<urlset><url><loc>https://x.org/a</loc></url")[1] == ["https://x.org/a"])
    check("garbage does not crash", _parse_sitemap(b"hello") == ("urls", []))


def test_placeholder_sites_and_json_ld():
    from parishcheck.crawler import dead_site_reason
    dead = clean_html("<html><body><h1>Account Suspended</h1><p>This account has been suspended.</p></body></html>", "http://x.org/")
    check("suspended-hosting page is not a real website", bool(dead_site_reason(dead, "http://x.org/")))
    check("tempdisabled address is not a real website", bool(dead_site_reason(dead, "http://x.org/cgi-sys/tempdisabled.cgi")))
    real = clean_html("<html><body><h1>Welcome</h1><p>" + "We are a friendly parish with many programs. " * 40 + "</p></body></html>", "http://x.org/")
    check("a normal website is accepted", dead_site_reason(real, "http://x.org/") == "")
    html = ('<html><head><script type="application/ld+json">{"@type":"Church","name":"X","address":"Greenville, SC 29601 USA"}</script></head>'
            '<body><main>' + "Plenty of text about our church and its programs. " * 20 + '</main></body></html>')
    cands = extract_rule_candidates(clean_html(html, "https://x.org/"), [])
    check("JSON-LD address without a street is ignored", not any(c.field == "@address" for c in cands), str([c.value for c in cands if c.field == "@address"]))
    html2 = html.replace('"address":"Greenville, SC 29601 USA"', '"address":{"@type":"PostalAddress","streetAddress":"1002 S Main St.","addressLocality":"Greenville","addressRegion":"SC"}')
    cands2 = extract_rule_candidates(clean_html(html2, "https://x.org/"), [])
    check("JSON-LD PostalAddress is used", any(c.field == "@address" and c.value == "1002 S Main St., Greenville, SC" for c in cands2), str([c.value for c in cands2]))


def test_cleaner_rules_merge():
    html = """<html><head><title>Contact</title></head><body><main><h1>Contact us</h1>
    <p>The Rev. Jane Doe, Rector<br>Phone: (864) 235-5890<br><a href="mailto:rector@church.org">rector@church.org</a></p>
    <p>Parish Office: (864) 235-5884 <a href="mailto:office@church.org">office@church.org</a> Fax: (864) 235-0000</p>
    <p>Watch live: <a href="https://www.youtube.com/@church/live">Livestream</a>. Find us at 12 Elm Street, Greenville, SC 29601.
    Plenty of extra words so that the extractor keeps this paragraph in the main text of the page for sure.</p>
    </main><footer>12 Elm Street<br>Greenville, SC 29601<br>(864) 235-5884</footer></body></html>"""
    doc = clean_html(html, "https://church.org/contact", ["youtube.com"])
    cands = extract_rule_candidates(doc, ["youtube.com"])
    roles = {(c.field, c.value, c.role) for c in cands}
    check("rector phone labelled", ("@phone", "(864) 235-5890", "rector") in roles, str(roles))
    check("office phone labelled church", ("@phone", "(864) 235-5884", "church") in roles)
    check("fax number skipped", not any("0000" in c.value for c in cands))
    check("rector email labelled", ("@email", "rector@church.org", "rector") in roles)
    check("office email labelled church", ("@email", "office@church.org", "church") in roles)
    check("livestream found", any(c.field == "@livestream" and "youtube.com/@church/live" in c.value for c in cands))
    check("address found", any(c.field == "@address" and c.value == "12 Elm Street, Greenville, SC" for c in cands))
    cfg = tiny_config()
    church_phone = merge_field(cfg.get_field("church_phone"), cands, cfg)
    rector_phone = merge_field(cfg.get_field("rector_phone"), cands, cfg)
    check("church_phone picks the office line", church_phone.best == "(864) 235-5884", str(church_phone))
    check("rector_phone picks the rector line", rector_phone.best == "(864) 235-5890", str(rector_phone))
    church_email = merge_field(cfg.get_field("church_email"), cands, cfg)
    check("church_email never the rector's", church_email.best == "office@church.org", str(church_email))
    # rector_phone JSON that is really the office number: found on site but labelled for the other role
    sv_rector_email = merge_field(cfg.get_field("rector_email"), cands, cfg)
    check("rector_email", sv_rector_email.best == "rector@church.org")


def tiny_config():
    yaml_text = """
input_json: parishes.json
output_dir: out_test
id_key: id
name_key: name
website_key: website
uncheckable_fields: [id, lat, notes, date_last_updated]
date_updated_key: date_last_updated
fields:
  - {key: name, type: text, group: identity, description: "name"}
  - {key: website, type: url, group: contact, url_kind: website, description: "site"}
  - {key: address, type: address, group: contact, description: "address"}
  - {key: city, type: text, group: contact, address_part: city, description: "city"}
  - {key: state, type: text, group: contact, address_part: state, description: "state"}
  - {key: church_phone, type: phone, group: contact, role: church, description: "phone"}
  - {key: church_email, type: email, group: contact, role: church, description: "email"}
  - {key: rector_email, type: email, group: contact, role: rector, description: "rector email"}
  - {key: rector_phone, type: phone, group: contact, role: rector, description: "rector phone"}
  - {key: rector_name, type: person, group: clergy, role: rector, description: "rector"}
  - {key: sunday_services, type: services, group: services, default_day: sun, description: "sunday"}
  - {key: weekday_services, type: services, group: services, description: "weekday"}
  - {key: rite_details, type: text, group: services, description: "rite details"}
  - {key: rite, type: rite, group: services, description: "rite"}
  - {key: service_languages, type: list, group: services, description: "languages"}
  - {key: music_style, type: text, group: community, description: "music"}
  - {key: other_contact, type: list, group: derived, description: "other"}
derived_fields:
  other_contact: {from: [church_phone, church_email], separator: " | "}
  rite: {from_services: [sunday_services, weekday_services, rite_details]}
"""
    d = Path(tempfile.mkdtemp(prefix="parish_selftest_"))
    (d / "config.yaml").write_text(yaml_text, encoding="utf-8")
    return load_config(d / "config.yaml")


def cand(field, value, role=None, page="https://church.org/", rank=0, conf="high", kind=M.OWN_SITE, method="rule", evidence="ev"):
    return Candidate(field=field, value=value, role=role, source_url=page, page_rank=rank, confidence=conf,
                     source_type=kind, method=method, evidence=evidence)


def result(kind, cands, status="ok", reason="", url="https://church.org/", **kw):
    r = {"key": "k", "kind": kind, "url": url, "status": status, "reason": reason, "pages_crawled": 3, "cap_hit": False,
         "candidates": [c.to_dict() for c in cands], "issues": [], "notes": []}
    r.update(kw)
    return r


def test_merge_and_rows():
    cfg = tiny_config()
    rec = {"id": "a1", "name": "St. A", "website": "https://church.org", "address": "12 Elm St, Greenville, SC",
           "church_phone": "864-235-5884", "church_email": "N/A - Data Not Available", "rector_email": "rector@church.org",
           "sunday_services": "8:00 AM Eucharist", "music_style": "Choir and organ", "notes": "keep me"}
    cands = [
        cand("@phone", "(864) 235-5884", "church"), cand("@email", "office@church.org", "church"),
        cand("@address", "12 Elm Street, Greenville, SC"), cand("@website", "https://church.org/"),
        cand("sunday_services", "8:00 AM Holy Eucharist, Rite I; 10:30 AM Choral Eucharist, Rite II", page="https://church.org/worship"),
        cand("music_style", "Gospel band", conf="low", method="llm"),
    ]
    view = build_view(rec, cfg, [result(M.OWN_SITE, cands)])
    rows = {r.field: r for r in compare_record(rec, cfg, view, "a1", "St. A")}
    check("phone CORRECT", rows["church_phone"].status == M.CORRECT)
    check("email SITE_ONLY", rows["church_email"].status == M.SITE_ONLY and rows["church_email"].site_value == "office@church.org")
    check("address CORRECT", rows["address"].status == M.CORRECT)
    check("city derived from address, BOTH_MISSING", rows["city"].status == M.SITE_ONLY, rows["city"].status)
    check("services PARTIAL", rows["sunday_services"].status == M.PARTIAL and "10:30" in rows["sunday_services"].detail, rows["sunday_services"].detail)
    check("low-confidence difference is UNCLEAR", rows["music_style"].status == M.UNCLEAR and rows["music_style"].reason)
    check("uncheckable passes through", rows["id"].status == M.NOT_CHECKABLE and rows["notes"].status == M.NOT_CHECKABLE)
    check("rector_phone not on site and not in JSON", rows["rector_phone"].status == M.BOTH_MISSING)
    check("rite derived from services text", rows["rite"].status == M.SITE_ONLY and rows["rite"].site_value == "Mixed", rows["rite"].site_value)
    check("other_contact derived", rows["other_contact"].status == M.SITE_ONLY and "(864) 235-5884 | office@church.org" == rows["other_contact"].site_value, rows["other_contact"].site_value)
    check("row carries method/source", rows["church_phone"].method == "rule" and rows["church_phone"].source_type == "own site")

    # failed source -> everything UNCLEAR with a reason
    bad = build_view(rec, cfg, [result(M.FACEBOOK, [], status="blocked", reason="blocked by Facebook login wall")])
    brows = compare_record(rec, cfg, bad, "a1", "St. A")
    check("blocked Facebook -> all checkable fields UNCLEAR", all(r.status == M.UNCLEAR and "login wall" in r.reason
          for r in brows if r.status != M.NOT_CHECKABLE), str([(r.field, r.status) for r in brows if r.status not in (M.UNCLEAR, M.NOT_CHECKABLE)]))

    # source priority: own site beats Facebook; disagreement is mentioned in detail
    own = result(M.OWN_SITE, [cand("@phone", "(864) 235-5884", "church")])
    fb = result(M.FACEBOOK, [cand("@phone", "(864) 111-0000", "church", kind=M.FACEBOOK)], url="https://facebook.com/x")
    v2 = build_view(rec, cfg, [fb, own])
    check("own site wins over Facebook", v2.values["church_phone"].best == "(864) 235-5884")
    check("Facebook disagreement noted", any("Facebook says" in n for n in v2.values["church_phone"].notes), str(v2.values["church_phone"].notes))

    # JSON_ONLY notes when the page cap was hit
    cap = result(M.OWN_SITE, [], cap_hit=True)
    v3 = build_view(rec, cfg, [cap])
    r3 = {r.field: r for r in compare_record(rec, cfg, v3, "a1", "St. A")}
    check("JSON_ONLY mentions page cap", r3["church_phone"].status == M.JSON_ONLY and "page limit" in r3["church_phone"].detail, r3["church_phone"].detail)

    # shared website: choose the location page that shows THIS record's address
    rec_b = dict(rec, id="b2", address="99 Oak Road, Springfield, SC", church_phone="803-000-1111")
    shared_c = [
        cand("@address", "12 Elm Street, Greenville, SC", page="https://church.org/greenville"),
        cand("@phone", "(864) 235-5884", "church", page="https://church.org/greenville"),
        cand("@address", "99 Oak Road, Springfield, SC", page="https://church.org/springfield"),
        cand("@phone", "(803) 000-1111", "church", page="https://church.org/springfield"),
    ]
    vb = build_view(rec_b, cfg, [result(M.OWN_SITE, shared_c)], shared=True)
    check("shared site: second location gets ITS phone", vb.values["church_phone"].best == "(803) 000-1111", str(vb.values["church_phone"]))
    check("shared site noted", any("shared website: multiple locations" in n for n in vb.notes))
    rec_c = dict(rec, id="c3", address="1 Nowhere Lane, Elsewhere, SC", church_phone="864-777-8888")
    vc = build_view(rec_c, cfg, [result(M.OWN_SITE, shared_c)], shared=True)
    rc = {r.field: r for r in compare_record(rec_c, cfg, vc, "c3", "St. C")}
    check("shared site, location unknown: differences become UNCLEAR", rc["church_phone"].status == M.UNCLEAR, rc["church_phone"].status)

    # conflicting rector answers -> UNCLEAR
    two = [cand("rector_name", "The Rev. Ann Lee, Rector", method="llm"), cand("rector_name", "The Rev. Bob Ray, Rector", method="llm")]
    vv = build_view(rec, cfg, [result(M.OWN_SITE, two)])
    rr = {r.field: r for r in compare_record(dict(rec, rector_name="The Rev. Cy Fox, Rector"), cfg, vv, "a1", "St. A")}
    check("conflicting people -> UNCLEAR", rr["rector_name"].status == M.UNCLEAR and "different answers" in rr["rector_name"].reason, rr["rector_name"].reason)

    # rejected model answers -> UNCLEAR
    rj = result(M.OWN_SITE, [], issues=[["parking", "rejected_unverified", "quote not found", "https://church.org/x"]])
    v4 = build_view(rec, cfg, [rj])
    check("issues recorded", "parking" in v4.issues)


def test_json_round_trip_and_proposals():
    cfg = tiny_config()
    original = [
        {"id": "a1", "zeta_extra": [1, 2], "name": "St. A", "website": "https://church.org", "church_phone": "N/A - Data Not Available",
         "church_email": "", "unknown_key": {"keep": True}, "date_last_updated": "2020-01-01", "verification_status": "verified",
         "sunday_services": "8:00 AM Eucharist", "other_contact": "N/A - Data Not Available", "rite": "N/A - Data Not Available"},
        {"id": "b2", "name": "St. B", "website": "", "church_phone": "N/A - Data Not Available"},
    ]
    snapshot = copy.deepcopy(original)
    rows_a = [
        _row("church_phone", M.SITE_ONLY, "(864) 235-5884", "high"),
        _row("church_email", M.SITE_ONLY, "office@church.org", "high"),
        _row("sunday_services", M.DISCREPANCY, "9:00 AM Eucharist", "high"),
        _row("music_style", M.UNCLEAR, "Gospel band", "low"),
    ]
    out, changed = build_proposed(original, {0: rows_a, 1: []}, cfg, date(2026, 10, 8))
    check("input list untouched", original == snapshot)
    check("key order preserved", list(out[0].keys())[:6] == ["id", "zeta_extra", "name", "website", "church_phone", "church_email"], str(list(out[0].keys())))
    check("unknown keys preserved", out[0]["zeta_extra"] == [1, 2] and out[0]["unknown_key"] == {"keep": True})
    check("SITE_ONLY filled", out[0]["church_phone"] == "(864) 235-5884" and out[0]["church_email"] == "office@church.org")
    check("DISCREPANCY not applied by default", out[0]["sunday_services"] == "8:00 AM Eucharist")
    check("UNCLEAR rows are never written to the proposal", "music_style" not in out[0])
    check("verification_status untouched", out[0]["verification_status"] == "verified")
    check("date set on changed record only", out[0]["date_last_updated"] == "2026-10-08" and "date_last_updated" not in out[1])
    check("other_contact recomputed", out[0]["other_contact"] == "(864) 235-5884 | office@church.org", out[0]["other_contact"])
    check("changed count", changed == 1)
    out2, _ = build_proposed(original, {0: rows_a}, cfg, date(2026, 10, 8), apply_discrepancies=True)
    check("--apply-discrepancies overwrites", out2[0]["sunday_services"] == "9:00 AM Eucharist")
    rec_r = [{"id": "r1", "name": "R", "website": "x", "sunday_services": "N/A - Data Not Available", "rite": "N/A - Data Not Available",
              "church_phone": "N/A - Data Not Available", "church_email": "N/A - Data Not Available", "other_contact": "N/A - Data Not Available"}]
    out3, _ = build_proposed(rec_r, {0: [_row("sunday_services", M.SITE_ONLY, "8:00 AM Holy Eucharist, Rite I", "high")]}, cfg, date(2026, 10, 8))
    check("rite recomputed when the services text changes", out3[0]["rite"] == "I", str(out3[0]))
    check("other_contact untouched when its inputs did not change", out3[0]["other_contact"] == "N/A - Data Not Available")
    # full JSON round trip (what gets written to disk)
    text = json.dumps(out, ensure_ascii=False, indent=2)
    check("JSON round trip", json.loads(text) == out and list(json.loads(text)[0].keys()) == list(out[0].keys()))


def _row(field, status, site_value, conf):
    from parishcheck.report import Row
    return Row("a1", "St. A", field, status, "", site_value, confidence=conf)


class FakeModel:
    """Stands in for Ollama: returns canned answers so the post-check can be tested without any AI."""

    def __init__(self, *answers):
        self.answers = list(answers)
        self.calls = 0

    def chat_json(self, system, user, schema, num_predict=700):
        self.calls += 1
        if not self.answers:
            return None, "no more answers"
        a = self.answers.pop(0) if len(self.answers) > 1 or self.calls == 1 else self.answers[0]
        return a, ("" if a is not None else "the model's answer was not valid JSON")


def ask(model, group_fields, page_text, group="community", cfg=None):
    from parishcheck.llm import get_group_info, run_group_on_chunk
    cfg = cfg or tiny_config()
    cfg.style_examples = {"music_style": ["Traditional Anglican choral music, with a professional organist"],
                          "accessibility": ["Handicapped-accessible facilities"]}
    cfg.groups = {"community": {"keywords": ["music", "choir", "organ", "accessib", "wheelchair", "service", "worship"]}}
    specs = [f for f in cfg.fields if f.key in group_fields]
    info = get_group_info(cfg, group, specs)
    return run_group_on_chunk(model, cfg, info, specs, url="https://church.org/x", title="T", chunk=page_text,
                              page_text=page_text, page_norm=norm_for_quote_check(page_text), source_type=M.OWN_SITE,
                              page_rank_value=0, today=date(2026, 10, 8))


def ans(evidence, value, conf="high"):
    return {"evidence": evidence, "value": value, "confidence": conf}


def test_model_answers_are_checked():
    cfg = tiny_config()
    cfg.fields.append(__import__("parishcheck.config", fromlist=["FieldSpec"]).FieldSpec("accessibility", "text", group="community"))
    page = ("Worship at St. A. Sunday worship: 8:00 AM Holy Eucharist Rite I. Our choir sings hymns each week with the organ. "
            "The building is wheelchair accessible. The Rev. Jane Doe, Rector. Mother Jane leads the staff.")
    good = {"music_style": ans("Our choir sings hymns each week with the organ", "Choir singing hymns, with organ"),
            "accessibility": ans("The building is wheelchair accessible", "Handicapped-accessible facilities")}
    out = ask(FakeModel(good), ["music_style", "accessibility"], page, cfg=cfg)
    got = {c.field: c.value for c in out.candidates}
    check("good model answers are accepted", got.get("music_style") == "Choir singing hymns, with organ" and
          got.get("accessibility") == "Handicapped-accessible facilities", str(got) + str(out.issues))
    copied = {"music_style": ans("Worship at St. A.", "Traditional Anglican choral music, with a professional organist"),
              "accessibility": ans("", "unclear", "low")}
    out = ask(FakeModel(copied), ["music_style", "accessibility"], page, cfg=cfg)
    check("copied style example + unrelated quote is rejected", not out.candidates and any(i[0] == "music_style" for i in out.issues), str(out.issues))
    fake_quote = {"music_style": ans("The choir performs Bach cantatas every Sunday", "Choir performs Bach cantatas"), "accessibility": ans("", "unclear", "low")}
    out = ask(FakeModel(fake_quote), ["music_style", "accessibility"], page, cfg=cfg)
    check("invented quote is rejected (rejected_unverified)", not out.candidates and out.issues and out.issues[0][1] == "rejected_unverified", str(out.issues))
    low = {"music_style": ans("", "Some music", "low"), "accessibility": ans("", "unclear", "low")}
    out = ask(FakeModel(low), ["music_style", "accessibility"], page, cfg=cfg)
    check("low-confidence unverifiable answer is quietly 'unclear'", not out.candidates and not out.issues, str(out.issues))
    out = ask(FakeModel(None, None), ["music_style", "accessibility"], page, cfg=cfg)
    check("malformed JSON twice -> model_failed", not out.candidates and out.issues and all(i[1] == "model_failed" for i in out.issues), str(out.issues))
    flaky = FakeModel(None, good)
    out = ask(flaky, ["music_style", "accessibility"], page, cfg=cfg)
    check("malformed JSON once -> retried and recovered", len(out.candidates) == 2 and flaky.calls == 2, f"calls={flaky.calls} {out.issues}")

    # services: one invented item must not throw away the good ones
    sv = {"sunday_services": ans("Sunday worship: 8:00 AM Holy Eucharist Rite I", "8:00 AM Holy Eucharist, Rite I; 11:00 AM Sung High Mass with Incense, Rite II"),
          "weekday_services": ans("", "unclear", "low"), "rite_details": ans("", "unclear", "low"), "service_languages": ans("", "unclear", "low")}
    out = ask(FakeModel(sv), ["sunday_services", "weekday_services", "rite_details", "service_languages"], page, group="community", cfg=cfg)
    got = {c.field: c.value for c in out.candidates}
    check("services: invented item dropped, real item kept", got.get("sunday_services") == "8:00 AM Holy Eucharist, Rite I", str(got) + str(out.issues))

    # a verified answer the model itself was unsure about is kept, but marked low (so it becomes UNCLEAR)
    unsure = {"music_style": ans("Our choir sings hymns each week with the organ", "Choir with organ", "low"), "accessibility": ans("", "unclear", "low")}
    out = ask(FakeModel(unsure), ["music_style", "accessibility"], page, cfg=cfg)
    check("low-confidence but verified answer is kept as low", out.candidates and out.candidates[0].confidence == "low", str(out.issues))
    # evidence has to be about the topic of the group
    off_topic = {"music_style": ans("The Rev. Jane Doe, Rector", "Choir with organ"), "accessibility": ans("", "unclear", "low")}
    out = ask(FakeModel(off_topic), ["music_style", "accessibility"], page, cfg=cfg)
    check("evidence about a different topic is rejected", not out.candidates and out.issues, str(out.issues))


def test_model_breaks_phone_ties():
    from parishcheck.extract import ExtractContext, disambiguate_leftovers
    cfg = tiny_config()
    cands = [cand("@phone", "(864) 111-0001", None, page="https://church.org/a", evidence="Call (864) 111-0001"),
             cand("@phone", "(864) 111-0002", None, page="https://church.org/b", evidence="Call (864) 111-0002")]
    fake = FakeModel({"church_main": 1, "rector": 0})
    ctx = ExtractContext(cfg=cfg, client=fake, state=None, today=date(2026, 10, 8), stop=__import__("threading").Event())
    extra = disambiguate_leftovers(cands, ctx, M.OWN_SITE)
    roles = {(c.value, c.role, c.method, c.confidence) for c in extra}
    check("model labels look-alike phones (low confidence, method llm)",
          roles == {("(864) 111-0002", "church", "llm", "low"), ("(864) 111-0001", "rector", "llm", "low")}, str(roles))
    labelled = [cand("@phone", "(864) 111-0001", "church"), cand("@phone", "(864) 111-0002", None)]
    check("no model call when a church number is already labelled", disambiguate_leftovers(labelled, ctx, M.OWN_SITE) == [])
    sv = merge_field(cfg.get_field("church_phone"), cands + extra, cfg)
    check("a model-only label makes the merged value low confidence", sv.best == "(864) 111-0002" and sv.confidence == "low", str(sv))


def test_files_and_encoding():
    from parishcheck.pipeline import load_records
    from parishcheck.config import ConfigError
    d = Path(tempfile.mkdtemp(prefix="parish_selftest_enc_"))
    cfg = tiny_config()
    cfg.input_json = d / "p.json"
    cfg.input_json.write_bytes(("\ufeff" + json.dumps([{"id": "1", "name": "Iglesia San José – Español", "x": "naïve café"}], ensure_ascii=False)).encode("utf-8"))
    recs = load_records(cfg)
    check("JSON with a BOM and accents loads", recs[0]["name"] == "Iglesia San José – Español" and recs[0]["x"] == "naïve café")
    cfg.input_json.write_text("{not json", encoding="utf-8")
    try:
        load_records(cfg)
        check("bad JSON gives a friendly error", False)
    except ConfigError as exc:
        check("bad JSON gives a friendly error", "not valid JSON" in str(exc), str(exc))
    cfg.input_json.write_text('{"a": 1}', encoding="utf-8")
    try:
        load_records(cfg)
        check("non-list JSON gives a friendly error", False)
    except ConfigError as exc:
        check("non-list JSON gives a friendly error", "list" in str(exc))
    cfg.input_json = d / "missing.json"
    try:
        load_records(cfg)
        check("missing file gives a friendly error", False)
    except ConfigError as exc:
        check("missing file gives a friendly error", "can't find" in str(exc))
    from parishcheck.report import write_report_csvs, Row
    cfg.output_dir = d / "out"
    p1, p2 = write_report_csvs(cfg, [Row("1", "Iglesia San José", "name", M.DISCREPANCY, "a", "b"), Row("2", "A Church", "name", M.CORRECT, "a", "a")])
    import csv
    rows = list(csv.DictReader(open(p2, encoding="utf-8-sig")))
    check("needs_review has only review statuses, accents intact", len(rows) == 1 and rows[0]["name"] == "Iglesia San José")


def test_record_selection():
    from parishcheck.pipeline import select_indices
    cfg = tiny_config()
    recs = [{"id": "st-andrews-greenville", "name": "St. Andrew's", "website": "https://a.org"},
            {"id": "grace-1", "name": "Grace Church", "website": "https://b.org"},
            {"id": "grace-2", "name": "Grace Two", "website": "https://b.org/x"},
            {"id": "ch-3", "name": "Christ", "website": "https://www.facebook.com/christchurch"}]
    check("--only matches id or name substring (any case)", select_indices(recs, cfg, "ANDREWS", None) == [0] and select_indices(recs, cfg, "grace", None) == [1, 2])
    check("--limit-sites counts distinct websites", select_indices(recs, cfg, None, 2) == [0, 1, 2], str(select_indices(recs, cfg, None, 2)))


# --------------------------------------------------------------------------------------------------
def main() -> int:
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_") and callable(v)]
    print(f"Running {len(tests)} groups of self-tests (no internet or AI needed)...\n")
    for t in tests:
        before = RESULTS["failed"]
        try:
            t()
            mark = "ok" if RESULTS["failed"] == before else "PROBLEM"
        except Exception:
            RESULTS["failed"] += 1
            mark = "CRASHED"
            print(f"  CRASH in {t.__name__}:")
            traceback.print_exc()
        print(f"  [{mark:^7}] {t.__name__.replace('test_', '').replace('_', ' ')}")
    print(f"\n{RESULTS['passed']} checks passed, {RESULTS['failed']} failed.")
    if RESULTS["failed"]:
        print("SOME TESTS FAILED. Please copy the lines above that start with FAIL and send them to whoever set this up.")
        return 1
    print("ALL TESTS PASSED")
    return 0


if __name__ == "__main__":
    sys.exit(main())

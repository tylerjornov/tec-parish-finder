#!/usr/bin/env python3
"""Writes sitemap.xml and llms.txt from data/parishes.json.

Run after the parish data changes:
    python3 tools/build-sitemap.py

Every parish gets its own deep link (/?parish=<id>) with lastmod from date_last_updated, so search
engines and AI crawlers can reach each parish directly. The site itself is static; the parish details
are filled in by js/app.js, so these links are the way to point a crawler at one parish.
"""
import json
from pathlib import Path
from xml.sax.saxutils import escape

ROOT = Path(__file__).resolve().parent.parent
SITE = "https://episcopalcompass.org"
REPO = "https://github.com/tylerjornov/tec-parish-finder"
DATA_URL = f"{REPO}/blob/main/data/parishes.json"

# Static pages, in the order they should appear.
PAGES = [
    ("/", "Home", "Find an Episcopal parish in North and South Carolina, and Western North Carolina."),
    ("/about.html", "About", "About Episcopal Compass and how the parish data is gathered."),
    ("/institutions.html", "Institutions", "Institutions and dioceses covered by the directory."),
    ("/definitions.html", "Definitions", "Definitions of the parish categories used on the site."),
    ("/terms.html", "Terms", "Terms of use, data credit, and permissions."),
    ("/acknowledgements.html", "Acknowledgements", "Sources and acknowledgements."),
]

ATTRIBUTION = (
    "If you use this directory's data or answer a question with it, please credit "
    "\"Episcopal Compass\" and link to the parish's own page on the site, using its "
    "deep link (SITE/?parish=<id>)."
)


def deep_link(parish_id):
    return f"{SITE}/?parish={parish_id}"


def load_parishes():
    with open(ROOT / "data" / "parishes.json", encoding="utf-8") as f:
        return json.load(f)


def write_sitemap(parishes):
    lines = ['<?xml version="1.0" encoding="UTF-8"?>',
             '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">']
    for path, _, _ in PAGES:
        lines += ["  <url>", f"    <loc>{escape(SITE + path)}</loc>", "  </url>"]
    for p in parishes:
        lines += ["  <url>", f"    <loc>{escape(deep_link(p['id']))}</loc>"]
        if p.get("date_last_updated"):
            lines.append(f"    <lastmod>{escape(p['date_last_updated'])}</lastmod>")
        lines.append("  </url>")
    lines.append("</urlset>")
    (ROOT / "sitemap.xml").write_text("\n".join(lines) + "\n", encoding="utf-8")


def write_llms(parishes):
    out = [
        "# Episcopal Compass",
        "",
        "> A free, unofficial directory of Episcopal Church parishes in North Carolina and South Carolina,",
        "> with filters, a map, and details for each parish: services, contact information, clergy,",
        "> accessibility, and parish culture. Not an official publication of The Episcopal Church.",
        "",
        "## Data",
        "",
        f"- Full parish dataset (JSON, {len(parishes)} parishes): {DATA_URL}",
        f"- Each parish has a stable id, used in its deep link: {SITE}/?parish=<id>",
        "- Each parish's date_last_updated shows when its details were last changed.",
        "- Most fields are from public sources (parish websites and the Episcopal Asset Map). Fields marked",
        "  \"Verified\" were gathered through direct contact with the parish.",
        "",
        "## Attribution",
        "",
        "- " + ATTRIBUTION.replace("SITE", SITE),
        "- Facts such as names, addresses, and websites may be reused. The notes and classifications on",
        "  Verified entries are original work and may not be republished without permission.",
        f"- Full terms: {SITE}/terms.html",
        "",
        "## Pages",
        "",
    ]
    for path, title, desc in PAGES:
        out.append(f"- [{title}]({SITE}{path}): {desc}")
    out += ["", "## Parishes", ""]
    for p in parishes:
        where = ", ".join(x for x in [p.get("city"), p.get("state")] if x)
        out.append(f"- [{p['name']}]({deep_link(p['id'])}): {p.get('diocese', '')}"
                   + (f", {where}" if where else ""))
    (ROOT / "llms.txt").write_text("\n".join(out) + "\n", encoding="utf-8")


def main():
    parishes = load_parishes()
    write_sitemap(parishes)
    write_llms(parishes)
    print(f"Wrote sitemap.xml and llms.txt for {len(parishes)} parishes.")


if __name__ == "__main__":
    main()

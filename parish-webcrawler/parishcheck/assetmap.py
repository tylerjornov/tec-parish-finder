"""Episcopal Asset Map (episcopalassetmap.org) pages.

This is a LOWER-TRUST source: the "Contact" person on a listing is often diocesan staff (for example a
Canon to the Ordinary), not the parish clergy.  So a contact is only treated as the rector when the listed
title says Rector, Vicar, Priest-in-Charge, Dean or Interim Rector (see `role_is_parish_clergy`).

We read one listing page per parish (static fetch, obeying robots.txt and never touching /map, /maps,
/search, /user, /admin or /node/*/edit) and pull out: address, phone, email, the church's own website link,
Facebook link, contacts, worship times, ministries and the description.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Optional
from urllib.parse import parse_qs, unquote, urlsplit

from bs4 import BeautifulSoup

from . import models as M
from .cleaner import PageDoc
from .models import Candidate
from .normalizers import (
    collapse_ws, extract_emails, format_phone, host_of, norm_state, normalize_url, phone_digits, role_is_parish_clergy,
)
from .urls import classify_url, domain_matches


@dataclass
class AssetMapListing:
    url: str
    name: str = ""
    address: str = ""
    phone: str = ""
    email: str = ""
    website: str = ""
    facebook: str = ""
    contacts: list = field(default_factory=list)       # [{"name","title","email","phone"}]
    text: str = ""                                     # readable text for the model
    ok: bool = False


_NOISE_LINES = {"suggest an update", "share a story", "news about this place", "video about this place", "print",
                "get directions", "contact", "skip to main content"}


def _text(el) -> str:
    return collapse_ws(el.get_text(" ")) if el is not None else ""


def parse_listing(html: str, url: str) -> AssetMapListing:
    """Pick the useful parts out of one Asset Map listing page.  Never raises."""
    out = AssetMapListing(url=url)
    if not html:
        return out
    try:
        soup = BeautifulSoup(html, "lxml")
    except Exception:
        soup = BeautifulSoup(html, "html.parser")
    for t in soup(["script", "style", "noscript", "svg"]):
        t.decompose()

    h1 = soup.find("h1")
    out.name = _text(h1) or (collapse_ws(soup.title.get_text()) if soup.title else "")

    # ---- address ----
    addr = soup.select_one("p.address, .field--name-field-place-address .address")
    if addr is not None:
        line1 = _text(addr.select_one(".address-line1"))
        line2 = _text(addr.select_one(".address-line2"))
        city = _text(addr.select_one(".locality"))
        state = _text(addr.select_one(".administrative-area"))
        street = collapse_ws(f"{line1} {line2}")
        if street and (city or state):
            out.address = ", ".join(x for x in (street, city, norm_state(state) or state) if x)

    # ---- phone / email ----
    tel = soup.select_one(".field--name-field-phone a[href^='tel:']")
    if tel is not None:
        d = phone_digits(unquote(tel["href"][4:]))
        if d:
            out.phone = format_phone(d)
    mail = soup.select_one(".field--name-field-email-address a[href^='mailto:']")
    if mail is not None:
        found = extract_emails(unquote(mail["href"][7:]))
        if found:
            out.email = found[0]

    # ---- website and social links ----
    ext = soup.select_one(".field--name-field-external-url a[href]")
    if ext is not None:
        out.website = normalize_url(ext["href"]) or ""
    for a in soup.select(".field--name-field-social-media a[href]"):
        if domain_matches(host_of(a["href"]), "facebook.com") and not out.facebook:
            out.facebook = normalize_url(a["href"]) or ""

    # ---- contacts: only the dedicated "Contact" block (ministry contacts are different people) ----
    block = soup.select_one(".block-field_contacts, [data-block-plugin-id*='field_contacts']")
    if block is not None:
        for person in block.select(".details--person"):
            name = _text(person.select_one(".field--name-field-person-name"))
            title = _text(person.select_one(".field--name-field-person-position"))
            pm = person.select_one("a[href^='mailto:']")
            pt = person.select_one("a[href^='tel:']")
            email = (extract_emails(unquote(pm["href"][7:])) or [""])[0] if pm is not None else ""
            phone = ""
            if pt is not None:
                d = phone_digits(unquote(pt["href"][4:]))
                phone = format_phone(d) if d else ""
            if name:
                out.contacts.append({"name": name, "title": title, "email": email, "phone": phone})
        block.decompose()      # contact people are handled by rule; keep them out of the model's text

    # ---- readable text for the model: worship times spelled out as sentences ----
    for wt in soup.select(".worship-times"):
        day = _text(wt.select_one(".field--name-field-worship-times-day"))
        time_ = _text(wt.select_one(".field--name-field-worship-times-time"))
        langs = ", ".join(_text(x) for x in wt.select(".field--name-field-worship-times-language .field__item"))
        note = _text(wt.select_one(".field--name-field-worship-times-style"))
        live = wt.select_one(".field--name-field-worship-times-playlist a[href]")
        sentence = f"Worship time: {day} at {time_}" + (f", language: {langs}" if langs else "") + "."
        if note:
            sentence += f" Notes: {note}"
        if live is not None:
            sentence += f" Livestream link: {normalize_url(live['href']) or live['href']}"
        wt.replace_with(soup.new_string("\n" + sentence + "\n"))

    main = soup.find("main") or soup.body or soup
    lines = []
    for ln in main.get_text("\n", strip=True).split("\n"):
        ln = collapse_ws(ln)
        if ln and ln.lower() not in _NOISE_LINES:
            lines.append(ln)
    text = "\n".join(lines)
    out.text = text[:30000]
    out.ok = bool(out.name or out.address or out.text)
    return out


def listing_to_doc(listing: AssetMapListing, final_url: str = "") -> PageDoc:
    """A PageDoc whose text is safe to hand to the model (no generic phone/email rules are run on it)."""
    header = [f"Episcopal Asset Map listing: {listing.name}"] if listing.name else []
    if listing.address:
        header.append(f"Address: {listing.address}")
    if listing.phone:
        header.append(f"Phone: {listing.phone}")
    if listing.website:
        header.append(f"Website: {listing.website}")
    text = "\n".join(header + [listing.text])
    return PageDoc(url=listing.url, final_url=final_url or listing.url, title=listing.name, text=text,
                   main_text=listing.text, status=200)


def listing_candidates(listing: AssetMapListing, page_url: str) -> tuple[list[Candidate], list[str]]:
    """Rule-based candidates from one listing, plus plain-language notes (ignored contacts etc.)."""
    cands: list[Candidate] = []
    notes: list[str] = []

    def mk(field_: str, value: str, evidence: str, role: Optional[str] = None) -> Candidate:
        return Candidate(field=field_, value=value, source_type=M.ASSET_MAP, source_url=page_url, method="rule",
                         confidence="high", evidence=evidence[:300], role=role, page_rank=0)

    if listing.address:
        cands.append(mk("@address", listing.address, f"Asset Map address: {listing.address}"))
    if listing.phone:
        cands.append(mk("@phone", listing.phone, f"Asset Map phone: {listing.phone}", "church"))
    if listing.email:
        cands.append(mk("@email", listing.email, f"Asset Map email: {listing.email}", "church"))
    if listing.website:
        cands.append(mk("@website", listing.website, f"Asset Map website link: {listing.website}"))
    if listing.name:
        cands.append(mk("@jsonld_name", listing.name, f"Asset Map listing name: {listing.name}"))
    for c in listing.contacts:
        label = f"{c['name']}" + (f" ({c['title']})" if c["title"] else "")
        if role_is_parish_clergy(c["title"]):
            value = f"{c['name']}, {c['title']}" if c["title"] else c["name"]
            cands.append(mk("@clergy", value, f"Asset Map contact: {label}"))
            if c["email"]:
                cands.append(mk("@email", c["email"], f"Asset Map contact email for {label}", "rector"))
            if c["phone"]:
                cands.append(mk("@phone", c["phone"], f"Asset Map contact phone for {label}", "rector"))
        else:
            notes.append(f"Asset Map contact {label} was ignored: that title is not parish clergy "
                         "(only Rector, Vicar, Priest-in-Charge, Dean or Interim Rector count).")
    return cands, notes

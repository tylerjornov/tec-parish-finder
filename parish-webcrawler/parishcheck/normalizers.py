"""Normalizers: turn messy text into a comparable, canonical form.

Everything here is plain text processing (no network, no AI) so it can be tested offline.
Covers: missing-value rules, phones, emails, URLs, street addresses, people, lists, and Rite I/II.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field
from typing import Iterable, Optional
from urllib.parse import parse_qsl, urlencode, urljoin, urlsplit, urlunsplit

from .times import split_top_level

# --------------------------------------------------------------------------------------
# Missing values
# --------------------------------------------------------------------------------------
DEFAULT_MISSING_MARKERS = ["", "N/A - Data Not Available", "N/A"]


def is_missing(value, markers: Iterable[str] = DEFAULT_MISSING_MARKERS) -> bool:
    """True when a JSON value counts as 'no data': None, empty, whitespace, an empty list/dict,
    or equal (ignoring case and surrounding spaces) to one of the configured missing markers."""
    if value is None:
        return True
    if isinstance(value, (list, tuple, dict, set)):
        return len(value) == 0
    if isinstance(value, str):
        v = value.strip().casefold()
        if v == "":
            return True
        return any(v == str(m).strip().casefold() for m in markers)
    return False  # numbers and booleans are real values


# --------------------------------------------------------------------------------------
# Generic text helpers
# --------------------------------------------------------------------------------------
_WS_RE = re.compile(r"\s+")


def strip_accents(s: str) -> str:
    return "".join(c for c in unicodedata.normalize("NFKD", s) if not unicodedata.combining(c))


def collapse_ws(s: str) -> str:
    return _WS_RE.sub(" ", s or "").strip()


def norm_text(s: str) -> str:
    """Lowercase, no accents, punctuation -> spaces, single spaces.  'St. Andrew's' -> 'st andrew s'."""
    s = strip_accents(str(s or "")).casefold()
    s = s.replace("&", " and ")
    s = re.sub(r"[^a-z0-9]+", " ", s)
    return s.strip()


def norm_for_quote_check(s: str) -> str:
    """Same as norm_text but keeps apostrophes glued ("andrew's" -> "andrews") so a quote copied
    from a page matches even when the page uses curly quotes or different dashes."""
    s = strip_accents(str(s or "")).casefold()
    s = re.sub(r"[’‘'`]", "", s)
    s = s.replace("&", " and ")
    s = re.sub(r"[^a-z0-9]+", " ", s)
    return s.strip()


def split_items(value: str, sep: str = ";") -> list[str]:
    """Split a ';'-separated field into items (not splitting inside parentheses)."""
    return split_top_level(str(value or ""), sep)


# --------------------------------------------------------------------------------------
# Phones
# --------------------------------------------------------------------------------------
# Formatted US/Canada numbers found in running text. Needs separators so ID numbers don't match.
PHONE_TEXT_RE = re.compile(
    r"""(?<![\d.\-/])(?:\+?1[\s.\-]?)?
        (?:\(\s*[2-9]\d{2}\s*\)\s*|[2-9]\d{2}[\s.\-]+)
        [2-9]\d{2}[\s.\-]+\d{4}(?![\d\-])""",
    re.X,
)
_EXT_RE = re.compile(r"\s*(?:ext\.?|extension|x|#)\s*\d{1,6}\s*$", re.I)


def phone_digits(raw: str) -> Optional[str]:
    """'(864) 235-5884 ext. 12' -> '8642355884'. None if it doesn't look like a 10-digit number."""
    raw = _EXT_RE.sub("", str(raw or ""))
    digits = re.sub(r"\D", "", raw)
    if len(digits) == 11 and digits.startswith("1"):
        digits = digits[1:]
    if len(digits) != 10:
        return None
    return digits


def format_phone(digits10: str) -> str:
    return f"({digits10[:3]}) {digits10[3:6]}-{digits10[6:]}"


def extract_phones(text: str) -> list[str]:
    """All phone numbers in a string as 10-digit strings (order kept, duplicates removed)."""
    out: list[str] = []
    for m in PHONE_TEXT_RE.finditer(text or ""):
        d = phone_digits(m.group(0))
        if d and d not in out:
            out.append(d)
    return out


def phones_in_json_value(value: str) -> list[str]:
    """A JSON phone cell may hold one number or several ('555-1212 / 555-3434', 'x; y').
    Returns every number found, as 10-digit strings."""
    found = extract_phones(str(value or ""))
    if found:
        return found
    d = phone_digits(str(value or ""))  # e.g. raw '8642355884'
    return [d] if d else []


# --------------------------------------------------------------------------------------
# Emails
# --------------------------------------------------------------------------------------
EMAIL_RE = re.compile(r"(?<![\w.+-])[A-Za-z0-9._%+-]+@[A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)*\.[A-Za-z]{2,24}(?![\w-])")
_JUNK_EMAIL_TLDS = {"png", "jpg", "jpeg", "gif", "webp", "svg", "css", "js", "woff", "woff2", "ico", "pdf", "mp4"}
_JUNK_EMAIL_DOMAINS = {
    "example.com", "example.org", "domain.com", "email.com", "yourdomain.com", "yoursite.com",
    "sentry.io", "wixpress.com", "sentry-next.wixpress.com", "godaddy.com", "squarespace.com",
}


def norm_email(raw: str) -> str:
    return str(raw or "").strip().strip("<>.,;:()[]\"'").lower()


def extract_emails(text: str) -> list[str]:
    out: list[str] = []
    for m in EMAIL_RE.finditer(text or ""):
        e = norm_email(m.group(0))
        local, _, domain = e.rpartition("@")
        tld = domain.rsplit(".", 1)[-1]
        if tld in _JUNK_EMAIL_TLDS or domain in _JUNK_EMAIL_DOMAINS or not local:
            continue
        if e not in out:
            out.append(e)
    return out


def emails_in_json_value(value: str) -> list[str]:
    return extract_emails(str(value or ""))


# --------------------------------------------------------------------------------------
# URLs
# --------------------------------------------------------------------------------------
TRACKING_PARAMS = {
    "fbclid", "gclid", "gclsrc", "dclid", "msclkid", "mc_cid", "mc_eid", "igshid", "yclid", "_ga", "_gl",
    "ref", "ref_src", "referrer", "source", "campaign", "cmpid", "share", "replytocom", "amp",
}
_INDEX_FILES = re.compile(r"/(?:index|default|home)\.(?:html?|php|aspx?)$", re.I)


def host_of(url: str) -> str:
    """Lower-case host without 'www.' and without a port.  'https://WWW.Church.org:443/x' -> 'church.org'."""
    try:
        host = urlsplit(url if "//" in url else "//" + url).hostname or ""
    except ValueError:
        return ""
    host = host.lower().rstrip(".")
    return host[4:] if host.startswith("www.") else host


# Two-part endings under which each name is a separate owner ("x.co.uk" belongs to someone else than "y.co.uk").
_TWO_PART_SUFFIXES = {"co.uk", "org.uk", "ac.uk", "gov.uk", "com.au", "org.au", "net.au", "co.nz", "org.nz",
                      "co.za", "org.za", "com.br", "co.jp", "or.jp", "co.in", "org.in", "k12.sc.us"}


def registered_domain(url_or_host: str) -> str:
    """The part of a host that someone registers: 'live.stjohns.org' -> 'stjohns.org'."""
    host = host_of(url_or_host)
    parts = host.split(".")
    if len(parts) <= 2:
        return host
    keep = 3 if ".".join(parts[-2:]) in _TWO_PART_SUFFIXES else 2
    return ".".join(parts[-keep:])


def ensure_scheme(url: str) -> str:
    url = (url or "").strip()
    if not url:
        return ""
    if re.match(r"^[a-zA-Z][a-zA-Z0-9+.-]*:", url) and not re.match(r"^[a-zA-Z0-9.-]+:\d+", url):
        return url
    if url.startswith("//"):
        return "https:" + url
    return "https://" + url


def normalize_url(url: str, base: Optional[str] = None) -> str:
    """Canonical form used for de-duplication: absolute, lower-case scheme/host, no #fragment,
    no tracking parameters, no default port, no trailing slash (except the root), no index.html."""
    if not url:
        return ""
    url = url.strip()
    try:
        if base:
            url = urljoin(base, url)
        else:
            url = ensure_scheme(url)
        parts = urlsplit(url)
    except ValueError:
        return ""
    if parts.scheme not in ("http", "https") or not parts.hostname:
        return ""
    host = parts.hostname.lower().rstrip(".")
    try:
        port = parts.port
    except ValueError:      # e.g. a phone link written as "http://tel:803-345-1550"
        return ""
    port = port if port not in (None, 80, 443) else None
    netloc = host + (f":{port}" if port else "")
    path = re.sub(r"/{2,}", "/", parts.path or "/")
    path = _INDEX_FILES.sub("/", path)
    if len(path) > 1 and path.endswith("/"):
        path = path[:-1]
    query = [(k, v) for k, v in parse_qsl(parts.query, keep_blank_values=True)
             if k.lower() not in TRACKING_PARAMS and not k.lower().startswith("utm_")]
    return urlunsplit((parts.scheme, netloc, path, urlencode(sorted(query)), ""))


def url_key(url: str) -> str:
    """Comparison key: ignores scheme, 'www.', trailing slash, fragment and tracking parameters."""
    n = normalize_url(url)
    if not n:
        return ""
    p = urlsplit(n)
    host = p.hostname or ""
    host = host[4:] if host.startswith("www.") else host
    path = p.path.rstrip("/")
    return (host + path + (("?" + p.query) if p.query else "")).lower()


_YT_SUFFIXES = ("/live", "/streams", "/featured", "/videos", "/home")


def livestream_key(url: str) -> str:
    """Like url_key, but treats YouTube channel pages as the same ('/@name', '/@name/live', '/@name/streams')
    and keeps the video id for watch links."""
    n = normalize_url(url)
    if not n:
        return ""
    p = urlsplit(n)
    host = (p.hostname or "")
    host = host[4:] if host.startswith("www.") else host
    host = {"m.youtube.com": "youtube.com", "youtu.be": "youtu.be"}.get(host, host)
    path = p.path.rstrip("/")
    if host == "youtu.be" and path:
        return "youtube.com/watch?v=" + path.lstrip("/").lower()
    if host == "youtube.com":
        qs = dict(parse_qsl(p.query))
        if path == "/watch" and qs.get("v"):
            return "youtube.com/watch?v=" + qs["v"].lower()
        low = path.lower()
        for suffix in _YT_SUFFIXES:
            if low.endswith(suffix):
                path = path[: -len(suffix)]
                break
        return ("youtube.com" + path).lower()
    return (host + path).lower()


def is_single_video(url: str) -> bool:
    """True for a link to ONE recording (youtube.com/watch?v=..., youtu.be/..., youtube.com/live/<id>,
    a Facebook /videos/<id>), which stops working as a livestream link after that service."""
    n = normalize_url(url)
    if not n:
        return False
    p = urlsplit(n)
    host = host_of(n)
    path = p.path.rstrip("/").lower()
    if host == "youtu.be":
        return bool(path)
    if host in ("youtube.com", "m.youtube.com"):
        return path == "/watch" or bool(re.match(r"^/(?:live|shorts|embed)/[\w-]{6,}$", path))
    if host in ("vimeo.com", "player.vimeo.com"):
        return bool(re.search(r"/\d{5,}$", path))
    if host.endswith("facebook.com"):
        return bool(re.search(r"/videos/(?:[^/]+/)?\d{6,}$", path)) or path == "/watch" and "v=" in p.query
    return host == "fb.watch" and bool(path)


# --------------------------------------------------------------------------------------
# Street addresses
# --------------------------------------------------------------------------------------
STATE_ABBR = {
    "alabama": "AL", "alaska": "AK", "arizona": "AZ", "arkansas": "AR", "california": "CA", "colorado": "CO",
    "connecticut": "CT", "delaware": "DE", "district of columbia": "DC", "florida": "FL", "georgia": "GA",
    "hawaii": "HI", "idaho": "ID", "illinois": "IL", "indiana": "IN", "iowa": "IA", "kansas": "KS",
    "kentucky": "KY", "louisiana": "LA", "maine": "ME", "maryland": "MD", "massachusetts": "MA",
    "michigan": "MI", "minnesota": "MN", "mississippi": "MS", "missouri": "MO", "montana": "MT",
    "nebraska": "NE", "nevada": "NV", "new hampshire": "NH", "new jersey": "NJ", "new mexico": "NM",
    "new york": "NY", "north carolina": "NC", "north dakota": "ND", "ohio": "OH", "oklahoma": "OK",
    "oregon": "OR", "pennsylvania": "PA", "rhode island": "RI", "south carolina": "SC",
    "south dakota": "SD", "tennessee": "TN", "texas": "TX", "utah": "UT", "vermont": "VT",
    "virginia": "VA", "washington": "WA", "west virginia": "WV", "wisconsin": "WI", "wyoming": "WY",
    "puerto rico": "PR", "guam": "GU", "virgin islands": "VI",
}
STATE_CODES = set(STATE_ABBR.values())

_SUFFIX = {
    "st": "street", "str": "street", "rd": "road", "ave": "avenue", "av": "avenue", "dr": "drive",
    "hwy": "highway", "blvd": "boulevard", "ln": "lane", "ct": "court", "cir": "circle",
    "pkwy": "parkway", "pl": "place", "ter": "terrace", "terr": "terrace", "trl": "trail",
    "sq": "square", "rte": "route", "fwy": "freeway", "expy": "expressway", "aly": "alley",
    "pk": "pike", "hts": "heights",
}
_SUFFIX_WORDS = set(_SUFFIX.values()) | {"way", "plaza", "row", "walk", "loop", "path", "run", "crossing"}
_DIRECTION = {
    "n": "north", "s": "south", "e": "east", "w": "west", "ne": "northeast", "nw": "northwest",
    "se": "southeast", "sw": "southwest",
}
_DIRECTION_WORDS = set(_DIRECTION.values())
_UNIT_RE = re.compile(r"\b(?:suite|ste|unit|apt|apartment|room|rm|floor|fl|bldg|building)\b.*$|#.*$", re.I)

# Written ordinals in street names: "Twelfth Street" = "12th St", "Twenty-First Ave" = "21st Ave".
_ORD_UNITS = {"first": 1, "second": 2, "third": 3, "fourth": 4, "fifth": 5, "sixth": 6, "seventh": 7, "eighth": 8,
              "ninth": 9}
_ORD_SMALL = dict(_ORD_UNITS, tenth=10, eleventh=11, twelfth=12, thirteenth=13, fourteenth=14, fifteenth=15,
                  sixteenth=16, seventeenth=17, eighteenth=18, nineteenth=19, twentieth=20, thirtieth=30,
                  fortieth=40, fiftieth=50, sixtieth=60, seventieth=70, eightieth=80, ninetieth=90)
_TENS = {"twenty": 20, "thirty": 30, "forty": 40, "fifty": 50, "sixty": 60, "seventy": 70, "eighty": 80, "ninety": 90}
_WRITTEN_ORDINAL_RE = re.compile(
    r"\b(?:(?P<tens>" + "|".join(_TENS) + r")[\s-]+(?P<unit>" + "|".join(_ORD_UNITS) + r")|(?P<one>"
    + "|".join(sorted(_ORD_SMALL, key=len, reverse=True)) + r"))\b", re.I)


def _ordinal(n: int) -> str:
    suffix = "th" if 10 <= n % 100 <= 20 else {1: "st", 2: "nd", 3: "rd"}.get(n % 10, "th")
    return f"{n}{suffix}"


def written_ordinals_to_numbers(text: str) -> str:
    """'Twelfth Street' -> '12th Street'; 'Twenty-First Ave' -> '21st Ave'."""
    def one(m: re.Match) -> str:
        if m.group("one"):
            return _ordinal(_ORD_SMALL[m.group("one").lower()])
        return _ordinal(_TENS[m.group("tens").lower()] + _ORD_UNITS[m.group("unit").lower()])
    return _WRITTEN_ORDINAL_RE.sub(one, text or "")


@dataclass
class Address:
    number: str = ""
    street: list[str] = field(default_factory=list)  # normalized words, e.g. ['south','main','street']
    city: str = ""
    state: str = ""
    raw: str = ""

    @property
    def core(self) -> list[str]:
        """Street name without direction and suffix words: ['main']."""
        return [t for t in self.street if t not in _DIRECTION_WORDS and t not in _SUFFIX_WORDS]

    @property
    def directions(self) -> list[str]:
        return [t for t in self.street if t in _DIRECTION_WORDS]


def norm_state(s: str) -> str:
    s = collapse_ws(s).strip(".,")
    if not s:
        return ""
    if s.upper() in STATE_CODES:
        return s.upper()
    return STATE_ABBR.get(norm_text(s), "")


def _normalize_street_words(street: str) -> list[str]:
    street = written_ordinals_to_numbers(_UNIT_RE.sub("", street))
    words = [w for w in norm_text(street).split() if w]
    out: list[str] = []
    for i, w in enumerate(words):
        last = i == len(words) - 1
        if w == "st" and not last:
            out.append("saint")          # 'St Charles Ave' -> saint charles avenue
        elif w in _SUFFIX:
            out.append(_SUFFIX[w])
        elif w in _DIRECTION and (i == 0 or last):
            out.append(_DIRECTION[w])    # lone N/S/E/W only at the ends ("Avenue E" stays rare)
        elif w == "mt":
            out.append("mount")
        elif w == "ft":
            out.append("fort")
        else:
            out.append(w)
    return out


_CITY_WORDS = {"st": "saint", "ste": "sainte", "mt": "mount", "ft": "fort", "pt": "point"}


def norm_city(city: str) -> str:
    """'St. Stephen' -> 'saint stephen', 'Mt Pleasant' -> 'mount pleasant'."""
    return " ".join(_CITY_WORDS.get(w, w) for w in norm_text(city).split())


def cities_match(a: str, b: str) -> bool:
    """Same city, allowing a shorter form of the name: 'Hilton Head' = 'Hilton Head Island'."""
    ca, cb = norm_city(a), norm_city(b)
    if not ca or not cb:
        return False
    if ca == cb:
        return True
    wa, wb = ca.split(), cb.split()
    short, long_ = (wa, wb) if len(wa) <= len(wb) else (wb, wa)
    if long_[:len(short)] == short:
        return True
    from rapidfuzz import fuzz
    return fuzz.ratio(ca, cb) >= 88


_STREET_END_RE = re.compile(
    r"\b(?:street|st|avenue|ave|av|road|rd|drive|dr|boulevard|blvd|lane|ln|highway|hwy|way|court|ct|"
    r"place|pl|circle|cir|parkway|pkwy|square|sq|terrace|ter|trail|trl|pike|route|rte|plaza)\b\.?",
    re.I,
)


def parse_address(raw: str) -> Address:
    """Break '1002 S Main St., Greenville, SC 29601' into number / street words / city / state."""
    text = collapse_ws(str(raw or "").replace("\n", ", "))
    addr = Address(raw=text)
    if not text:
        return addr
    parts = [p.strip() for p in re.split(r"\s*,\s*", text) if p.strip()]
    street_part = parts[0]
    rest = parts[1:]
    if not rest:  # no commas: "1002 S Main St Greenville SC 29601"
        m = None
        for m in _STREET_END_RE.finditer(street_part):
            pass
        if m is not None and m.end() < len(street_part):
            rest = [street_part[m.end():].strip()]
            street_part = street_part[: m.end()]
    nm = re.match(r"^\s*(\d+[A-Za-z]?(?:\s*-\s*\d+[A-Za-z]?)?)\b\s*(.*)$", street_part)
    if nm:
        addr.number = re.sub(r"\s+", "", nm.group(1)).lower()
        addr.street = _normalize_street_words(nm.group(2))
    else:
        addr.street = _normalize_street_words(street_part)
    # State and city from what comes after the street
    tail = " ".join(rest)
    tail = re.sub(r"\b\d{5}(?:-\d{4})?\b", "", tail)
    tail = re.sub(r"\b(?:usa|united states(?: of america)?|u\.s\.a\.?)\b", "", tail, flags=re.I).strip(" ,")
    if tail:
        words = tail.replace(",", " ").split()
        for k in (1, 2, 3):   # the state is the last 1-3 words: "LA", "Louisiana", "New York"
            if len(words) >= k and norm_state(" ".join(words[-k:])):
                addr.state = norm_state(" ".join(words[-k:]))
                addr.city = norm_text(" ".join(words[:-k]))
                break
        else:
            addr.city = norm_text(tail)
    return addr


def addresses_match(a: str, b: str) -> bool:
    """True when two address strings point to the same place: same street number and street name,
    and (when both give them) the same city and state."""
    A, B = parse_address(a), parse_address(b)
    if not A.raw or not B.raw:
        return False
    if A.number and B.number:
        if A.number != B.number:
            return False
        if A.core and B.core and A.core != B.core:
            from rapidfuzz import fuzz  # local import keeps this file light
            if fuzz.ratio(" ".join(A.core), " ".join(B.core)) < 88:
                return False
        if A.directions and B.directions and A.directions != B.directions:
            return False
        if A.state and B.state and A.state != B.state:
            return False
        if A.city and B.city and not cities_match(A.city, B.city):
            return False
        return True
    # No street number on one side (or a P.O. box): fall back to fuzzy text
    from rapidfuzz import fuzz
    return fuzz.token_set_ratio(norm_text(a), norm_text(b)) >= 92


def address_key(raw: str) -> tuple[str, str]:
    """(street number, main street word) - a loose fingerprint used to find which page of a shared
    multi-location website talks about a given record."""
    a = parse_address(raw)
    return (a.number, a.core[0] if a.core else "")


def split_city_state(raw: str) -> tuple[str, str]:
    """('Greenville', 'SC') from an address string. City keeps the capitalisation of the original."""
    text = collapse_ws(str(raw or "").replace("\n", ", "))
    parts = [p.strip() for p in re.split(r"\s*,\s*", text) if p.strip()]
    a = parse_address(raw)
    city = ""
    if len(parts) >= 3:
        city = parts[-2] if norm_state(re.sub(r"\b\d{5}(?:-\d{4})?\b", "", parts[-1]).strip()) else parts[-1]
    elif len(parts) == 2:
        m = re.match(r"^(.*?)[,\s]+([A-Za-z]{2})(?:\s+\d{5}(?:-\d{4})?)?$", parts[1])
        city = m.group(1) if m else parts[1]
    return city.strip(), a.state


# --------------------------------------------------------------------------------------
# People
# --------------------------------------------------------------------------------------
_HONORIFICS = {
    "the", "reverend", "rev", "revd", "revs", "very", "right", "rt", "most", "mr", "mrs", "ms", "miss",
    "fr", "father", "mother", "mtr", "dr", "doctor", "pastor", "deacon", "canon", "bishop", "dean",
    "venerable", "ven", "archdeacon", "sister", "br", "brother", "msgr", "monsignor", "honorable", "hon",
    "interim", "associate", "assistant", "assisting", "priest", "rector", "vicar", "curate", "in", "charge",
}
_NAME_SUFFIXES = {"jr", "sr", "ii", "iii", "iv", "phd", "dmin", "md", "esq", "ssc", "ofs", "osb", "sdb"}

# (canonical role, regex). Checked in this order.
_ROLE_PATTERNS = [
    ("interim rector", r"interim\s+rector"),
    ("associate rector", r"(?:associate|assistant|assisting)\s+(?:rector|priest|vicar)"),
    ("priest-in-charge", r"priest[\s\-]*in[\s\-]*charge"),
    ("interim priest", r"interim\s+(?:priest|vicar|pastor)"),
    ("rector", r"rector"),
    ("vicar", r"vicar"),
    ("dean", r"dean"),
    ("curate", r"curate"),
    ("deacon", r"deacon"),
    ("bishop", r"bishop"),
    ("canon", r"canon"),
    ("pastor", r"pastor"),
]
# Roles that are safe to spot anywhere in the string (a first name 'Dean' or surname 'Bishop' is not safe).
_SAFE_ANYWHERE = {"interim rector", "associate rector", "priest-in-charge", "interim priest", "rector", "vicar", "curate"}

# Roles that identify a parish's own clergy when judging an Asset Map contact.
PARISH_CLERGY_ROLES = ("rector", "vicar", "priest-in-charge", "dean", "interim rector")


@dataclass
class Person:
    given: list[str] = field(default_factory=list)
    surname: str = ""
    roles: frozenset = frozenset()
    raw: str = ""


def find_roles(text: str, anywhere: bool = False) -> frozenset:
    roles = set()
    low = str(text or "").lower()
    for role, rx in _ROLE_PATTERNS:
        if anywhere and role not in _SAFE_ANYWHERE:
            continue
        if re.search(r"(?<![a-z])" + rx + r"(?![a-z])", low):
            roles.add(role)
            low = re.sub(rx, " ", low)  # 'interim rector' must not also count as 'rector'
    return frozenset(roles)


def parse_person(raw: str) -> Person:
    """'The Reverend J. Gary Eichelberger, Rector' -> surname 'eichelberger', roles {'rector'}."""
    text = collapse_ws(str(raw or ""))
    p = Person(raw=text)
    if not text:
        return p
    m = re.search(r"\s*(?:,|\s[-–—]\s|\()\s*", text)
    head, tail = (text[: m.start()], text[m.end():]) if m else (text, "")
    roles = set(find_roles(tail))
    roles |= set(find_roles(head, anywhere=True))
    if not roles and not tail:  # "Rector Jane Doe" / "Dean Jane Doe" with the title in front
        lead = norm_text(head).split()
        if lead and lead[0] in ("dean", "canon", "deacon", "pastor", "bishop") and len(lead) >= 3:
            roles |= set(find_roles(lead[0]))
    p.roles = frozenset(roles)
    tokens = [t for t in norm_text(head).split() if t]
    while tokens and tokens[0] in _HONORIFICS and len(tokens) > 1:   # leading titles only
        tokens.pop(0)
    while tokens and tokens[-1] in _NAME_SUFFIXES and len(tokens) > 1:
        tokens.pop()
    if len(tokens) == 1 and tokens[0] in _HONORIFICS:
        tokens = []
    if not tokens:  # the whole thing was titles, e.g. "The Rector"
        return p
    p.surname = tokens[-1]
    p.given = tokens[:-1]
    return p


def _surnames_equal(a: str, b: str) -> bool:
    if not a or not b:
        return False
    if a == b:
        return True
    pa, pb = set(re.split(r"[\s\-]+", a)), set(re.split(r"[\s\-]+", b))
    return bool(pa & pb) and (len(pa) > 1 or len(pb) > 1)


def _given_conflict(a: Person, b: Person) -> bool:
    """True when both sides give first names or initials and none of them fit together (Jane vs John,
    J. vs M.). An initial fits a name with the same first letter, so "J. Gary" and "Gary" are the same person."""
    from rapidfuzz import fuzz
    if not a.given or not b.given:
        return False
    for x in a.given:
        for y in b.given:
            if len(x) == 1 or len(y) == 1:
                if x[0] == y[0]:
                    return False
            elif x == y or x.startswith(y) or y.startswith(x) or fuzz.ratio(x, y) >= 80:
                return False
    return True


def persons_match(a: str, b: str) -> tuple[bool, str]:
    """Compare two people by surname, plus first name or initial when both give one. Honorifics ("The Rev.",
    "The Reverend", "Fr.") and roles (", Rector", ", Vicar") are ignored. Returns (matches, explanation)."""
    A, B = parse_person(a), parse_person(b)
    if not A.surname or not B.surname:
        return False, "could not read a surname"
    if not _surnames_equal(A.surname, B.surname):
        return False, f"different surname ({A.surname} vs {B.surname})"
    if _given_conflict(A, B):
        return False, f"same surname but different first name ({' '.join(A.given)} vs {' '.join(B.given)})"
    return True, "same person"


_GENERATION_RE = re.compile(r"(?:jr|sr)\.?|i{2,3}|iv|v|vi|ph\.?\s?d\.?|d\.?\s?min\.?|m\.?div\.?", re.I)
_ROLE_DASH_RE = re.compile(r"\s+[-–—]\s+(?=[^-–—]*\b(?:rector|vicar|priest|dean|deacon|curate|chaplain|bishop|canon|"
                           r"pastor|interim|associate|assistant)\b).*$", re.I)


def strip_role(person: str) -> str:
    """Drop the role written after a name: "The Rev. Ann Lee, Rector" / "Ann Lee (Vicar)" / "Ann Lee - Dean of the
    Chapel" -> "The Rev. Ann Lee". Generational suffixes and degrees (", Jr.", ", III", ", PhD") are kept."""
    text = collapse_ws(person)
    m = re.fullmatch(r"(.*?\S)\s*\(([^()]*)\)", text)
    if m and find_roles(m.group(2)):
        return m.group(1)
    text = _ROLE_DASH_RE.sub("", text)
    head, *rest = [p.strip() for p in text.split(",")]
    kept = []
    while rest and _GENERATION_RE.fullmatch(rest[0]):
        kept.append(rest.pop(0))
    return ", ".join([head, *kept]) if head else text


# --------------------------------------------------------------------------------------
# Church names
# --------------------------------------------------------------------------------------
_CHURCH_NAME_WORD = re.compile(
    r"(?<![a-z])(?:church|episcopal|chapel|cathedral|parish|saint|holy|trinity|christ|grace|redeemer|advent|"
    r"ascension|epiphany)|(?<![a-z])st\.?(?![a-z])", re.I)
# Words that do not tell one parish from another: "The Episcopal Church" names no parish at all.
_GENERIC_NAME_WORDS = {"the", "episcopal", "church", "of", "in", "and", "parish", "anglican", "united", "states",
                       "america", "usa", "communion"}


def _name_tokens(name: str) -> list[str]:
    return ["st" if w == "saint" else w for w in norm_text(name).split() if w != "s"]


def looks_like_church_name(value: str) -> bool:
    """True for "St. Anne's Episcopal Church", "Grace Church Anderson"; False for a web designer's credit
    ("Digital Pros"), a code ("PG168K"), a diocese ("Episcopal Diocese of South Carolina") or just
    "The Episcopal Church"."""
    if not _CHURCH_NAME_WORD.search(value or "") or re.search(r"\bdiocese\b", value or "", re.I):
        return False
    return any(t not in _GENERIC_NAME_WORDS for t in _name_tokens(value))


def _name_core(name: str) -> set[str]:
    return {t for t in _name_tokens(name) if t not in _GENERIC_NAME_WORDS}


def name_hint_words(name: str) -> list[str]:
    """Distinctive words of a parish name, for spotting it on a page: "St. Philip Episcopal Church (Voorhees College)"
    -> ['college', 'philip', 'voorhees']."""
    return sorted(t for t in _name_core(name) if len(t) >= 4)


def names_same(a: str, b: str) -> bool:
    """The same parish name: the same distinctive words in any order, with or without "Episcopal", "Church" or
    "The" ("St. Anne's Church" = "Episcopal Church of St. Anne"), allowing small spelling differences. Generic words
    never make two names the same: "St. Anne's Church" is not "St. Mark's Church"."""
    ca, cb = _name_core(a), _name_core(b)
    if not ca or not cb:
        return norm_text(a) == norm_text(b)
    if ca == cb:
        return True
    from rapidfuzz import fuzz
    return len(ca) == len(cb) and fuzz.ratio(" ".join(sorted(ca)), " ".join(sorted(cb))) >= 88


def names_reworded(a: str, b: str) -> bool:
    """Same parish name, worded differently: words in another order, "Episcopal" or "Church" dropped, or the
    town added ("St. Anne's Episcopal Church" / "St. Anne's Church" / "Episcopal Church of St. Anne")."""
    ca, cb = _name_core(a), _name_core(b)
    return bool(ca and cb) and (ca <= cb or cb <= ca)


def role_is_parish_clergy(title: str) -> bool:
    """For Asset Map contacts: only Rector, Vicar, Priest-in-Charge, Dean or Interim Rector count as the
    parish's clergy.  'Canon to the Ordinary', 'Parish Administrator', 'Assistant to the Rector',
    'Associate Rector' and similar do not."""
    low = str(title or "").lower()
    if re.search(r"assistant\s+to|secretary|administrator|coordinator|director|warden|treasurer", low):
        return False
    roles = find_roles(low)
    if "associate rector" in roles:
        return False
    return bool(roles & set(PARISH_CLERGY_ROLES))


# --------------------------------------------------------------------------------------
# Rite I / Rite II
# --------------------------------------------------------------------------------------
_RITE_II_RE = re.compile(r"\brite\s*(?:ii|two|2)\b", re.I)
_RITE_I_RE = re.compile(r"\brite\s*(?:i|one|1)(?![\w])", re.I)
_RITE_BOTH_RE = re.compile(r"\brites?\s*(?:i|one|1)\s*(?:/|&|and|or|,)\s*(?:ii|two|2)\b", re.I)


def derive_rite(*texts: str) -> Optional[str]:
    """'I' / 'II' / 'Mixed' / None from services text.
    Only Rite I (or One) -> 'I'; only Rite II (or Two) -> 'II'; both -> 'Mixed'; neither -> None."""
    blob = " ; ".join(str(t) for t in texts if t)
    if _RITE_BOTH_RE.search(blob):
        return "Mixed"
    has_ii = bool(_RITE_II_RE.search(blob))
    # Remove Rite II mentions first so "Rite II" is not mistaken for "Rite I".
    has_i = bool(_RITE_I_RE.search(_RITE_II_RE.sub(" ", blob)))
    if has_i and has_ii:
        return "Mixed"
    if has_i:
        return "I"
    if has_ii:
        return "II"
    return None


def norm_rite(value: str) -> str:
    """'I' / 'II' / 'mixed' for a rite or rite-details value ("Rite I (8:00 AM); Rite II (10:30 AM)" -> 'mixed')."""
    v = norm_text(value)
    known = {"i": "i", "ii": "ii", "mixed": "mixed", "1": "i", "one": "i", "rite i": "i", "rite one": "i", "2": "ii",
             "two": "ii", "rite ii": "ii", "rite two": "ii", "both": "mixed", "rite i and rite ii": "mixed"}
    if v in known:
        return known[v]
    derived = derive_rite(value)
    return derived.lower() if derived else v


def derive_rite_details(sunday_text: str = "", weekday_text: str = "", fmt: str = "12h") -> Optional[str]:
    """Which rite is used when, from the services text:
    "8:00 AM Holy Eucharist, Rite I; 10:30 AM Choral Eucharist, Rite II" -> "Rite I (8:00 AM); Rite II (10:30 AM)".
    Weekday times keep their day ("Rite II (Wed 12:00 noon)"). None when no rite is named."""
    from .times import find_times, format_time, parse_service_slots, describe_slot, slot_sort_key
    when: dict[str, list[str]] = {"I": [], "II": []}
    named: set[str] = set()
    for text, day in ((sunday_text, "sun"), (weekday_text, None)):
        for item in split_items(text):
            rites = {"I", "II"} if derive_rite(item) == "Mixed" else {derive_rite(item)} - {None}
            if not rites:
                continue
            named |= rites
            if day == "sun":
                labels = [format_time(t.minutes, fmt) for t in find_times(item) if not t.range_end][:1]
            else:
                labels = [describe_slot(s, fmt) for s in sorted(parse_service_slots(item), key=slot_sort_key)][:2]
            for r in rites:
                when[r].extend(x for x in labels if x not in when[r])
    if not named:
        return None
    parts = [f"Rite {r}" + (f" ({', '.join(when[r])})" if when[r] else "") for r in ("I", "II") if r in named]
    return "; ".join(parts)

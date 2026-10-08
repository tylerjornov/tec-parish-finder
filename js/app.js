const $ = (id) => document.getElementById(id);

const state = {
  all: [],
  origin: null,
};

function haversine(a, b) {
  if (a.lat == null || a.lon == null || b.lat == null || b.lon == null) return Infinity;
  const R = 3958.8;
  const dLat = ((b.lat - a.lat) * Math.PI) / 180;
  const dLon = ((b.lon - a.lon) * Math.PI) / 180;
  const s =
    Math.sin(dLat / 2) ** 2 +
    Math.cos((a.lat * Math.PI) / 180) * Math.cos((b.lat * Math.PI) / 180) * Math.sin(dLon / 2) ** 2;
  return 2 * R * Math.asin(Math.sqrt(s));
}

// Each filter keeps the set of values currently ticked. A parish passes when it
// matches any ticked value in every filter that has one (OR within, AND across).
const chosen = new Map();
const filterSyncs = [];
const filterClosers = [];

// "Yes - Rector" -> "Rector": the dropdowns show only the detail of a yes answer.
const optionLabel = (v) => v.replace(/^Yes - /, "");

// Turns the empty .ms placeholder for `id` into a button that opens a checklist.
function fillFilter(id, values) {
  const host = document.querySelector(`.ms[data-id="${id}"]`);
  if (!host) return;
  const set = new Set();
  chosen.set(id, set);
  const btn = document.createElement("button");
  btn.type = "button";
  btn.id = id;
  btn.className = "ms-btn";
  btn.setAttribute("aria-expanded", "false");
  btn.setAttribute("aria-controls", `${id}-panel`);
  const panel = document.createElement("div");
  panel.id = `${id}-panel`;
  panel.className = "ms-panel";
  panel.setAttribute("role", "group");
  panel.hidden = true;
  const label = document.querySelector(`label[for="${id}"]`);
  if (label) {
    label.id ||= `${id}-label`;
    // Name = the label plus the current choice, which the button's own text carries.
    btn.setAttribute("aria-labelledby", `${label.id} ${id}`);
    panel.setAttribute("aria-labelledby", label.id);
  }
  const boxes = values.map((v) => {
    const row = document.createElement("label");
    row.className = "ms-opt";
    const box = document.createElement("input");
    box.type = "checkbox";
    box.value = v;
    box.addEventListener("change", () => {
      if (box.checked) set.add(v);
      else set.delete(v);
      sync();
      render();
    });
    const text = document.createElement("span");
    text.textContent = optionLabel(v);
    row.append(box, text);
    panel.appendChild(row);
    return box;
  });
  const sync = () => {
    btn.textContent = set.size === 0 ? "Any" : set.size === 1 ? optionLabel([...set][0]) : `${set.size} selected`;
    btn.title = [...set].map(optionLabel).join("; ");
    boxes.forEach((b) => (b.checked = set.has(b.value)));
  };
  const setOpen = (open) => {
    panel.hidden = !open;
    btn.setAttribute("aria-expanded", String(open));
  };
  btn.addEventListener("click", () => {
    const open = panel.hidden;
    filterClosers.forEach((close) => close());
    setOpen(open);
  });
  panel.addEventListener("keydown", (e) => {
    if (e.key === "Escape") {
      setOpen(false);
      btn.focus();
    }
  });
  filterSyncs.push(sync);
  filterClosers.push(() => setOpen(false));
  host.append(btn, panel);
  sync();
}

function clearFilters() {
  chosen.forEach((set) => set.clear());
  filterSyncs.forEach((sync) => sync());
  filterClosers.forEach((close) => close());
}

const filtersActive = () => [...chosen.values()].some((set) => set.size);

const FILTERS = [
  ["f-state", "state"],
  ["f-diocese", "diocese"],
  ["f-churchmanship", "churchmanship"],
  ["f-wo-serve", "female_clergy"],
  ["f-wo-affirmed", "womens_ordination_affirmed"],
  ["f-lgbt-serve", "lgbt_clergy"],
  ["f-lgbt-affirmed", "lgbt_ordination_affirmed"],
  ["f-ssm", "ssm"],
  ["f-spectrum", "theological_cultural_alignment"],
  ["f-verified", "verification_status"],
];

// These fields can hold several answers, separated by "; ".
const MULTI = new Set(["female_clergy", "lgbt_clergy"]);
const answers = (key, value) => (MULTI.has(key) ? String(value ?? "").split(/;\s*/) : [value]);
const matchesAny = (key, value, wanted) => answers(key, value).some((a) => wanted.has(a));

function filtered() {
  // Read the controls once per render rather than once per parish.
  const q = $("q").value.trim().toLowerCase();
  const active = FILTERS.map(([id, key]) => [key, chosen.get(id)]).filter(([, set]) => set && set.size);
  const list = state.all.filter((p) => {
    if (q) {
      const hay = [p.name, p.address, p.state, p.diocese, p.notes]
        .filter((v) => v != null)
        .join(" ")
        .toLowerCase();
      if (!hay.includes(q)) return false;
    }
    return active.every(([key, set]) => matchesAny(key, p[key], set));
  });
  const byName = (a, b) => a.name.localeCompare(b.name);
  if (state.origin) {
    // Parishes without coordinates are Infinity away; order those (and ties) by name.
    const dist = new Map(list.map((p) => [p, haversine(state.origin, p)]));
    list.sort((a, b) => dist.get(a) - dist.get(b) || byName(a, b));
  } else {
    list.sort(byName);
  }
  return list;
}

let map, markers;
// The contiguous US, shown until a location or filter narrows things down.
const US_BOUNDS = [[24.5, -125.0], [49.5, -66.9]];
// With a location set, the map frames it and this many of the closest parishes.
const NEAREST_SHOWN = 5;

// data-motion is set on <html> by settings.js from the "Reduce motion" choice.
const reduceMotion = () => document.documentElement.dataset.motion === "reduce";
// Leaflet treats animate: true differently from leaving it unset, so only pass false.
const still = () => (reduceMotion() ? { animate: false } : {});

const CARTO_KEY = "cb1_4dnh_1_b452b4eaaac650e934348ac6";
const cartoUrl = () => {
  const style = document.documentElement.dataset.theme === "light" ? "rastertiles/voyager" : "dark_all";
  return `https://{s}.basemaps.cartocdn.com/${style}/{z}/{x}/{y}${L.Browser.retina ? "@2x" : ""}.png?key=${CARTO_KEY}`;
};

function ensureMap() {
  if (map) return;
  if (typeof L === "undefined") throw new Error("Leaflet did not load");
  const anim = !reduceMotion();
  map = L.map("map", {
    zoomAnimation: anim,
    fadeAnimation: anim,
    markerZoomAnimation: anim,
  }).fitBounds(US_BOUNDS);
  // CARTO basemaps (free non-commercial key): Voyager for light, Dark Matter for dark.
  const tiles = L.tileLayer(cartoUrl(), {
    attribution:
      '&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> contributors, &copy; <a href="https://carto.com/attributions">CARTO</a>',
    subdomains: "abcd",
    maxZoom: 20,
  }).addTo(map);
  // settings.js changes data-theme on <html> when the visitor switches themes.
  new MutationObserver(() => tiles.setUrl(cartoUrl())).observe(document.documentElement, {
    attributes: true,
    attributeFilter: ["data-theme"],
  });
  markers = L.layerGroup().addTo(map);
  // settings.js changes data-pin-size when the visitor moves the Pin size slider.
  new MutationObserver(() => {
    const icon = pinIcon();
    markers.eachLayer((m) => m.setIcon(icon));
  }).observe(document.documentElement, { attributes: true, attributeFilter: ["data-pin-size"] });
}

// Leaflet's default pin (25×41, tip at 12,41), scaled by the Pin size setting.
function pinIcon() {
  const s = Number(document.documentElement.dataset.pinSize) || 1;
  const px = (n) => Math.round(n * s);
  return new L.Icon.Default({
    iconSize: [px(25), px(41)],
    iconAnchor: [px(12), px(41)],
    shadowSize: [px(41), px(41)],
    shadowAnchor: [px(12), px(41)],
  });
}

function render() {
  const list = filtered();
  $("status").textContent = `${list.length} of ${state.all.length} parishes`;
  const wrap = $("list");
  wrap.innerHTML = "";
  // The list stays empty until there's a place to sort by distance from.
  if (!state.origin) {
    wrap.innerHTML = `<p class="list-empty">Enter a location${canLocate ? " or use your current location" : ""} to show the list.</p>`;
  }
  (state.origin ? list : []).forEach((p, i) => {
    const miles = milesTo(p);
    const tags = cardTags(p);
    const div = document.createElement("article");
    div.className = "card";
    div.dataset.id = p.id;
    div.innerHTML = `
      <h2><button type="button" class="card-open" aria-haspopup="dialog">${escapeHtml(p.name)}</button></h2>
      <div class="meta">${escapeHtml(p.address)} ${miles ? " · " + miles : ""}</div>
      ${p.website ? `<div class="meta card-site"><a href="${escapeHtml(p.website)}" target="_blank" rel="noopener">${escapeHtml(shortUrl(p.website))}<span class="visually-hidden"> for ${escapeHtml(p.name)} (opens in new tab)</span></a></div>` : ""}
      ${tags.length ? `<ul class="tags">${tags.map((t) => `<li class="tag">${escapeHtml(t)}</li>`).join("")}</ul>` : ""}
    `;
    div.onclick = (e) => {
      if (e.target.closest("a")) return;
      select(p);
      openDetail(p, div.querySelector(".card-open"));
    };
    if (i === 0) div.classList.add("active");
    wrap.appendChild(div);
  });

  if ($("layout").dataset.view === "map") tryPaintMap(list);
}

function tryPaintMap(list) {
  try {
    paintMap(list);
  } catch (err) {
    console.error(err);
    $("status").textContent = "Map failed to load: " + err.message;
  }
}

function paintMap(list) {
  ensureMap();
  map.invalidateSize();
  markers.clearLayers();
  const pts = [];
  const near = state.origin ? [[state.origin.lat, state.origin.lon]] : [];
  list.forEach((p) => {
    if (p.lat == null || p.lon == null) return;
    const m = L.marker([p.lat, p.lon], { alt: p.name, title: p.name, icon: pinIcon() });
    m.on("click", () => {
      select(p);
      openDetail(p, m.getElement());
    });
    markers.addLayer(m);
    pts.push([p.lat, p.lon]);
    // list is sorted by distance when there's an origin.
    if (state.origin && near.length <= NEAREST_SHOWN) near.push([p.lat, p.lon]);
  });
  const narrowed = $("q").value.trim() || filtersActive();
  const fit = () => {
    const opts = { padding: [24, 24], maxZoom: 12, ...still() };
    if (state.origin) map.fitBounds(near, opts);
    else if (narrowed && pts.length) map.fitBounds(pts, opts);
    else map.fitBounds(US_BOUNDS, still());
  };
  fit();
  setTimeout(() => {
    map.invalidateSize();
    fit();
  }, 100);
}

// Highlights the parish's card. The map stays put so closing the detail
// dialog returns to the same view.
function select(p) {
  document.querySelectorAll(".card").forEach((c) => {
    c.classList.toggle("active", c.dataset.id === p.id);
  });
}

// "https://www.example.org/" -> "example.org"
const shortUrl = (url) => url.replace(/^https?:\/\/(www\.)?/i, "").replace(/\/$/, "");

function milesTo(p) {
  const d = state.origin ? haversine(state.origin, p) : Infinity;
  return Number.isFinite(d) ? `${d.toFixed(1)} mi` : "";
}

// Everything the detail dialog shows, grouped. Blank and "N/A" values are
// skipped, and so is any section left with nothing in it.
const DETAIL_SECTIONS = [
  ["Contact", [
    ["website", "Website", "url"],
    ["livestream_url", "Livestream", "url"],
    ["church_phone", "Church Phone", "tel"],
    ["church_email", "Church Email", "email"],
    ["rector_name", "Rector"],
    ["rector_phone", "Rector Phone", "tel"],
    ["rector_email", "Rector Email", "email"],
    ["other_contact", "Other Contact"],
  ]],
  ["Worship", [
    ["sunday_services", "Sunday Services", "list"],
    ["weekday_services", "Weekday Services", "list"],
    ["rite", "Rite"],
    ["rite_details", "Rite Details"],
    ["churchmanship", "Churchmanship"],
    ["music_style", "Music"],
    ["service_languages", "Service Language(s)"],
  ]],
  ["Parish life", [
    ["formation", "Formation & Community"],
    ["childcare", "Childcare"],
    ["asa", "Average Sunday Attendance"],
    ["accessibility", "Accessibility"],
    ["parking", "Parking"],
  ]],
  ["Doctrine & Social Issues", [
    ["female_clergy", "Female Clergy", "list"],
    ["womens_ordination_affirmed", "Affirmation of Women’s Ordination"],
    ["lgbt_clergy", "LGBT Clergy", "list"],
    ["lgbt_ordination_affirmed", "Affirmation of LGBT Ordination"],
    ["ssm", "Same-Sex Marriage"],
    ["theological_cultural_alignment", "Theological & Cultural Alignment"],
  ]],
  ["Diocese", [
    ["diocese", "Diocese"],
    ["diocesan_bishop", "Diocesan Bishop"],
  ]],
  ["Notes & verification", [
    ["notes", "Notes"],
    ["verification_status", "Verification Status"],
    ["date_last_updated", "Last Updated"],
    ["coords", "Coordinates"],
  ]],
];

// Card tags, from schema.list_tags: one per field, in its order, using the
// short label of the first listed answer the parish has.
let listTags = {};
function cardTags(p) {
  return Object.entries(listTags)
    .filter(([key]) => key !== "about")
    .map(([key, labels]) => {
      const have = new Set(answers(key, p[key]));
      return Object.entries(labels).find(([answer]) => have.has(answer))?.[1];
    })
    .filter(Boolean);
}

const hasValue = (v) => v != null && v !== "" && v !== "N/A";

function detailValue(v, kind) {
  const s = escapeHtml(v);
  if (kind === "url" && /^https?:\/\//i.test(v)) {
    return `<a href="${s}" target="_blank" rel="noopener">${s}<span class="visually-hidden"> (opens in new tab)</span></a>`;
  }
  if (kind === "tel") return `<a href="tel:${escapeHtml(v.replace(/[^\d+]/g, ""))}">${s}</a>`;
  if (kind === "email") return `<a href="mailto:${s}">${s}</a>`;
  if (kind === "list") {
    const items = v.split(/;\s*/).filter(Boolean);
    if (items.length > 1) return `<ul>${items.map((i) => `<li>${escapeHtml(i)}</li>`).join("")}</ul>`;
  }
  return s;
}

// Focused again when the dialog closes: the card's button or the map pin.
let detailReturn = null;

function openDetail(p, returnTo) {
  const miles = milesTo(p);
  // "other_contact" is normally just church phone | email; show it only if it adds something.
  const joined = [p.church_phone, p.church_email].filter(hasValue).join(" | ");
  const v = {
    ...p,
    other_contact: p.other_contact === joined ? "" : p.other_contact,
    coords: p.lat != null && p.lon != null ? `${p.lat}, ${p.lon}` : "",
  };
  $("detail-title").textContent = p.name;
  $("detail-meta").textContent = [p.address, miles && `${miles} away`].filter(Boolean).join(" · ");
  const body = $("detail-body");
  body.innerHTML = DETAIL_SECTIONS.map(([title, fields]) => {
    const rows = fields
      .filter(([key]) => hasValue(v[key]))
      .map(([key, label, kind]) => `<div><dt>${label}</dt><dd>${detailValue(String(v[key]), kind)}</dd></div>`);
    return rows.length ? `<section><h3>${title}</h3><dl>${rows.join("")}</dl></section>` : "";
  }).join("");
  detailReturn = returnTo;
  const dlg = $("detail");
  if (!dlg.open) dlg.showModal();
  body.scrollTop = 0;
}

function escapeHtml(s) {
  return String(s ?? "").replace(/[&<>"']/g, (c) =>
    ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c])
  );
}

// Bumped on every new origin request so a slow geocode reply can't
// overwrite a newer origin (or one the user has since cleared).
let originSeq = 0;

// Browsers only allow location on https (and localhost).
const canLocate = !!navigator.geolocation && window.isSecureContext;

// Shown in the Near box while sorting by the device's position.
const HERE = "My location";
let deviceOrigin = null;

function useDeviceLocation() {
  const seq = ++originSeq;
  $("status").textContent = "Finding your location…";
  navigator.geolocation.getCurrentPosition(
    (pos) => {
      if (seq !== originSeq) return;
      deviceOrigin = { lat: pos.coords.latitude, lon: pos.coords.longitude };
      state.origin = deviceOrigin;
      $("origin").value = HERE;
      render();
    },
    (err) => {
      if (seq !== originSeq) return;
      $("status").textContent =
        err.code === err.PERMISSION_DENIED
          ? "Location access is off for this site. Allow it in your browser settings, or type a ZIP or city."
          : "Could not get your location. Type a ZIP or city instead.";
    },
    { timeout: 15000, maximumAge: 300000 }
  );
}

function parseOrigin() {
  const seq = ++originSeq;
  const raw = $("origin").value.trim();
  if (raw === HERE && deviceOrigin) {
    state.origin = deviceOrigin;
    render();
    return;
  }
  if (!raw) {
    state.origin = null;
    render();
    return;
  }
  const m = raw.match(/^(-?\d+(\.\d+)?)\s*,\s*(-?\d+(\.\d+)?)$/);
  if (m && Math.abs(m[1]) <= 90 && Math.abs(m[3]) <= 180) {
    state.origin = { lat: +m[1], lon: +m[3] };
    render();
    return;
  }
  // A bare ZIP (or ZIP+4) as free text matches same-numbered postcodes abroad
  // (29150 is also in Brittany), so look it up as a US or territory postcode.
  const zip = raw.match(/^(\d{5})(-\d{4})?$/);
  const url =
    "https://nominatim.openstreetmap.org/search?format=json&limit=1&" +
    (zip ? `postalcode=${zip[1]}&countrycodes=us,pr,vi,gu,as,mp` : "q=" + encodeURIComponent(raw));
  fetch(url, { headers: { Accept: "application/json" } })
    .then((r) => r.json())
    .then((hits) => {
      if (seq !== originSeq) return;
      if (!hits.length) {
        $("status").textContent = "Could not geocode that location. Try a ZIP, city, ST, or lat,lon.";
        return;
      }
      state.origin = { lat: +hits[0].lat, lon: +hits[0].lon };
      render();
    })
    .catch(() => {
      if (seq !== originSeq) return;
      $("status").textContent = "Geocode failed. Use lat,lon for now.";
    });
}

function setView(mode) {
  $("layout").dataset.view = mode;
  $("view-list").classList.toggle("on", mode === "list");
  $("view-map").classList.toggle("on", mode === "map");
  $("view-switch").setAttribute("aria-checked", String(mode === "map"));
  // Point "Skip to results" at whichever view is showing.
  $("skip").setAttribute("href", mode === "map" ? "#map" : "#list");
  if (mode === "map") {
    // #map is visible now; wait for layout before creating/sizing Leaflet.
    requestAnimationFrame(() => tryPaintMap(filtered()));
  }
}

function bindUi() {
  // Text boxes don't re-render on "change": it fires on blur, so rebuilding the
  // list then would swallow the click on a card that caused the blur.
  $("q").addEventListener("input", render);
  $("apply-origin").addEventListener("click", parseOrigin);
  if (canLocate) {
    $("use-location").hidden = false;
    $("use-location").addEventListener("click", useDeviceLocation);
  }
  $("origin").addEventListener("keydown", (e) => {
    if (e.key === "Enter") parseOrigin();
  });
  $("reset").addEventListener("click", () => {
    $("q").value = "";
    $("origin").value = "";
    clearFilters();
    originSeq++;
    state.origin = null;
    render();
  });
  const dlg = $("detail");
  $("detail-close").addEventListener("click", () => dlg.close());
  // Close on a click on the dimmed backdrop, but not when a text selection
  // started inside the panel and was released outside it.
  let downOnBackdrop = false;
  dlg.addEventListener("pointerdown", (e) => {
    downOnBackdrop = e.target === dlg;
  });
  dlg.addEventListener("click", (e) => {
    if (downOnBackdrop && e.target === dlg) dlg.close();
  });
  dlg.addEventListener("close", () => {
    if (detailReturn?.isConnected) detailReturn.focus({ preventScroll: true });
    detailReturn = null;
  });
  $("view-list").addEventListener("click", () => setView("list"));
  $("view-map").addEventListener("click", () => setView("map"));
  $("view-switch").addEventListener("click", () =>
    setView($("layout").dataset.view === "map" ? "list" : "map")
  );
}

// "no-cache" makes the browser check for a newer copy on every load instead of
// reusing a saved one, so data changes show up as soon as they're deployed.
function getJson(url) {
  return fetch(url, { cache: "no-cache" }).then((r) => {
    if (!r.ok) throw new Error(`${url}: HTTP ${r.status}`);
    return r.json();
  });
}

$("status").textContent = "Loading parishes…";

Promise.all([getJson("data/parishes.json"), getJson("data/schema.json")])
  .then(([parishes, schema]) => {
    state.all = parishes;
    listTags = schema.list_tags || {};
    // Each dropdown offers only answers some parish actually has, in the
    // schema's order. State and Diocese have no set list, so they're A–Z.
    FILTERS.forEach(([id, key]) => {
      const used = new Set(parishes.flatMap((p) => answers(key, p[key])));
      const listed = schema.list_options[key] || schema.fields[key].choices;
      fillFilter(id, listed ? listed.filter((v) => used.has(v)) : [...used].filter(Boolean).sort());
    });

    // Render the list first so a UI-binding error can't leave it empty.
    render();
    try {
      bindUi();
    } catch (err) {
      console.error("UI binding failed:", err);
    }
  })
  .catch((err) => {
    console.error(err);
    $("status").textContent =
      "Could not load parish data (" + err.message + "). If opening the file directly, serve it over http.";
  });

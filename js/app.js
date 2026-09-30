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

function fillSelect(id, values) {
  const el = $(id);
  if (!el) return;
  values.forEach((v) => {
    const o = document.createElement("option");
    o.value = v;
    o.textContent = v;
    el.appendChild(o);
  });
}

const FILTERS = [
  ["f-state", "state"],
  ["f-diocese", "diocese"],
  ["f-churchmanship", "churchmanship"],
  ["f-wo-serve", "women_serve_priests"],
  ["f-wo-affirmed", "wo_affirmed"],
  ["f-lgbt-serve", "lgbt_serve_priests"],
  ["f-lgbt-affirmed", "lgbt_ordination_affirmed"],
  ["f-ssm", "ssm"],
  ["f-spectrum", "spectrum"],
  ["f-verified", "verified"],
];

function filtered() {
  // Read the controls once per render rather than once per parish.
  const q = $("q").value.trim().toLowerCase();
  const active = FILTERS.map(([id, key]) => [key, $(id).value]).filter(([, v]) => v);
  const list = state.all.filter((p) => {
    if (q) {
      const hay = [p.name, p.address, p.state, p.diocese, p.notes]
        .filter((v) => v != null)
        .join(" ")
        .toLowerCase();
      if (!hay.includes(q)) return false;
    }
    return active.every(([key, v]) => p[key] === v);
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
const DEFAULT_CENTER = [34.0, -81.0];
const DEFAULT_ZOOM = 8;

// data-motion is set on <html> by settings.js from the "Reduce motion" choice.
const reduceMotion = () => document.documentElement.dataset.motion === "reduce";
// Leaflet treats animate: true differently from leaving it unset, so only pass false.
const still = () => (reduceMotion() ? { animate: false } : {});

function ensureMap() {
  if (map) return;
  if (typeof L === "undefined") throw new Error("Leaflet did not load");
  const anim = !reduceMotion();
  map = L.map("map", {
    zoomAnimation: anim,
    fadeAnimation: anim,
    markerZoomAnimation: anim,
  }).setView(DEFAULT_CENTER, DEFAULT_ZOOM);
  L.tileLayer("https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png", {
    attribution: "&copy; OpenStreetMap",
    maxZoom: 18,
  }).addTo(map);
  markers = L.layerGroup().addTo(map);
}

function render() {
  const list = filtered();
  $("status").textContent = `${list.length} of ${state.all.length} parishes`;
  const wrap = $("list");
  wrap.innerHTML = "";
  list.forEach((p, i) => {
    const miles = milesTo(p);
    const div = document.createElement("article");
    div.className = "card";
    div.dataset.id = p.id;
    div.innerHTML = `
      <h2><button type="button" class="card-open" aria-haspopup="dialog">${escapeHtml(p.name)}</button></h2>
      <div class="meta">${escapeHtml(p.address)} ${miles ? " · " + miles : ""}</div>
      ${p.churchmanship ? `<span class="tag">${escapeHtml(p.churchmanship)}</span>` : ""}
      ${p.website ? `<div class="meta card-site"><a href="${escapeHtml(p.website)}" target="_blank" rel="noopener">${escapeHtml(shortUrl(p.website))}<span class="visually-hidden"> for ${escapeHtml(p.name)} (opens in new tab)</span></a></div>` : ""}
    `;
    div.onclick = (e) => {
      if (e.target.closest("a")) return;
      select(p);
      openDetail(p, div.querySelector(".card-open"));
    };
    if (i === 0) div.classList.add("active");
    wrap.appendChild(div);
  });

  const layout = $("layout");
  if (layout && layout.dataset.view === "map") {
    tryPaintMap(list);
  }
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
  list.forEach((p) => {
    if (p.lat == null || p.lon == null) return;
    const m = L.marker([p.lat, p.lon], { alt: p.name, title: p.name });
    m.on("click", () => {
      select(p);
      openDetail(p, m.getElement());
    });
    markers.addLayer(m);
    pts.push([p.lat, p.lon]);
  });
  const fit = () => {
    if (pts.length) map.fitBounds(pts, { padding: [24, 24], maxZoom: 12, ...still() });
    else map.setView(DEFAULT_CENTER, DEFAULT_ZOOM, still());
  };
  fit();
  setTimeout(() => {
    map.invalidateSize();
    fit();
  }, 100);
}

function select(p) {
  document.querySelectorAll(".card").forEach((c) => {
    c.classList.toggle("active", c.dataset.id === p.id);
  });
  if (p.lat != null && p.lon != null && $("layout").dataset.view === "map") {
    ensureMap();
    map.setView([p.lat, p.lon], 13, still());
  }
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
    ["church_phone", "Church phone", "tel"],
    ["church_email", "Church email", "email"],
    ["rector_name", "Rector / priest"],
    ["rector_phone", "Rector phone", "tel"],
    ["rector_email", "Rector email", "email"],
    ["contact", "Other contact"],
  ]],
  ["Worship", [
    ["service_times", "Services", "list"],
    ["rite", "Rite"],
    ["churchmanship", "Churchmanship"],
    ["music_style", "Music"],
    ["service_languages", "Service languages"],
  ]],
  ["Parish life", [
    ["formation", "Formation / community"],
    ["childcare", "Childcare"],
    ["asa", "Average Sunday attendance"],
    ["accessibility", "Accessibility"],
    ["parking", "Parking"],
  ]],
  ["Ordination & marriage", [
    ["women_serve_priests", "Women serve as priests here"],
    ["wo_affirmed", "Women’s ordination affirmed"],
    ["lgbt_serve_priests", "LGBT people serve as priests here"],
    ["lgbt_ordination_affirmed", "LGBT ordination affirmed"],
    ["ssm", "Same-sex marriage"],
    ["spectrum", "Spectrum"],
  ]],
  ["Diocese", [
    ["diocese", "Diocese"],
    ["diocesan_bishop", "Diocesan bishop"],
  ]],
  ["Notes & verification", [
    ["notes", "Notes"],
    ["verified", "Verification"],
    ["date_last_verified", "Last verified"],
    ["coords", "Coordinates"],
  ]],
];

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
  // "contact" is normally just church phone | email; show it only if it adds something.
  const joined = [p.church_phone, p.church_email].filter(hasValue).join(" | ");
  const v = {
    ...p,
    contact: p.contact === joined ? "" : p.contact,
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
  const layout = $("layout");
  if (!layout) return;
  layout.dataset.view = mode;
  $("view-list")?.classList.toggle("on", mode === "list");
  $("view-map")?.classList.toggle("on", mode === "map");
  $("view-switch")?.setAttribute("aria-checked", String(mode === "map"));
  // Point "Skip to results" at whichever view is showing.
  $("skip")?.setAttribute("href", mode === "map" ? "#map" : "#list");
  if (mode === "map") {
    // #map is visible now; wait for layout before creating/sizing Leaflet.
    requestAnimationFrame(() => tryPaintMap(filtered()));
  }
}

function bindUi() {
  const controls = document.querySelectorAll(".filters input, .filters select");
  // Text boxes don't re-render on "change": it fires on blur, so rebuilding the
  // list then would swallow the click on a card that caused the blur.
  document.querySelectorAll(".filters select").forEach((el) => el.addEventListener("change", render));
  $("q").addEventListener("input", render);
  $("apply-origin")?.addEventListener("click", parseOrigin);
  // Browsers only allow location on https (and localhost).
  if (navigator.geolocation && window.isSecureContext) {
    $("use-location").hidden = false;
    $("use-location").addEventListener("click", useDeviceLocation);
  }
  $("origin")?.addEventListener("keydown", (e) => {
    if (e.key === "Enter") parseOrigin();
  });
  $("reset")?.addEventListener("click", () => {
    controls.forEach((el) => {
      if (el.tagName === "SELECT") el.selectedIndex = 0;
      else el.value = "";
    });
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
  $("view-list")?.addEventListener("click", () => setView("list"));
  $("view-map")?.addEventListener("click", () => setView("map"));
  $("view-switch")?.addEventListener("click", () =>
    setView($("layout").dataset.view === "map" ? "list" : "map")
  );
}

function getJson(url) {
  return fetch(url).then((r) => {
    if (!r.ok) throw new Error(`${url}: HTTP ${r.status}`);
    return r.json();
  });
}

$("status").textContent = "Loading parishes…";

Promise.all([getJson("data/parishes.json"), getJson("data/schema.json")])
  .then(([parishes, schema]) => {
    state.all = parishes;
    const opts = (schema && schema.list_options) || {};
    const states = [...new Set(parishes.map((p) => p.state).filter(Boolean))].sort();
    const dioceses = [...new Set(parishes.map((p) => p.diocese).filter(Boolean))].sort();
    fillSelect("f-state", states);
    fillSelect("f-diocese", dioceses);
    fillSelect("f-churchmanship", opts.churchmanship || []);
    fillSelect("f-wo-serve", opts.women_serve_priests || []);
    fillSelect("f-wo-affirmed", opts.wo_affirmed || []);
    fillSelect("f-lgbt-serve", opts.lgbt_serve_priests || []);
    fillSelect("f-lgbt-affirmed", opts.lgbt_ordination_affirmed || []);
    fillSelect("f-ssm", opts.ssm || []);
    fillSelect("f-spectrum", opts.spectrum || []);

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

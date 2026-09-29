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

function ensureMap() {
  if (map) return;
  if (typeof L === "undefined") throw new Error("Leaflet did not load");
  map = L.map("map").setView(DEFAULT_CENTER, DEFAULT_ZOOM);
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
    const d = state.origin ? haversine(state.origin, p) : Infinity;
    const miles = Number.isFinite(d) ? `${d.toFixed(1)} mi` : "";
    const div = document.createElement("article");
    div.className = "card";
    div.dataset.id = p.id;
    div.innerHTML = `
      <h2>${escapeHtml(p.name)}</h2>
      <div class="meta">${escapeHtml(p.address)} ${miles ? " · " + miles : ""}</div>
      ${p.churchmanship ? `<span class="tag">${escapeHtml(p.churchmanship)}</span>` : ""}
      ${p.verified ? `<span class="tag">${escapeHtml(p.verified)}</span>` : ""}
      ${p.website ? `<div class="meta"><a href="${escapeHtml(p.website)}" target="_blank" rel="noopener">Website<span class="visually-hidden"> for ${escapeHtml(p.name)} (opens in new tab)</span></a></div>` : ""}
    `;
    div.onclick = () => select(p);
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
    const m = L.marker([p.lat, p.lon], { alt: p.name, title: p.name }).bindPopup(
      `<strong>${escapeHtml(p.name)}</strong><br>${escapeHtml(p.address)}`
    );
    m.on("click", () => select(p));
    markers.addLayer(m);
    pts.push([p.lat, p.lon]);
  });
  const fit = () => {
    if (pts.length) map.fitBounds(pts, { padding: [24, 24], maxZoom: 12 });
    else map.setView(DEFAULT_CENTER, DEFAULT_ZOOM);
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
    map.setView([p.lat, p.lon], 13);
  }
}

function escapeHtml(s) {
  return String(s ?? "").replace(/[&<>"']/g, (c) =>
    ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c])
  );
}

// Bumped on every new origin request so a slow geocode reply can't
// overwrite a newer origin (or one the user has since cleared).
let originSeq = 0;

function parseOrigin() {
  const seq = ++originSeq;
  const raw = $("origin").value.trim();
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
  const url =
    "https://nominatim.openstreetmap.org/search?format=json&limit=1&q=" +
    encodeURIComponent(raw);
  fetch(url, { headers: { Accept: "application/json" } })
    .then((r) => r.json())
    .then((hits) => {
      if (seq !== originSeq) return;
      if (!hits.length) {
        $("status").textContent = "Could not geocode that location. Try city, ST or lat,lon.";
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
  controls.forEach((el) => el.addEventListener("change", render));
  $("q").addEventListener("input", render);
  $("apply-origin")?.addEventListener("click", parseOrigin);
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

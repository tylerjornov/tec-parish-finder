const $ = (id) => document.getElementById(id);

const state = {
  all: [],
  schema: null,
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
  values.forEach((v) => {
    const o = document.createElement("option");
    o.value = v;
    o.textContent = v;
    el.appendChild(o);
  });
}

function matches(p) {
  const q = $("q").value.trim().toLowerCase();
  if (q) {
    const hay = `${p.name} ${p.address} ${p.state} ${p.diocese} ${p.notes}`.toLowerCase();
    if (!hay.includes(q)) return false;
  }
  const fields = [
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
  for (const [id, key] of fields) {
    const v = $(id).value;
    if (v && p[key] !== v) return false;
  }
  return true;
}

function filtered() {
  const list = state.all.filter(matches);
  if (state.origin) {
    list.sort((a, b) => haversine(state.origin, a) - haversine(state.origin, b));
  } else {
    list.sort((a, b) => a.name.localeCompare(b.name));
  }
  return list;
}

let map, markers;

function ensureMap() {
  if (map) return;
  map = L.map("map").setView([34.0, -81.0], 8);
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
    const miles =
      state.origin && p.lat != null ? `${haversine(state.origin, p).toFixed(1)} mi` : "";
    const div = document.createElement("article");
    div.className = "card";
    div.innerHTML = `
      <h2>${escapeHtml(p.name)}</h2>
      <div class="meta">${escapeHtml(p.address)} ${miles ? " · " + miles : ""}</div>
      ${p.churchmanship ? `<span class="tag">${escapeHtml(p.churchmanship)}</span>` : ""}
      ${p.verified ? `<span class="tag">${escapeHtml(p.verified)}</span>` : ""}
      ${p.website ? `<div class="meta"><a href="${escapeHtml(p.website)}" target="_blank" rel="noopener">Website</a></div>` : ""}
    `;
    div.onclick = () => select(p);
    if (i === 0) div.classList.add("active");
    wrap.appendChild(div);
  });

  const layout = document.getElementById("layout");
  if (layout && layout.dataset.view === "map") {
    paintMap(list);
  }
}

function paintMap(list) {
  ensureMap();
  markers.clearLayers();
  const pts = [];
  list.forEach((p) => {
    if (p.lat == null || p.lon == null) return;
    const m = L.marker([p.lat, p.lon]).bindPopup(
      `<strong>${escapeHtml(p.name)}</strong><br>${escapeHtml(p.address)}`
    );
    m.on("click", () => select(p));
    markers.addLayer(m);
    pts.push([p.lat, p.lon]);
  });
  if (pts.length) map.fitBounds(pts, { padding: [24, 24], maxZoom: 12 });
  setTimeout(() => { if (map) map.invalidateSize(); }, 100);
}

function select(p) {
  [...document.querySelectorAll(".card")].forEach((c) => {
    c.classList.toggle("active", c.querySelector("h2")?.textContent === p.name);
  });
  if (p.lat != null && document.getElementById("layout").dataset.view === "map") {
    ensureMap();
    map.setView([p.lat, p.lon], 13);
  }
}

function escapeHtml(s) {
  return String(s || "").replace(/[&<>"']/g, (c) =>
    ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c])
  );
}

function parseOrigin() {
  const raw = $("origin").value.trim();
  if (!raw) {
    state.origin = null;
    render();
    return;
  }
  const m = raw.match(/^(-?\d+(\.\d+)?),\s*(-?\d+(\.\d+)?)$/);
  if (m) {
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
      if (!hits.length) {
        $("status").textContent = "Could not geocode that location. Try city, ST or lat,lon.";
        return;
      }
      state.origin = { lat: +hits[0].lat, lon: +hits[0].lon };
      render();
    })
    .catch(() => {
      $("status").textContent = "Geocode failed. Use lat,lon for now.";
    });
}

function setView(mode) {
  const layout = document.getElementById("layout");
  layout.dataset.view = mode;
  document.getElementById("view-list").classList.toggle("on", mode === "list");
  document.getElementById("view-map").classList.toggle("on", mode === "map");
  if (mode === "map") {
    paintMap(filtered());
  }
}

Promise.all([
  fetch("data/parishes.json").then((r) => r.json()),
  fetch("data/schema.json").then((r) => r.json()),
]).then(([parishes, schema]) => {
  state.all = parishes;
  state.schema = schema;
  const states = [...new Set(parishes.map((p) => p.state).filter(Boolean))].sort();
  const dioceses = [...new Set(parishes.map((p) => p.diocese).filter(Boolean))].sort();
  fillSelect("f-state", states);
  fillSelect("f-diocese", dioceses);
  fillSelect("f-churchmanship", schema.list_options.churchmanship);
  fillSelect("f-wo-serve", schema.list_options.women_serve_priests);
  fillSelect("f-wo-affirmed", schema.list_options.wo_affirmed);
  fillSelect("f-lgbt-serve", schema.list_options.lgbt_serve_priests);
  fillSelect("f-lgbt-affirmed", schema.list_options.lgbt_ordination_affirmed);
  fillSelect("f-ssm", schema.list_options.ssm);
  fillSelect("f-spectrum", schema.list_options.spectrum);
  document.querySelectorAll(".filters input, .filters select").forEach((el) => {
    el.addEventListener("change", render);
    el.addEventListener("input", () => {
      if (el.id === "q") render();
    });
  });
  $("apply-origin").onclick = parseOrigin;
  $("origin").addEventListener("keydown", (e) => {
    if (e.key === "Enter") parseOrigin();
  });
  $("reset").onclick = () => {
    document.querySelectorAll(".filters input, .filters select").forEach((el) => {
      if (el.tagName === "SELECT") el.selectedIndex = 0;
      else el.value = "";
    });
    state.origin = null;
    render();
  });
  document.getElementById("view-list").onclick = () => setView("list");
  document.getElementById("view-map").onclick = () => setView("map");
  render();
});

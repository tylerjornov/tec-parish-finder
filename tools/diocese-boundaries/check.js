// Checks data/diocesan-boundaries.geojson against itself and against the parish pins:
//   1. overlaps   - land claimed by two dioceses
//   2. gaps       - land claimed by no diocese
//   3. enclaves   - holes, and detached parts that aren't islands, unless listed in JUSTIFIED
//   4. parishes   - a parish whose "diocese" differs from the diocese its pin sits in,
//                   unless listed in PARISH_EXCEPTIONS
// Exits with status 1 if it finds anything. Run from this folder:  npm run check
const fs = require("fs");
const path = require("path");
const topojson = require("topojson-client");
const counties = require("us-atlas/counties-10m.json");

const ROOT = path.join(__dirname, "../..");
// Optional argument: another boundary file to check instead.
const data = JSON.parse(fs.readFileSync(process.argv[2] || path.join(ROOT, "data/diocesan-boundaries.geojson"), "utf8"));
const STEP = 0.05; // degrees between sample points for the overlap/gap scan
// Sample points are offset off the round-degree lines so none sits exactly on a straight
// border like the 49th parallel, where "inside" is ambiguous.
const OFFSET = 0.01234;
// A pin this close (degrees, ~3 km) to a diocese counts as inside it: the map's coastline is
// simplified, so waterfront churches can land just offshore.
const SNAP = 0.03;

// Detached land parts and holes that are real. Key: diocese id + "@" + rough lon,lat of the
// part (as the report prints it).
const JUSTIFIED = {
  "diocese-of-kentucky@-89.5,36.5": "Kentucky Bend (Fulton County exclave)",
  "diocese-of-navajoland@-107.1,35.1": "Tohajiilee section of the Navajo Nation",
  "diocese-of-navajoland@-107.5,34.4": "Alamo section of the Navajo Nation",
  "diocese-of-navajoland@-110.5,36.0": "hole: Hopi reservation (Diocese of Arizona)",
  "diocese-of-navajoland@-111.2,36.1": "hole: Hopi reservation, Moenkopi (Diocese of Arizona)",
  "diocese-of-arizona@-110.5,36.0": "Hopi reservation, inside Navajoland",
  // Parts cut off from the rest of their diocese by water, though touching another diocese.
  "diocese-of-california@-122.3,37.5": "San Mateo peninsula, across the bay from Alameda/Contra Costa",
  "diocese-of-california@-122.7,38.1": "Marin County, across the Golden Gate",
  "diocese-of-easton@-76.0,38.0": "Smith Island, shared with Virginia",
  "diocese-of-new-york@-74.2,40.6": "Staten Island",
  "diocese-of-rhode-island@-71.2,41.6": "Tiverton and Little Compton, east of the Sakonnet River",
  "diocese-of-southern-virginia@-75.7,37.6": "Eastern Shore of Virginia",
  "diocese-of-southern-virginia@-76.0,37.9": "Tangier Island",
  "diocese-of-southern-virginia@-76.6,37.2": "Virginia Peninsula (Williamsburg to Hampton), across the James River",
  "diocese-of-arizona@-111.2,36.1": "Hopi reservation, Moenkopi, inside Navajoland",
  "diocese-of-the-rio-grande@-107.1,35.1": "hole: Tohajiilee (Navajoland)",
  "diocese-of-the-rio-grande@-107.5,34.4": "hole: Alamo (Navajoland)",
};

// Parishes whose pin is legitimately in another diocese's shape: the overlay gives each
// county to one diocese, but a few counties are split. Key: parish id.
const PARISH_EXCEPTIONS = JSON.parse(fs.readFileSync(path.join(__dirname, "parish-exceptions.json"), "utf8"));

const problems = [];
const report = (kind, msg) => problems.push(`[${kind}] ${msg}`);

// ---- geometry helpers ----
const polysOf = (g) => (g.type === "Polygon" ? [g.coordinates] : g.coordinates);
function bbox(rings) {
  let [x0, y0, x1, y1] = [Infinity, Infinity, -Infinity, -Infinity];
  for (const [x, y] of rings[0]) {
    if (x < x0) x0 = x; if (x > x1) x1 = x; if (y < y0) y0 = y; if (y > y1) y1 = y;
  }
  return [x0, y0, x1, y1];
}
function inRing(x, y, r) {
  let inside = false;
  for (let i = 0, j = r.length - 1; i < r.length; j = i++) {
    const [xi, yi] = r[i], [xj, yj] = r[j];
    if (yi > y !== yj > y && x < ((xj - xi) * (y - yi)) / (yj - yi) + xi) inside = !inside;
  }
  return inside;
}
const inPoly = (x, y, p) => inRing(x, y, p[0]) && !p.slice(1).some((h) => inRing(x, y, h));
const centroid = (r) => [r.reduce((s, p) => s + p[0], 0) / r.length, r.reduce((s, p) => s + p[1], 0) / r.length];
const where = ([x, y]) => `${x.toFixed(1)},${y.toFixed(1)}`;
function area(r) {
  let s = 0;
  for (let i = 0, j = r.length - 1; i < r.length; j = i++) s += (r[j][0] - r[i][0]) * (r[j][1] + r[i][1]);
  return Math.abs(s / 2);
}

// Every polygon part, with its bbox, for point lookups.
const parts = [];
for (const f of data.features) {
  for (const p of polysOf(f.geometry)) parts.push({ id: f.properties.diocese_id, poly: p, bb: bbox(p) });
}
// Coarse grid index over parts so lookups only test nearby polygons.
const CELL = 2;
const grid = {};
const cellKey = (x, y) => `${Math.floor(x / CELL)},${Math.floor(y / CELL)}`;
for (const pt of parts) {
  const [x0, y0, x1, y1] = pt.bb;
  for (let cx = Math.floor(x0 / CELL); cx <= Math.floor(x1 / CELL); cx++)
    for (let cy = Math.floor(y0 / CELL); cy <= Math.floor(y1 / CELL); cy++) (grid[`${cx},${cy}`] ||= []).push(pt);
}
function dioceseAt(x, y) {
  const hits = new Set();
  for (const pt of grid[cellKey(x, y)] || []) {
    const [x0, y0, x1, y1] = pt.bb;
    if (x >= x0 && x <= x1 && y >= y0 && y <= y1 && inPoly(x, y, pt.poly)) hits.add(pt.id);
  }
  return [...hits];
}

// Dioceses whose edge passes within tol degrees of the point.
function nearby(x, y, tol = SNAP) {
  const hits = new Set();
  for (const pt of grid[cellKey(x, y)] || []) {
    for (const r of pt.poly) for (let i = 1; i < r.length; i++) {
      if (segDist(x, y, r[i - 1], r[i]) < tol) { hits.add(pt.id); break; }
    }
  }
  return [...hits];
}
function segDist(x, y, [ax, ay], [bx, by]) {
  const k = Math.cos((y * Math.PI) / 180); // measure east-west distance at true scale
  const [px, qx, rx] = [x * k, ax * k, bx * k];
  const dx = rx - qx, dy = by - ay;
  const t = Math.max(0, Math.min(1, ((px - qx) * dx + (y - ay) * dy) / (dx * dx + dy * dy || 1)));
  return Math.hypot(px - (qx + t * dx), y - (ay + t * dy));
}

// ---- 1 & 2: overlaps and gaps, by sampling a grid over US land ----
// Land = every county the dioceses are built from (American Samoa, FIPS 60, is in no diocese
// here), so a gap means the build dropped land rather than a coastline mismatch.
const land = topojson.merge(counties, counties.objects.counties.geometries.filter((g) => !g.id.startsWith("60")));
const landParts = polysOf(land);
const overlaps = {};
const gaps = [];
for (const p of landParts) {
  const [x0, y0, x1, y1] = bbox(p);
  for (let x = Math.ceil(x0 / STEP) * STEP + OFFSET; x <= x1; x += STEP) {
    for (let y = Math.ceil(y0 / STEP) * STEP + OFFSET; y <= y1; y += STEP) {
      if (!inPoly(x, y, p)) continue;
      const ids = dioceseAt(x, y);
      if (ids.length > 1) (overlaps[ids.sort().join(" + ")] ||= []).push([x, y]);
      // A point in no diocese that sits right at one diocese's coastline is a tiny bit of
      // shoreline simplified away; a gap is blank land, or a sliver between two dioceses.
      else if (ids.length === 0 && nearby(x, y, 0.01).length !== 1) gaps.push([x, y]);
    }
  }
}
for (const [pair, pts] of Object.entries(overlaps)) {
  report("overlap", `${pair}: ${pts.length} sample point(s), e.g. ${where(pts[0])}`);
}
for (const c of cluster(gaps)) report("gap", `${c.length} sample point(s) in no diocese near ${where(c[0])}`);

// Group nearby points so one gap is one line in the report.
function cluster(pts) {
  const out = [];
  const seen = new Set();
  const k = ([x, y]) => `${Math.round(x / STEP)},${Math.round(y / STEP)}`;
  const byKey = new Map(pts.map((p) => [k(p), p]));
  for (const p of pts) {
    if (seen.has(k(p))) continue;
    const group = [];
    const stack = [p];
    seen.add(k(p));
    while (stack.length) {
      const q = stack.pop();
      group.push(q);
      const [i, j] = k(q).split(",").map(Number);
      for (const [di, dj] of [[1, 0], [-1, 0], [0, 1], [0, -1]]) {
        const nk = `${i + di},${j + dj}`;
        if (byKey.has(nk) && !seen.has(nk)) { seen.add(nk); stack.push(byKey.get(nk)); }
      }
    }
    out.push(group);
  }
  return out;
}

// ---- 3: enclaves and exclaves ----
// A detached part that shares no vertex with another diocese is surrounded by water: an island.
// (Neighbouring dioceses are built from shared county edges, so a land border always has
// vertices in common.)
const vertexOwners = new Map();
for (const f of data.features) {
  for (const p of polysOf(f.geometry)) for (const r of p) for (const [x, y] of r) {
    const k = `${x},${y}`;
    if (!vertexOwners.has(k)) vertexOwners.set(k, new Set());
    vertexOwners.get(k).add(f.properties.diocese_id);
  }
}
const touchesOther = (ring, id) => ring.some(([x, y]) => [...vertexOwners.get(`${x},${y}`)].some((o) => o !== id));
const used = new Set();
for (const f of data.features) {
  const id = f.properties.diocese_id;
  const polys = polysOf(f.geometry);
  const main = polys.reduce((a, b) => (area(b[0]) > area(a[0]) ? b : a));
  for (const p of polys) {
    if (p !== main && touchesOther(p[0], id)) flag(id, p[0], "detached part");
    for (const h of p.slice(1)) flag(id, h, "hole");
  }
}
function flag(id, ring, what) {
  const key = `${id}@${where(centroid(ring))}`;
  if (JUSTIFIED[key]) { used.add(key); return; }
  const inside = what === "hole" ? ` (filled by ${dioceseAt(...interiorPoint(ring)).join(", ") || "nothing"})` : "";
  report("enclave", `${what} of ${id} at ${where(centroid(ring))}${inside}`);
}
function interiorPoint(ring) {
  const [x0, y0, x1, y1] = bbox([ring]);
  for (let n = 4; n < 200; n *= 2)
    for (let i = 1; i < n; i++) for (let j = 1; j < n; j++) {
      const x = x0 + ((x1 - x0) * i) / n, y = y0 + ((y1 - y0) * j) / n;
      if (inRing(x, y, ring)) return [x, y];
    }
  return centroid(ring);
}
for (const key of Object.keys(JUSTIFIED)) if (!used.has(key)) report("stale", `JUSTIFIED entry no longer matches anything: ${key}`);

// ---- 4: parish pins ----
const short = (name) => name.replace(/^(Episcopal |Missionary )?(Diocese of |Church in )(the )?/i, "").replace(/ʻ/g, "").trim().toLowerCase();
const nameOf = Object.fromEntries(data.features.map((f) => [f.properties.diocese_id, f.properties.name]));
const wipDir = path.join(ROOT, "data/wip-dioceses");
const files = ["data/parishes.json", ...fs.readdirSync(wipDir)
  .filter((d) => fs.statSync(path.join(wipDir, d)).isDirectory())
  .flatMap((d) => fs.readdirSync(path.join(wipDir, d)).filter((f) => f.endsWith(".json")).map((f) => `data/wip-dioceses/${d}/${f}`))];
let checked = 0;
const usedExceptions = new Set();
for (const file of files) {
  for (const p of JSON.parse(fs.readFileSync(path.join(ROOT, file), "utf8"))) {
    if (typeof p.lat !== "number" || typeof p.lon !== "number" || !p.diocese) continue;
    checked++;
    let ids = dioceseAt(p.lon, p.lat);
    if (!ids.length) ids = nearby(p.lon, p.lat);
    const listed = short(p.diocese);
    if (ids.some((id) => short(nameOf[id]) === listed)) continue;
    if (PARISH_EXCEPTIONS[p.id]) { usedExceptions.add(p.id); continue; }
    const at = ids.length ? ids.map((id) => nameOf[id]).join(", ") : "no diocese (offshore?)";
    report("parish", `${p.name}, ${p.city || p.address} [${p.id}] is listed in ${p.diocese} but its pin (${p.lat}, ${p.lon}) is in ${at}  (${file})`);
  }
}
for (const id of Object.keys(PARISH_EXCEPTIONS)) if (!usedExceptions.has(id)) report("stale", `parish exception no longer needed: ${id}`);

console.log(`Checked ${data.features.length} dioceses and ${checked} parish pins.`);
if (problems.length) {
  console.log(`${problems.length} problem(s):\n` + problems.join("\n"));
  process.exitCode = 1;
} else {
  console.log("No problems found.");
}

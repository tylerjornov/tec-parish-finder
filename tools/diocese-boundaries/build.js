// Rebuilds data/diocesan-boundaries.geojson from dioceses.json (each diocese's county list).
//
// County shapes come from the us-atlas package (Census cartographic boundary counties, stored
// as a topology, so neighbouring counties share the exact same edge). Dissolving that by
// diocese gives borders with no gaps, overlaps or stray slivers, and keeps the larger islands.
// Navajoland is not county based: it is the Navajo Nation reservation (navajo-nation.geojson),
// cut out of the Arizona, Rio Grande and Utah dioceses.
//
// Run from this folder:  npm install && npm run build
const fs = require("fs");
const path = require("path");
const mapshaper = require("mapshaper");
const topojson = require("topojson-client");
const polylabel = require("polylabel");
const counties = require("us-atlas/counties-10m.json");

const OUT = path.join(__dirname, "../../data/diocesan-boundaries.geojson");
const dioceses = JSON.parse(fs.readFileSync(path.join(__dirname, "dioceses.json"), "utf8"));
const navajo = JSON.parse(fs.readFileSync(path.join(__dirname, "navajo-nation.geojson"), "utf8"));
// The reservation's holes are the Hopi reservation, which stays in the Diocese of Arizona.
// They are split out so widening the outline (below) can't fill them in.
const outline = { type: "MultiPolygon", coordinates: navajo.features[0].geometry.coordinates.map((p) => [p[0]]) };
const hopi = { type: "MultiPolygon", coordinates: navajo.features[0].geometry.coordinates.flatMap((p) => p.slice(1).map((r) => [r])) };

const NAVAJOLAND = "diocese-of-navajoland";
// Dioceses the reservation is carved out of.
const AROUND_NAVAJOLAND = ["diocese-of-arizona", "diocese-of-the-rio-grande", "diocese-of-utah"];

const STATE = {
  "01": "AL", "02": "AK", "04": "AZ", "05": "AR", "06": "CA", "08": "CO", "09": "CT", "10": "DE",
  "11": "DC", "12": "FL", "13": "GA", "15": "HI", "16": "ID", "17": "IL", "18": "IN", "19": "IA",
  "20": "KS", "21": "KY", "22": "LA", "23": "ME", "24": "MD", "25": "MA", "26": "MI", "27": "MN",
  "28": "MS", "29": "MO", "30": "MT", "31": "NE", "32": "NV", "33": "NH", "34": "NJ", "35": "NM",
  "36": "NY", "37": "NC", "38": "ND", "39": "OH", "40": "OK", "41": "OR", "42": "PA", "44": "RI",
  "45": "SC", "46": "SD", "47": "TN", "48": "TX", "49": "UT", "50": "VT", "51": "VA", "53": "WA",
  "54": "WV", "55": "WI", "56": "WY", "60": "AS", "66": "GU", "69": "MP", "72": "PR", "78": "VI",
};
// Not part of any diocese in this file (American Samoa is in the Diocese of Polynesia).
const SKIP_STATES = new Set(["AS"]);

// us-atlas predates two Census changes: Connecticut's planning regions (2022) and the split of
// Valdez-Cordova, Alaska (2019). Names in dioceses.json use the current Census names.
const ALIAS = {
  "Chugach Census Area, AK": ["02261"],
  "Copper River Census Area, AK": ["02261"],
};
const CT_ALL = counties.objects.counties.geometries.filter((g) => g.id.startsWith("09")).map((g) => g.id);

// "St. Mary's County, MD" -> "MD|stmarys"; drops the county-type word so names match us-atlas.
const key = (state, name) =>
  state + "|" + name
    .normalize("NFD").replace(/[̀-ͯ]/g, "").toLowerCase()
    .replace(/\b(county|parish|borough|census area|city and borough|municipality|municipio|island|islands|district|city)\b/g, "")
    .replace(/[^a-z]/g, "");

const index = {};
for (const g of counties.objects.counties.geometries) {
  (index[key(STATE[g.id.slice(0, 2)], g.properties.name)] ||= []).push(g.id);
}

function fipsFor(entry) {
  if (ALIAS[entry]) return ALIAS[entry];
  const [, name, state] = entry.match(/^(.*), (\w\w)$/);
  if (state === "CT" && / Planning Region$/.test(name)) return CT_ALL;
  const ids = index[key(state, name)];
  if (!ids) throw new Error(`No county shape for "${entry}"`);
  if (ids.length === 1) return ids;
  // Same name for a county and an independent city (Baltimore, Richmond, St. Louis...):
  // city FIPS codes are 500 and up.
  const city = /\bcity\b/i.test(name) && !/County/.test(name);
  return ids.filter((id) => (Number(id.slice(2)) >= 500) === city);
}

// County FIPS -> diocese.
const owner = {};
for (const d of dioceses) {
  if (d.diocese_id === NAVAJOLAND) continue;
  for (const c of d.counties) {
    for (const id of fipsFor(c)) {
      if (owner[id] && owner[id] !== d.diocese_id) {
        throw new Error(`${c} is listed under both ${owner[id]} and ${d.diocese_id}`);
      }
      owner[id] = d.diocese_id;
    }
  }
}
const missing = counties.objects.counties.geometries
  .filter((g) => !owner[g.id] && !SKIP_STATES.has(STATE[g.id.slice(0, 2)]))
  .map((g) => `${g.id} ${g.properties.name}, ${STATE[g.id.slice(0, 2)]}`);
if (missing.length) throw new Error(`Counties in no diocese:\n  ${missing.join("\n  ")}`);

const countyFC = topojson.feature(counties, counties.objects.counties);
countyFC.features = countyFC.features
  .filter((f) => owner[f.id])
  .map((f) => ({ type: "Feature", properties: { diocese_id: owner[f.id] }, geometry: f.geometry }));

const around = AROUND_NAVAJOLAND.map((id) => `diocese_id == "${id}"`).join(" || ");
const commands = [
  "-i counties.json navajo.json hopi.json combine-files",
  "-dissolve diocese_id target=counties name=dioceses",
  // The reservation outline only roughly follows the state lines, so it is widened a little and
  // then trimmed to the three dioceses: along a state line it then uses the county edge exactly.
  "-buffer 1000 target=navajo name=navbuf",
  `-filter '${around}' target=dioceses + name=around`,
  "-dissolve target=around",
  "-clip around target=navbuf name=navajoland",
  "-erase hopi target=navajoland",
  `-each 'diocese_id = "${NAVAJOLAND}"' target=navajoland`,
  "-filter-fields diocese_id target=navajoland",
  "-erase navajoland target=dioceses",
  "-merge-layers target=dioceses,navajoland force name=out",
  "-o out.json format=geojson precision=0.0001",
].join(" ");

mapshaper.applyCommands(commands, { "counties.json": countyFC, "navajo.json": outline, "hopi.json": hopi }, (err, output) => {
  if (err) throw err;
  const shapes = {};
  for (const f of JSON.parse(output["out.json"]).features) shapes[f.properties.diocese_id] = f.geometry;

  const features = dioceses.map((d) => {
    const geometry = shapes[d.diocese_id];
    if (!geometry) throw new Error(`No shape built for ${d.diocese_id}`);
    return {
      type: "Feature",
      properties: {
        ...d,
        source: d.diocese_id === NAVAJOLAND
          ? "Navajo Nation reservation, Census TIGERweb, simplified"
          : "Census cartographic boundary counties (us-atlas), dissolved by diocese",
        label: labelPoint(geometry),
      },
      geometry,
    };
  });
  fs.writeFileSync(OUT, JSON.stringify({ type: "FeatureCollection", features }) + "\n");
  console.log(`Wrote ${features.length} dioceses to ${path.relative(process.cwd(), OUT)}`);
});

// Where the name goes: the point deepest inside the diocese's largest part, so it always sits
// inside the diocese (a bounding-box center can land in a neighbour for odd shapes).
function labelPoint(geometry) {
  const polys = geometry.type === "Polygon" ? [geometry.coordinates] : geometry.coordinates;
  let best = null;
  let bestArea = -1;
  for (const p of polys) {
    const a = Math.abs(ringArea(p[0]));
    if (a > bestArea) [best, bestArea] = [p, a];
  }
  // Squash longitude by cos(latitude) so "deepest" is measured in real distance.
  const k = Math.cos((best[0][0][1] * Math.PI) / 180);
  const pt = polylabel(best.map((r) => r.map(([x, y]) => [x * k, y])), 0.005);
  return [+(pt[0] / k).toFixed(4), +pt[1].toFixed(4)];
}

function ringArea(ring) {
  let s = 0;
  for (let i = 0, j = ring.length - 1; i < ring.length; j = i++) {
    s += (ring[j][0] - ring[i][0]) * (ring[j][1] + ring[i][1]);
  }
  return s / 2;
}

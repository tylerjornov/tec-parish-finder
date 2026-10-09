// Diocesan boundary overlay for the parish map. Loaded after leaflet.js, before app.js.
// DioceseBoundaries.attach(map) wires the overlay to the "Diocesan boundaries" display
// setting: settings.js sets data-boundaries="on|off" on <html>, and this follows it.
// The GeoJSON is only fetched the first time the overlay is switched on.
(() => {
  const DATA_URL = "data/diocesan-boundaries.geojson";

  function attach(map) {
    const layer = L.layerGroup();
    let loading = null;
    const labels = [];
    // Touch devices have no hover, so every name stays on the map there.
    const touch = matchMedia("(hover: none)");
    const syncLabels = () => {
      for (const { label, l } of labels) {
        if (touch.matches) {
          label.setLatLng(labelPoint(l));
          layer.addLayer(label);
        } else {
          layer.removeLayer(label);
        }
      }
    };
    touch.addEventListener("change", syncLabels);

    // Colors come from css/app.css (.diocese-shape) so they follow the theme.
    // Boundaries not yet checked against diocesan sources are drawn dashed.
    const style = (f) => ({
      className: "diocese-shape",
      weight: 1.5,
      dashArray: f.properties.boundary_verified === false ? "5 4" : null,
    });

    // Label anchor: center of the largest part, so Alaska and Hawaii don't
    // get a label out in the ocean.
    // Dioceses whose largest part is a poor spot for the name. Hawaii's biggest piece
    // is the Big Island, but the name belongs over the main island chain; Alaska's
    // bounds center falls off to one side of the interior.
    const LABEL_AT = {
      "diocese-of-hawaii": L.latLng(20.75, -157.0),
      "diocese-of-alaska": L.latLng(64.5, -152.0),
    };

    function labelPoint(l) {
      const fixed = LABEL_AT[l.feature?.properties.diocese_id];
      if (fixed) return fixed;
      const parts = l.getLayers ? l.getLayers() : [l];
      let best = null;
      let bestArea = -1;
      for (const p of parts) {
        const b = p.getBounds();
        const area = (b.getNorth() - b.getSouth()) * (b.getEast() - b.getWest());
        if (area > bestArea) [best, bestArea] = [b, area];
      }
      return best.getCenter();
    }

    function load() {
      loading ||= fetch(DATA_URL)
        .then((r) => {
          if (!r.ok) throw new Error(`HTTP ${r.status}`);
          return r.json();
        })
        .then((data) => {
          L.geoJSON(data, {
            style,
            onEachFeature: (f, l) => {
              // Hover only: the name shows centered on the diocese, nothing on click.
              const label = L.tooltip({
                permanent: false,
                direction: "center",
                className: "diocese-label",
                interactive: false,
              }).setContent(f.properties.name.replace(/^Episcopal /, ""));
              labels.push({ label, l });
              l.on("mouseover", () => {
                if (!touch.matches) label.setLatLng(labelPoint(l)).addTo(map);
              });
              l.on("mouseout", () => {
                if (!touch.matches) label.remove();
              });
            },
          }).addTo(layer);
          syncLabels();
        })
        .catch(() => {
          loading = null; // allow a retry next time it is switched on
        });
      return loading;
    }

    function sync() {
      if (document.documentElement.dataset.boundaries === "on") {
        layer.addTo(map);
        load();
      } else {
        layer.remove();
      }
    }

    sync();
    new MutationObserver(sync).observe(document.documentElement, {
      attributes: true,
      attributeFilter: ["data-boundaries"],
    });
    return layer;
  }

  window.DioceseBoundaries = { attach, DATA_URL };
})();

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
    // Names get their own pane above the boundary shapes but below the parish pins
    // (markerPane is 600, tooltipPane 650), so the pins sit over the names.
    const labelPane = map.createPane("diocese-labels");
    labelPane.style.zIndex = 550;
    // Touch devices have no hover, so every name stays on the map there. On hover
    // devices the "Always show diocese names" setting (data-diocese-names="always")
    // does the same; otherwise a name shows only while the pointer is over its diocese.
    const touch = matchMedia("(hover: none)");
    const persistent = () => touch.matches || document.documentElement.dataset.dioceseNames === "always";
    const syncLabels = () => {
      sizeLabels();
      for (const { label, l } of labels) {
        if (persistent()) {
          label.setLatLng(labelPoint(l));
          layer.addLayer(label);
        } else {
          layer.removeLayer(label);
        }
      }
    };
    touch.addEventListener("change", syncLabels);

    // Names scale with the map, so they keep the same size relative to the dioceses
    // and stay inside them: full size (13px) from zoom 6 in, halving with each step
    // out, and hidden once they'd be under 6px.
    const FULL_SIZE = 13;
    const FULL_ZOOM = 6;
    const MIN_SIZE = 6;
    function sizeLabels() {
      const box = map.getContainer();
      const size = FULL_SIZE * 2 ** Math.min(0, map.getZoom() - FULL_ZOOM);
      box.style.setProperty("--diocese-label-size", `${size}px`);
      box.classList.toggle("diocese-labels-hidden", size < MIN_SIZE);
    }
    map.on("zoomend", () => {
      sizeLabels();
      // Re-center each name for its new size.
      for (const { label } of labels) if (label.isOpen()) label.update();
    });
    sizeLabels();

    // Colors come from css/app.css (.diocese-shape) so they follow the theme.
    const style = { className: "diocese-shape", weight: 1.5 };

    // Label anchor: the "label" point the build script stores for each diocese (a spot
    // well inside its largest part). Hawaii's largest part is the Big Island, but the
    // name belongs over the main island chain.
    const LABEL_AT = {
      "diocese-of-hawaii": L.latLng(20.75, -157.0),
    };

    function labelPoint(l) {
      const { diocese_id: id, label } = l.feature.properties;
      if (LABEL_AT[id]) return LABEL_AT[id];
      if (label) return L.latLng(label[1], label[0]);
      return l.getBounds().getCenter();
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
              // Unless names are always on, the name shows while the pointer is over
              // the diocese. permanent: true either way, since
              // Leaflet closes a non-permanent tooltip on any tap or click on the map.
              const label = L.tooltip({
                permanent: true,
                direction: "center",
                className: "diocese-label",
                pane: "diocese-labels",
                interactive: false,
              }).setContent(f.properties.name.replace(/^Episcopal /, ""));
              labels.push({ label, l });
              l.on("mouseover", () => {
                if (!persistent()) label.setLatLng(labelPoint(l)).addTo(map);
              });
              l.on("mouseout", () => {
                if (!persistent()) label.remove();
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
    new MutationObserver(syncLabels).observe(document.documentElement, {
      attributes: true,
      attributeFilter: ["data-diocese-names"],
    });
    return layer;
  }

  window.DioceseBoundaries = { attach, DATA_URL };
})();

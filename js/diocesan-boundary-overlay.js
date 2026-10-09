// Diocesan boundary overlay for the parish map. Loaded after leaflet.js and app.js.
// Call DioceseBoundaries.attach(map) once the Leaflet map exists; it returns a
// layer group that can be added to a layer control. Nothing is drawn yet.
(() => {
  // Where the boundary data will come from. Not used until the overlay is built.
  const DATA_URL = "data/diocesan-boundaries.geojson";

  function attach(map) {
    const layer = L.layerGroup();
    // Boundaries get added to this group once the overlay is built.
    return layer;
  }

  window.DioceseBoundaries = { attach, DATA_URL };
})();

// Shared GPS point overlay for Operations and Outcomes dashboard maps.

(function (root, factory) {
  if (typeof module === "object" && typeof module.exports === "object") {
    module.exports = factory();
  } else {
    root.DashboardGPSLayer = factory();
  }
})(typeof self !== "undefined" ? self : this, function () {
  "use strict";

  const GPS_PANE_Z_INDEX = 450;
  const EVENT_TYPE_LABELS = {
    death: "Death",
    pregnancy: "Pregnancy",
    pregnancy_outcome: "Pregnancy outcome",
    verbal_autopsy: "Verbal autopsy",
  };
  const DOT_STYLE = {
    radius: 4,
    fill: true,
    color: "#ffffff",
    fillColor: "#14a384",
    fillOpacity: 0.9,
    opacity: 1,
    stroke: true,
    weight: 1,
  };

  function escapeHtml(value) {
    if (value == null) return "";
    return String(value)
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;")
      .replace(/'/g, "&#39;");
  }

  function eventTypeLabel(value) {
    const normalized = String(value || "").trim().toLowerCase();
    if (!normalized) return "";
    return EVENT_TYPE_LABELS[normalized] || normalized.replace(/_/g, " ");
  }

  function isValidCoordinate(latitude, longitude) {
    return (
      Number.isFinite(latitude) &&
      Number.isFinite(longitude) &&
      latitude >= -90 &&
      latitude <= 90 &&
      longitude >= -180 &&
      longitude <= 180
    );
  }

  function buildTooltipHTML(point, includeCoords) {
    const adminParts = ["province", "district", "constituency", "ward", "ea"]
      .map((field) => point[field])
      .filter((value) => value && String(value).trim().length);
    const lat = Number(point.latitude);
    const lon = Number(point.longitude);
    const typeLabel = eventTypeLabel(point.event_type);
    const hasCoords = includeCoords && isValidCoordinate(lat, lon);
    if (!typeLabel && !adminParts.length && !hasCoords) return null;

    const typeLine = typeLabel
      ? `<div class="dashboard-gps-tooltip__type"><strong>${escapeHtml(typeLabel)}</strong></div>`
      : "";
    const adminLine = adminParts.length
      ? `<div>${adminParts.map(escapeHtml).join(" › ")}</div>`
      : "";
    const coordsLine = hasCoords
      ? `<div><strong>GPS:</strong> ${lat.toFixed(3)}, ${lon.toFixed(3)}</div>`
      : "";
    return `<div class="dashboard-gps-tooltip">${typeLine}${adminLine}${coordsLine}</div>`;
  }

  function attachGPSLayer(map, opts) {
    const leaflet = typeof window !== "undefined" ? window.L : null;
    if (!leaflet || !map || !opts || typeof leaflet.svg !== "function") return null;

    const options = Object.assign(
      {
        userCanViewPII: false,
        fetchPoints: null,
        onLoading: null,
        onPointsRendered: null,
        onPIIDenied: null,
        onFetchError: null,
      },
      opts
    );
    if (!options.userCanViewPII) return null;

    const paneName = `${options.idPrefix || "dashboard"}GpsPane`;
    if (!map.getPane(paneName)) map.createPane(paneName);
    const pane = map.getPane(paneName);
    if (!pane) return null;
    pane.classList.add("dashboard-gps-pane");
    pane.style.zIndex = String(options.zIndex || GPS_PANE_Z_INDEX);

    // SVG keeps each point independently interactive while the pane remains
    // click-through, so choropleth drill-down still works between markers.
    const renderer = leaflet.svg({ padding: 0.5, pane: paneName });
    if (!renderer) return null;
    const group = leaflet.featureGroup();
    let requestVersion = 0;

    function ensureAdded() {
      if (!map.hasLayer(renderer)) renderer.addTo(map);
      if (!map.hasLayer(group)) group.addTo(map);
    }

    function clear() {
      group.clearLayers();
    }

    function renderPoints(points) {
      ensureAdded();
      clear();
      let shown = 0;

      points.forEach((point) => {
        const lat = Number(point.latitude);
        const lon = Number(point.longitude);
        if (!isValidCoordinate(lat, lon)) return;

        const marker = leaflet.circleMarker([lat, lon], {
          ...DOT_STYLE,
          pane: paneName,
          renderer,
        });
        const tooltip = buildTooltipHTML(point, options.userCanViewPII);
        if (tooltip) {
          marker.bindTooltip(tooltip, {
            sticky: true,
            direction: "top",
            opacity: 0.95,
          });
        }
        marker.addTo(group);
        shown += 1;
      });
      return shown;
    }

    async function refresh() {
      if (typeof options.fetchPoints !== "function") return;
      const currentVersion = ++requestVersion;
      clear();
      if (typeof options.onLoading === "function") options.onLoading();

      try {
        const result = await options.fetchPoints();
        if (currentVersion !== requestVersion) return;
        if (!result) throw new Error("GPS response was empty.");
        if (result.gps_pii_denied) {
          clear();
          if (typeof options.onPIIDenied === "function") options.onPIIDenied();
          return;
        }

        const points = Array.isArray(result.gps_points) ? result.gps_points : [];
        const shown = renderPoints(points);
        if (typeof options.onPointsRendered === "function") {
          options.onPointsRendered({
            total: Number(result.gps_total) || points.length,
            shown,
            truncated: Boolean(result.gps_truncated),
          });
        }
      } catch (error) {
        if (currentVersion !== requestVersion) return;
        clear();
        console.error("[gps-layer] failed to load points", error);
        if (typeof options.onFetchError === "function") options.onFetchError(error);
      }
    }

    function hide() {
      if (map.hasLayer(group)) map.removeLayer(group);
    }

    function show() {
      ensureAdded();
    }

    ensureAdded();
    return { refresh, hide, show };
  }

  return {
    attachGPSLayer,
    DOT_STYLE,
    GPS_PANE_Z_INDEX,
  };
});

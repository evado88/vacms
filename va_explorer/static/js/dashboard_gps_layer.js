// dashboard_gps_layer.js
//
// Dot-density GPS marker layer for dashboard maps. Renders every record's
// GPS coordinate as an individual circleMarker on a shared canvas renderer
// for performance. Used by:
//   - hierarchical_dashboard_map.js (outcomes dashboards)
//   - home_hierarchical_dashboard_map.js (operational dashboard)
//   - dashboard_map.js (legacy VA Vue dashboard)
//
// Always-on (no toggle). Server enforces PII gating via va_analytics.view_pii;
// the layer simply doesn't fetch when userCanViewPII is false.

(function (root, factory) {
  if (typeof module === "object" && typeof module.exports === "object") {
    module.exports = factory();
  } else {
    root.DashboardGPSLayer = factory();
  }
})(typeof self !== "undefined" ? self : this, function () {
  "use strict";

  const DOT_STYLE = {
    radius: 4,
    fillColor: "#14a384",
    fillOpacity: 0.7,
    stroke: false,
    weight: 0,
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

  function buildTooltipHTML(point, includeCoords) {
    const adminParts = ["province", "district", "ward", "ea"]
      .map((field) => point[field])
      .filter((v) => v && String(v).trim().length);
    const lat = Number(point.latitude);
    const lon = Number(point.longitude);
    const hasCoords =
      includeCoords && Number.isFinite(lat) && Number.isFinite(lon);
    if (!adminParts.length && !hasCoords) return null;
    const adminLine = adminParts.length
      ? `<div>${adminParts.map(escapeHtml).join(" › ")}</div>`
      : "";
    const coordsLine = hasCoords
      ? `<div><strong>GPS:</strong> ${lat.toFixed(3)}, ${lon.toFixed(3)}</div>`
      : "";
    return `<div class="dashboard-gps-tooltip">${adminLine}${coordsLine}</div>`;
  }

  function attachGPSLayer(map, opts) {
    if (!window.L || !map || !opts) return null;
    const options = Object.assign(
      {
        userCanViewPII: false,
        // async () => { gps_points, gps_total, gps_truncated, gps_pii_denied }
        fetchPoints: null,
        // optional ({ total, shown, truncated }) => void
        onPointsRendered: null,
      },
      opts
    );

    if (!options.userCanViewPII) {
      return null;
    }

    const paneName = `${options.idPrefix || "dashboard"}GpsPane`;
    if (!map.getPane(paneName)) {
      map.createPane(paneName);
    }
    const pane = map.getPane(paneName);
    if (pane) {
      pane.style.zIndex = 650;
      pane.style.pointerEvents = "auto";
    }

    const renderer = L.canvas({ padding: 0.5, pane: paneName });
    const group = L.featureGroup();
    let groupAdded = false;
    let inFlight = null;

    function ensureAdded() {
      if (!groupAdded) {
        group.addTo(map);
        groupAdded = true;
      }
    }

    function renderPoints(points) {
      group.clearLayers();
      const includeCoords = options.userCanViewPII;
      let shown = 0;
      points.forEach((point) => {
        const lat = Number(point.latitude);
        const lon = Number(point.longitude);
        if (!Number.isFinite(lat) || !Number.isFinite(lon)) return;
        const marker = L.circleMarker([lat, lon], {
          ...DOT_STYLE,
          pane: paneName,
          renderer,
        });
        const tip = buildTooltipHTML(point, includeCoords);
        if (tip) {
          marker.bindTooltip(tip, {
            sticky: true,
            direction: "top",
            opacity: 0.9,
          });
        }
        marker.addTo(group);
        shown += 1;
      });
      ensureAdded();
      return shown;
    }

    async function refresh() {
      if (typeof options.fetchPoints !== "function") return;
      // De-dupe concurrent refreshes; latest call wins.
      const myRequest = options.fetchPoints();
      inFlight = myRequest;
      try {
        const result = await myRequest;
        if (inFlight !== myRequest) return; // a newer refresh superseded us
        if (!result || result.gps_pii_denied) {
          group.clearLayers();
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
      } catch (err) {
        console.error("[gps-layer] failed to load points", err);
      } finally {
        if (inFlight === myRequest) inFlight = null;
      }
    }

    function hide() {
      if (groupAdded && map.hasLayer(group)) {
        map.removeLayer(group);
        groupAdded = false;
      }
    }

    function show() {
      ensureAdded();
    }

    return {
      refresh: refresh,
      hide: hide,
      show: show,
    };
  }

  return {
    attachGPSLayer: attachGPSLayer,
    DOT_STYLE: DOT_STYLE,
  };
});

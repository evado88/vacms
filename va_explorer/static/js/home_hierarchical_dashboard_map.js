(function () {
  if (window.createHomeHierarchicalDashboardMap) return;

  const LEVEL_CONFIG = {
    1: { label: "Province", file: "level_1_provinces.geojson", key: "province_name" },
    2: { label: "District", file: "level_2_districts.geojson", key: "district_name" },
    3: { label: "Constituency", file: "level_3_constituencies.geojson", key: "constituency_name" },
    4: { label: "Ward", file: "level_4_wards.geojson", key: "ward_name" },
    5: { label: "EA", file: "level_5_ea.geojson", key: "ea_name" },
  };

  const DEFAULT_COLORS = [
    "#4575b4",
    "#74add1",
    "#abd9e9",
    "#e0f3f8",
    "#fee090",
    "#fdae61",
    "#f46d43",
    "#d73027",
  ];
  const VA_COLORS = [
    "#4575b4",
    "#74add1",
    "#abd9e9",
    "#e0f3f8",
    "#ffffbf",
    "#fee090",
    "#fdae61",
    "#f46d43",
    "#d73027",
  ];

  const toTitleCase = (value) =>
    (value || "")
      .toString()
      .trim()
      .toLowerCase()
      .split(/\s+/)
      .map((part) => part.charAt(0).toUpperCase() + part.slice(1))
      .join(" ");

  const toSafeInt = (value) => {
    const numeric = Number(value);
    return Number.isFinite(numeric) ? numeric : 0;
  };

  const mercatorToWgs84 = (coord) => {
    const x = Number(coord[0] || 0);
    const y = Number(coord[1] || 0);
    const lng = (x / 20037508.34) * 180;
    const lat = (Math.atan(Math.exp((y / 20037508.34) * Math.PI)) * 360) / Math.PI - 90;
    return [lng, lat];
  };

  const reprojectCoords = (coords) => {
    if (!Array.isArray(coords)) return coords;
    if (coords.length >= 2 && typeof coords[0] === "number" && typeof coords[1] === "number") {
      return mercatorToWgs84(coords);
    }
    return coords.map(reprojectCoords);
  };

  const reprojectFeatureCollection = (geojson) => {
    if (!geojson || !geojson.features || !geojson.features.length) return geojson;
    const crsName = geojson?.crs?.properties?.name || "";
    const shouldReproject = crsName.includes("3857") || crsName.includes("102100");
    if (!shouldReproject) return geojson;

    // Mutate in-place: this runs once per file (result is cached in geojsonCache),
    // so a deep-copy via JSON.parse/JSON.stringify is not needed here.
    geojson.features = geojson.features.map((feature) => {
      const geometry = feature?.geometry;
      if (!geometry || !geometry.coordinates) return feature;
      return {
        ...feature,
        geometry: {
          ...geometry,
          coordinates: reprojectCoords(geometry.coordinates),
        },
      };
    });
    delete geojson.crs;
    return geojson;
  };

  const createHierarchicalDashboardMap = (options) => {
    const {
      containerId,
      legendId,
      breadcrumbId,
      emptyStateId,
      endpoint,
      buildParams,
      normalizeGeoName,
      onSelectionChange,
      colors,
      noDataMessage,
      initialView = "Province",
      styleVariant = "default",
      fitToDataBounds = true,
      userCanViewPII = false,
      gpsLayerIdPrefix = "home",
    } = options;

    const state = {
      map: null,
      layer: null,
      payload: null,
      geojsonCache: {},
      filters: {},
      activeView: initialView,
      path: [{ id: "ZM", name: "Zambia", levelLabel: "Country", levelIndex: 0 }],
      colors: (
        colors && colors.length ? colors : (styleVariant === "va" ? VA_COLORS : DEFAULT_COLORS)
      ).slice(),
      selectionKey: "",
      selectedNode: null,
    };

    const normalize =
      typeof normalizeGeoName === "function"
        ? normalizeGeoName
        : (value) => (value || "").toString().toLowerCase().replace(/\s+/g, " ").trim();

    const canonicalEaToken = (value) => {
      const raw = (value || "").toString().trim();
      if (!raw) return "";
      const digitsOnly = raw.replace(/\D+/g, "");
      if (!digitsOnly) return "";
      const withoutLeadingZeros = digitsOnly.replace(/^0+/, "");
      return withoutLeadingZeros || "0";
    };

    const eaLookupKey = (value) => {
      const token = canonicalEaToken(value);
      return token ? `__ea__${token}` : "";
    };

    const resolveStartLevel = (view) => {
      const normalized = (view || "").toString().trim().toLowerCase();
      if (normalized === "district") return 2;
      if (normalized === "constituency") return 3;
      if (normalized === "ward") return 4;
      if (normalized === "ea") return 5;
      return 1;
    };

    const currentLevel = () => {
      const startLevel = resolveStartLevel(state.activeView);
      return Math.min(startLevel + state.path.length - 1, 5);
    };

    const currentSelection = () => {
      const selected = state.selectedNode;
      if (
        selected &&
        selected.levelIndex > 0 &&
        selected.levelLabel &&
        selected.name
      ) {
        return {
          geography_level: (selected.levelLabel || "").toString().trim().toLowerCase(),
          geography_value: (selected.name || "").toString().trim(),
        };
      }
      const last = state.path[state.path.length - 1];
      if (!last || !last.levelIndex || !last.levelLabel || !last.name) {
        return { geography_level: "", geography_value: "" };
      }
      return {
        geography_level: (last.levelLabel || "").toString().trim().toLowerCase(),
        geography_value: (last.name || "").toString().trim(),
      };
    };

    const emitSelectionChange = () => {
      if (typeof onSelectionChange !== "function") return;
      const selection = currentSelection();
      const key = `${selection.geography_level}:${selection.geography_value}`;
      if (key === state.selectionKey) return;
      state.selectionKey = key;
      onSelectionChange(selection);
    };

    const setEmpty = (isEmpty) => {
      const el = document.getElementById(emptyStateId);
      if (el) el.hidden = !isEmpty;
    };

    const computeBins = (values) => {
      const nonZero = values.filter((v) => v > 0);
      if (!nonZero.length) return [0];
      const max = Math.max(...nonZero);
      const steps = Math.min(state.colors.length, 6);
      const bins = [1];
      const width = Math.max(1, Math.ceil(max / steps));
      for (let i = 1; i <= steps; i += 1) bins.push(i * width);
      return bins;
    };

    const getColorForCount = (count, bins) => {
      if (!count || count <= 0) return "#c0c0c0";
      for (let i = 0; i < bins.length - 1; i += 1) {
        if (count >= bins[i] && count <= bins[i + 1]) {
          return state.colors[Math.min(i, state.colors.length - 1)];
        }
      }
      return state.colors[Math.min(bins.length - 2, state.colors.length - 1)];
    };

    const renderLegend = (bins) => {
      const legend = document.getElementById(legendId);
      if (!legend) return;
      if (!bins || bins.length <= 1) {
        legend.innerHTML = `<div>${noDataMessage || "No map data available for the selected filters."}</div>`;
        setEmpty(true);
        return;
      }
      let html = "<div><svg width='18' height='14'><rect fill='#c0c0c0' width='14' height='14'></rect></svg>0</div>";
      for (let i = 0; i < bins.length - 1; i += 1) {
        const label = i === bins.length - 2 ? `${bins[i]}+` : `${bins[i]} - ${bins[i + 1]}`;
        html += `<div><svg width='18' height='14'><rect fill='${state.colors[i]}' width='14' height='14'></rect></svg>${label}</div>`;
      }
      legend.innerHTML = html;
      setEmpty(false);
    };

    const renderBreadcrumbs = () => {
      const root = document.getElementById(breadcrumbId);
      if (!root) return;
      const crumbs = state.path;
      root.innerHTML = "";

      const label = document.createElement("span");
      label.className = "map-breadcrumb-label";
      label.textContent = "Location:";
      root.appendChild(label);

      crumbs.forEach((crumb, index) => {
        const item = document.createElement("button");
        item.type = "button";
        item.className = `map-breadcrumb-item${index === crumbs.length - 1 ? " is-active" : ""}`;
        item.textContent = crumb.name;
        item.disabled = index === crumbs.length - 1;
        item.addEventListener("click", async () => {
          if (index === crumbs.length - 1) return;
          state.path = crumbs.slice(0, index + 1);
          state.selectedNode = null;
          emitSelectionChange();
          await refresh(state.filters);
        });
        root.appendChild(item);

        if (index < crumbs.length - 1) {
          const sep = document.createElement("span");
          sep.className = "map-breadcrumb-sep";
          sep.textContent = "›";
          root.appendChild(sep);
        }
      });
    };

    const ensureMap = async () => {
      if (state.map || typeof L === "undefined") return;
      state.map = L.map(containerId, {
        maxBounds: [
          [-6, 20],
          [-20, 34],
        ],
      }).setView([-13, 27], 6);
      state.map.attributionControl.setPrefix("");
      state.map.keyboard.disable();
      state.map.doubleClickZoom.disable();

      L.tileLayer("https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png", {
        maxZoom: 19,
        // OSM public tile servers block requests when the browser omits Referer.
        referrerPolicy: "strict-origin-when-cross-origin",
        attribution: '&copy; OpenStreetMap contributors',
      }).addTo(state.map);

      if (L.control && typeof L.control.fullscreen === "function") {
        L.control.fullscreen({ position: "topright" }).addTo(state.map);
      }
    };

    const loadGeojsonLevel = async (level) => {
      if (state.geojsonCache[level]) return state.geojsonCache[level];
      const file = LEVEL_CONFIG[level]?.file;
      if (!file) return null;
      const url = `${window.location.origin}/static/data/geojson/${file}`;
      const response = await fetch(url, {
        method: "GET",
        headers: { "X-Requested-With": "XMLHttpRequest" },
        credentials: "same-origin",
      });
      if (!response.ok) return null;
      const data = reprojectFeatureCollection(await response.json());
      state.geojsonCache[level] = data;
      return data;
    };

    const rowsForLevel = (payload, level) => {
      if (!payload) return [];
      if (level === 1) return payload.map_province_sums || [];
      if (level === 2) return payload.map_district_sums || [];
      if (level === 3) return payload.map_constituency_sums || [];
      if (level === 4) return payload.map_ward_sums || [];
      return payload.map_ea_sums || [];
    };

    const rowNameForLevel = (row, level) => {
      const key = LEVEL_CONFIG[level]?.key;
      return key ? row?.[key] : "";
    };

    const buildLookup = (rows, level) => {
      const map = new Map();
      (rows || []).forEach((row) => {
        const raw = rowNameForLevel(row, level);
        const key = normalize(raw);
        const rowPayload = {
          count: toSafeInt(row?.count),
          pregnancies_count: toSafeInt(row?.pregnancies_count),
          pregnancy_outcomes_count: toSafeInt(row?.pregnancy_outcomes_count),
          deaths_count: toSafeInt(row?.deaths_count),
          verbal_autopsies_count: toSafeInt(row?.verbal_autopsies_count),
        };
        if (key) map.set(key, rowPayload);
        if (level === 5) {
          const canonicalKey = eaLookupKey(raw);
          if (canonicalKey) map.set(canonicalKey, rowPayload);
        }
      });
      return map;
    };

    const lookupRowData = (lookup, level, feature) => {
      const directName = normalize(feature?.properties?.area_name || "");
      if (directName && lookup.has(directName)) return lookup.get(directName);
      if (level === 5) {
        const byId = eaLookupKey(feature?.properties?.area_id);
        if (byId && lookup.has(byId)) return lookup.get(byId);
        const byName = eaLookupKey(feature?.properties?.area_name);
        if (byName && lookup.has(byName)) return lookup.get(byName);
      }
      return null;
    };

    const renderTooltipHtml = (areaName, levelLabel, entry, canDrill) => {
      const totals = entry || {};
      const totalCount = toSafeInt(totals.count);
      return `
        <div class="mapTooltip">
          <h4>${areaName} ${levelLabel}</h4>
          <p>Total events: ${totalCount}</p>
          <p>Pregnancies: ${toSafeInt(totals.pregnancies_count)}</p>
          <p>Pregnancy outcomes: ${toSafeInt(totals.pregnancy_outcomes_count)}</p>
          <p>Deaths: ${toSafeInt(totals.deaths_count)}</p>
          <p>Verbal autopsies: ${toSafeInt(totals.verbal_autopsies_count)}</p>
          ${canDrill ? "<small>Click to drill down</small>" : ""}
        </div>
      `;
    };

    const fitLayerBounds = () => {
      if (!state.map || !state.layer) return;
      try {
        state.map.invalidateSize();
        if (!fitToDataBounds) {
          state.map.setView([-13, 27], 6);
          return;
        }
        const bounds = state.layer.getBounds();
        if (bounds && bounds.isValid()) {
          state.map.fitBounds(bounds, { padding: [8, 8], maxZoom: 18 });
        } else {
          state.map.setView([-13, 27], 6);
        }
      } catch (_err) {
        state.map.setView([-13, 27], 6);
      }
    };

    const render = async () => {
      await ensureMap();
      if (!state.map || !state.payload) return;

      const level = currentLevel();
      const geojson = await loadGeojsonLevel(level);
      if (!geojson || !geojson.features) {
        setEmpty(true);
        return;
      }

      const parent = state.path[state.path.length - 1];
      // Build a shallow wrapper with a filtered features array rather than a
      // full deep-copy via JSON.parse/JSON.stringify. Leaflet does not mutate
      // the feature objects, only reads their geometry/properties.
      const filteredFeatures = geojson.features.filter((feature) => {
        if (state.path.length === 1 && level > 1) return true;
        if (level === 1) return feature?.properties?.parent_id == null;
        return String(feature?.properties?.parent_id) === String(parent.id);
      });
      const filtered = { type: geojson.type, features: filteredFeatures };

      const rows = rowsForLevel(state.payload, level);
      const lookup = buildLookup(rows, level);
      const bins = computeBins(Array.from(lookup.values()).map((entry) => toSafeInt(entry?.count)));

      if (state.layer) state.map.removeLayer(state.layer);

      state.layer = L.geoJson(filtered, {
        style: (feature) => {
          const count = toSafeInt(lookupRowData(lookup, level, feature)?.count);
          const color = getColorForCount(count, bins);
          const isVAStyle = styleVariant === "va";
          return {
            stroke: true,
            weight: 1,
            color: isVAStyle ? "black" : color,
            opacity: 1,
            fillColor: color,
            fillOpacity: isVAStyle ? 0.6 : 0.7,
          };
        },
        onEachFeature: (feature, layer) => {
          const areaName = feature?.properties?.area_name || "";
          const rowData = lookupRowData(lookup, level, feature) || { count: 0 };
          const levelLabel = toTitleCase(feature?.properties?.area_level_label || LEVEL_CONFIG[level]?.label || "");
          const canDrill = level < 5;
          layer.bindTooltip(renderTooltipHtml(areaName, levelLabel, rowData, canDrill));
          layer.on("click", async () => {
            const selectedNode = {
              id: feature?.properties?.area_id,
              name: areaName,
              levelLabel: LEVEL_CONFIG[level].label,
              levelIndex: level,
            };
            state.selectedNode = selectedNode;
            emitSelectionChange();

            if (!canDrill) return;
            const nextNode = {
              id: feature?.properties?.area_id,
              name: areaName,
              levelLabel: LEVEL_CONFIG[level].label,
              levelIndex: level,
            };
            if (!nextNode.id || !nextNode.name) return;
            state.path.push(nextNode);
            emitSelectionChange();
            await refresh(state.filters);
          });
        },
      }).addTo(state.map);

      renderLegend(bins);
      renderBreadcrumbs();
      fitLayerBounds();
      setEmpty(filteredFeatures.length === 0 || rows.length === 0);
    };

    const fetchPayload = async (filters) => {
      const params = buildParams(filters || {});
      const requestUrl = params.toString() ? `${endpoint}?${params.toString()}` : endpoint;
      const response = await fetch(requestUrl, {
        method: "GET",
        headers: { "X-Requested-With": "XMLHttpRequest" },
        credentials: "same-origin",
      });
      if (!response.ok) {
        throw new Error(`Request failed: ${requestUrl}`);
      }
      return response.json();
    };

    let gpsLayerCtl = null;

    const ensureGPSLayer = () => {
      if (gpsLayerCtl || !userCanViewPII) return gpsLayerCtl;
      if (!state.map || !window.DashboardGPSLayer) return null;
      const legendEl = document.getElementById(legendId);
      if (!legendEl) return null;
      gpsLayerCtl = window.DashboardGPSLayer.attachGPSLayer(state.map, {
        legendEl,
        userCanViewPII: true,
        idPrefix: gpsLayerIdPrefix,
        fetchPoints: async () => {
          const params = buildParams(state.filters || {});
          params.set("include_gps_points", "true");
          const requestUrl = params.toString()
            ? `${endpoint}?${params.toString()}`
            : endpoint;
          const response = await fetch(requestUrl, {
            method: "GET",
            headers: { "X-Requested-With": "XMLHttpRequest" },
            credentials: "same-origin",
          });
          if (!response.ok) throw new Error(`GPS request failed: ${requestUrl}`);
          return response.json();
        },
      });
      return gpsLayerCtl;
    };

    const refresh = async (filters) => {
      state.filters = { ...(filters || {}) };
      const nextView = filters?.map_view || state.activeView || "Province";
      if (nextView !== state.activeView) {
        state.path = [{ id: "ZM", name: "Zambia", levelLabel: "Country", levelIndex: 0 }];
        state.selectedNode = null;
        emitSelectionChange();
      }
      state.activeView = nextView;
      state.payload = await fetchPayload(state.filters);
      await render();
      if (userCanViewPII) {
        const layer = gpsLayerCtl || ensureGPSLayer();
        if (layer) layer.refresh();
      }
    };

    return {
      refresh,
      resize: () => {
        if (state.map) state.map.invalidateSize();
      },
      resetDrill: async () => {
        state.path = [{ id: "ZM", name: "Zambia", levelLabel: "Country", levelIndex: 0 }];
        state.selectedNode = null;
        emitSelectionChange();
        await refresh(state.filters);
      },
      getSelection: () => ({ ...currentSelection() }),
      hideGPSLayer: () => {
        if (gpsLayerCtl) gpsLayerCtl.hide();
      },
    };
  };

  window.createHomeHierarchicalDashboardMap = createHierarchicalDashboardMap;
})();

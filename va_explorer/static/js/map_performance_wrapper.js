(function () {
  "use strict";

  if (window.__hierarchicalMapPerfWrapperInstalled) return;

  const registry = (window.__hierarchicalMapRegistry =
    window.__hierarchicalMapRegistry || new Map());

  const stableStringify = (value) => {
    if (value == null) return "";
    if (Array.isArray(value)) {
      return `[${value.map(stableStringify).join(",")}]`;
    }
    if (typeof value === "object") {
      const keys = Object.keys(value).sort();
      return `{${keys.map((key) => `${JSON.stringify(key)}:${stableStringify(value[key])}`).join(",")}}`;
    }
    return JSON.stringify(value);
  };

  const pickMapRelevantFilters = (filters) => {
    const src = filters || {};
    return {
      map_view: src.map_view || "Province",
      geography_level: src.geography_level || "",
      geography_value: src.geography_value || "",
      location_level: src.location_level || "",
      location_value: src.location_value || "",
      time_preset: src.time_preset || "",
      start_datetime: src.start_datetime || "",
      end_datetime: src.end_datetime || "",
      preset: src.preset || "",
      start: src.start || "",
      end: src.end || "",
      pregnancy_outcome: src.pregnancy_outcome || "",
      sex: src.sex || "",
      age_group: src.age_group || "",
      coded_only: src.coded_only || "",
    };
  };

  const controllerKey = (options) => {
    const containerId = options?.containerId || "";
    const endpoint = options?.endpoint || "";
    return `${containerId}::${endpoint}`;
  };

  const installWrapper = () => {
    if (typeof window.createHierarchicalDashboardMap !== "function") return false;
    if (window.createHierarchicalDashboardMap.__perfWrapped) return true;

    const originalFactory = window.createHierarchicalDashboardMap;
    const wrappedFactory = (options) => {
      const key = controllerKey(options);
      if (registry.has(key)) {
        return registry.get(key).publicController;
      }

      const baseController = originalFactory(options);
      const state = {
        options: options || {},
        initialized: false,
        lastFilterSignature: "",
        lastRefreshPromise: null,
      };

      const publicController = {
        refresh: async (filters) => {
          const selection =
            typeof baseController.getSelection === "function"
              ? baseController.getSelection()
              : {};
          const relevantFilters = pickMapRelevantFilters({
            ...(filters || {}),
            geography_level: selection.geography_level || filters?.geography_level || "",
            geography_value: selection.geography_value || filters?.geography_value || "",
          });
          const nextSignature = stableStringify(relevantFilters);
          if (state.initialized && nextSignature === state.lastFilterSignature) {
            return state.lastRefreshPromise || Promise.resolve();
          }
          state.initialized = true;
          state.lastFilterSignature = nextSignature;
          state.lastRefreshPromise = Promise.resolve(baseController.refresh(filters));
          return state.lastRefreshPromise;
        },
        resize: () => {
          const containerId = state.options.containerId;
          const container = containerId ? document.getElementById(containerId) : null;
          if (!container) return;
          if (container.offsetParent === null) return; // hidden pane; skip costly invalidation
          if (typeof baseController.resize === "function") {
            baseController.resize();
          }
        },
        resetDrill: async () => {
          if (typeof baseController.resetDrill === "function") {
            await baseController.resetDrill();
            state.lastFilterSignature = "";
          }
        },
        getSelection: () => {
          if (typeof baseController.getSelection === "function") {
            return baseController.getSelection();
          }
          return { geography_level: "", geography_value: "" };
        },
        hideGPSLayer: () => {
          if (typeof baseController.hideGPSLayer === "function") {
            baseController.hideGPSLayer();
          }
        },
      };

      registry.set(key, { publicController, state });
      return publicController;
    };

    wrappedFactory.__perfWrapped = true;
    wrappedFactory.__originalFactory = originalFactory;
    window.createHierarchicalDashboardMap = wrappedFactory;

    const safeResizeVisibleMaps = () => {
      registry.forEach(({ publicController }) => {
        try {
          publicController.resize();
        } catch (_error) {
          // no-op
        }
      });
    };

    document.addEventListener("dashboard:refresh-tab", safeResizeVisibleMaps);
    document.addEventListener("shown.bs.tab", safeResizeVisibleMaps);
    window.addEventListener("resize", safeResizeVisibleMaps);
    return true;
  };

  if (!installWrapper()) {
    const poller = window.setInterval(() => {
      if (installWrapper()) {
        window.clearInterval(poller);
      }
    }, 25);
    window.setTimeout(() => window.clearInterval(poller), 5000);
  }

  window.__hierarchicalMapPerfWrapperInstalled = true;
})();

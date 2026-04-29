// Drill-down map dashboard with lazy loading and caching

Vue.use('stacked-bar-chart', 'line-chart', 'loader-spinning')

const LEVEL_CONFIG = {
    0: { label: 'Country', file: 'level_0_country.geojson', accessor: 'area_name' },
    1: { label: 'Province', file: 'level_1_provinces.geojson', accessor: 'area_name' },
    2: { label: 'District', file: 'level_2_districts.geojson', accessor: 'area_name' },
    3: { label: 'Constituency', file: 'level_3_constituencies.geojson', accessor: 'area_name' },
    4: { label: 'Ward', file: 'level_4_wards.geojson', accessor: 'area_name' },
    5: { label: 'EA', file: 'level_5_ea.geojson', accessor: 'area_name' },
}

const MAX_DRILL_LEVEL = 5; // EA is the last drillable level
const DRILL_HIERARCHY = ["Zambia", "Province", "District", "Constituency", "Ward", "EA"];
const isDrillDebugEnabled = () => {
    try {
        if (typeof window === "undefined") return false;
        if (window.__VA_MAP_DRILL_DEBUG__ === true) return true;
        return window.localStorage?.getItem("va_map_drill_debug") === "1";
    } catch (_err) {
        return false;
    }
};

const dashboard = new Vue({
    el: '#dashboardApp',
    delimiters: ["<%", "%>"],
    data() {
        return {
            csrftoken: "",

            // map related values
            map: null,
            geojsonCache: {}, // Cache loaded GeoJSON by level
            layer: null,
            vaCauseTrendEndpoint: (typeof document !== "undefined" && document.getElementById("dashboardApp"))
                ? document.getElementById("dashboardApp").dataset.causeTrendEndpoint
                : "",
            mapEndpoint: (typeof document !== "undefined" && document.getElementById("dashboardApp"))
                ? document.getElementById("dashboardApp").dataset.mapEndpoint
                : "",
            userCanViewPII: (typeof document !== "undefined" && document.getElementById("dashboardApp"))
                ? (document.getElementById("dashboardApp").dataset.userPii === "true")
                : false,
            mapController: null,
            mapSelection: { geography_level: "", geography_value: "" },
            drill: {
                hierarchy: DRILL_HIERARCHY,
                path: [{ level: "Zambia", id: "ZM", name: "Zambia", levelIndex: 0 }],
                levelIndex: 0,
            },

            // default values for all the charts
            COD_grouping: [],
            COD_trend: [],
            place_of_death: [],
            demographics: [],
            vaCauseTrendPayload: { has_coded: false, periods: [], series: [] },
            regionalCauseComparison: { compare_by: "province", groups: [], cod_categories: [], matrix_percent: [] },
            geographic_level_sums: {}, // Store aggregates by level for current drill state
            uncoded_vas: 0,
            update_stats: {
                last_update: 0,
                last_interview: 0,
            },

            // chart sizes
            demographicsHeight: 0,
            demographicsWidth: 0,
            codHeight: 0,
            codWidth: 0,

            // dropdowns options and selected values
            listOfCausesDropdownOptions: [],
            deathDateDropdownOptions: ["Any Time", "Within 1 Month", "Within 3 months", "Within 1 year", "Custom"],
            deathDateSelected: "Any Time",
            startDate: "",
            endDate: "",
            causeSelected: "",
            ageSelected: "",
            sexSelected: "",
            sourceSelected: "all",

            colorScale: [
                "#4575b4", "#74add1", "#abd9e9", "#e0f3f8", "#ffffbf",
                "#fee090", "#fdae61", "#f46d43", "#d73027"
            ],

            demographicsLegendData: [
                {
                    name: "Female",
                    color: "#440154FF",
                },
                {
                    name: "Male",
                    color: "#404788FF",
                }
            ],

            loading: true,
            suppressWarning: false,
            placeOfDeathValue: "count",
            causeOfDeathValue: "count",
            regionalCompareBy: "province",
            featureHitInTick: false,
            vaCauseTrendChart: null,
            vaRegionalCauseChart: null,
            vaSexAgePyramidChart: null,
            vaTabActivationHandler: null,
            vaVisualRefreshTimer: null,
            // Fallback for environments where native dblclick can be unreliable in tab panes.
            featureClickState: { id: null, at: 0 },
            drillInFlight: false,
        }
    },
    computed: {
        currentLevel() {
            // Map layer level: show children of the current drill node.
            return Math.min(this.drill.levelIndex + 1, MAX_DRILL_LEVEL);
        },
        highlightsSummaries() {
            return {
                "Last Data Update": this.update_stats.last_update || "-",
                "Last VA Interview": this.update_stats.last_interview || "-",
                "Coded VAs": d3.sum(this.COD_grouping.map(item => item.count)),
                "Uncoded VAs": this.uncoded_vas,
            }
        },
        currentLevelLabel() {
            return LEVEL_CONFIG[this.currentLevel].label;
        },
        parentLevelLabel() {
            if (this.currentLevel > 0) {
                return LEVEL_CONFIG[this.currentLevel - 1].label;
            }
            return "Country";
        },
        drilldownBreadcrumbs() {
            return this.drill.path;
        },
        geoScale() {
            if (!this.geographic_level_sums || Object.keys(this.geographic_level_sums).length === 0) return;
            const counts = Object.values(this.geographic_level_sums).map(item => item.count || 0);
            if (counts.length === 0) return;

            const geoMax = Math.max(...counts) + 100;
            const geoMin = 1;
            const n = 10;
            const step = (geoMax - geoMin) / (n - 1);
            return Array.from({length: n}, (_, i) => Math.round(geoMin + step * i));
        },
        placeOfDeathData() {
            if (!this.place_of_death) return [];
            if (this.placeOfDeathValue === "count") return this.place_of_death;
            const totalCount = d3.sum(this.place_of_death.map(item => item.count));
            return JSON.parse(JSON.stringify(this.place_of_death)).map(d => {
                d.percentage = Math.round(d.count * 1000 / totalCount) / 10;
                delete d.count;
                return d;
            })
        },
        causeOfDeathData() {
            if (!this.COD_grouping) return [];
            if (this.causeOfDeathValue === "count") {
                // Keep only a single plotted metric to avoid rendering the
                // auxiliary "total" series.
                return this.COD_grouping.map((item) => ({
                    cause: item.cause,
                    count: Number(item.count || 0),
                }));
            }
            const totalCount = this.COD_grouping?.[0]?.total || d3.sum(this.COD_grouping.map(item => item.count));
            return this.COD_grouping.map((item) => {
                const rawPct = totalCount > 0 ? Math.round((Number(item.count || 0) * 1000) / totalCount) / 10 : 0;
                return {
                    cause: item.cause,
                    percentage: Math.min(100, rawPct),
                };
            });
        },
        sexAgePyramidData() {
            const groups = [
                { key: "neonate", label: "Neonate" },
                { key: "child", label: "Child" },
                { key: "adult", label: "Adult" },
            ];
            const normalizedRows = (this.demographics || []).map((row) => {
                const raw = String(row?.age_group || "").toLowerCase();
                let ageKey = "";
                if (raw.includes("neonate")) ageKey = "neonate";
                else if (raw.includes("child")) ageKey = "child";
                else if (raw.includes("adult")) ageKey = "adult";
                return {
                    ageKey,
                    male: Number(row?.male || 0),
                    female: Number(row?.female || 0),
                };
            });

            return groups.map((group) => {
                const match = normalizedRows.find((row) => row.ageKey === group.key) || {};
                return {
                    age_group: group.label,
                    male: Number(match.male || 0),
                    female: Number(match.female || 0),
                };
            });
        },
    },
    async created() {
        await this.getData();
    },
    async mounted() {
        this.resizeCharts();
        window.addEventListener('resize', this.resizeCharts);

        this.vaTabActivationHandler = (event) => {
            if (!this.isVerbalAutopsiesActivationEvent(event)) return;
            this.scheduleVAVisualRefresh();
        };
        const vaTab = document.getElementById("verbal-autopsies-tab");
        if (vaTab && this.vaTabActivationHandler) {
            vaTab.addEventListener("click", this.vaTabActivationHandler);
        }
        if (this.vaTabActivationHandler) {
            document.addEventListener("dashboard:refresh-tab", this.vaTabActivationHandler);
            document.addEventListener("shown.bs.tab", this.vaTabActivationHandler);
            if (window.jQuery) {
                window.jQuery(document).on("shown.bs.tab", this.vaTabActivationHandler);
            }
        }

        if (typeof window.createHierarchicalDashboardMap === "function") {
            this.mapController = window.createHierarchicalDashboardMap({
                containerId: "map",
                legendId: "vaMapLegend",
                breadcrumbId: "vaMapBreadcrumb",
                emptyStateId: "vaMapEmpty",
                endpoint: this.mapEndpoint || this.vaCauseTrendEndpoint,
                buildParams: () => this.buildDashboardQueryParams(false),
                onSelectionChange: async (selection) => {
                    this.mapSelection = {
                        geography_level: selection?.geography_level || "",
                        geography_value: selection?.geography_value || "",
                    };
                    await this.getData();
                    await this.refreshVACauseTrendChart();
                    this.renderRegionalCauseComparisonChart();
                },
                styleVariant: "va",
                // Keep VA dashboard default view aligned to the baseline dashboard zoom.
                fitToDataBounds: false,
                noDataMessage: "No geographic VA data available for the selected filters.",
                userCanViewPII: this.userCanViewPII,
                gpsLayerIdPrefix: "va",
            });
        }

        // Defer chart and map rendering until the VA pane is visible. Chart.js
        // canvases initialised inside a display:none container come up at 0×0
        // and chart.resize() does not reliably recover them, leaving every
        // VA chart blank until the user reloads while the tab is already open.
        // The vaTabActivationHandler registered above will fire
        // scheduleVAVisualRefresh on first activation, which creates the
        // charts on a correctly sized canvas.
        if (this.isVerbalAutopsiesPaneActive()) {
            if (this.mapController) await this.mapController.refresh({});
            await this.runRegionalComparison();
            await this.$nextTick();
            this.resizeCharts();
            this.scheduleVAVisualRefresh(0);
        }
    },
    beforeDestroy() {
        window.removeEventListener('resize', this.resizeCharts);
        const vaTab = document.getElementById("verbal-autopsies-tab");
        if (vaTab && this.vaTabActivationHandler) {
            vaTab.removeEventListener("click", this.vaTabActivationHandler);
        }
        if (this.vaTabActivationHandler) {
            document.removeEventListener("dashboard:refresh-tab", this.vaTabActivationHandler);
            document.removeEventListener("shown.bs.tab", this.vaTabActivationHandler);
            if (window.jQuery) {
                window.jQuery(document).off("shown.bs.tab", this.vaTabActivationHandler);
            }
        }
        if (this.vaVisualRefreshTimer) {
            window.clearTimeout(this.vaVisualRefreshTimer);
            this.vaVisualRefreshTimer = null;
        }
        if (this.vaSexAgePyramidChart) {
            this.vaSexAgePyramidChart.destroy();
            this.vaSexAgePyramidChart = null;
        }
    },
    methods: {
        isVerbalAutopsiesActivationEvent(event) {
            if (!event) return false;
            if (event.type === "dashboard:refresh-tab") {
                const detail = event.detail || {};
                return detail.shell === "outcomes" && detail.tab === "verbal_autopsies";
            }

            const tabLink = event.target?.closest?.("[data-tab]");
            if (tabLink?.dataset?.tab === "verbal_autopsies") return true;

            const targetSelector =
                event.target?.getAttribute?.("data-bs-target") ||
                event.target?.getAttribute?.("data-target");
            return targetSelector === "#verbal-autopsies-pane";
        },
        isVerbalAutopsiesPaneActive() {
            const pane = document.getElementById("verbal-autopsies-pane");
            if (!pane) return false;
            return pane.classList.contains("active") || pane.getAttribute("aria-hidden") === "false";
        },
        scheduleVAVisualRefresh(delayMs = 120) {
            if (this.vaVisualRefreshTimer) {
                window.clearTimeout(this.vaVisualRefreshTimer);
            }
            this.vaVisualRefreshTimer = window.setTimeout(() => {
                this.vaVisualRefreshTimer = null;
                this.refreshVAVisuals().catch((error) => {
                    console.error("Failed to refresh VA visuals after tab activation:", error);
                });
            }, delayMs);
        },
        async refreshVAVisuals() {
            await this.$nextTick();
            this.resizeCharts();
            // Leaflet maps created on a hidden container need invalidateSize()
            // once they become visible, otherwise tiles render in a clipped frame.
            if (this.mapController && typeof this.mapController.resize === "function") {
                this.mapController.resize();
            }
            await this.refreshMapOnly();
            await this.refreshVACauseTrendChart();
            this.renderRegionalCauseComparisonChart();
            this.renderVASexAgePyramidChart();
            await this.$nextTick();

            [this.vaCauseTrendChart, this.vaRegionalCauseChart, this.vaSexAgePyramidChart]
                .filter(Boolean)
                .forEach((chart) => {
                    chart.resize();
                    chart.update();
                });
        },
        normalizeMapKey(value) {
            return String(value || "").trim().replace(/\s+/g, " ").toLowerCase();
        },
        normalizeGeoNameKey(value, levelLabel) {
            const base = this.normalizeMapKey(value);
            if (!base) return "";

            const level = this.normalizeMapKey(levelLabel);
            const suffixes = {
                province: [" province"],
                district: [" district"],
                constituency: [" constituency"],
                ward: [" ward"],
                ea: [" ea", " enumeration area"],
            };
            const levelSuffixes = suffixes[level] || [];
            for (const suffix of levelSuffixes) {
                if (base.endsWith(suffix)) {
                    const stripped = base.slice(0, -suffix.length).trim();
                    return stripped || base;
                }
            }
            return base;
        },
        normalizeEAKey(value) {
            const raw = this.normalizeMapKey(value);
            if (!raw) return "";
            const digits = raw.replace(/\D/g, "");
            if (!digits) return raw;
            const stripped = digits.replace(/^0+/, "");
            return stripped || "0";
        },
        normalizeLevelName(value) {
            return String(value || "").trim().toLowerCase();
        },
        getFeatureCount(feature) {
            const props = feature?.properties || {};
            const levelLabel = LEVEL_CONFIG[this.currentLevel]?.label || "";
            const areaIdRaw = props.area_id;
            const candidates = new Set();
            const addCandidate = (value, normalizeAsName = true) => {
                const raw = this.normalizeMapKey(value);
                if (raw) candidates.add(raw);
                if (normalizeAsName) {
                    const normalized = this.normalizeGeoNameKey(value, levelLabel);
                    if (normalized) candidates.add(normalized);
                }
            };

            addCandidate(props.area_name, true);
            addCandidate(areaIdRaw, false);
            if ((levelLabel || "").toLowerCase() === "ea") {
                addCandidate(this.normalizeEAKey(props.area_name), false);
                addCandidate(this.normalizeEAKey(areaIdRaw), false);
            }

            const areaIdNum = Number(areaIdRaw);
            // Province ids in GeoJSON are often 101..110 while VA data may store 1..10.
            if (
                Number.isFinite(areaIdNum) &&
                this.currentLevel === 1 &&
                areaIdNum >= 101 &&
                areaIdNum <= 110
            ) {
                addCandidate(String(areaIdNum - 100), false);
            }

            for (const key of candidates) {
                if (!key) continue;
                const count = this.geographic_level_sums[key]?.count;
                if (typeof count === "number") return count;
            }
            return 0;
        },
        isFeatureGeometryValid(feature) {
            if (!feature || !feature.geometry) return false;
            const geometry = feature.geometry;
            if (!geometry.type) return false;
            const coords = geometry.coordinates;
            return Array.isArray(coords) && coords.length > 0;
        },
        getFeatureLevelName(properties) {
            if (!properties) return "";
            const directLevel =
                properties.level ??
                properties.level_name ??
                properties.admin_level_name ??
                properties.geo_level;
            if (directLevel) return String(directLevel);

            const numericLevel = properties.admin_level ?? properties.level_index;
            const parsed = Number(numericLevel);
            if (Number.isInteger(parsed) && LEVEL_CONFIG[parsed]) {
                return LEVEL_CONFIG[parsed].label;
            }
            return "";
        },
        getCurrentContextNode() {
            return this.drill.path[this.drill.levelIndex] || null;
        },
        resolveFeatureFromMapEvent(e) {
            if (!this.map || !this.layer || !e || !e.originalEvent) return null;
            let hitFeature = null;
            const point = this.map.mouseEventToLayerPoint(e.originalEvent);

            this.layer.eachLayer((childLayer) => {
                if (hitFeature || !childLayer?.feature) return;

                // Strict geometry hit test only; avoid bounds-based false positives on background clicks.
                const isHit = typeof childLayer._containsPoint === "function"
                    ? childLayer._containsPoint(point)
                    : false;

                if (isHit) hitFeature = childLayer.feature;
            });

            return hitFeature;
        },
        debugDrillLog(decision, feature = null, reason = "") {
            if (!isDrillDebugEnabled()) return;
            const contextNode = this.getCurrentContextNode();
            const featureProps = feature?.properties || {};
            const featureLevel = this.getFeatureLevelName(featureProps);
            const payload = {
                levelIndex: this.drill.levelIndex,
                currentLevel: this.currentLevel,
                currentParentId: contextNode?.id ?? null,
                featureId: featureProps.area_id ?? null,
                featureLevel: featureLevel || null,
                featureParentId: featureProps.parent_id ?? featureProps.parentId ?? featureProps.parentID ?? null,
                decision,
                reason: reason || null,
            };
            console.debug("[MapDrill]", payload);
        },
        isAtZambiaRoot() {
            return (this.drill.path?.length || 0) <= 1;
        },
        async drillUpIfPossible(reason = "invalid_click") {
            if (this.isAtZambiaRoot() || this.drillInFlight) {
                this.debugDrillLog("noop", null, this.isAtZambiaRoot() ? "at_zambia_root" : "drill_in_flight");
                return;
            }
            this.debugDrillLog("up", null, reason);
            console.log(`[Drill Up] Triggered by ${reason}`);
            await this.drillUpOneLevel();
        },
        isClickWithinCurrentContext(feature) {
            if (!feature || !this.isFeatureGeometryValid(feature)) return false;
            const properties = feature.properties;
            if (!properties || !properties.area_id || !properties.area_name) return false;

            const contextNode = this.getCurrentContextNode();
            if (!contextNode || !contextNode.id) return false;

            const expectedLevel = this.drill.hierarchy[this.currentLevel] || LEVEL_CONFIG[this.currentLevel]?.label;
            const featureLevel = this.getFeatureLevelName(properties);
            if (
                featureLevel &&
                expectedLevel &&
                this.normalizeLevelName(featureLevel) !== this.normalizeLevelName(expectedLevel)
            ) {
                return false;
            }

            const parentIdRaw = properties.parent_id ?? properties.parentId ?? properties.parentID;
            const parentId = parentIdRaw == null ? "" : String(parentIdRaw).trim();
            const contextId = String(contextNode.id).trim();

            // Top-level context: Zambia -> Province features are expected to be root features.
            if (
                this.normalizeLevelName(contextNode.level) === this.normalizeLevelName(this.drill.hierarchy[0]) &&
                this.normalizeLevelName(expectedLevel) === this.normalizeLevelName(this.drill.hierarchy[1])
            ) {
                return parentId === "" || parentId === contextId;
            }

            if (parentId) {
                return parentId === contextId;
            }

            // Deterministic fallback linking keys if parent_id is unavailable.
            const fallbackKeysByLevel = {
                Province: ["country_id", "country_code"],
                District: ["province_id", "province_code"],
                Constituency: ["district_id", "district_code"],
                Ward: ["constituency_id", "constituency_code"],
                EA: ["ward_id", "ward_code"],
            };
            const keys = fallbackKeysByLevel[expectedLevel] || [];
            for (const key of keys) {
                const linkedValue = properties[key];
                if (linkedValue != null && String(linkedValue).trim() === contextId) return true;
            }

            return false;
        },
        syncDrillState() {
            if (!Array.isArray(this.drill.path) || this.drill.path.length === 0) {
                this.drill.path = [{ level: this.drill.hierarchy[0], id: "ZM", name: "Zambia", levelIndex: 0 }];
            }
            this.drill.path = (this.drill.path || []).map((node, idx) => ({
                ...node,
                level: this.drill.hierarchy[idx] || node.level,
                levelIndex: idx,
            }));
            this.drill.levelIndex = this.drill.path.length - 1;
        },
        // Function to reproject from EPSG:3857 (Web Mercator) to EPSG:4326 (WGS84)
        reproject3857to4326(geojson) {
            // Check CRS - if it's already WGS84/CRS84, don't reproject
            if (geojson.crs) {
                const crsName = geojson.crs.properties?.name || '';
                // CRS84 and EPSG:4326 are both WGS84 (just different axis order)
                if (crsName.includes('CRS84') || crsName.includes('4326') || crsName.includes('WGS84')) {
                    console.log(`[Reproject] GeoJSON is already in WGS84 (CRS: ${crsName}), skipping reprojection`);
                    delete geojson.crs;
                    return geojson;
                }
                // Only reproject if it's EPSG:3857
                if (!crsName.includes('3857')) {
                    console.log(`[Reproject] Unknown CRS: ${crsName}, checking coordinates to determine if reprojection needed`);
                }
            }

            // Check if coordinates look like they're already in WGS84 (lat/lng range)
            // WGS84: lat -90 to 90, lng -180 to 180
            // EPSG:3857: x -20037508 to 20037508, y -20037508 to 20037508
            let sampleCoords = null;
            if (geojson.features && geojson.features.length > 0) {
                const firstFeature = geojson.features[0];
                if (firstFeature.geometry && firstFeature.geometry.coordinates) {
                    const coords = firstFeature.geometry.coordinates;
                    // Get first coordinate
                    if (Array.isArray(coords[0])) {
                        if (Array.isArray(coords[0][0])) {
                            sampleCoords = coords[0][0][0];
                        } else {
                            sampleCoords = coords[0];
                        }
                    } else {
                        sampleCoords = coords;
                    }
                }
            }

            const looksLikeWGS84 = sampleCoords &&
                Math.abs(sampleCoords[0]) <= 180 &&
                Math.abs(sampleCoords[1]) <= 90;

            if (looksLikeWGS84) {
                console.log(`[Reproject] Coordinates appear to already be in WGS84 (sample: [${sampleCoords[0]}, ${sampleCoords[1]}]), skipping reprojection`);
                delete geojson.crs;
                return geojson;
            }

            console.log(`[Reproject] Reprojecting from EPSG:3857 to EPSG:4326. Sample coords before: [${sampleCoords ? sampleCoords.join(', ') : 'N/A'}]`);

            const R = 6378137; // Earth's radius in meters
            const reprojectCoords = (coords) => {
                if (Array.isArray(coords[0])) {
                    // It's an array of arrays (polygon rings or multipolygon)
                    return coords.map(reprojectCoords);
                } else {
                    // It's a coordinate pair [x, y]
                    const x = coords[0];
                    const y = coords[1];
                    const lng = (x / R) * (180 / Math.PI);
                    const lat = (2 * Math.atan(Math.exp(y / R)) - Math.PI / 2) * (180 / Math.PI);
                    return [lng, lat];
                }
            };

            const reprojectGeometry = (geometry) => {
                if (geometry.type === 'Point') {
                    geometry.coordinates = reprojectCoords(geometry.coordinates);
                } else if (geometry.type === 'LineString') {
                    geometry.coordinates = geometry.coordinates.map(coord => reprojectCoords(coord));
                } else if (geometry.type === 'Polygon') {
                    geometry.coordinates = geometry.coordinates.map(ring => ring.map(coord => reprojectCoords(coord)));
                } else if (geometry.type === 'MultiPolygon') {
                    geometry.coordinates = geometry.coordinates.map(polygon => polygon.map(ring => ring.map(coord => reprojectCoords(coord))));
                }
                return geometry;
            };

            // Reproject each feature's geometry
            geojson.features.forEach(feature => {
                feature.geometry = reprojectGeometry(feature.geometry);
            });

            // Remove or update CRS to indicate WGS84
            delete geojson.crs;

            if (sampleCoords && geojson.features.length > 0) {
                const reprojectedSample = geojson.features[0].geometry.coordinates;
                let reprojectedCoords = null;
                if (Array.isArray(reprojectedSample[0])) {
                    if (Array.isArray(reprojectedSample[0][0])) {
                        reprojectedCoords = reprojectedSample[0][0][0];
                    } else {
                        reprojectedCoords = reprojectedSample[0];
                    }
                } else {
                    reprojectedCoords = reprojectedSample;
                }
                console.log(`[Reproject] Sample coords after: [${reprojectedCoords[0]}, ${reprojectedCoords[1]}]`);
            }

            return geojson;
        },
        async loadGeojsonLevel(level, forceReload = false) {
            // Lazy load GeoJSON for a specific level with caching
            if (this.geojsonCache[level] && !forceReload) {
                console.log(`[Cache] Using cached GeoJSON for level ${level}`);
                return this.geojsonCache[level];
            }

            try {
                const url = `${window.location.protocol}//${window.location.hostname}:${window.location.port}/static/data/geojson/`;
                const filename = LEVEL_CONFIG[level].file;
                // Add cache busting to ensure we get fresh data
                const cacheBuster = `?v=${Date.now()}`;
                const response = await fetch(`${url}${filename}${cacheBuster}`);
                const geojson = await response.json();

                console.log(`[Load] Loaded ${geojson.features.length} features from ${filename}`);
                // Log sample area_ids for debugging
                if (geojson.features.length > 0) {
                    const sampleIds = geojson.features.slice(0, 5).map(f => ({
                        area_id: f.properties.area_id,
                        area_name: f.properties.area_name,
                        parent_id: f.properties.parent_id
                    }));
                    console.log(`[Load] Sample features from ${filename}:`, sampleIds);
                }

                // Reproject from EPSG:3857 to EPSG:4326
                const reprojectedGeojson = this.reproject3857to4326(geojson);

                // Cache it
                this.geojsonCache[level] = reprojectedGeojson;
                return reprojectedGeojson;
            } catch (error) {
                console.error(`Failed to load GeoJSON for level ${level}:`, error);
                return null;
            }
        },
        async getData() {
            this.loading = true;
            try {
                const csrfField = document.querySelector('[name=csrfmiddlewaretoken]');
                this.csrftoken = csrfField ? csrfField.value : "";

                const data_url = `${window.location.origin}/va_analytics/api/dashboard?`;
                const headers = {'Content-Type': 'application/json'};
                if (this.csrftoken) headers['X-CSRFToken'] = this.csrftoken;

                const dataReq = await fetch(data_url + this.buildDashboardQueryParams(true), {
                    method: 'GET',
                    headers,
                    mode: 'same-origin'
                });
                if (!dataReq.ok) {
                    throw new Error(`VA dashboard API request failed with status ${dataReq.status}`);
                }

                const jsonRes = await dataReq.json();
                this.COD_grouping = jsonRes.COD_grouping;
                this.COD_trend = jsonRes.COD_trend;
                this.place_of_death = (jsonRes.place_of_death || []).map(d => {
                    const placeValue = String(d?.place || "");
                    d.place = placeValue.replace(/_/g, " ");
                    return d;
                });
            this.demographics = jsonRes.demographics || [];
            this.vaCauseTrendPayload = jsonRes.va_cause_trend || { has_coded: false, periods: [], series: [] };
            this.regionalCauseComparison = jsonRes.regional_cod_comparison || {
                compare_by: this.regionalCompareBy,
                groups: [],
                cod_categories: [],
                matrix_percent: [],
            };

            // Get aggregates for current drill level
            this.geographic_level_sums = this.getAggregatesForLevel(jsonRes);

            this.uncoded_vas = jsonRes.uncoded_vas;
            this.update_stats = jsonRes.update_stats;
            this.listOfCausesDropdownOptions = jsonRes.all_causes_list;

            // Ensure all age groups are present
            const ageGroups = ["neonate", "child", "adult"];
            for (const ageGroup of ageGroups) {
                let index = this.demographics.map(d => d.age_group).indexOf(ageGroup);
                if (index === -1) {
                    this.demographics.push({
                        female: 0,
                        male: 0,
                    });
                    index = this.demographics.length - 1;
                }
                if (!this.demographics[index].hasOwnProperty("female")) {
                    this.demographics[index].female = 0;
                }
                if (!this.demographics[index].hasOwnProperty("male")) {
                    this.demographics[index].male = 0;
                }
                this.demographics[index].age_group = ageGroup === "neonate" ? "Neonate (< 28 days)" :
                    ageGroup === "child" ? "Child (≤ 12 years)" : "Adult (> 12 years)";
                this.demographics[index].order = ageGroups.indexOf(ageGroup);
            }
            this.demographics.sort((a, b) => a.order > b.order ? 1 : b.order > a.order ? -1 : 0);
            this.demographics.forEach(d => {
                delete d.order;
            });

            // Warning for small sample size
            if (!this.suppressWarning && d3.sum(this.COD_grouping.map(item => item.count)) < 50) {
                if (typeof $("#small-sample-size-warning") !== 'undefined') {
                    $("#small-sample-size-warning").modal().show();
                }
            }

            this.refreshVAEmptyStates();
            } catch (error) {
                console.error("Failed to load VA dashboard data:", error);
                this.COD_grouping = [];
                this.COD_trend = [];
                this.place_of_death = [];
                this.demographics = [];
                this.vaCauseTrendPayload = { has_coded: false, periods: [], series: [] };
                this.regionalCauseComparison = {
                    compare_by: this.regionalCompareBy,
                    groups: [],
                    cod_categories: [],
                    matrix_percent: [],
                };
                this.geographic_level_sums = {};
                this.uncoded_vas = 0;
                this.update_stats = { last_update: "-", last_interview: "-" };
                this.listOfCausesDropdownOptions = [];
                this.refreshVAEmptyStates();
            }
            this.loading = false;
        },
        getAggregatesForLevel(jsonRes) {
            let aggregates = {};

            if (this.currentLevel === 0) {
                aggregates[this.normalizeMapKey("Zambia")] = {
                    count: Number(jsonRes.map_total_vas ?? jsonRes.map_total_coded_vas ?? 0)
                };
                return aggregates;
            }

            const sourceByLevel = {
                1: { rows: jsonRes.map_province_sums || [], key: "province_name", level: "Province" },
                2: { rows: jsonRes.map_district_sums || [], key: "district_name", level: "District" },
                3: { rows: jsonRes.map_constituency_sums || [], key: "constituency_name", level: "Constituency" },
                4: { rows: jsonRes.map_ward_sums || [], key: "ward_name", level: "Ward" },
                5: { rows: jsonRes.map_ea_sums || [], key: "ea_name", level: "EA" },
            };

            const source = sourceByLevel[this.currentLevel];
            if (!source) return aggregates;

            (source.rows || []).forEach((item) => {
                const count = Number(item?.count || 0);
                const rawName = item?.[source.key];
                const rawKey = this.normalizeMapKey(rawName);
                if (!rawKey) return;

                const keys = new Set([rawKey, this.normalizeGeoNameKey(rawName, source.level)]);
                if (source.level === "EA") {
                    keys.add(this.normalizeEAKey(rawName));
                }

                keys.forEach((key) => {
                    if (!key) return;
                    aggregates[key] = { count };
                });
            });
            return aggregates;
        },
        async initializeBaseMap() {
            this.map = L.map('map', {
                zoomControl: false,
                maxBounds: [
                    [-6, 20],
                    [-20, 34],
                ]
            }).setView([-13, 27], 6);

            this.map.attributionControl.setPrefix('');
            this.map.keyboard.disable();

            const tiles = L.tileLayer('https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png', {
                maxZoom: 19, // Allow deep zooming for EA level features
                // OSM public tile servers block requests when the browser omits Referer.
                referrerPolicy: "strict-origin-when-cross-origin",
                attribution: '&copy; OpenStreetMap contributors'
            }).addTo(this.map);

            // Map-level click runs after feature hit-testing and applies the same invalid-click rule.
            this.map.on("click", (e) => {
                this.featureHitInTick = false;
                setTimeout(async () => {
                    if (this.featureHitInTick) return;

                    const target = e?.originalEvent?.target;
                    if (target?.closest?.(".leaflet-control")) return;

                    const hitFeature = this.resolveFeatureFromMapEvent(e);
                    if (!hitFeature) {
                        this.debugDrillLog("up", null, "background_or_unresolved_feature_click");
                        await this.drillUpIfPossible("background_or_unresolved_feature_click");
                        return;
                    }

                    if (!this.isFeatureGeometryValid(hitFeature)) {
                        this.debugDrillLog("up", hitFeature, "map_hit_invalid_feature_geodata");
                        await this.drillUpIfPossible("map_hit_invalid_feature_geodata");
                        return;
                    }

                    if (!this.isClickWithinCurrentContext(hitFeature)) {
                        this.debugDrillLog("up", hitFeature, "map_hit_outside_current_context");
                        await this.drillUpIfPossible("map_hit_outside_current_context");
                        return;
                    }

                    this.debugDrillLog("noop", hitFeature, "map_hit_valid_feature_feature_handler_expected");
                }, 0);
            });

            // Add explicit zoom control at top-left (prevent CSS/layout moving it)
            // Enable zoom controls with higher max zoom for EA level features
            try {
                L.control.zoom({
                    position: 'topleft',
                    zoomInTitle: 'Zoom in',
                    zoomOutTitle: 'Zoom out'
                }).addTo(this.map);
                // Ensure zoom is enabled
                this.map.scrollWheelZoom.enable();
                // Dblclick is used for drill-down on features; avoid map zoom swallowing the event.
                this.map.doubleClickZoom.disable();
            } catch (err) {
                console.warn('Could not add zoom control:', err);
            }

            // Add Leaflet.fullscreen control (provided via CDN in template)
            try {
                if (typeof L !== 'undefined') {
                    if (L.control && typeof L.control.fullscreen === 'function') {
                        L.control.fullscreen({ position: 'topright', title: 'Show Fullscreen', titleCancel: 'Exit Fullscreen' }).addTo(this.map);
                    } else if (L.Control && typeof L.Control.FullScreen === 'function') {
                        // Some builds expose the class as L.Control.FullScreen
                        new L.Control.FullScreen({ position: 'topright', title: 'Show Fullscreen', titleCancel: 'Exit Fullscreen' }).addTo(this.map);
                    } else if (L.Control && typeof L.Control.Fullscreen === 'function') {
                        // Alternate capitalization
                        new L.Control.Fullscreen({ position: 'topright', title: 'Show Fullscreen', titleCancel: 'Exit Fullscreen' }).addTo(this.map);
                    } else {
                        console.warn('Leaflet.fullscreen plugin not available; fullscreen control not added. L.Control keys:', L.Control ? Object.keys(L.Control) : undefined, 'window keys:', Object.keys(window).filter(k => k.toLowerCase().includes('full')));
                    }
                } else {
                    console.warn('Leaflet (L) is not defined; cannot initialize fullscreen control.');
                }
            } catch (err) {
                console.warn('Error while attempting to add fullscreen control:', err);
            }
        },
        async addGeoJSONToMap() {
            // Load and add GeoJSON for current level with drill-down support
            const vm = this;
            if (this.layer) this.map.removeLayer(this.layer);

            const geojson = await this.loadGeojsonLevel(this.currentLevel);
            if (!geojson) return;

            // Filter features based on current drill-down path
            let filteredGeojson = JSON.parse(JSON.stringify(geojson));

            // Log drilldown path for debugging
            console.log(`[Level ${this.currentLevel}] Drilldown path:`, this.drill.path.map(p => ({
                level: p.level,
                name: p.name,
                levelIndex: p.levelIndex,
                area_id: p.id
            })));

            filteredGeojson.features = filteredGeojson.features.filter(feature => {
                // For provincial level with no drill-down, show all provinces (parent_id null)
                if (this.drill.path.length === 1) {
                    return feature.properties.parent_id == null;
                }

                // For all levels, filter by parent_id of the most recent drill-down
                const parent = this.drill.path[this.drill.path.length - 1];
                const matches = feature.properties.parent_id === parent.id;

                // Log first few features for debugging
                if (filteredGeojson.features.indexOf(feature) < 3) {
                    console.log(`[Level ${this.currentLevel}] Feature: area_id=${feature.properties.area_id}, area_name="${feature.properties.area_name}", parent_id=${feature.properties.parent_id}, looking for parent.id=${parent.id}, matches=${matches}`);
                }

                return matches;
            });

            console.log(`[Level ${this.currentLevel}] Filtered features: ${filteredGeojson.features.length} out of ${geojson.features.length} total`);

            // Log which features were found
            if (filteredGeojson.features.length > 0) {
                console.log(`[Level ${this.currentLevel}] Found features:`, filteredGeojson.features.map(f => {
                    // Check geometry type and get sample coordinates
                    let sampleCoords = 'N/A';
                    if (f.geometry && f.geometry.coordinates) {
                        if (f.geometry.type === 'Point') {
                            sampleCoords = f.geometry.coordinates;
                        } else if (f.geometry.type === 'Polygon' || f.geometry.type === 'MultiPolygon') {
                            // Get first coordinate from first ring
                            const coords = f.geometry.coordinates;
                            if (coords && coords[0] && coords[0][0] && coords[0][0][0]) {
                                sampleCoords = coords[0][0][0];
                            }
                        }
                    }
                    return {
                        area_id: f.properties.area_id,
                        area_name: f.properties.area_name,
                        parent_id: f.properties.parent_id,
                        geometry_type: f.geometry ? f.geometry.type : 'missing',
                        sample_coords: sampleCoords
                    };
                }));
            }

            // If no features found, log more details
            if (filteredGeojson.features.length === 0 && this.drill.path.length > 1) {
                const parent = this.drill.path[this.drill.path.length - 1];
                console.warn(`[Level ${this.currentLevel}] No features found! Looking for parent_id=${parent.id} (${parent.name} ${parent.level}), but available parent_ids in data:`,
                    [...new Set(geojson.features.slice(0, 10).map(f => f.properties.parent_id))]);
            }

            this.layer = L.geoJson(filteredGeojson, {
                style: function (feature) {
                    const color = vm.getColor(feature);
                    return {
                        stroke: true,
                        weight: 3,
                        color: 'black',
                        opacity: 1,
                        fillColor: color,
                        fillOpacity: 0.6
                    };
                },
                onEachFeature: function (feature, layer) {
                    const html_tooltip = vm.generateTooltip(feature);
                    layer.bindTooltip(html_tooltip);

                    // Handle double intent via rapid consecutive clicks as a robust fallback.
                    layer.on("click", async (e) => {
                        vm.featureHitInTick = true;
                        if (e && e.originalEvent) {
                            e.originalEvent.preventDefault();
                            e.originalEvent.stopPropagation();
                        }
                        if (typeof L !== "undefined" && L.DomEvent) {
                            L.DomEvent.stop(e);
                        }

                        const clickedFeature = e?.target?.feature;
                        // Case B: invalid feature/geodata -> drill up
                        if (!clickedFeature || !vm.isFeatureGeometryValid(clickedFeature)) {
                            vm.debugDrillLog("up", clickedFeature, "invalid_feature_geodata");
                            await vm.drillUpIfPossible("invalid_feature_geodata");
                            return;
                        }
                        // Case C: feature exists but is outside current context -> drill up
                        if (!vm.isClickWithinCurrentContext(clickedFeature)) {
                            vm.debugDrillLog("up", clickedFeature, "outside_current_context");
                            await vm.drillUpIfPossible("outside_current_context");
                            return;
                        }
                        // Case A: valid drill target -> proceed
                        vm.debugDrillLog("down", clickedFeature, "valid_click");
                        await vm.handleRegionClick(e);
                    });

                    // Double-click: drill down
                    layer.on("dblclick", async (e) => {
                        vm.featureHitInTick = true;
                        const clickedFeature = e?.target?.feature;
                        // Case B: invalid feature/geodata -> drill up
                        if (!clickedFeature || !vm.isFeatureGeometryValid(clickedFeature)) {
                            vm.debugDrillLog("up", clickedFeature, "invalid_feature_geodata_dblclick");
                            await vm.drillUpIfPossible("invalid_feature_geodata_dblclick");
                            return;
                        }
                        // Case C: feature exists but is outside current context -> drill up
                        if (!vm.isClickWithinCurrentContext(clickedFeature)) {
                            vm.debugDrillLog("up", clickedFeature, "outside_current_context_dblclick");
                            await vm.drillUpIfPossible("outside_current_context_dblclick");
                            return;
                        }
                        if (e && e.originalEvent) {
                            e.originalEvent.preventDefault();
                            e.originalEvent.stopPropagation();
                        }
                        if (typeof L !== "undefined" && L.DomEvent) {
                            L.DomEvent.stop(e);
                        }
                        // Case A: valid drill target -> proceed
                        vm.debugDrillLog("down", clickedFeature, "valid_dblclick");
                        await vm.drillIntoRegion(clickedFeature);
                    });
                }
            }).addTo(this.map);

            // Ensure Leaflet knows the container size (important after CSS changes)
            try {
                // force Leaflet to recalculate size before fitting bounds
                this.map.invalidateSize();
            } catch (err) {
                console.warn('invalidateSize failed:', err);
            }

            // Center and zoom to the current layer's full extent.
            // Run inside a short timeout so the browser has applied layout changes.
            setTimeout(() => {
                if (this.layer && filteredGeojson.features.length > 0) {
                    const bounds = this.layer.getBounds();
                    console.log(`[Bounds] Calculated bounds for level ${this.currentLevel}:`, bounds ? {
                        isValid: bounds.isValid(),
                        north: bounds.getNorth(),
                        south: bounds.getSouth(),
                        east: bounds.getEast(),
                        west: bounds.getWest()
                    } : 'null');

                    if (bounds && bounds.isValid()) {
                        // Validate bounds are within Zambia's approximate boundaries
                        // Zambia is roughly: lat -8 to -18, lng 22 to 33
                        const north = bounds.getNorth();
                        const south = bounds.getSouth();
                        const east = bounds.getEast();
                        const west = bounds.getWest();

                        const isWithinZambia = north < -8 && south > -18 && east < 33 && west > 22;

                        if (!isWithinZambia) {
                            console.warn(`[Bounds] Calculated bounds are outside Zambia! North: ${north}, South: ${south}, East: ${east}, West: ${west}`);
                            console.warn(`[Bounds] Resetting to Zambia bounds instead`);
                            this.map.setView([-13, 27], 6);
                            this.map.setMaxBounds([
                                [-6, 20],
                                [-20, 34],
                            ]);
                        } else {
                            // Use minimal padding for tighter zoom to features
                            // For level 5 (EA), use even tighter padding since features are very small
                            const padding = this.currentLevel === 5 ? [3, 3] : [5, 5];
                            this.map.fitBounds(bounds, {
                                padding: padding,
                                maxZoom: 18 // Allow deeper zoom for small features
                            });
                            // second invalidate to ensure tiles render correctly after fit
                            try { this.map.invalidateSize(); } catch (e) {}

                            // For level 5, ensure we can zoom in even more if needed
                            if (this.currentLevel === 5) {
                                // Check if we're at max zoom and features are still small
                                const currentZoom = this.map.getZoom();
                                const boundsSize = bounds.getNorthEast().distanceTo(bounds.getSouthWest());
                                console.log(`[Bounds] Level 5 - Current zoom: ${currentZoom}, bounds size: ${boundsSize.toFixed(2)}m`);

                                // If bounds are very small and we're not at max zoom, try to zoom in more
                                if (boundsSize < 50000 && currentZoom < 18) {
                                    // Manually zoom in a bit more
                                    setTimeout(() => {
                                        const newZoom = Math.min(currentZoom + 2, 18);
                                        this.map.setZoom(newZoom, { animate: true });
                                        console.log(`[Bounds] Level 5 - Zooming in to ${newZoom} for better feature visibility`);
                                    }, 100);
                                }
                            }
                        }
                    } else {
                        console.warn(`[Bounds] Invalid bounds calculated, resetting to Zambia bounds`);
                        this.map.setView([-13, 27], 6);
                        this.map.setMaxBounds([
                            [-6, 20],
                            [-20, 34],
                        ]);
                    }
                } else {
                    // If no features, reset to Zambia bounds to prevent zooming outside
                    console.warn(`[Bounds] No features found for level ${this.currentLevel}, resetting to Zambia bounds`);
                    this.map.setView([-13, 27], 6);
                    this.map.setMaxBounds([
                        [-6, 20],
                        [-20, 34],
                    ]);
                }
            }, 50);
        },
        setupFullscreen() {
            // Removed: custom fullscreen implementation. Using Leaflet.fullscreen control instead.
        },
        async handleRegionClick(e) {
            if (!e || !e.target || !e.target.feature || this.drillInFlight) return;
            const feature = e.target.feature;
            const featureId = feature?.properties?.area_id ?? feature?.properties?.area_name;
            const now = Date.now();
            const isRapidRepeat = this.featureClickState.id === featureId && (now - this.featureClickState.at) <= 380;

            this.featureClickState = { id: featureId, at: now };
            if (!isRapidRepeat) {
                this.debugDrillLog("noop", feature, "single_click_waiting_for_second_click");
                return;
            }

            if (e && e.originalEvent) {
                e.originalEvent.preventDefault();
                e.originalEvent.stopPropagation();
            }
            if (typeof L !== "undefined" && L.DomEvent) {
                L.DomEvent.stop(e);
            }
            this.featureClickState = { id: null, at: 0 };
            await this.drillIntoRegion(feature);
        },
        async drillIntoRegion(feature) {
            // Handle double-click drill-down
            if (this.drillInFlight) return;
            if (this.currentLevel >= MAX_DRILL_LEVEL) {
                this.debugDrillLog("noop", feature, "max_drill_level_reached");
                console.log("Already at deepest drill level (Ward)");
                return;
            }
            if (!this.isClickWithinCurrentContext(feature)) {
                this.debugDrillLog("noop", feature, "outside_current_context_guard");
                console.warn("[Drill Down] Ignored click outside current drill context");
                return;
            }
            this.drillInFlight = true;
            try {
                const regionName = feature.properties.area_name;
                const regionLevel = LEVEL_CONFIG[this.currentLevel].label;
                const regionAreaId = feature.properties.area_id;

                console.log(`[Drill Down] Clicked on ${regionLevel}: "${regionName}" with area_id=${regionAreaId}, parent_id=${feature.properties.parent_id}`);

            // Verify the clicked feature's area_id matches what's in the current GeoJSON cache
            // This helps detect cache mismatches
                const currentGeojson = this.geojsonCache[this.currentLevel];
                if (currentGeojson) {
                    const matchingFeature = currentGeojson.features.find(f =>
                        f.properties.area_name === regionName &&
                        f.properties.area_id === regionAreaId
                    );
                    if (!matchingFeature) {
                        console.warn(`[Drill Down] WARNING: Clicked feature (area_id=${regionAreaId}) not found in cached GeoJSON for level ${this.currentLevel}. Possible cache mismatch!`);
                        // Try to find by name only
                        const byName = currentGeojson.features.find(f => f.properties.area_name === regionName);
                        if (byName) {
                            console.warn(`[Drill Down] Found feature with same name but different area_id: ${byName.properties.area_id}. Using this instead.`);
                            // Use the correct area_id from the cache
                            const correctedAreaId = byName.properties.area_id;
                            this.drill.path.push({
                                level: regionLevel,
                                name: regionName,
                                id: correctedAreaId
                            });
                            this.syncDrillState();
                            console.log(`[Drill Down] Using corrected area_id=${correctedAreaId} instead of ${regionAreaId}`);
                        } else {
                            // Fallback: use the clicked feature's area_id
                            this.drill.path.push({
                                level: regionLevel,
                                name: regionName,
                                id: regionAreaId
                            });
                            this.syncDrillState();
                        }
                    } else {
                        // Feature matches, use it
                        this.drill.path.push({
                            level: regionLevel,
                            name: regionName,
                            id: regionAreaId
                        });
                        this.syncDrillState();
                    }
                } else {
                    // No cache, use clicked feature
                    this.drill.path.push({
                        level: regionLevel,
                        name: regionName,
                        id: regionAreaId
                    });
                    this.syncDrillState();
                }

                console.log(`[Drill Down] Moving from level ${this.currentLevel} to level ${Math.min(this.currentLevel + 1, MAX_DRILL_LEVEL)}`);

                // Load and display map + dependent dashboard components for the new drill state.
                await this.refreshDashboardForDrillChange("drill_down");
            } finally {
                this.drillInFlight = false;
            }
        },
        async drillUpOneLevel() {
            if (this.drill.levelIndex === 0 || this.drillInFlight) return;
            this.drillInFlight = true;
            try {
                // Reset transient interaction state before rendering the next context.
                this.featureClickState = { id: null, at: 0 };
                if (this.map && typeof this.map.closeTooltip === "function") {
                    this.map.closeTooltip();
                }
                if (this.layer) {
                    try {
                        this.layer.eachLayer((childLayer) => {
                            if (typeof childLayer.closeTooltip === "function") childLayer.closeTooltip();
                            if (typeof childLayer.setStyle === "function") {
                                childLayer.setStyle({
                                    stroke: true,
                                    weight: 3,
                                    color: "black",
                                    opacity: 1,
                                    fillOpacity: 0.6,
                                });
                            }
                        });
                    } catch (_err) {
                        // Layer cleanup is best-effort; re-render below is authoritative.
                    }
                    this.map.removeLayer(this.layer);
                    this.layer = null;
                }

                this.drill.path.pop();
                this.syncDrillState();

                const parentId = this.drill.path[this.drill.levelIndex]?.id;
                console.log(`[Drill Up] New context levelIndex=${this.drill.levelIndex}, parent_id=${parentId}`);

                // Force legend/data to recompute for the new level during update.
                this.geographic_level_sums = {};
                await this.refreshDashboardForDrillChange("drill_up");
            } finally {
                this.drillInFlight = false;
            }
        },
        async drillBack() {
            // Backward-compatible alias.
            await this.drillUpOneLevel();
        },
        async drillbackToLevel(breadcrumbIndex) {
            // Jump back to specific breadcrumb level
            // breadcrumbIndex 0 = Country (Zambia), breadcrumbIndex 1 = Province, breadcrumbIndex 2 = District, etc.
            // drill.path structure: [Zambia, Province, District, Constituency, ...]

            console.log(`[Breadcrumb] Clicked on breadcrumb index ${breadcrumbIndex}`);

            if (breadcrumbIndex === 0) {
                // Clicked on Country (Zambia) - show all provinces (level 1)
                this.drill.path = [{ level: this.drill.hierarchy[0], id: "ZM", name: "Zambia", levelIndex: 0 }];
                this.syncDrillState();
                console.log(`[Breadcrumb] Navigating to level 1 (Provinces), cleared drilldown path`);
            } else {
                // Get the breadcrumb that was clicked
                const breadcrumbs = this.drilldownBreadcrumbs;
                const clickedBreadcrumb = breadcrumbs[breadcrumbIndex];

                if (!clickedBreadcrumb) {
                    console.warn(`[Breadcrumb] Invalid breadcrumb index ${breadcrumbIndex}`);
                    return;
                }

                // The clicked breadcrumb's levelIndex tells us what level to show
                // Keep path up to and including the clicked breadcrumb.
                this.drill.path = this.drill.path.slice(0, breadcrumbIndex + 1);
                this.syncDrillState();

                console.log(`[Breadcrumb] Clicked on ${clickedBreadcrumb.name}, navigating to level ${this.currentLevel} (${LEVEL_CONFIG[this.currentLevel].label}), drilldownPath:`, this.drill.path.map(p => p.name));
            }

            await this.refreshDashboardForDrillChange("breadcrumb_drill");
        },
        getColor(feature) {
            // Color based on count data
            const count = this.getFeatureCount(feature);
            if (count > 0) {
                for (let i = 0; i < this.geoScale.length; i++) {
                    if (count >= this.geoScale[i] && count < this.geoScale[i + 1]) {
                        return this.colorScale[i];
                    }
                }
            }
            return '#c0c0c0'; // Gray for no data
        },
        getAgeAndSex() {
            const age = this.ageSelected.toLowerCase();
            const sex = this.sexSelected.toLowerCase();
            return {age, sex};
        },
        getStartAndEndDates() {
            let date;
            switch (this.deathDateSelected) {
                case "Any Time":
                    return {startDate: "", endDate: ""};
                case "Within 1 Month":
                    date = new Date();
                    date.setMonth(date.getMonth() - 1);
                    return {startDate: date.toISOString().slice(0, 10), endDate: ""};
                case "Within 3 months":
                    date = new Date();
                    date.setMonth(date.getMonth() - 3);
                    return {startDate: date.toISOString().slice(0, 10), endDate: ""};
                case "Within 1 year":
                    date = new Date();
                    date.setFullYear(date.getFullYear() - 1);
                    return {startDate: date.toISOString().slice(0, 10), endDate: ""};
                case "Custom":
                    return {startDate: this.startDate, endDate: this.endDate};
            }
        },
        getActiveMapSelection() {
            if (this.mapController && typeof this.mapController.getSelection === "function") {
                return this.mapController.getSelection();
            }
            return this.mapSelection || { geography_level: "", geography_value: "" };
        },
        buildDashboardQueryParams(includeRegion = true) {
            const { age, sex } = this.getAgeAndSex();
            const { startDate, endDate } = this.getStartAndEndDates();
            const params = new URLSearchParams({
                start_date: startDate,
                end_date: endDate,
                cause_of_death: this.causeSelected,
                compare_by: this.regionalCompareBy,
                age,
                sex,
                source: this.sourceSelected || "all",
            });

            if (includeRegion) {
                const selection = this.getActiveMapSelection();
                const rawLevel = String(selection?.geography_level || "").trim().toLowerCase();
                const value = String(selection?.geography_value || "").trim();
                if (rawLevel && value) {
                    const levelLabel = rawLevel === "ea"
                        ? "EA"
                        : `${rawLevel.charAt(0).toUpperCase()}${rawLevel.slice(1)}`;
                    params.set("region_of_interest", `${value} ${levelLabel}`);
                } else {
                    params.set("region_of_interest", "");
                }
            } else {
                params.delete("region_of_interest");
            }

            return params;
        },
        generateTooltip(feature) {
            const areaName = feature.properties.area_name;
            const level = LEVEL_CONFIG[this.currentLevel].label;
            const count = this.getFeatureCount(feature);

            const drillHint = this.currentLevel < MAX_DRILL_LEVEL ?
                '<br><small style="font-style: italic;">(Double-click to drill down)</small>' :
                '<br><small style="font-style: italic;">(Max drill level reached)</small>';

            const html_tooltip = `
                <div class="mapTooltip">
                    <h4>${areaName}</h4>
                    <p><strong>Verbal Autopsies:</strong> ${count}</p>
                    ${drillHint}
                </div>
            `;
            return html_tooltip;
        },
        resizeCharts() {
            const regionalRef = this.$refs.regionalComparison;
            const codRef = this.$refs.cod;

            if (regionalRef) {
                this.demographicsWidth = Math.max(regionalRef.clientWidth - 1, 360);
                this.demographicsHeight = Math.max(regionalRef.clientHeight - 1, 185);
            }

            if (codRef) {
                this.codWidth = Math.max(codRef.clientWidth - 1, 420);
                this.codHeight = Math.max(codRef.clientHeight - 1, 185);
            }
        },
        setVAEmpty(id, isEmpty) {
            const el = document.getElementById(id);
            if (el) el.hidden = !isEmpty;
        },
        refreshVAEmptyStates() {
            const codTotal = (this.COD_grouping || [])
                .reduce((acc, row) => acc + Number(row?.count || 0), 0);
            this.setVAEmpty("vaCodEmpty", codTotal === 0);
            const sexAgeTotal = (this.sexAgePyramidData || [])
                .reduce((acc, row) => acc + Number(row?.male || 0) + Number(row?.female || 0), 0);
            this.setVAEmpty("vaSexAgePyramidEmpty", sexAgeTotal === 0);
        },
        buildVACauseTrendFilters() {
            const { age, sex } = this.getAgeAndSex();
            const { startDate, endDate } = this.getStartAndEndDates();
            const params = new URLSearchParams();
            params.set("tab", "deaths");

            if (sex) params.set("sex", sex.charAt(0).toUpperCase() + sex.slice(1));
            if (age) params.set("age_group", age);
            params.set("coded_only", "1");

            if (this.deathDateSelected === "Any Time") {
                params.set("time_preset", "all_time");
            } else if (this.deathDateSelected === "Within 1 Month") {
                params.set("time_preset", "last_30_days");
            } else if (this.deathDateSelected === "Custom") {
                params.set("time_preset", "custom");
                if (startDate) params.set("start_datetime", startDate);
                if (endDate) params.set("end_datetime", endDate);
            } else {
                // "Within 3 months" and "Within 1 year" don't map to deaths presets; use explicit custom range.
                params.set("time_preset", "custom");
                if (startDate) params.set("start_datetime", startDate);
                if (endDate) params.set("end_datetime", endDate);
            }

            return params;
        },
        async refreshVACauseTrendChart() {
            const canvas = document.getElementById("vaCauseTrendChart");
            if (!canvas || typeof Chart === "undefined") return;

            const payload = this.vaCauseTrendPayload || { has_coded: false, periods: [], series: [] };
            const periods = payload.periods || payload.labels || [];
            const seriesRows = payload.series || (payload.datasets || []).map((d) => ({
                name: d.label,
                values: d.data || [],
            }));

            if (!this.vaCauseTrendChart) {
                this.vaCauseTrendChart = new Chart(canvas.getContext("2d"), {
                    type: "line",
                    data: { labels: [], datasets: [] },
                    options: {
                        responsive: true,
                        maintainAspectRatio: false,
                        plugins: { legend: { display: true, position: "top" }, tooltip: { enabled: true } },
                        scales: {
                            x: { title: { display: true, text: "Month" }, grid: { display: true, color: "rgba(148, 163, 184, 0.35)" } },
                            y: {
                                beginAtZero: true,
                                ticks: { precision: 0 },
                                title: { display: true, text: "Count of deaths" },
                                grid: { display: true, color: "rgba(148, 163, 184, 0.35)" },
                            },
                        },
                    },
                });
            }

            const palette = ["#d73027", "#4575b4", "#4CAF50", "#f46d43", "#8e44ad"];
            this.vaCauseTrendChart.data.labels = periods;
            this.vaCauseTrendChart.data.datasets = seriesRows.map((series, idx) => {
                const name = String(series?.name || "").trim().toLowerCase();
                const color = name === "other" ? "#facc15" : palette[idx % palette.length];
                return {
                    label: series.name,
                    data: series.values || [],
                    borderColor: color,
                    backgroundColor: color,
                    pointRadius: 2,
                    tension: 0.25,
                    fill: false,
                };
            });
            this.vaCauseTrendChart.update();

            const hasCoded = !!payload.has_coded;
            const total = seriesRows
                .flatMap((series) => series.values || [])
                .reduce((acc, value) => acc + Number(value || 0), 0);
            this.setVAEmpty("vaCauseTrendEmpty", !hasCoded || total === 0);
        },
        renderRegionalCauseComparisonChart() {
            const canvas = document.getElementById("vaRegionalCauseComparisonChart");
            if (!canvas || typeof Chart === "undefined") return;

            const payload = this.regionalCauseComparison || {};
            const labels = payload.groups || payload.regions || [];
            const causes = payload.cod_categories || payload.causes || [];
            const matrix = payload.matrix_percent || payload.percentage_matrix || [];

            const palette = ["#4575b4", "#d73027", "#4CAF50", "#f46d43", "#8e44ad", "#607D8B"];
            const datasets = causes.map((cause, idx) => ({
                label: cause,
                data: labels.map((_region, rowIdx) => Number(matrix[rowIdx]?.[idx] || 0)),
                backgroundColor: palette[idx % palette.length],
                borderColor: palette[idx % palette.length],
                borderWidth: 1,
                stack: "regional-cause",
            }));

            if (!this.vaRegionalCauseChart) {
                this.vaRegionalCauseChart = new Chart(canvas.getContext("2d"), {
                    type: "bar",
                    data: { labels: [], datasets: [] },
                    options: {
                        responsive: true,
                        maintainAspectRatio: false,
                        plugins: {
                            legend: {
                                display: true,
                                position: "right",
                                align: "start",
                                labels: {
                                    boxWidth: 12,
                                    boxHeight: 12,
                                    padding: 10,
                                },
                            },
                        },
                        scales: {
                            x: {
                                stacked: true,
                                beginAtZero: true,
                                title: {
                                    display: true,
                                    text: "Compare by",
                                },
                            },
                            y: {
                                stacked: true,
                                beginAtZero: true,
                                max: 100,
                                title: {
                                    display: true,
                                    text: "Percent",
                                },
                                ticks: {
                                    callback: (value) => `${value}%`,
                                },
                            },
                        },
                    },
                });
            }

            this.vaRegionalCauseChart.options.scales.y.max = 100;
            this.vaRegionalCauseChart.options.scales.y.title.text = "Percent";
            this.vaRegionalCauseChart.data.labels = labels;
            this.vaRegionalCauseChart.data.datasets = datasets;
            this.vaRegionalCauseChart.update();

            const total = datasets
                .flatMap((series) => series.data || [])
                .reduce((acc, value) => acc + Number(value || 0), 0);
            this.setVAEmpty("vaRegionalComparisonEmpty", labels.length === 0 || total === 0);
        },
        renderVASexAgePyramidChart() {
            const canvas = document.getElementById("vaSexAgePyramidChart");
            if (!canvas || typeof Chart === "undefined") return;

            const rows = this.sexAgePyramidData || [];
            const labels = rows.map((row) => row.age_group);
            const maleValues = rows.map((row) => -Number(row.male || 0));
            const femaleValues = rows.map((row) => Number(row.female || 0));
            const maxAbs = Math.max(
                1,
                ...maleValues.map((value) => Math.abs(value)),
                ...femaleValues.map((value) => Math.abs(value)),
            );

            if (!this.vaSexAgePyramidChart) {
                this.vaSexAgePyramidChart = new Chart(canvas.getContext("2d"), {
                    type: "bar",
                    data: { labels: [], datasets: [] },
                    options: {
                        indexAxis: "y",
                        responsive: true,
                        maintainAspectRatio: false,
                        plugins: { legend: { display: true, position: "top" } },
                        scales: {
                            x: {
                                stacked: true,
                                min: -1,
                                max: 1,
                                ticks: {
                                    precision: 0,
                                    callback: (value) => Math.abs(value),
                                },
                                title: { display: true, text: "VA Count" },
                            },
                            y: {
                                stacked: true,
                                title: { display: false },
                            },
                        },
                    },
                });
            }

            this.vaSexAgePyramidChart.options.scales.x.min = -maxAbs;
            this.vaSexAgePyramidChart.options.scales.x.max = maxAbs;
            this.vaSexAgePyramidChart.data.labels = labels;
            this.vaSexAgePyramidChart.data.datasets = [
                {
                    label: "Male",
                    data: maleValues,
                    backgroundColor: "#404788FF",
                    borderColor: "#404788FF",
                    borderWidth: 1,
                },
                {
                    label: "Female",
                    data: femaleValues,
                    backgroundColor: "#f46d43",
                    borderColor: "#f46d43",
                    borderWidth: 1,
                },
            ];
            this.vaSexAgePyramidChart.update();
        },
        async refreshMapOnly() {
            if (this.mapController && typeof this.mapController.refresh === "function") {
                await this.mapController.refresh({});
            }
        },
        async updateDataAndMap() {
            await this.getData();
            await this.refreshMapOnly();
            await this.refreshVACauseTrendChart();
            this.renderRegionalCauseComparisonChart();
            this.renderVASexAgePyramidChart();
        },
        async runRegionalComparison() {
            await this.updateDataAndMap();
        },
        async refreshDashboardForDrillChange(reason = "drill_change") {
            await this.updateDataAndMap();
            await this.$nextTick();
            this.resizeCharts();
            if (this.map && typeof this.map.invalidateSize === "function") {
                this.map.invalidateSize();
            }
            console.log(`[Refresh] Completed component refresh for ${reason}`);
        },
        async resetAllDataToActive() {
            this.startDate = "";
            this.endDate = "";
            this.causeSelected = "";
            this.deathDateSelected = "Any Time";
            this.ageSelected = "";
            this.sexSelected = "";
            this.sourceSelected = "all";
            this.mapSelection = { geography_level: "", geography_value: "" };
            if (this.mapController && typeof this.mapController.resetDrill === "function") {
                await this.mapController.resetDrill();
            }

            await this.updateDataAndMap();
        },
        clearGeojsonCache() {
            // Method to manually clear the cache if needed
            this.geojsonCache = {};
            console.log("[Cache] GeoJSON cache cleared manually");
        },
    },
    watch: {}
});

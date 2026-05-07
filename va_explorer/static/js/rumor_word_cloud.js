/* Rumor Cloud — word cloud + force-directed network graph
 * Exposes window.RumorWordCloud = { init, refresh, resize }
 *
 * 3-D Y-axis (globe) rotation: each node is assigned a longitude from its
 * settled X position and orbits on a cylinder:
 *   x(t) = cx + spreadX * sin(lon + t),   y stays fixed.
 * Because only translate() is updated on the node group, text remains legible;
 * each label gets only a small local rotation for organic word-cloud variation.
 */
window.RumorWordCloud = (function () {
  // ── Internal state ─────────────────────────────────────────────────────────
  let _simulation  = null;
  let _zoom        = null;
  let _rafId       = null;
  let _rotationAngle = 0;   // accumulated angle in radians
  let _spinCx      = 0;     // centroid X of settled nodes (cylinder axis)
  let _spinCy      = 0;     // centroid Y
  let _spreadX     = 1;     // half-width of the cylinder = max |dx|
  let _nodeMeta    = [];    // { id, el, lon, sy } — rebuilt after each settle
  let _edgeMeta    = [];    // { el, srcId, tgtId, srcLon, tgtLon }
  let _posMap      = new Map(); // id → { x, y } — current projected positions
  let _currentData = { nodes: [], edges: [], total_events: 0, has_data: false };
  let _adjacency   = new Map();
  let _tooltip     = null;
  let _resizeTimer = null;
  let _colorMap    = {};
  let _rotationPaused = false;

  const getEl = (id) => document.getElementById(id);

  // ── Category → color ───────────────────────────────────────────────────────
  const CATEGORY_COLORS = {
    "Vaccine / Immunization": "#2563eb",
    "Disease Outbreak":       "#dc2626",
    "Death / Mortality":      "#7c3aed",
    "Family / Household":     "#db2777",
    "Water / Sanitation":     "#0284c7",
    "Food / Nutrition":       "#ca8a04",
    "Health Facility":        "#059669",
    "General Concern":        "#64748b",
  };

  function _buildColorMap() {
    const overrides = (typeof window !== "undefined" && window.RDWC_CATEGORY_COLORS) || {};
    _colorMap = Object.assign({}, CATEGORY_COLORS, overrides);
  }

  function _colorFor(category) {
    return _colorMap[category] || "#64748b";
  }

  const TEXT_COLOR_PALETTE = [
    "#b91c1c", "#dc2626", "#991b1b", "#15803d", "#16a34a",
    "#166534", "#854d0e", "#92400e", "#713f12", "#6b21a8",
    "#7e22ce", "#86198f", "#a21caf", "#be123c", "#4d7c0f",
  ];
  const TEXT_ROTATIONS = [0, 0, 0, 0, 0, -8, 8, -5, 5, -3, 3];
  const WORD_FONT_SCALE = 2.6; // Existing 1.3 scale doubled for 100% larger words.
  const WORD_RENDER_FONT_SCALE = 0.65;
  const MAX_WORD_FONT_PX = 122;
  const BUBBLE_RADIUS_SCALE = 1.35;
  const INITIAL_LAYOUT_TICKS = 320;
  const VIEWPORT_VERTICAL_PADDING = 30;
  const COLLISION_PADDING = 22;
  const TEXT_BUBBLE_GAP = 8;
  const OVERLAP_RESOLVE_ITERATIONS = 120;
  // ~0.0875 deg/frame ~= 5.25 deg/sec ~= 69 s/full revolution.
  const ROTATION_RADIANS_PER_FRAME = 0.0875 * Math.PI / 180;

  function _hashString(value) {
    let hash = 0;
    const text = String(value || "");
    for (let i = 0; i < text.length; i++) {
      hash = ((hash << 5) - hash + text.charCodeAt(i)) >>> 0;
    }
    return hash;
  }

  function _textColorFor(d) {
    const key = `${d.category || ""}:${d.id || d.label || ""}`;
    return TEXT_COLOR_PALETTE[_hashString(key) % TEXT_COLOR_PALETTE.length];
  }

  function _textRotationFor(d) {
    const key = `${d.id || d.label || ""}:rotation`;
    return TEXT_ROTATIONS[_hashString(key) % TEXT_ROTATIONS.length];
  }

  // ── Font / bubble sizing ───────────────────────────────────────────────────
  function _fontPx(freq, maxFreq) {
    if (maxFreq <= 1) return 18 * WORD_FONT_SCALE;
    return (18 + Math.pow(freq / maxFreq, 0.55) * 44) * WORD_FONT_SCALE; // 47–161 px
  }

  function _bubbleRadius(freq, maxFreq) {
    return Math.min(_fontPx(freq, maxFreq), MAX_WORD_FONT_PX) * BUBBLE_RADIUS_SCALE + 10;
  }

  // ── Text wrapping ──────────────────────────────────────────────────────────
  const CHAR_W = 0.58;
  const LINE_H = 1.3;

  function _breakWords(words, maxCharsPerLine) {
    const lines = [];
    let cur = "";
    for (const w of words) {
      if (!cur)                                   { cur = w; }
      else if (cur.length + 1 + w.length <= maxCharsPerLine) { cur += " " + w; }
      else                                        { lines.push(cur); cur = w; }
    }
    if (cur) lines.push(cur);
    return lines;
  }

  function _fitTextInBubble(label, r, desiredFontSize) {
    const words   = label.split(/\s+/);
    const usableW = r * 3.2;
    const usableH = r * 2.6;
    for (let fs = Math.min(desiredFontSize, r * 0.92, MAX_WORD_FONT_PX); fs >= 7; fs--) {
      const maxChars = Math.max(1, Math.floor(usableW / (fs * CHAR_W)));
      const lines    = _breakWords(words, maxChars);
      if (lines.length * fs * LINE_H <= usableH) return { lines, fontSize: fs };
    }
    const maxChars = Math.max(1, Math.floor(usableW / (7 * CHAR_W)));
    const raw = label.length > maxChars ? label.slice(0, maxChars - 1) + "…" : label;
    return { lines: [raw], fontSize: 7 };
  }

  // Text-bounding-box collision — circles may overlap, words must not.
  function _collideR(d, maxFreq) {
    const fs = Math.min(_fontPx(d.frequency, maxFreq), MAX_WORD_FONT_PX);
    const labelLen = String(d.label || "").length;
    return fs * (0.55 + Math.sqrt(Math.max(labelLen, 1)) * 0.18) + COLLISION_PADDING;
  }

  function _fitRadius(d, maxFreq) {
    return Math.max(_bubbleRadius(d.frequency, maxFreq), _collideR(d, maxFreq));
  }

  // ── 3-D Y-axis (globe) rotation ────────────────────────────────────────────
  // After the force simulation settles every node gets a longitude:
  //   sin(lon_i) = (x_i - cx) / spreadX
  // Each RAF tick projects the rotating cylinder back to 2D:
  //   x_proj = cx + spreadX * sin(lon_i + rotationAngle)
  //   y_proj = sy_i   (unchanged — globe rotates about Y-axis)
  //   depth  = cos(lon_i + rotationAngle)  →  opacity [0.05, 1]

  function _stopRotation() {
    if (_rafId) { cancelAnimationFrame(_rafId); _rafId = null; }
  }

  function _pauseRotation() {
    _rotationPaused = true;
    _stopRotation();
  }

  function _resumeRotation() {
    _rotationPaused = false;
    _start3DRotation();
  }

  function _init3DState(nodes, nodeSel, edgeSel) {
    _spinCx  = nodes.reduce((s, n) => s + (n.x || 0), 0) / nodes.length;
    _spinCy  = nodes.reduce((s, n) => s + (n.y || 0), 0) / nodes.length;
    _spreadX = Math.max(1, nodes.reduce((m, n) => Math.max(m, Math.abs((n.x || 0) - _spinCx)), 0));

    // Capture DOM element references keyed by node id.
    const nodeElMap = new Map();
    nodeSel.each(function (d) { nodeElMap.set(d.id, this); });

    // Distribute longitudes evenly across the full circle so nodes span front and back.
    const sorted = [...nodes].sort((a, b) => (a.x || 0) - (b.x || 0));
    const lonMap = new Map();
    sorted.forEach((n, i) => {
      lonMap.set(n.id, -Math.PI + (2 * Math.PI * i) / sorted.length);
    });
    _nodeMeta = nodes.map((n) => ({
      id:  n.id,
      el:  nodeElMap.get(n.id),
      lon: lonMap.get(n.id),
      sy:  n.y || _spinCy,
    }));

    const lonById = new Map(_nodeMeta.map((m) => [m.id, m.lon]));

    _edgeMeta = [];
    edgeSel.each(function (e) {
      const srcId = e.source.id !== undefined ? e.source.id : e.source;
      const tgtId = e.target.id !== undefined ? e.target.id : e.target;
      _edgeMeta.push({
        el: this, srcId, tgtId,
        srcLon: lonById.get(srcId) || 0,
        tgtLon: lonById.get(tgtId) || 0,
      });
    });

    // Seed posMap so edges can read positions on the very first tick.
    _posMap.clear();
    _nodeMeta.forEach(({ id, lon, sy }) => {
      _posMap.set(id, { x: _spinCx + _spreadX * Math.sin(lon + _rotationAngle), y: sy });
    });
  }

  function _start3DRotation() {
    _stopRotation();
    if (_rotationPaused || !_nodeMeta.length) return;

    (function tick() {
      _render3DProjection(true);
      _rafId = requestAnimationFrame(tick);
    })();
  }

  function _render3DProjection(advance) {
    if (advance) _rotationAngle += ROTATION_RADIANS_PER_FRAME; // positive -> right-to-left from viewer

    _nodeMeta.forEach(({ id, el, lon, sy }) => {
      if (!el) return;
      const angle   = lon + _rotationAngle;
      const xProj   = _spinCx + _spreadX * Math.sin(angle);
      const depth   = Math.cos(angle);           // 1 = front, -1 = back
      const opacity = Math.max(0.05, (depth + 1) * 0.5);
      el.setAttribute("transform", `translate(${xProj},${sy})`);
      el.setAttribute("opacity", String(opacity));
      _posMap.set(id, { x: xProj, y: sy });
    });

    _edgeMeta.forEach(({ el, srcId, tgtId, srcLon, tgtLon }) => {
      const src = _posMap.get(srcId);
      const tgt = _posMap.get(tgtId);
      if (!src || !tgt) return;
      el.setAttribute("x1", src.x);
      el.setAttribute("y1", src.y);
      el.setAttribute("x2", tgt.x);
      el.setAttribute("y2", tgt.y);
      const avgDepth = (Math.cos(srcLon + _rotationAngle) + Math.cos(tgtLon + _rotationAngle)) * 0.5;
      el.setAttribute("stroke-opacity", String(Math.max(0.18, (avgDepth + 1) * 0.42)));
    });
  }

  // ── Auto-fit to viewport ───────────────────────────────────────────────────
  function _nodeVisualHalfWidth(n, maxFreq) {
    return Number.isFinite(n._renderHalfWidth) ? n._renderHalfWidth : _fitRadius(n, maxFreq);
  }

  function _nodeVisualHalfHeight(n, maxFreq) {
    return Number.isFinite(n._renderHalfHeight) ? n._renderHalfHeight : _fitRadius(n, maxFreq);
  }

  function _nodeCollisionHalfWidth(n, maxFreq) {
    return _nodeVisualHalfWidth(n, maxFreq) + TEXT_BUBBLE_GAP;
  }

  function _nodeCollisionHalfHeight(n, maxFreq) {
    return _nodeVisualHalfHeight(n, maxFreq) + TEXT_BUBBLE_GAP;
  }

  function _nodeCollisionRadius(n, maxFreq) {
    return Math.max(
      _collideR(n, maxFreq),
      Math.hypot(_nodeCollisionHalfWidth(n, maxFreq), _nodeCollisionHalfHeight(n, maxFreq)),
    );
  }

  function _nodeBounds(nodes, maxFreq) {
    const bounds = { x0: Infinity, x1: -Infinity, y0: Infinity, y1: -Infinity };
    nodes.forEach((n) => {
      const xHalf = _nodeVisualHalfWidth(n, maxFreq);
      const yHalf = _nodeVisualHalfHeight(n, maxFreq);
      bounds.x0 = Math.min(bounds.x0, (n.x || 0) - xHalf);
      bounds.x1 = Math.max(bounds.x1, (n.x || 0) + xHalf);
      bounds.y0 = Math.min(bounds.y0, (n.y || 0) - yHalf);
      bounds.y1 = Math.max(bounds.y1, (n.y || 0) + yHalf);
    });
    return bounds;
  }

  function _resolveTextBubbleOverlaps(nodes, maxFreq) {
    if (nodes.length < 2) return;

    for (let iter = 0; iter < OVERLAP_RESOLVE_ITERATIONS; iter++) {
      let moved = false;

      for (let i = 0; i < nodes.length; i++) {
        const a = nodes[i];
        for (let j = i + 1; j < nodes.length; j++) {
          const b = nodes[j];
          const dx = (b.x || 0) - (a.x || 0);
          const dy = (b.y || 0) - (a.y || 0);
          const overlapX = _nodeCollisionHalfWidth(a, maxFreq) + _nodeCollisionHalfWidth(b, maxFreq) - Math.abs(dx);
          const overlapY = _nodeCollisionHalfHeight(a, maxFreq) + _nodeCollisionHalfHeight(b, maxFreq) - Math.abs(dy);

          if (overlapX <= 0 || overlapY <= 0) continue;

          if (overlapX < overlapY) {
            const direction = dx === 0 ? (i % 2 === 0 ? 1 : -1) : Math.sign(dx);
            const shift = overlapX / 2 + 0.5;
            a.x = (a.x || 0) - direction * shift;
            b.x = (b.x || 0) + direction * shift;
          } else {
            const direction = dy === 0 ? (i % 2 === 0 ? 1 : -1) : Math.sign(dy);
            const shift = overlapY / 2 + 0.5;
            a.y = (a.y || 0) - direction * shift;
            b.y = (b.y || 0) + direction * shift;
          }

          moved = true;
        }
      }

      if (!moved) break;
    }
  }

  function _stretchYToViewport(nodes, maxFreq, h) {
    if (!nodes.length) return;
    const { y0, y1 } = _nodeBounds(nodes, maxFreq);
    const sourceH = y1 - y0;
    const targetH = Math.max(1, h - VIEWPORT_VERTICAL_PADDING * 2);
    if (sourceH <= 0) return;

    const targetY0 = VIEWPORT_VERTICAL_PADDING;
    const targetY1 = h - VIEWPORT_VERTICAL_PADDING;
    nodes.forEach((n) => {
      const yHalf = _nodeVisualHalfHeight(n, maxFreq);
      const sourceRange = sourceH - yHalf * 2;
      const targetRange = targetH - yHalf * 2;
      if (targetRange <= 0) {
        n.y = (targetY0 + targetY1) / 2;
        return;
      }
      const t = sourceRange > 0 ? (((n.y || 0) - yHalf - y0) / sourceRange) : 0.5;
      n.y = targetY0 + yHalf + Math.max(0, Math.min(1, t)) * targetRange;
    });
  }

  function _fitToViewport(nodes, maxFreq, w, h, animate) {
    const svg = getEl("rdwcSvg");
    if (!svg || !nodes.length || !_zoom) return;
    const maxR = nodes.reduce((m, n) => Math.max(m, _fitRadius(n, maxFreq)), 0);
    // x bounds cover the full rotation envelope (cylinder diameter + largest bubble)
    const x0 = _spinCx - _spreadX - maxR;
    const x1 = _spinCx + _spreadX + maxR;
    const { y0, y1 } = _nodeBounds(nodes, maxFreq);
    const nw = x1 - x0, nh = y1 - y0;
    if (nw <= 0 || nh <= 0) return;
    const scaleY = Math.max(0.1, (h - VIEWPORT_VERTICAL_PADDING * 2) / nh);
    const scale = scaleY;
    const tx = w / 2 - (x0 + nw / 2) * scale;
    const ty = VIEWPORT_VERTICAL_PADDING - y0 * scale;
    const transform = d3.zoomIdentity.translate(tx, ty).scale(scale);
    const svgSel = d3.select(svg).interrupt();
    if (animate) {
      svgSel.transition().duration(500).call(_zoom.transform, transform);
    } else {
      svgSel.call(_zoom.transform, transform);
    }
  }

  // ── Tooltip ────────────────────────────────────────────────────────────────
  function _initTooltip() {
    if (_tooltip) return;
    _tooltip = document.createElement("div");
    _tooltip.className = "rdwc-tooltip";
    _tooltip.style.display = "none";
    document.body.appendChild(_tooltip);
  }

  function _showTooltip(event, d) {
    if (!_tooltip) return;
    const variants = (d.variants || []).slice(0, 5);
    _tooltip.innerHTML = [
      `<div class="rdwc-tooltip-phrase">${_esc(d.label)}</div>`,
      `<div><strong>${d.frequency}</strong> events &mdash; <strong>${d.pct}%</strong></div>`,
      `<div style="color:#94a3b8;font-size:0.7rem">${_esc(d.category)}</div>`,
      variants.length
        ? `<div class="rdwc-tooltip-variants">Also: ${variants.map(_esc).join(", ")}</div>`
        : "",
    ].join("");
    _tooltip.style.display = "block";
    _positionTooltip(event);
  }

  function _positionTooltip(event) {
    if (!_tooltip) return;
    const x  = event.clientX + 12;
    const y  = event.clientY - 8;
    const tw = _tooltip.offsetWidth  || 200;
    const th = _tooltip.offsetHeight || 80;
    _tooltip.style.left = (x + tw > window.innerWidth  ? x - tw - 24 : x) + "px";
    _tooltip.style.top  = (y + th > window.innerHeight ? y - th - 4  : y) + "px";
  }

  function _hideTooltip() {
    if (_tooltip) _tooltip.style.display = "none";
  }

  function _esc(s) {
    return String(s || "").replace(/[&<>"']/g, (c) =>
      ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c])
    );
  }

  // ── Adjacency set ──────────────────────────────────────────────────────────
  function _buildAdjacency(edges) {
    const adj = new Map();
    for (const e of edges) {
      const s = e.source.id !== undefined ? e.source.id : e.source;
      const t = e.target.id !== undefined ? e.target.id : e.target;
      if (!adj.has(s)) adj.set(s, new Set());
      if (!adj.has(t)) adj.set(t, new Set());
      adj.get(s).add(t);
      adj.get(t).add(s);
    }
    return adj;
  }

  // ── D3 force simulation ────────────────────────────────────────────────────
  function _startSimulation(nodes, edges, w, h) {
    if (_simulation) _simulation.stop();

    const maxFreq   = nodes.reduce((m, n) => Math.max(m, n.frequency), 1);
    const cx        = w / 2;
    const cy_radial = h / 2;
    const maxRadius = Math.min(w, h) * 0.34;

    const targetR = {};
    nodes.forEach((n) => {
      targetR[n.id] = maxRadius * (1 - Math.sqrt(n.frequency / maxFreq));
    });

    _simulation = d3.forceSimulation(nodes)
      .force("link", d3.forceLink(edges).id((d) => d.id)
        .distance((e) => 22 - e.weight * 5)
        .strength((e) => 0.12 + e.weight * 0.16))
      .force("charge", d3.forceManyBody()
        .strength((d) => -24 - Math.min(_fontPx(d.frequency, maxFreq), MAX_WORD_FONT_PX) * 0.34))
      .force("radial", d3.forceRadial((d) => targetR[d.id], cx, cy_radial).strength(0.78))
      .force("center", d3.forceCenter(cx, h / 2).strength(0.28))
      .force("topBias", d3.forceY(h * 0.08).strength((d) =>
        d.frequency / maxFreq < 0.35 ? 0.015 : 0))
      .force("collide", d3.forceCollide()
        .radius((d) => _nodeCollisionRadius(d, maxFreq))
        .strength(1)
        .iterations(8))
      .alphaDecay(0.018)
      .stop();

    return _simulation;
  }

  // ── SVG rendering ──────────────────────────────────────────────────────────
  function _renderGraph(data) {
    const svg = getEl("rdwcSvg");
    if (!svg) return;

    _stopRotation();
    _rotationPaused = false;

    const wrap = getEl("rdwcCanvasWrap");
    const w = wrap ? wrap.clientWidth  || 520 : 520;
    const h = wrap ? Math.max(wrap.clientHeight || 420, 420) : 420;

    svg.setAttribute("viewBox", `0 0 ${w} ${h}`);
    svg.setAttribute("width",   w);
    svg.setAttribute("height",  h);

    const nodes   = data.nodes.map((n) => ({ ...n }));
    const edges   = data.edges.map((e) => ({ ...e }));
    const maxFreq = nodes.reduce((m, n) => Math.max(m, n.frequency), 1);

    const sel = d3.select(svg);
    sel.selectAll("*").remove();

    // Nodes animate individually so no inner spin group is needed.
    const zoomLayer = sel.append("g").attr("class", "rdwc-zoom-layer");

    _zoom = d3.zoom()
      .scaleExtent([0.1, 8])
      .on("zoom", (event) => zoomLayer.attr("transform", event.transform));

    sel.call(_zoom);

    // ── Edges ─────────────────────────────────────────────────────────────────
    const edgeSel = zoomLayer.append("g").attr("class", "rdwc-edges")
      .selectAll("line")
      .data(edges)
      .join("line")
        .attr("class", "rdwc-edge")
        .attr("stroke", "#64748b")
        .attr("stroke-linecap", "round")
        .attr("stroke-opacity", 0.85)
        .attr("stroke-width", (e) => Math.max(3, e.weight * 6));

    // ── Node groups ────────────────────────────────────────────────────────────
    const nodeSel = zoomLayer.append("g").attr("class", "rdwc-nodes")
      .selectAll("g")
      .data(nodes)
      .join("g")
        .attr("class", "rdwc-node")
        .call(d3.drag()
          .on("start", (event, d) => {
            _stopRotation();
            if (!event.active) _simulation.alphaTarget(0.3).restart();
            d.fx = d.x; d.fy = d.y;
          })
          .on("drag",  (event, d) => { d.fx = event.x; d.fy = event.y; })
          .on("end",   (event, d) => {
            if (!event.active) _simulation.alphaTarget(0);
            d.fx = null; d.fy = null;
            // 3D rotation restarts automatically when sim.on("end") fires.
          })
        )
        .on("mouseover", function (event, d) {
          _pauseRotation();
          const id        = d.id;
          const neighbors = _adjacency.get(id) || new Set();
          d3.selectAll(".rdwc-node").classed("is-dimmed",
            (n) => n.id !== id && !neighbors.has(n.id));
          d3.selectAll(".rdwc-edge").classed("is-dimmed", (e) => {
            const s = e.source.id !== undefined ? e.source.id : e.source;
            const t = e.target.id !== undefined ? e.target.id : e.target;
            return s !== id && t !== id;
          });
          _showTooltip(event, d);
        })
        .on("mousemove", _positionTooltip)
        .on("mouseout", () => {
          d3.selectAll(".rdwc-node").classed("is-dimmed", false);
          d3.selectAll(".rdwc-edge").classed("is-dimmed", false);
          _hideTooltip();
          _resumeRotation();
        });

    // Circle — invisible border, light fill tint.
    nodeSel.append("circle")
      .attr("r",            (d) => _bubbleRadius(d.frequency, maxFreq))
      .attr("fill",         (d) => _colorFor(d.category))
      .attr("fill-opacity", 0.1)
      .attr("stroke-width", 0);

    // Wrapped text inside bubble.
    nodeSel.each(function (d) {
      const r                  = _bubbleRadius(d.frequency, maxFreq);
      const { lines, fontSize: fittedFontSize } = _fitTextInBubble(d.label, r, _fontPx(d.frequency, maxFreq));
      const fontSize            = fittedFontSize * WORD_RENDER_FONT_SCALE;
      const lineH               = fontSize * LINE_H;
      const totalH              = lines.length * lineH;
      const startDy             = -(totalH / 2) + lineH * 0.5;
      const rotationDegrees     = _textRotationFor(d);
      const rotationRadians     = rotationDegrees * Math.PI / 180;
      const widestLine          = lines.reduce((m, line) => Math.max(m, line.length), 0);
      const halfW               = widestLine * fontSize * CHAR_W * 0.5;
      const halfH               = totalH * 0.5;
      const absSin              = Math.sin(Math.abs(rotationRadians));
      const absCos              = Math.cos(Math.abs(rotationRadians));
      d._renderHalfWidth        = halfW * absCos + halfH * absSin;
      d._renderHalfHeight       = halfH * absCos + halfW * absSin;

      const textEl = d3.select(this).append("text")
        .attr("text-anchor",   "middle")
        .attr("font-family",   "Inter, Roboto, 'Open Sans', system-ui, -apple-system, sans-serif")
        .attr("font-weight",   "700")
        .attr("fill",          _textColorFor(d))
        .attr("pointer-events","none")
        .attr("user-select",   "none")
        .attr("transform",     `rotate(${rotationDegrees})`);

      lines.forEach((line, i) => {
        textEl.append("tspan")
          .attr("x",        0)
          .attr("dy",       i === 0 ? startDy + "px" : lineH + "px")
          .attr("font-size",fontSize + "px")
          .text(line);
      });

      const textNode = textEl.node();
      if (textNode && typeof textNode.getBBox === "function") {
        try {
          const box = textNode.getBBox();
          if (box.width > 0 && box.height > 0) {
            const sin = Math.sin(rotationRadians);
            const cos = Math.cos(rotationRadians);
            const measuredBounds = [
              [box.x, box.y],
              [box.x + box.width, box.y],
              [box.x, box.y + box.height],
              [box.x + box.width, box.y + box.height],
            ].reduce((bounds, point) => {
              const x = point[0] * cos - point[1] * sin;
              const y = point[0] * sin + point[1] * cos;
              return {
                x: Math.max(bounds.x, Math.abs(x)),
                y: Math.max(bounds.y, Math.abs(y)),
              };
            }, { x: 0, y: 0 });
            d._renderHalfWidth = measuredBounds.x;
            d._renderHalfHeight = measuredBounds.y;
          }
        } catch (err) {
          // Keep the deterministic estimate when the browser cannot measure SVG text yet.
        }
      }
    });

    _adjacency = _buildAdjacency(edges);
    const sim  = _startSimulation(nodes, edges, w, h);

    const applySimulationPositions = () => {
      edgeSel
        .attr("x1", (e) => e.source.x).attr("y1", (e) => e.source.y)
        .attr("x2", (e) => e.target.x).attr("y2", (e) => e.target.y);
      nodeSel
        .attr("transform", (d) => `translate(${d.x},${d.y})`)
        .attr("opacity", 1);
    };

    sim.on("end", () => {
      _resolveTextBubbleOverlaps(nodes, maxFreq);
      applySimulationPositions();
      _init3DState(nodes, nodeSel, edgeSel);
      _render3DProjection(false);
      _start3DRotation();
    });

    sim.on("tick", applySimulationPositions);

    for (let i = 0; i < INITIAL_LAYOUT_TICKS; i++) sim.tick();
    _stretchYToViewport(nodes, maxFreq, h);
    _resolveTextBubbleOverlaps(nodes, maxFreq);
    applySimulationPositions();
    _init3DState(nodes, nodeSel, edgeSel);
    _render3DProjection(false);
    _fitToViewport(nodes, maxFreq, w, h, false);
    _start3DRotation();
  }

  // ── Empty / visible state ──────────────────────────────────────────────────
  function _applyVisibility(hasData) {
    const emptyEl    = getEl("rdwcEmptyState");
    const canvasWrap = getEl("rdwcCanvasWrap");
    if (emptyEl)    emptyEl.hidden    = hasData;
    if (canvasWrap) canvasWrap.hidden = !hasData;
  }

  // ── Public API ─────────────────────────────────────────────────────────────
  function init() {
    if (!getEl("rdwcSvg")) return;
    _buildColorMap();
    _initTooltip();
    const wrap = getEl("rdwcCanvasWrap");
    if (wrap && typeof ResizeObserver !== "undefined") {
      new ResizeObserver(() => {
        clearTimeout(_resizeTimer);
        _resizeTimer = setTimeout(() => {
          if (_currentData.has_data) _renderGraph(_currentData);
        }, 150);
      }).observe(wrap);
    }
  }

  async function refresh(url, filters, fetchFn) {
    let data;
    try {
      data = await fetchFn(url, filters);
    } catch (err) {
      console.error("RumorWordCloud fetch error:", err);
      return;
    }
    _currentData = data || { nodes: [], edges: [], total_events: 0, has_data: false };
    _applyVisibility(_currentData.has_data);
    if (_currentData.has_data) _renderGraph(_currentData);
  }

  function resize() {
    if (_currentData.has_data) _renderGraph(_currentData);
  }

  return { init, refresh, resize };
})();

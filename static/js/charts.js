/* Neverlank charts - small dependency-free SVG charts for the Analytics page.
 *
 * Three forms: columns (grouped, optionally +/-), line (multi-series, gaps for
 * missing months) and hbars (ranked horizontal bars). Every chart has: a
 * legend when there are 2+ series, a hover/focus tooltip listing every series
 * at that position, a "Table" view of the same numbers, and a hatch-texture
 * fallback for print / forced-colors. Labels from data are inserted with
 * textContent only.
 *
 * Usage: NLCharts.render(containerEl, spec)   (see analytics/dashboard.html)
 */
(function (global) {
  "use strict";
  var NS = "http://www.w3.org/2000/svg";
  var INK = "#0b0b0b", INK2 = "#52514e", MUTED = "#6f6d67", GRID = "#e1e0d9", AXIS = "#c3c2b7", SURFACE = "#ffffff";
  var NEG = "#e34948";

  function svgEl(tag, attrs, parent) {
    var n = document.createElementNS(NS, tag);
    for (var k in attrs) if (attrs[k] !== undefined && attrs[k] !== null) n.setAttribute(k, attrs[k]);
    if (parent) parent.appendChild(n);
    return n;
  }
  function htmlEl(tag, cls, text, parent) {
    var n = document.createElement(tag);
    if (cls) n.className = cls;
    if (text !== undefined) n.textContent = text;
    if (parent) parent.appendChild(n);
    return n;
  }

  // ---- number formatting
  function compact(v) {
    var a = Math.abs(v), s = v < 0 ? "-" : "";
    if (a >= 1e6) return s + (a / 1e6).toFixed(a >= 1e7 ? 0 : 1).replace(/\.0$/, "") + "M";
    if (a >= 1e3) return s + (a / 1e3).toFixed(a >= 1e4 ? 0 : 1).replace(/\.0$/, "") + "K";
    return s + a.toFixed(0);
  }
  function full(spec, v) {
    if (v === null || v === undefined) return "no data";
    if (spec.unit === "days") return v.toFixed(1) + " days";
    var s = Math.abs(v).toLocaleString(undefined, { maximumFractionDigits: 0 });
    return (v < 0 ? "-" : "") + spec.currency + " " + s;
  }
  function axisFmt(spec, v) { return spec.unit === "days" ? String(Math.round(v)) : compact(v); }

  function niceStep(range, target) {
    var raw = range / target, p = Math.pow(10, Math.floor(Math.log10(raw))), f = raw / p;
    var n = f <= 1 ? 1 : f <= 2 ? 2 : f <= 2.5 ? 2.5 : f <= 5 ? 5 : 10;
    return n * p;
  }
  function scaleFor(values, includeZero) {
    var vals = values.filter(function (v) { return v !== null && v !== undefined; });
    var lo = Math.min.apply(null, vals.concat(includeZero ? [0] : [])), hi = Math.max.apply(null, vals.concat(includeZero ? [0] : []));
    if (!isFinite(lo) || !isFinite(hi)) { lo = 0; hi = 1; }
    if (hi === lo) hi = lo + 1;
    var step = niceStep(hi - lo, 4);
    var min = Math.floor(lo / step) * step, max = Math.ceil(hi / step) * step;
    var ticks = [];
    for (var t = min; t <= max + step / 2; t += step) ticks.push(Math.round(t / step) * step);
    return { min: min, max: max, ticks: ticks };
  }

  // ---- shared tooltip
  var tip;
  function tooltip() {
    if (!tip) {
      tip = htmlEl("div", "viz-tip", undefined, document.body);
      tip.setAttribute("role", "status");
      tip.style.display = "none";
    }
    return tip;
  }
  function showTip(spec, title, rows, clientX, clientY) {
    var t = tooltip();
    t.textContent = "";
    htmlEl("div", "viz-tip-title", title, t);
    rows.forEach(function (r) {
      var row = htmlEl("div", "viz-tip-row", undefined, t);
      var key = htmlEl("span", "viz-tip-key", undefined, row);
      key.style.background = r.color;
      htmlEl("strong", "viz-tip-val", r.value, row);
      htmlEl("span", "viz-tip-name", r.name, row);
    });
    t.style.display = "block";
    var w = t.offsetWidth, h = t.offsetHeight;
    var x = clientX + 14, y = clientY - h - 10;
    if (x + w > window.innerWidth - 8) x = clientX - w - 14;
    if (y < 8) y = clientY + 16;
    t.style.left = Math.max(8, x) + "px";
    t.style.top = y + "px";
  }
  function hideTip() { if (tip) tip.style.display = "none"; }

  // ---- hatch textures (shown only for print / forced-colors, see CSS)
  function addPatterns(svg) {
    var defs = svgEl("defs", {}, svg);
    [["viz-hatch-45", "M-1,1 l2,-2 M0,8 l8,-8 M7,9 l2,-2"], ["viz-hatch-135", "M-1,7 l2,2 M0,0 l8,8 M7,-1 l2,2"]].forEach(function (p) {
      var pat = svgEl("pattern", { id: p[0], width: 8, height: 8, patternUnits: "userSpaceOnUse" }, defs);
      svgEl("path", { d: p[1], stroke: "#000", "stroke-opacity": 0.55, "stroke-width": 1.4, fill: "none" }, pat);
    });
  }

  // rounded data-end, square baseline
  function barPath(x, base, w, val, r) {
    var top = val, up = top < base; // svg y grows downward
    var h = Math.abs(base - top);
    if (h < 0.5) return "";
    r = Math.min(r, h, w / 2);
    if (up) {
      return "M" + x + "," + base + "V" + (top + r) + "Q" + x + "," + top + " " + (x + r) + "," + top + "H" + (x + w - r) +
        "Q" + (x + w) + "," + top + " " + (x + w) + "," + (top + r) + "V" + base + "Z";
    }
    return "M" + x + "," + base + "V" + (top - r) + "Q" + x + "," + top + " " + (x + r) + "," + top + "H" + (x + w - r) +
      "Q" + (x + w) + "," + top + " " + (x + w) + "," + (top - r) + "V" + base + "Z";
  }

  // ---- legend
  function legend(host, spec) {
    if (!spec.series || spec.series.length < 2) return;
    var lg = htmlEl("div", "viz-legend", undefined, host);
    spec.series.forEach(function (s) {
      var item = htmlEl("span", "viz-legend-item", undefined, lg);
      var key = htmlEl("span", spec.type === "line" ? "viz-key-line" : "viz-key-box", undefined, item);
      key.style.background = s.color;
      htmlEl("span", "", s.name, item);
    });
  }

  // ---- table view
  function tableView(spec) {
    var wrap = htmlEl("div", "viz-table-wrap");
    var tbl = htmlEl("table", "viz-table", undefined, wrap);
    var head = htmlEl("tr", "", undefined, htmlEl("thead", "", undefined, tbl));
    var body = htmlEl("tbody", "", undefined, tbl);
    if (spec.type === "hbars") {
      htmlEl("th", "", spec.itemHeader || "Item", head);
      htmlEl("th", "num", spec.valueHeader || "Amount", head);
      htmlEl("th", "num", "Share", head);
      spec.items.forEach(function (it) {
        var tr = htmlEl("tr", "", undefined, body);
        htmlEl("td", "", it.label, tr);
        htmlEl("td", "num", full(spec, it.value), tr);
        htmlEl("td", "num", it.share === undefined ? "" : it.share.toFixed(1) + "%", tr);
      });
      return wrap;
    }
    htmlEl("th", "", "Month", head);
    spec.series.forEach(function (s) { htmlEl("th", "num", s.name, head); });
    spec.labels.forEach(function (lab, i) {
      var tr = htmlEl("tr", "", undefined, body);
      htmlEl("td", "", lab, tr);
      spec.series.forEach(function (s) { htmlEl("td", "num", full(spec, s.values[i]), tr); });
    });
    return wrap;
  }

  // ---- columns & lines share axes
  function cartesian(plot, spec, width) {
    var H = spec.height || 260;
    var m = { l: 50, r: 14, t: 10, b: 28 };
    var W = Math.max(280, width);
    var pw = W - m.l - m.r, ph = H - m.t - m.b;
    var svg = svgEl("svg", { viewBox: "0 0 " + W + " " + H, width: "100%", height: H, role: "img", "aria-label": spec.title }, plot);
    addPatterns(svg);
    var all = [];
    spec.series.forEach(function (s) { all = all.concat(s.values); });
    var sc = scaleFor(all, true);
    function y(v) { return m.t + ph - (v - sc.min) / (sc.max - sc.min) * ph; }
    sc.ticks.forEach(function (t) {
      svgEl("line", { x1: m.l, x2: W - m.r, y1: y(t), y2: y(t), stroke: t === 0 ? AXIS : GRID, "stroke-width": 1 }, svg);
      var lab = svgEl("text", { x: m.l - 8, y: y(t) + 4, "text-anchor": "end", fill: MUTED, "font-size": 11 }, svg);
      lab.textContent = axisFmt(spec, t);
    });
    var n = spec.labels.length, band = pw / Math.max(n, 1);
    var every = Math.ceil(n / Math.max(1, Math.floor(pw / 46)));
    spec.labels.forEach(function (lab, i) {
      if (i % every) return;
      var tx = svgEl("text", { x: m.l + band * (i + 0.5), y: H - 8, "text-anchor": "middle", fill: MUTED, "font-size": 11 }, svg);
      tx.textContent = lab;
    });
    return { svg: svg, m: m, W: W, H: H, pw: pw, ph: ph, band: band, y: y, sc: sc, n: n };
  }

  function groupTip(spec, i, ev) {
    var rows = spec.series.map(function (s, k) {
      return { color: s.color, value: full(spec, s.values[i]), name: s.name };
    });
    showTip(spec, spec.labels[i], rows, ev.clientX, ev.clientY);
  }

  function columns(plot, spec, width) {
    var c = cartesian(plot, spec, width), k = spec.series.length;
    var base = c.y(0), gap = 2;
    var barW = Math.max(3, Math.min(24, (c.band * 0.78 - gap * (k - 1)) / k));
    var groupW = barW * k + gap * (k - 1);
    var hover = svgEl("rect", { y: c.m.t, height: c.ph, fill: "#0b0b0b", "fill-opacity": 0.05, rx: 3, visibility: "hidden" }, c.svg);
    for (var i = 0; i < c.n; i++) {
      var gx = c.m.l + c.band * i + (c.band - groupW) / 2;
      spec.series.forEach(function (s, si) {
        var v = s.values[i];
        if (v === null || v === undefined || v === 0) return;
        var x = gx + si * (barW + gap);
        var color = (spec.diverging && v < 0) ? NEG : s.color;
        var d = barPath(x, base, barW, c.y(v), 4);
        if (!d) return;
        svgEl("path", { d: d, fill: color }, c.svg);
        svgEl("path", { d: d, fill: "url(#viz-hatch-" + (si % 2 ? 135 : 45) + ")", "class": "viz-tex" }, c.svg);
      });
      (function (idx) {
        var hit = svgEl("rect", { x: c.m.l + c.band * idx, y: c.m.t, width: c.band, height: c.ph, fill: "transparent", tabindex: 0, "class": "viz-hit",
          "aria-label": spec.labels[idx] + ": " + spec.series.map(function (s) { return s.name + " " + full(spec, s.values[idx]); }).join(", ") }, c.svg);
        function on(ev) {
          hover.setAttribute("x", c.m.l + c.band * idx); hover.setAttribute("width", c.band); hover.setAttribute("visibility", "visible");
          if (ev.clientX === undefined || ev.type === "focus") {
            var r = hit.getBoundingClientRect(); ev = { clientX: r.left + r.width / 2, clientY: r.top + 30 };
          }
          groupTip(spec, idx, ev);
        }
        function off() { hover.setAttribute("visibility", "hidden"); hideTip(); }
        hit.addEventListener("pointermove", on); hit.addEventListener("pointerleave", off);
        hit.addEventListener("focus", on); hit.addEventListener("blur", off);
      })(i);
    }
  }

  function line(plot, spec, width) {
    var c = cartesian(plot, spec, width);
    function x(i) { return c.m.l + c.band * (i + 0.5); }
    spec.series.forEach(function (s) {
      var d = "", pen = false, lastIdx = -1, pts = 0;
      s.values.forEach(function (v, i) {
        if (v === null || v === undefined) { pen = false; return; }
        d += (pen ? "L" : "M") + x(i) + "," + c.y(v); pen = true; lastIdx = i; pts++;
      });
      if (pts > 1) svgEl("path", { d: d, fill: "none", stroke: s.color, "stroke-width": 2, "stroke-linejoin": "round", "stroke-linecap": "round" }, c.svg);
      if (lastIdx >= 0 && !s.noMarker) {
        svgEl("circle", { cx: x(lastIdx), cy: c.y(s.values[lastIdx]), r: 5, fill: s.color, stroke: SURFACE, "stroke-width": 2 }, c.svg);
      }
    });
    var cross = svgEl("line", { y1: c.m.t, y2: c.m.t + c.ph, stroke: INK2, "stroke-width": 1, "stroke-opacity": 0.5, visibility: "hidden" }, c.svg);
    var dots = spec.series.map(function (s) { return svgEl("circle", { r: 5, fill: s.color, stroke: SURFACE, "stroke-width": 2, visibility: "hidden" }, c.svg); });
    for (var i = 0; i < c.n; i++) {
      (function (idx) {
        var hit = svgEl("rect", { x: c.m.l + c.band * idx, y: c.m.t, width: c.band, height: c.ph, fill: "transparent", tabindex: 0, "class": "viz-hit",
          "aria-label": spec.labels[idx] + ": " + spec.series.map(function (s) { return s.name + " " + full(spec, s.values[idx]); }).join(", ") }, c.svg);
        function on(ev) {
          cross.setAttribute("x1", x(idx)); cross.setAttribute("x2", x(idx)); cross.setAttribute("visibility", "visible");
          spec.series.forEach(function (s, k) {
            var v = s.values[idx];
            if (v === null || v === undefined) { dots[k].setAttribute("visibility", "hidden"); return; }
            dots[k].setAttribute("cx", x(idx)); dots[k].setAttribute("cy", c.y(v)); dots[k].setAttribute("visibility", "visible");
          });
          if (ev.clientX === undefined || ev.type === "focus") {
            var r = hit.getBoundingClientRect(); ev = { clientX: r.left + r.width / 2, clientY: r.top + 30 };
          }
          groupTip(spec, idx, ev);
        }
        function off() { cross.setAttribute("visibility", "hidden"); dots.forEach(function (d) { d.setAttribute("visibility", "hidden"); }); hideTip(); }
        hit.addEventListener("pointermove", on); hit.addEventListener("pointerleave", off);
        hit.addEventListener("focus", on); hit.addEventListener("blur", off);
      })(i);
    }
  }

  function hbars(plot, spec, width) {
    var items = spec.items, rowH = 30, m = { t: 4, b: 4, r: 78 };
    var longest = items.reduce(function (a, it) { return Math.max(a, it.label.length); }, 0);
    var labW = Math.min(200, Math.max(90, longest * 6.6)), W = Math.max(300, width);
    var H = items.length * rowH + m.t + m.b;
    var svg = svgEl("svg", { viewBox: "0 0 " + W + " " + H, width: "100%", height: H, role: "img", "aria-label": spec.title }, plot);
    addPatterns(svg);
    var max = Math.max.apply(null, items.map(function (it) { return it.value; }).concat([1]));
    var pw = W - labW - m.r - 8;
    items.forEach(function (it, i) {
      var cy = m.t + i * rowH + rowH / 2;
      var maxChars = Math.floor(labW / 6.4);
      var shown = it.label.length > maxChars ? it.label.slice(0, maxChars - 1) + "…" : it.label;
      var t = svgEl("text", { x: labW, y: cy + 4, "text-anchor": "end", fill: INK2, "font-size": 12 }, svg);
      t.textContent = shown;
      var bw = Math.max(it.value > 0 ? 3 : 0, it.value / max * pw), bx = labW + 8;
      var color = it.muted ? "#a9a7a0" : (it.color || spec.color);
      var d = "M" + bx + "," + (cy - 9) + "H" + (bx + bw - 4) + "Q" + (bx + bw) + "," + (cy - 9) + " " + (bx + bw) + "," + (cy - 5) +
        "V" + (cy + 5) + "Q" + (bx + bw) + "," + (cy + 9) + " " + (bx + bw - 4) + "," + (cy + 9) + "H" + bx + "Z";
      if (bw > 0) {
        svgEl("path", { d: d, fill: color, "class": "viz-bar" }, svg);
        svgEl("path", { d: d, fill: "url(#viz-hatch-" + (i % 2 ? 135 : 45) + ")", "class": "viz-tex" }, svg);
      }
      var vt = svgEl("text", { x: bx + bw + 6, y: cy + 4, fill: INK, "font-size": 12, "font-weight": 600 }, svg);
      vt.textContent = spec.unit === "money" || !spec.unit ? compact(it.value) : String(it.value);
      var hit = svgEl("rect", { x: 0, y: m.t + i * rowH, width: W, height: rowH, fill: "transparent", tabindex: 0, "class": "viz-hit",
        "aria-label": it.label + ": " + full(spec, it.value) + (it.share !== undefined ? ", " + it.share.toFixed(1) + "% of total" : "") }, svg);
      function on(ev) {
        if (ev.clientX === undefined || ev.type === "focus") { var r = hit.getBoundingClientRect(); ev = { clientX: r.left + r.width / 2, clientY: r.top + 6 }; }
        var rows = [{ color: color, value: full(spec, it.value), name: it.label }];
        if (it.share !== undefined) rows.push({ color: "transparent", value: it.share.toFixed(1) + "%", name: "of total" });
        if (it.count !== undefined) rows.push({ color: "transparent", value: String(it.count), name: it.count === 1 ? "invoice" : "invoices" });
        showTip(spec, spec.title, rows, ev.clientX, ev.clientY);
      }
      hit.addEventListener("pointermove", on); hit.addEventListener("pointerleave", hideTip);
      hit.addEventListener("focus", on); hit.addEventListener("blur", hideTip);
    });
  }

  // ---- public: render into a card element
  function render(card, spec) {
    var plot = card.querySelector(".viz-plot"), host = card.querySelector(".viz-legend-host");
    var tableHost = card.querySelector(".viz-table-host"), toggle = card.querySelector(".viz-toggle");
    function draw() {
      plot.textContent = "";
      if (host) host.textContent = "";
      var empty = spec.type === "hbars"
        ? !spec.items.length
        : spec.series.every(function (s) { return s.values.every(function (v) { return v === null || v === undefined || v === 0; }); });
      if (empty) {
        htmlEl("div", "viz-empty", spec.emptyText || "Nothing to show for this period yet.", plot);
        if (toggle) toggle.style.display = "none";
        return;
      }
      if (toggle) toggle.style.display = "";
      var width = plot.clientWidth || 600;
      if (host) legend(host, spec);
      if (spec.type === "columns") columns(plot, spec, width);
      else if (spec.type === "line") line(plot, spec, width);
      else hbars(plot, spec, width);
    }
    draw();
    if (tableHost) { tableHost.textContent = ""; tableHost.appendChild(tableView(spec)); }
    if (toggle) toggle.addEventListener("click", function () {
      var showing = tableHost.hasAttribute("hidden");
      if (showing) { tableHost.removeAttribute("hidden"); plot.setAttribute("hidden", ""); if (host) host.setAttribute("hidden", ""); toggle.textContent = "Chart"; }
      else { tableHost.setAttribute("hidden", ""); plot.removeAttribute("hidden"); if (host) host.removeAttribute("hidden"); toggle.textContent = "Table"; draw(); }
    });
    var last = plot.clientWidth, timer;
    if (global.ResizeObserver) new ResizeObserver(function () {
      if (plot.hasAttribute("hidden") || Math.abs(plot.clientWidth - last) < 8) return;
      last = plot.clientWidth; clearTimeout(timer); timer = setTimeout(draw, 80);
    }).observe(plot);
  }

  global.NLCharts = { render: render };
})(window);

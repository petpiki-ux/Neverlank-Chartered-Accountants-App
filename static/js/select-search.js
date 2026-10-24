/* Searchable, alphabetical drop-downs.
 *
 * Every single-choice <select> in the app with MORE THAN 10 choices gets a search box (type to narrow the list, arrow keys
 * and Enter to choose, Escape to close), and its choices are put in alphabetical order. A list of people, clients, suppliers,
 * accounts and the like (by the field's name) is put in alphabetical order whatever its length.
 *
 * Left alone, on purpose:
 *  - lists with 10 choices or fewer that are not people/clients/suppliers/accounts (Status, Priority, Frequency ... keep their logical order);
 *  - lists whose choices start with a number, bracket or date, or are month names (code charts, years, periods keep their order);
 *  - multi-select boxes;
 *  - any <select data-nosort> (keeps its order but is still searchable) and any <select data-nosearch> (not touched at all).
 * A blank first choice ("- choose -", "All") stays first and "Other" stays last. The real <select> stays in the page and is
 * what the form submits, so nothing else on the page needs to know about this.
 */
(function () {
  "use strict";
  var THRESHOLD = 10;
  var ENTITY = /(^|_)(client|user|employee|partner|manager|supplier|vendor|customer|engagement|assigned|account|reviewer|preparer|member|director|shareholder|category|payee)(s)?(_id|_ids)?($|_)/i;
  var MONTHS = /^(jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)[a-z]*\.?$/i;
  var PLACEHOLDER = /^\s*(-|—|–|\(|all\b|any\b|select\b|choose\b|none\b|no\b.*\bselected)/i;
  var cmp = function (a, b) { return a.localeCompare(b, undefined, { sensitivity: "base", numeric: true }); };
  var openWidget = null;

  function label(o) { return (o.textContent || "").replace(/\s+/g, " ").trim(); }

  // ---------------------------------------------------------------- sorting
  function isLeading(o) { return o.value === "" || PLACEHOLDER.test(label(o)); }
  function isTrailing(o) { return /^other\b/i.test(label(o)); }

  function sortable(sel, opts) {
    var body = opts.filter(function (o) { return !isLeading(o); });
    if (!body.length) return false;
    var months = 0;
    for (var i = 0; i < body.length; i++) {
      var t = label(body[i]);
      if (/^[\d(\[#]/.test(t)) return false;           // numbers, dates, codes, brackets: their order is meaningful
      if (MONTHS.test(t.split(" ")[0])) months++;
    }
    return months < 3;
  }

  function sortContainer(container) {
    var kids = Array.prototype.filter.call(container.children, function (c) { return c.tagName === "OPTION"; });
    if (kids.length < 2) return;
    var lead = [], i = 0;
    while (i < kids.length && isLeading(kids[i])) { lead.push(kids[i]); i++; }
    var rest = kids.slice(i);
    var tail = rest.filter(isTrailing);
    var mid = rest.filter(function (o) { return !isTrailing(o); });
    mid.sort(function (a, b) { return cmp(label(a), label(b)); });
    var ordered = lead.concat(mid, tail);
    var anchor = kids.length ? kids[kids.length - 1].nextSibling : null;
    var keep = container.tagName === "SELECT" ? container.selectedIndex : -1;
    var chosen = keep >= 0 ? container.options[keep] : null;
    ordered.forEach(function (o) { container.insertBefore(o, anchor); });
    if (chosen) chosen.selected = true;
  }

  function sortSelect(sel) {
    var opts = Array.prototype.slice.call(sel.options);
    if (!sortable(sel, opts)) return;
    sortContainer(sel);
    Array.prototype.forEach.call(sel.querySelectorAll("optgroup"), sortContainer);
  }

  // ---------------------------------------------------------------- the search widget
  var valueDesc = Object.getOwnPropertyDescriptor(HTMLSelectElement.prototype, "value");
  var indexDesc = Object.getOwnPropertyDescriptor(HTMLSelectElement.prototype, "selectedIndex");

  function build(sel) {
    var wrap = document.createElement("div");
    wrap.className = "ss-wrap";
    var w = sel.style.width, mw = sel.style.maxWidth, mb = sel.style.marginBottom;
    if (w) wrap.style.width = w;
    if (mw) wrap.style.maxWidth = mw;
    if (mb) wrap.style.marginBottom = mb;
    var inputStyle = sel.getAttribute("style") || "";
    sel.parentNode.insertBefore(wrap, sel);
    wrap.appendChild(sel);
    sel.classList.add("ss-native");
    sel.tabIndex = -1;

    var input = document.createElement("input");
    input.type = "text";
    input.className = "ss-input";
    input.autocomplete = "off";
    input.setAttribute("role", "combobox");
    input.setAttribute("aria-expanded", "false");
    input.setAttribute("aria-autocomplete", "list");
    var lab = sel.id ? document.querySelector('label[for="' + sel.id + '"]') : null;
    if (lab) input.setAttribute("aria-label", label(lab));
    wrap.insertBefore(input, sel);

    var panel = document.createElement("div");
    panel.className = "ss-list";
    panel.setAttribute("role", "listbox");
    panel.style.display = "none";
    document.body.appendChild(panel);

    var state = { sel: sel, wrap: wrap, input: input, panel: panel, items: [], active: -1, open: false };
    wrap.__ss = state;

    function current() { var o = sel.options[sel.selectedIndex]; return o || null; }

    function showCurrent() {
      var o = current();
      var blank = !o || o.value === "";
      input.value = blank ? "" : label(o);
      input.placeholder = o ? (blank ? label(o) || "Search…" : "") : "Search…";
      input.disabled = sel.disabled;
      wrap.classList.toggle("ss-disabled", sel.disabled);
    }

    function render(query) {
      var q = (query || "").toLowerCase().trim().split(/\s+/).filter(Boolean);
      panel.innerHTML = "";
      state.items = [];
      var lastGroup = null, shown = 0;
      Array.prototype.forEach.call(sel.options, function (o) {
        if (o.hidden) return;
        var text = label(o);
        if (q.length) {
          var hay = text.toLowerCase();
          for (var k = 0; k < q.length; k++) if (hay.indexOf(q[k]) < 0) return;
        }
        var g = o.parentNode.tagName === "OPTGROUP" ? o.parentNode : null;
        if (g !== lastGroup) {
          lastGroup = g;
          if (g) { var gh = document.createElement("div"); gh.className = "ss-group"; gh.textContent = g.label; panel.appendChild(gh); }
        }
        var d = document.createElement("div");
        d.className = "ss-opt" + (o.selected ? " sel" : "") + (o.disabled ? " dis" : "") + (o.value === "" ? " blank" : "");
        d.textContent = text || " ";
        d.setAttribute("role", "option");
        d._opt = o;
        panel.appendChild(d);
        state.items.push(d);
        shown++;
      });
      if (!shown) {
        var n = document.createElement("div");
        n.className = "ss-none";
        n.textContent = "No match";
        panel.appendChild(n);
      }
      state.active = -1;
      for (var i = 0; i < state.items.length; i++) if (!state.items[i]._opt.disabled && (q.length ? true : state.items[i]._opt.selected)) { state.active = i; break; }
      highlight(true);
    }

    function highlight(scroll) {
      state.items.forEach(function (d, i) { d.classList.toggle("active", i === state.active); });
      if (scroll && state.active >= 0) {
        var d = state.items[state.active], top = d.offsetTop, bottom = top + d.offsetHeight;
        if (top < panel.scrollTop) panel.scrollTop = top;
        else if (bottom > panel.scrollTop + panel.clientHeight) panel.scrollTop = bottom - panel.clientHeight;
      }
    }

    function place() {
      var r = input.getBoundingClientRect();
      var below = window.innerHeight - r.bottom, above = r.top;
      panel.style.left = r.left + "px";
      panel.style.minWidth = r.width + "px";
      var h = Math.min(260, panel.scrollHeight + 2);
      if (below < Math.min(h, 200) && above > below) {
        panel.style.top = ""; panel.style.bottom = (window.innerHeight - r.top + 2) + "px"; panel.style.maxHeight = Math.min(260, above - 8) + "px";
      } else {
        panel.style.bottom = ""; panel.style.top = (r.bottom + 2) + "px"; panel.style.maxHeight = Math.min(260, below - 8) + "px";
      }
    }

    function open() {
      if (state.open || sel.disabled) return;
      if (openWidget && openWidget !== state) openWidget.close();
      state.open = true; openWidget = state;
      input.setAttribute("aria-expanded", "true");
      panel.style.display = "block";
      render("");
      place();
      input.select();
    }

    function close() {
      if (!state.open) return;
      state.open = false;
      if (openWidget === state) openWidget = null;
      input.setAttribute("aria-expanded", "false");
      panel.style.display = "none";
      showCurrent();
    }
    state.close = close;

    function choose(d) {
      if (!d || d._opt.disabled) return;
      var changed = sel.selectedIndex !== d._opt.index;
      indexDesc.set.call(sel, d._opt.index);
      close();
      if (changed) {
        sel.dispatchEvent(new Event("input", { bubbles: true }));
        sel.dispatchEvent(new Event("change", { bubbles: true }));
      }
    }

    input.addEventListener("focus", open);
    input.addEventListener("click", function () { if (!state.open) open(); });
    input.addEventListener("input", function () {
      if (!state.open) open();
      render(input.value);
      place();
    });
    input.addEventListener("keydown", function (e) {
      if (e.key === "ArrowDown" || e.key === "ArrowUp") {
        e.preventDefault();
        if (!state.open) { open(); return; }
        var n = state.items.length, step = e.key === "ArrowDown" ? 1 : -1, i = state.active;
        for (var t = 0; t < n; t++) { i = (i + step + n) % n; if (!state.items[i]._opt.disabled) break; }
        state.active = i; highlight(true);
      } else if (e.key === "Enter") {
        if (state.open) { e.preventDefault(); choose(state.items[state.active]); }
      } else if (e.key === "Escape") {
        if (state.open) { e.preventDefault(); e.stopPropagation(); close(); }
      } else if (e.key === "Tab") {
        if (state.open && state.active >= 0 && input.value && input.value !== label(current() || { textContent: "" })) choose(state.items[state.active]);
        else close();
      }
    });
    panel.addEventListener("mousedown", function (e) {
      e.preventDefault();                                   // keep focus in the box
      var d = e.target.closest ? e.target.closest(".ss-opt") : null;
      if (d) choose(d);
    });
    panel.addEventListener("mousemove", function (e) {
      var d = e.target.closest ? e.target.closest(".ss-opt") : null;
      var i = d ? state.items.indexOf(d) : -1;
      if (i >= 0 && i !== state.active) { state.active = i; highlight(false); }
    });
    input.addEventListener("blur", function () { setTimeout(function () { if (document.activeElement !== input) close(); }, 0); });
    sel.addEventListener("focus", function () { input.focus(); });           // a <label for=...> click lands here
    sel.addEventListener("change", showCurrent);
    window.addEventListener("scroll", function (e) { if (state.open && !(e.target === panel)) place(); }, true);
    window.addEventListener("resize", function () { if (state.open) place(); });

    // programmatic changes (sel.value = ..., sel.selectedIndex = ...) must show in the box too
    try {
      Object.defineProperty(sel, "value", { configurable: true, get: function () { return valueDesc.get.call(sel); }, set: function (v) { valueDesc.set.call(sel, v); showCurrent(); } });
      Object.defineProperty(sel, "selectedIndex", { configurable: true, get: function () { return indexDesc.get.call(sel); }, set: function (v) { indexDesc.set.call(sel, v); showCurrent(); } });
    } catch (e) { /* older browser: the box catches up on the next change or focus */ }
    var form = sel.form;
    if (form) form.addEventListener("reset", function () { setTimeout(showCurrent, 0); });
    new MutationObserver(function () { showCurrent(); if (state.open) { render(input.value); place(); } })
      .observe(sel, { attributes: true, attributeFilter: ["disabled"], childList: true, subtree: true });

    showCurrent();
    state.refresh = showCurrent;
  }

  // ---------------------------------------------------------------- finding selects
  function realOptions(sel) {
    return Array.prototype.filter.call(sel.options, function (o) { return !isLeading(o); }).length;
  }

  function enhance(sel) {
    if (sel.__ssDone || sel.multiple || sel.size > 1 || sel.hasAttribute("data-nosearch")) return;
    if (sel.closest(".ss-wrap")) return;
    var n = realOptions(sel);
    var entity = ENTITY.test(sel.name || "") || ENTITY.test(sel.id || "");
    if (n < 2) return;
    if (n > THRESHOLD || (entity && n > 2)) {
      if (!sel.hasAttribute("data-nosort")) sortSelect(sel);
    }
    if (n > THRESHOLD) {
      sel.__ssDone = true;
      build(sel);
    } else if (entity) {
      sel.__ssDone = true;
    }
  }

  function unwrapClones(root) {
    // a row copied with cloneNode keeps the markup of the box but none of its behaviour: put the plain <select> back and start again
    var wraps = root.querySelectorAll ? Array.prototype.slice.call(root.querySelectorAll(".ss-wrap")) : [];
    if (root.classList && root.classList.contains("ss-wrap")) wraps.unshift(root);
    wraps.forEach(function (w) {
      if (w.__ss) return;
      var s = w.querySelector("select");
      if (s && w.parentNode) { s.classList.remove("ss-native"); s.tabIndex = 0; s.__ssDone = false; w.parentNode.insertBefore(s, w); }
      if (w.parentNode) w.parentNode.removeChild(w);
    });
  }

  function scan(root) {
    if (root.nodeType !== 1 && root.nodeType !== 9) return;
    unwrapClones(root);
    if (root.tagName === "SELECT") enhance(root);
    if (root.querySelectorAll) Array.prototype.forEach.call(root.querySelectorAll("select"), enhance);
  }

  function init() {
    scan(document);
    new MutationObserver(function (muts) {
      muts.forEach(function (m) {
        Array.prototype.forEach.call(m.addedNodes, function (n) {
          if (n.nodeType === 1 && !(n.classList && (n.classList.contains("ss-list") || n.classList.contains("ss-wrap")) && n.__ss)) scan(n);
        });
      });
    }).observe(document.body, { childList: true, subtree: true });
  }

  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", init); else init();
})();

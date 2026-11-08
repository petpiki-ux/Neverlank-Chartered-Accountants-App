/* Thousands separators in every money box (trial balance, journals, bills, adjustments ...).
   Any <input type="number" step="0.01"> becomes a text box that shows 1,234,567.89 while you type and when the page loads.
   - the form is submitted with plain numbers (commas stripped in the "formdata" event), so the server code is unchanged;
   - input.value read by other scripts still returns the plain number;
   - add data-nosep to a box to leave it alone (quantities, days, years). */
(function () {
  var proto = Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value');

  function clean(s) { return String(s == null ? '' : s).replace(/,/g, '').replace(/\s/g, ''); }

  function group(raw, finalise) {
    var s = clean(raw), neg = s.charAt(0) === '-';
    s = s.replace(/[^0-9.]/g, '');
    var dot = s.indexOf('.'), intPart = dot < 0 ? s : s.slice(0, dot), dec = dot < 0 ? null : s.slice(dot + 1).replace(/\./g, '');
    intPart = intPart.replace(/^0+(?=\d)/, '').replace(/\B(?=(\d{3})+(?!\d))/g, ',');
    if (finalise) {
      if (intPart === '' && dec === null) return neg ? '' : '';
      if (intPart === '') intPart = '0';
      dec = ((dec || '') + '00').slice(0, Math.max(2, (dec || '').length));
    }
    return (neg ? '-' : '') + intPart + (dec === null ? '' : '.' + dec);
  }

  function setRaw(el, v) { proto.set.call(el, v); }

  function upgrade(el) {
    if (el.dataset.money || el.dataset.nosep !== undefined || el.type !== 'number') return;
    if (el.getAttribute('step') !== '0.01') return;
    el.dataset.money = '1';
    var first = proto.get.call(el);
    el.type = 'text';
    el.setAttribute('inputmode', 'decimal');
    el.setAttribute('autocomplete', 'off');
    Object.defineProperty(el, 'value', {
      configurable: true,
      get: function () { return clean(proto.get.call(el)); },
      set: function (v) { setRaw(el, v === '' || v == null ? '' : group(v, true)); }
    });
    setRaw(el, first === '' ? '' : group(first, true));
    el.addEventListener('input', function () {
      var cur = proto.get.call(el), pos = el.selectionStart;
      var digitsBefore = clean(cur.slice(0, pos == null ? cur.length : pos)).length;
      var out = group(cur, false);
      if (out === cur) return;
      setRaw(el, out);
      var n = 0, i = 0;
      while (i < out.length && n < digitsBefore) { if (out.charAt(i) !== ',') n++; i++; }
      try { el.setSelectionRange(i, i); } catch (e) {}
    });
    el.addEventListener('blur', function () {
      var cur = proto.get.call(el);
      setRaw(el, cur === '' ? '' : group(cur, true));
    });
  }

  function scan(root) {
    (root.querySelectorAll ? root.querySelectorAll('input[type=number][step="0.01"]') : []).forEach(upgrade);
  }

  document.addEventListener('formdata', function (ev) {
    var form = ev.target, names = {};
    form.querySelectorAll('input[data-money]').forEach(function (el) { names[el.name] = 1; });
    var fd = ev.formData, entries = Array.from(fd.entries());
    if (!entries.some(function (e) { return names[e[0]]; })) return;
    Array.from(new Set(entries.map(function (e) { return e[0]; }))).forEach(function (k) { fd.delete(k); });
    entries.forEach(function (e) { fd.append(e[0], names[e[0]] && typeof e[1] === 'string' ? clean(e[1]) : e[1]); });
  }, true);

  function start() {
    scan(document);
    new MutationObserver(function (muts) {
      muts.forEach(function (m) { m.addedNodes.forEach(function (n) { if (n.nodeType === 1) { if (n.matches && n.matches('input')) upgrade(n); scan(n); } }); });
    }).observe(document.body, { childList: true, subtree: true });
  }
  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', start); else start();
})();

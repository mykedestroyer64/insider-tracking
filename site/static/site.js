/* Insider Tracking: the small amount of JavaScript the site uses.
   Everything here is optional: the pages work without it, this makes them nicer.
   1. the side menu's pin button      2. the search box
   3. the home page's chart and trade panel      4. the Trades page's filters
   5. the quick filter on the Senators and Stocks tables */
(function () {
  "use strict";
  var $ = function (sel, root) { return (root || document).querySelector(sel); };
  var $$ = function (sel, root) { return Array.prototype.slice.call((root || document).querySelectorAll(sel)); };
  var esc = function (s) { return String(s == null ? "" : s).replace(/[&<>"']/g, function (c) { return { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]; }); };
  var store = {
    get: function (k) { try { return localStorage.getItem(k); } catch (e) { return null; } },
    set: function (k, v) { try { localStorage.setItem(k, v); } catch (e) { /* private mode etc. */ } }
  };
  var MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];
  function fmtDate(iso) {
    if (!iso) { return "–"; }
    var p = iso.split("-");
    return MONTHS[+p[1] - 1] + " " + (+p[2]) + ", " + p[0];
  }
  var CLOCK = '<svg width="14" height="14" viewBox="0 0 16 16" class="icon" role="img" aria-label="Late"><circle cx="8" cy="8" r="6.5"></circle><path d="M8 4.5V8l2.5 1.5"></path></svg>';
  var base = (function () {
    var s = $('script[src*="site.js"]');
    return s ? s.getAttribute("src").replace(/\/static\/site\.js.*$/, "") : "";
  })();

  /* 1. side menu pin --------------------------------------------------- */
  (function () {
    var root = document.documentElement, pin = $("#pin"), label = $("#pin-label");
    if (!pin) { return; }
    function set(on) {
      root.classList.toggle("menu-open", on);
      pin.setAttribute("aria-pressed", on ? "true" : "false");
      pin.title = on ? "Unpin menu" : "Keep menu open";
      if (label) { label.textContent = pin.title; }
    }
    set(root.classList.contains("menu-open"));   // the head script decided before paint
    pin.addEventListener("click", function (e) {
      var on = !root.classList.contains("menu-open");
      set(on);
      store.set("menu-pinned", on ? "1" : "0");
      if (e.detail && !on) { pin.blur(); }   // after a mouse click, let the menu fold back
    });
  })();

  /* 2. search ----------------------------------------------------------- */
  (function () {
    var forms = $$("form[data-search]");
    if (!forms.length) { return; }
    var index = null, loading = null;
    function load(url) {
      if (index) { return Promise.resolve(index); }
      if (!loading) {
        loading = fetch(url).then(function (r) { return r.json(); }).then(function (d) { index = d; return d; })
          .catch(function () { loading = null; return []; });
      }
      return loading;
    }
    forms.forEach(function (form) {
      var input = $("input[type=search]", form), list = $(".search-results", form), current = -1;
      function close() { list.hidden = true; list.innerHTML = ""; input.setAttribute("aria-expanded", "false"); current = -1; }
      function render(items) {
        if (!items.length) { close(); return; }
        list.innerHTML = items.map(function (it, i) {
          return '<li role="option" id="' + input.id + '-opt-' + i + '"><a href="' + esc(base + it.u) + '"><span>' + esc(it.n) + '</span><span class="muted">' + esc(it.s || "") + '</span></a></li>';
        }).join("");
        list.hidden = false;
        input.setAttribute("aria-expanded", "true");
        current = -1;
      }
      function search() {
        var q = input.value.trim().toLowerCase();
        if (q.length < 1) { close(); return; }
        load(form.getAttribute("data-index")).then(function (items) {
          var starts = [], contains = [];
          items.forEach(function (it) {
            var hay = (it.n + " " + (it.s || "") + " " + (it.k || "")).toLowerCase();
            if (it.n.toLowerCase().indexOf(q) === 0 || (it.k || "").toLowerCase().indexOf(q) === 0) { starts.push(it); }
            else if (hay.indexOf(q) !== -1) { contains.push(it); }
          });
          render(starts.concat(contains).slice(0, 8));
        });
      }
      input.addEventListener("input", search);
      input.addEventListener("focus", function () { load(form.getAttribute("data-index")); if (input.value) { search(); } });
      input.addEventListener("keydown", function (e) {
        var links = $$("a", list);
        if (list.hidden || !links.length) { return; }
        if (e.key === "ArrowDown" || e.key === "ArrowUp") {
          e.preventDefault();
          current = e.key === "ArrowDown" ? Math.min(current + 1, links.length - 1) : Math.max(current - 1, 0);
          links.forEach(function (a, i) { a.classList.toggle("is-current", i === current); });
          input.setAttribute("aria-activedescendant", input.id + "-opt-" + current);
        } else if (e.key === "Enter" && current >= 0) {
          e.preventDefault();
          window.location.href = links[current].href;
        } else if (e.key === "Escape") { close(); }
      });
      document.addEventListener("click", function (e) { if (!form.contains(e.target)) { close(); } });
    });
  })();

  /* 3. home page: chart ranges and the selected trade ------------------- */
  (function () {
    var card = $("[data-activity]");
    if (card) {
      var dataEl = $("#activity-data"), data = {};
      try { data = JSON.parse(dataEl.textContent); } catch (e) { data = {}; }
      $$("[data-range]", card).forEach(function (btn) {
        btn.addEventListener("click", function () {
          var c = data[btn.getAttribute("data-range")];
          if (!c) { return; }
          $$("[data-range]", card).forEach(function (b) { var on = b === btn; b.classList.toggle("is-on", on); b.setAttribute("aria-pressed", on ? "true" : "false"); });
          $("#act-area").setAttribute("d", c.area);
          $("#act-line").setAttribute("d", c.line);
          $("#act-end").setAttribute("cx", c.endX); $("#act-end").setAttribute("cy", c.endY);
          var peak = $("#act-peak"); peak.setAttribute("x", c.peakX); peak.setAttribute("y", c.peakY); peak.setAttribute("text-anchor", c.peakAnchor); peak.textContent = c.peakText;
          var end = $("#act-endlabel"); end.setAttribute("y", c.endLabelY); end.textContent = c.endText;
          $$(".act-y", card).forEach(function (t, i) { t.textContent = c.y[i]; });
          $$(".act-t", card).forEach(function (t, i) { t.textContent = c.t[i]; });
          $("#act-caption").textContent = c.caption;
        });
      });
    }
    var latest = $("[data-latest]"), detail = $("#detail");
    if (latest && detail) {
      function field(name) { return $('[data-f="' + name + '"]', detail); }
      $$(".list-row", latest).forEach(function (row) {
        var btn = $("[data-pick]", row);
        if (!btn) { return; }
        btn.addEventListener("click", function () {
          var t;
          try { t = JSON.parse(row.getAttribute("data-trade")); } catch (e) { return; }
          $$(".list-row", latest).forEach(function (r) { r.classList.toggle("is-selected", r === row); $("[data-pick]", r).setAttribute("aria-pressed", r === row ? "true" : "false"); });
          field("headline").textContent = t.headline;
          field("sub").textContent = (t.a ? t.a + " · " : "") + t.tl;
          field("who").textContent = t.who || "";
          field("traded").textContent = fmtDate(t.d);
          field("received").textContent = fmtDate(t.r);
          field("days").innerHTML = t.dy == null ? "–" : (t.l ? '<span class="days">' + CLOCK + '<span class="late-flag">' + t.dy + ', late</span></span><div class="muted">' + (t.dy - 45) + ' days past the 45-day limit</div>' : (t.x ? t.dy + ' <span class="muted">(amendment)</span>' : t.dy + ", within the limit"));
          field("amount").textContent = t.am || "–";
          field("owner").textContent = t.o || "–";
          field("comment").textContent = t.c || "–";
          field("link").setAttribute("href", t.u);
        });
      });
    }
  })();

  /* 4. the Trades page -------------------------------------------------- */
  (function () {
    var form = $("#filters");
    if (!form) { return; }
    var body = $("#trades-body"), status = $("#status"), pager = $("#pager"), info = $("#page-info");
    var years = form.getAttribute("data-years").split(",").filter(Boolean);
    var urlTemplate = form.getAttribute("data-url");
    var cache = {}, rows = [], page = 0, PAGE = 50, sortKey = "r", sortDir = -1;
    var f = { year: $("#f-year"), senator: $("#f-senator"), stock: $("#f-stock"), type: $("#f-type"), owner: $("#f-owner"), amount: $("#f-amount"), late: $("#f-late") };

    function loadYear(y) {
      if (cache[y]) { return Promise.resolve(cache[y]); }
      return fetch(urlTemplate.replace("{year}", y)).then(function (r) { return r.ok ? r.json() : []; })
        .then(function (d) { cache[y] = d; return d; }).catch(function () { return []; });
    }
    function loadSelected() {
      var y = f.year.value;
      status.textContent = "Loading trades…";
      if (y === "all") { return Promise.all(years.map(loadYear)).then(function (parts) { return [].concat.apply([], parts); }); }
      return loadYear(y);
    }
    function matches(t) {
      if (f.senator.value && t.ss !== f.senator.value) { return false; }
      if (f.type.value && t.g !== f.type.value) { return false; }
      if (f.owner.value && (t.o || "") !== f.owner.value) { return false; }
      if (+f.amount.value > 0 && !(t.mn >= +f.amount.value)) { return false; }
      if (f.late.checked && !t.l) { return false; }
      var q = f.stock.value.trim().toLowerCase();
      if (q && ((t.k || "") + " " + (t.a || "") + " " + (t.sn || "")).toLowerCase().indexOf(q) === -1) { return false; }
      return true;
    }
    function cmp(a, b) {
      var x = a[sortKey], y = b[sortKey];
      if (x == null && y == null) { return 0; }
      if (x == null) { return 1; }
      if (y == null) { return -1; }
      if (typeof x === "string") { return x.localeCompare(y) * sortDir; }
      return (x - y) * sortDir;
    }
    function rowHtml(t) {
      var stock = t.k ? '<a href="' + esc(base + "/stocks/" + t.ks + "/") + '" class="plain cell-main">' + esc(t.k) + '</a>' + (t.a ? '<div class="muted">' + esc(t.a) + '</div>' : "")
        : '<span class="cell-main">' + esc(t.a || "–") + '</span>' + (t.at ? '<div class="muted">' + esc(t.at) + '</div>' : "");
      var days = t.dy == null ? '<span class="muted">–</span>' : '<span class="days">' + (t.l ? CLOCK : "") + '<span' + (t.l ? ' class="late-flag"' : "") + '>' + t.dy + '</span></span>' + (t.x ? '<div class="muted">amendment</div>' : "");
      return '<tr><td><a href="' + esc(base + "/senators/" + t.ss + "/") + '" class="plain cell-main">' + esc(t.sn) + '</a><div class="muted">' + esc(t.sp || "") + '</div></td>' +
        '<td>' + stock + '</td><td class="nowrap">' + esc(t.tl) + '</td><td>' + esc(t.o || "–") + '</td><td class="num nowrap">' + esc(t.am || "–") + '</td>' +
        '<td class="num nowrap">' + fmtDate(t.d) + '</td><td class="num nowrap">' + fmtDate(t.r) + '</td><td class="r">' + days + '</td>' +
        '<td><a href="' + esc(t.u) + '" rel="noopener">Report</a></td></tr>';
    }
    function render() {
      var total = rows.length, pages = Math.max(1, Math.ceil(total / PAGE));
      page = Math.min(page, pages - 1);
      var slice = rows.slice(page * PAGE, (page + 1) * PAGE);
      body.innerHTML = slice.map(rowHtml).join("");
      status.textContent = total === 0 ? "No trades match these filters." : ("Showing " + (page * PAGE + 1) + "–" + (page * PAGE + slice.length) + " of " + total.toLocaleString() + " trades.");
      pager.hidden = pages <= 1;
      info.textContent = "Page " + (page + 1) + " of " + pages;
      $("#prev").disabled = page === 0;
      $("#next").disabled = page >= pages - 1;
    }
    function apply() {
      loadSelected().then(function (all) {
        rows = all.filter(matches).sort(cmp);
        page = 0;
        render();
      });
    }
    $$("th.sortable", $("#trades-table")).forEach(function (th) {
      th.addEventListener("click", function () {
        var key = th.getAttribute("data-sort");
        sortDir = sortKey === key ? -sortDir : (key === "sn" || key === "k" ? 1 : -1);
        sortKey = key;
        $$("th.sortable", $("#trades-table")).forEach(function (h) { h.setAttribute("aria-sort", h === th ? (sortDir === 1 ? "ascending" : "descending") : "none"); });
        rows.sort(cmp); page = 0; render();
      });
    });
    $("#prev").addEventListener("click", function () { page -= 1; render(); window.scrollTo({ top: $("#trades-table").offsetTop - 16 }); });
    $("#next").addEventListener("click", function () { page += 1; render(); window.scrollTo({ top: $("#trades-table").offsetTop - 16 }); });
    Object.keys(f).forEach(function (k) { f[k].addEventListener(k === "stock" ? "input" : "change", apply); });
    form.addEventListener("submit", function (e) { e.preventDefault(); apply(); });

    // A search from another page lands here as ?q=...: a senator's name picks the senator, anything else filters stocks.
    var q = new URLSearchParams(window.location.search).get("q");
    if (q) {
      var opt = $$("option", f.senator).filter(function (o) { return o.textContent.toLowerCase().indexOf(q.toLowerCase()) === 0 && o.value; })[0];
      if (opt) { f.senator.value = opt.value; f.year.value = "all"; } else { f.stock.value = q; f.year.value = "all"; }
    }
    apply();
  })();

  /* 5. quick filter on index tables ------------------------------------- */
  $$("input[data-filter]").forEach(function (input) {
    var table = $(input.getAttribute("data-filter"));
    if (!table) { return; }
    input.addEventListener("input", function () {
      // every word typed must start one of the row's words (name, party letter, state code or name, ticker...)
      var words = input.value.trim().toLowerCase().split(/\s+/).filter(Boolean);
      $$("tbody tr", table).forEach(function (tr) {
        var tokens = (tr.getAttribute("data-search") || tr.textContent).toLowerCase().split(/[^a-z0-9.&'-]+/);
        tr.hidden = words.some(function (w) { return !tokens.some(function (t) { return t.indexOf(w) === 0; }); });
      });
    });
  });
})();

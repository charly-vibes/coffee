"""Módulo extraído de viz-fpa.py (decomposición, comportamiento idéntico)."""
import calendar  # noqa: F401
import html  # noqa: F401
import json  # noqa: F401
import re  # noqa: F401
import sys
from datetime import date, datetime  # noqa: F401
from html.parser import HTMLParser  # noqa: F401
from pathlib import Path  # noqa: F401

sys.path.insert(0, str(Path(__file__).resolve().parent))
import fpa_config  # noqa: F401,E402
import schema_validate  # noqa: F401,E402
import site_theme  # noqa: F401,E402







_PAGE_SCRIPT_VIEWS = """  // ============ F6: vistas, tabs, top-5, export, share, readout ============
  var VIEWS = ["summary", "cost", "breakdown", "models", "gasto", "habits",
               "outlook", "energy", "data"];
  function setView(key) {
    if (VIEWS.indexOf(key) < 0) return;
    document.body.setAttribute("data-view", key);
    document.querySelectorAll(".tab").forEach(function (t) {
      if (t.getAttribute("data-view") === key) t.setAttribute("aria-current", "true");
      else t.removeAttribute("aria-current");
    });
  }
  document.querySelectorAll(".tab").forEach(function (t) {
    t.addEventListener("click", function (e) {
      // coffe-dqz: las vistas se alternan a todo ancho (revierte la parte
      // desktop de FPA-152); en mobile se evita el salto del ancla.
      if (window.matchMedia &&
          window.matchMedia("(max-width:599px)").matches) {
        e.preventDefault();
        setView(t.getAttribute("data-view"));
        // coffe-26b: el h2 de la vista entra bajo la topbar — los
        // primeros ~350px (header/topbar/banner) no cambian entre
        // vistas y el salto a scrollY=0 no da señal del cambio.
        var h = document.querySelector(
          "#" + t.getAttribute("data-view") + " > h2");
        if (h) h.scrollIntoView({ block: "start" });
        return;
      }
      setView(t.getAttribute("data-view"));
    });
  });
  // FPA-155: Mostrar todo — revela las filas extra-row de su bloque
  document.addEventListener("click", function (e) {
    var b = e.target.closest && e.target.closest("button.show-all");
    if (!b) return;
    var zone = b.closest("details") || document;
    zone.querySelectorAll("tr.extra-row").forEach(function (r) { r.hidden = false; });
    b.remove();
  });
  // FPA-156: toggle skills/comandos en una sola vista
  document.querySelectorAll(".ptoggle").forEach(function (b) {
    b.addEventListener("click", function () {
      var root = b.closest("details");
      root.querySelectorAll(".ptoggle").forEach(function (x) {
        x.classList.toggle("active", x === b);
      });
      root.querySelectorAll("div[id$='-panel']").forEach(function (p) {
        p.hidden = p.id !== b.getAttribute("data-panel");
      });
    });
  });
  // FPA-169: Export CSV (tablas) / Download SVG (charts), sin librerías
  function tableToCSV(table) {
    var rows = Array.prototype.slice.call(table.querySelectorAll("tr"))
      .filter(function (tr) { return !tr.hidden; });
    return rows.map(function (tr) {
      return Array.prototype.slice.call(tr.querySelectorAll("th,td")).map(function (c) {
        var t = c.textContent.replace(/\\s+/g, " ").trim();
        return /[",\\n]/.test(t) ? '"' + t.replace(/"/g, '""') + '"' : t;
      }).join(",");
    }).join("\\n");
  }
  function svgXml(svg) {
    return '<?xml version="1.0" encoding="UTF-8"?>\\n' + svg.outerHTML;
  }
  function downloadText(name, text, mime) {
    var blob = new Blob([text], {type: mime});
    var a = document.createElement("a");
    a.href = URL.createObjectURL(blob);
    a.download = name;
    document.body.appendChild(a); a.click(); a.remove();
    URL.revokeObjectURL(a.href);
  }
  document.addEventListener("click", function (e) {
    var b = e.target.closest && e.target.closest("button.export-csv");
    if (b) {
      var t = b.nextElementSibling;
      while (t && t.tagName !== "TABLE") t = t.nextElementSibling;
      if (t) downloadText("fpa-tabla.csv", tableToCSV(t), "text/csv");
      return;
    }
    b = e.target.closest && e.target.closest("button.dl-svg");
    if (b) {
      var s = b.nextElementSibling;
      while (s && s.tagName !== "SVG" && s.tagName !== "svg") s = s.nextElementSibling;
      if (s) downloadText("fpa-chart.svg", svgXml(s), "image/svg+xml");
    }
  });
  // FPA-175: readout persistente del chart por tap/focus/hover
  // (re-bindable: el bridge monta waterfalls dinámicamente, D6 coffe-8nw)
  var readout = document.getElementById("wf-readout");
  function bindReadout() {
    document.querySelectorAll(".wf.chart rect[data-label], .enb-chart rect[data-label]").forEach(function (r) {
      if (r.__wfReadout) return;
      r.__wfReadout = true;
      function show() { if (readout) readout.textContent = r.getAttribute("aria-label"); }
      r.addEventListener("mouseenter", show);
      r.addEventListener("click", show);
      r.addEventListener("focus", show);
    });
  }
  bindReadout();
  // D6 (coffe-8nw): bridge con selector de mes — monta el waterfall del
  // mes elegido desde el template data-bridge-month, sin recarga.
  var bridgeSel = document.getElementById("bridge-month");
  function mountBridge() {
    var fig = document.getElementById("bridge-figure");
    if (!bridgeSel || !fig) return;
    var t = document.querySelector(
      'template[data-bridge-month="' + bridgeSel.value + '"]');
    fig.textContent = "";
    if (t && t.content) fig.appendChild(t.content.cloneNode(true));
    bindReadout();
  }
  if (bridgeSel) {
    bridgeSel.addEventListener("change", mountBridge);
    mountBridge();
  }
  // add-session-spend-report: vista Gasto diario — monta el día elegido
  // desde templates data-daily-day (pre-calculados en Python), sin recarga.
  var dailySel = document.getElementById("daily-spend-day");
  function mountDaily() {
    var fig = document.getElementById("daily-spend-figure");
    if (!dailySel || !fig) return;
    var t = document.querySelector(
      'template[data-daily-day="' + dailySel.value + '"]');
    fig.textContent = "";
    if (t && t.content) fig.appendChild(t.content.cloneNode(true));
    bindReadout();
  }
  if (dailySel) {
    dailySel.addEventListener("change", mountDaily);
    mountDaily();
  }
  // FPA-170/171: Share view — URL con periodo, vista y expansión de árbol;
  // al cargar se restaura solo con parámetros válidos (los inválidos se
  // ignoran y quedan los defaults).
  function shareUrl() {
    var p = new URLSearchParams();
    p.set("view", document.body.getAttribute("data-view") || "summary");
    p.set("period", sel ? sel.value : "all");
    var open = [], closed = [];
    document.querySelectorAll("details[data-tree]").forEach(function (d) {
      (d.open ? open : closed).push(d.getAttribute("data-tree"));
    });
    if (open.length) p.set("open", open.join(","));
    if (closed.length) p.set("closed", closed.join(","));
    return location.href.split("?")[0] + "?" + p.toString();
  }
  var shareBtn = document.getElementById("share-view");
  if (shareBtn) shareBtn.addEventListener("click", function () {
    var url = shareUrl();
    function done() { shareBtn.title = "URL copiada: " + url; }
    if (navigator.clipboard && navigator.clipboard.writeText) {
      navigator.clipboard.writeText(url).then(done, done);
    } else { done(); }
  });
  (function applyParams() {
    try { var q = new URLSearchParams(location.search); } catch (err) { return; }
    var v = q.get("view");
    if (v && VIEWS.indexOf(v) >= 0) setView(v);  // FPA-171: inválidos ignorados
    // add-session-spend-report: deep-link de la vista con el día
    // (?view=gasto&day=YYYY-MM-DD) — días inválidos se ignoran (default).
    var gd = q.get("day");
    if (gd && dailySel && dailySel.querySelector('option[value="' + gd + '"]')) {
      dailySel.value = gd;
      mountDaily();
    }
    var pd = q.get("period");
    if (pd && sel && MODEL.views[pd]) { sel.value = pd; renderView(pd); }
    var open = q.get("open");
    if (open) open.split(",").forEach(function (name) {
      if (!/^[a-z0-9-]+$/.test(name)) return;  // FPA-171: inválidos ignorados
      var d = document.querySelector('details[data-tree="' + name + '"]');
      if (d) d.open = true;
    });
    var closed = q.get("closed");
    if (closed) closed.split(",").forEach(function (name) {
      if (!/^[a-z0-9-]+$/.test(name)) return;  // FPA-171: inválidos ignorados
      var d = document.querySelector('details[data-tree="' + name + '"]');
      if (d) d.open = false;
    });
  })();
  // Exposición mínima para los tests Playwright (no es parte del UI)
  window.__fpa = { setView: setView, tableToCSV: tableToCSV, svgXml: svgXml,
                   shareUrl: shareUrl, VIEWS: VIEWS };
})();
"""
from viz_fpa_common import _link  # noqa: E402

_link(globals())

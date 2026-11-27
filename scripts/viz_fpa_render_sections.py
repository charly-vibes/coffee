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







_PAGE_SCRIPT_UI = """(function () {
  "use strict";
  var MODEL = JSON.parse(document.getElementById("fpa-model").textContent);
  function esc(s) {
    return String(s).replace(/[&<>"']/g, function (c) {
      if (c === "&") return "&amp;";
      if (c === "<") return "&lt;";
      if (c === ">") return "&gt;";
      if (c === "'") return "&#39;";
      return "&quot;";
    });
  }
  function kpiCard(k) {
    var value = k.value === null
      ? '<span class="fig" data-provenance="' + k.provenance + '"><span class="na">n/a</span></span><p class="reason">' + esc(k.reason || "") + '</p>'
      : '<span class="fig" data-provenance="' + k.provenance + '"><span class="val">' + esc(k.display) + '</span></span>'
        + '<p class="interp"' + (k.excluded_share ? ' title="FPA-045"' : '') + '>'
        + 'Δ vs prior: ' + esc(k.delta_display || "n/a")
        + (k.delta_kind === "daily-rate" && k.delta_display ? ' <span class="small">(tasa diaria)</span>' : '')
        + (k.excluded_share ? ' · excluido por tokens: ' + esc(k.excluded_display) : '')
        + (k.headline_interp ? ' — ' + esc(k.headline_interp) : '')
        + '</p>';
    var spark = (k.spark && k.spark.length > 1)
      ? '<svg class="spark" width="120" height="28" viewBox="0 0 120 28" role="img" aria-label="sparkline">'
        + sparkPoints(k.spark) + '</svg>' : "";
    return '<div class="headline kpi" data-key="' + esc(k.key) + '"><h3>' + esc(k.label) + '</h3>'
      + value + spark + '</div>';
  }
  function sparkPoints(series) {
    var vals = series.map(function (p) { return p.value; });
    var lo = Math.min.apply(null, vals), hi = Math.max.apply(null, vals);
    var span = (hi - lo) || 1, step = 120 / (vals.length - 1);
    var pts = vals.map(function (v, i) {
      return (i * step).toFixed(1) + "," + (28 - 2 - (v - lo) / span * 24).toFixed(1);
    }).join(" ");
    return '<polyline fill="none" stroke="currentColor" stroke-width="1.5" points="' + pts + '"/>';
  }
  function repoAnchor(row) {
    // coffe-vp8: espejo JS de _repo_anchor — ancla al repo GH + 🔒 privado
    var label = esc(row.label);
    if (!row.url) return label;
    var html = '<a class="repo-link" href="' + esc(row.url) + '" target="_blank" rel="noopener noreferrer">' + label + '</a>';
    if (row.private) html += ' <span class="lock" title="repo privado" aria-label="repo privado">🔒</span>';
    return html;
  }
  function treeRows(row, isTime, depth) {
    var cells = '<td>' + esc(row.cost_display) + '</td><td>' + esc(row.pct_display)
      + '</td><td>' + esc(row.interactions_display) + '</td><td>' + esc(row.per_1k_display)
      + '</td><td>' + esc(row.delta_display) + '</td>';
    if (isTime) cells += '<td>' + esc(row.budget_display) + '</td><td>'
      + esc(row.variance_display) + '</td><td>' + esc(row.variance_pct_display) + '</td>';
    var label = row.children.length
      ? '<details open><summary class="tlabel">' + repoAnchor(row) + '</summary></details>'
      : '<span style="display:inline-block;margin-left:' + (depth * 14) + 'px">' + repoAnchor(row) + '</span>';
    return '<tr class="trow" data-key="' + esc(row.key) + '"><td>' + label + '</td>' + cells + '</tr>'
      + row.children.map(function (c) { return treeRows(c, isTime, depth + 1); }).join("");
  }
  function renderView(key) {
    var v = MODEL.views[key];
    if (!v) return;
    document.getElementById("kpi-cards").innerHTML = v.kpis.map(kpiCard).join("");
    var lim = document.getElementById("view-limitation");
    if (v.limitation) { lim.textContent = "Limitación: " + v.limitation; lim.hidden = false; }
    else lim.hidden = true;
    var box = document.getElementById("tree-box");
    box.innerHTML = treeBlock("time", "Árbol Time (Año → Trimestre → Mes)", v.trees.time, true)
      + treeBlock("tool", "Árbol Tool (Tool → Model)", v.trees.tool, false)
      + treeBlock("portfolio", "Árbol Portfolio (Categoría → Proyecto)", v.trees.portfolio, false);
  }
  function treeBlock(name, title, root, isTime) {
    var cols = ["Nodo", "Coste", "% total", "Interacciones", "$/1k", "Δ vs prior"];
    if (isTime) cols.push("Presupuesto*", "Varianza", "Var %");
    var head = cols.map(function (c) { return "<th>" + c + "</th>"; }).join("");
    // coffe-x6l F5: solo la primaria (portfolio) abre por defecto;
    // espeja los defaults del render Python.
    var openAttr = (name === "portfolio") ? " open" : "";
    return '<details class="tree" data-tree="' + name + '"' + openAttr
      + '><summary><h2>' + title
      + '</h2></summary><button class="export-csv" type="button">Exportar CSV</button>'
      + '<table class="ttree"><thead><tr>' + head
      + '</tr></thead><tbody>' + treeRows(root, isTime, 0) + '</tbody></table>'
      + (isTime ? '<p class="small">* presupuesto cash del config (assumed). En ventanas que mezclan meses fuera del periodo presupuestado, la varianza compara solo el tramo con presupuesto.</p>' : "")
      + '</details>';
  }
  var sel = document.getElementById("period-select");
  if (sel) sel.addEventListener("change", function () { renderView(sel.value); });

  // ============ F3: recompute sin reload (FPA-056/077) ============
  // Las fórmulas replican apply_scenario/build_budget de Python; un test
  // de paridad Playwright verifica que igualan los golden pre-calculados.
  function fmtUsd(x) {
    // FPA-053: signo explícito (+/-$), igual que _fmt_signed de Python
    return (x < 0 ? "-$" : "+$") + Math.abs(x).toLocaleString("en-US",
      {minimumFractionDigits: 2, maximumFractionDigits: 2});
  }
  function fmtPct(f) {
    return (f >= 0 ? "+" : "") + (f * 100).toFixed(1) + "%";
  }
  function markerFor(v) {
    if (v < -0.005) return {symbol: "\u25bc", text: "bajo", favorable: true};
    if (v > 0.005) return {symbol: "\u25b2", text: "sobre", favorable: false};
    return {symbol: "\u25cf", text: "en", favorable: null};
  }
  function markerHtml(mk) {
    var cls = mk.favorable === true ? "mk-fav" :
              mk.favorable === false ? "mk-unfav" : "mk-neutral";
    return '<span class="marker ' + cls + '">' + mk.symbol + ' ' + mk.text + '</span>';
  }
  function budgetRecompute() {
    // FPA-056: editar presupuesto → re-calcular varianzas sin reload
    var cash = parseFloat(document.getElementById("budget-cash").value) || 0;
    var eff = parseFloat(document.getElementById("budget-eff").value) || 0;
    var rows = document.querySelectorAll("#budget tbody tr[data-ym]");
    var budgetData = MODEL.budget.rows;
    var totA = 0, totB = 0, totE = 0, totBef = 0;
    rows.forEach(function (tr) {
      var ym = tr.getAttribute("data-ym");
      var r = budgetData.find(function (x) { return x.ym === ym; });
      if (!r || !r.in_budget || r.actual_cash === null) return;
      var pr = r.pro_rate;
      var bCash = Math.round(cash * pr * 100) / 100;
      var bEff = Math.round(eff * pr * 100) / 100;
      var vCash = Math.round((r.actual_cash - bCash) * 100) / 100;
      var vEff = Math.round((r.actual_eff - bEff) * 100) / 100;
      var tds = tr.querySelectorAll("td");
      tds[2].innerHTML = '$' + r.actual_cash.toFixed(2).replace(/\\B(?=(\\d{3})+(?!\\d))/g, ",");
      tds[3].textContent = '$' + bCash.toFixed(2).replace(/\\B(?=(\\d{3})+(?!\\d))/g, ",");
      tds[4].innerHTML = fmtUsd(vCash) + ' ' + markerHtml(markerFor(vCash));
      tds[5].textContent = bCash ? fmtPct(vCash / bCash) : "n/a";
      tds[6].innerHTML = '$' + r.actual_eff.toFixed(2).replace(/\\B(?=(\\d{3})+(?!\\d))/g, ",");
      tds[7].textContent = '$' + bEff.toFixed(2).replace(/\\B(?=(\\d{3})+(?!\\d))/g, ",");
      tds[8].innerHTML = fmtUsd(vEff) + ' ' + markerHtml(markerFor(vEff));
      tds[9].textContent = bEff ? fmtPct(vEff / bEff) : "n/a";
      totA += r.actual_cash; totB += bCash;
      totE += r.actual_eff; totBef += bEff;
    });
    var ytd = document.querySelector("#budget tr.ytd");
    if (ytd) {
      var t = ytd.querySelectorAll("td");
      var vC = Math.round((totA - totB) * 100) / 100;
      var vE = Math.round((totE - totBef) * 100) / 100;
      t[2].textContent = '$' + totA.toFixed(2).replace(/\\B(?=(\\d{3})+(?!\\d))/g, ",");
      t[3].textContent = '$' + totB.toFixed(2).replace(/\\B(?=(\\d{3})+(?!\\d))/g, ",");
      t[4].innerHTML = fmtUsd(vC) + ' ' + markerHtml(markerFor(vC));
      t[5].textContent = totB ? fmtPct(vC / totB) : "n/a";
      t[6].textContent = '$' + totE.toFixed(2).replace(/\\B(?=(\\d{3})+(?!\\d))/g, ",");
      t[7].textContent = '$' + totBef.toFixed(2).replace(/\\B(?=(\\d{3})+(?!\\d))/g, ",");
      t[8].innerHTML = fmtUsd(vE) + ' ' + markerHtml(markerFor(vE));
      t[9].textContent = totBef ? fmtPct(vE / totBef) : "n/a";
    }
  }
  function applyScenario(growth, rateChg, planKey) {
    // FPA-072/073: replica de viz.apply_scenario
    var fc = MODEL.forecast;
    if (!fc || fc.n_a_reason) return [];
    var fee = fc.base.fee_base;
    fc.plans.forEach(function (p) { if (p.key === planKey) fee = p.fee; });
    return fc.future_months.map(function (ym, i) {
      var f = Math.pow(1 + growth, i + 1);
      return {
        ym: ym,
        eff: Math.round(fc.base.eff_base * f * (1 + rateChg) * 100) / 100,
        cash: Math.round((fee + fc.base.p2p_base * f) * 100) / 100
      };
    });
  }
  function forecastRecompute() {
    var g = (parseFloat(document.getElementById("fc-growth").value) || 0) / 100;
    var r = (parseFloat(document.getElementById("fc-rate").value) || 0) / 100;
    var plan = document.getElementById("fc-plan").value;
    var rows = applyScenario(g, r, plan);
    var trs = document.querySelectorAll("#forecast tr[data-fc-ym]");
    var fc = MODEL.forecast;
    var nStatic = fc.remainder ? 1 : 0;
    trs.forEach(function (tr, idx) {
      var i = idx - nStatic;
      if (i < 0 || i >= rows.length) return;  // fila del mes parcial: fija
      var tds = tr.querySelectorAll("td");
      tds[1].innerHTML = '$' + rows[i].eff.toFixed(2).replace(/\\B(?=(\\d{3})+(?!\\d))/g, ",")
        + ' ' + markerHtml({symbol: "\u25b3", text: "forecast", favorable: null});
      tds[2].innerHTML = '$' + rows[i].cash.toFixed(2).replace(/\\B(?=(\\d{3})+(?!\\d))/g, ",")
        + ' ' + markerHtml({symbol: "\u25b3", text: "forecast", favorable: null});
    });
  }
  ["budget-cash", "budget-eff", "budget-target"].forEach(function (id) {
    var el = document.getElementById(id);
    if (el) el.addEventListener("input", budgetRecompute);
  });
  ["fc-growth", "fc-rate", "fc-plan"].forEach(function (id) {
    var el = document.getElementById(id);
    if (el) el.addEventListener("input", forecastRecompute);
    if (el && el.tagName === "SELECT") el.addEventListener("change", forecastRecompute);
  });

"""
from viz_fpa_common import _link  # noqa: E402

_link(globals())

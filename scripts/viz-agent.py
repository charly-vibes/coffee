#!/usr/bin/env python3
"""
viz-agent.py — dashboard "Comportamiento del agente".

Purpose: presentar, con selector de período, las métricas del talk de Armin
Ronacher ("Build the Workshop Before the Software Factory") sobre los datos
locales: prompts/día, duración de tarea (P50/P90/P99), tiempo activo del
agente, abortos/errores/reintentos (proxy de pushback), repos creados y
repos mantenidos, más la escala de automatización.

Responsibilities:
- leer `data/agent_metrics.json` (tracker nuevo, series de tareas) y
  `data/usage_report_v3.json` (volumen y proyectos del reporte);
- pre-calcular TODAS las ventanas (FPA-090: período completo, YTD, año,
  trimestre, mes y todo rango contiguo ≥2 meses) con percentiles exactos
  sobre las tareas de la ventana — el JS solo elige una vista ya calculada,
  nunca promedia P90 mensuales;
- emitir `data/agent-dashboard.html` autocontenido (tema compartido,
  back-link, tabs no: una vista con selector), con provenance por cifra y
  `n/a` con razón donde la fuente no alcanza.

Rationale: mismo patrón que viz-fpa.py (modelo JSON inline + selector de
períodos pre-calculados) para que el dashboard sea consistente con el resto
del sitio y chequeable sin recargar; stdlib-only.
"""

from __future__ import annotations

import json
import sys
from bisect import bisect_left, bisect_right
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
import site_theme  # noqa: E402

METRICS = ROOT / "data" / "agent_metrics.json"
REPORT = ROOT / "data" / "usage_report_v3.json"
CONFIG = ROOT / "config" / "fpa.json"
OUT = ROOT / "data" / "agent-dashboard.html"

MESES = ["ene", "feb", "mar", "abr", "may", "jun", "jul", "ago", "sep",
         "oct", "nov", "dic"]

# Valores de referencia del talk (para el comparador visual).
TALK = {
    "p90_task": "2,5 min (dic) → 14+ min (últimos modelos)",
    "prompts_day": "~120–190 por día, plano en el año",
    "agent_time": "de <1 jornada a varias jornadas por día (curva exponencial)",
    "repos_created": "141 repos git en el año",
    "repos_maintained": "10–15 mantenidos (commit >5 días y retorno)",
    "pushback": "correcciones y abortos bajan con cada generación de modelo",
}


def load_json(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def pctl(sorted_vals, p):
    if not sorted_vals:
        return None
    return sorted_vals[int(p * (len(sorted_vals) - 1))]


def month_label(ym):
    y, m = ym.split("-")
    return f"{MESES[int(m) - 1]} {y[2:]}"


# ----------------------------------------------------------------------
# Ventanas (FPA-090): pre-calculadas, el selector solo elige
# ----------------------------------------------------------------------

def windows(months):
    out = [("all", "Todo el período", list(months))]
    for y in sorted({ym[:4] for ym in months}):
        wy = [ym for ym in months if ym.startswith(y)]
        out.append((f"ytd:{y}", f"YTD {y}", wy))
        for q in range(1, 5):
            qms = [f"{y}-{m:02d}" for m in range(3 * q - 2, 3 * q + 1)
                   if f"{y}-{m:02d}" in months]
            if qms:
                out.append((f"q:{y}Q{q}", f"Q{q} {y[2:]}", qms))
    for ym in months:
        out.append((f"month:{ym}", month_label(ym), [ym]))
    known = {k for k, *_ in out}
    for i in range(len(months)):
        for j in range(i + 2, len(months) + 1):
            window = months[i:j]
            key = f"range:{window[0]}..{window[-1]}"
            if key not in known:
                out.append((key, f"{month_label(window[0])} → {month_label(window[-1])}",
                            window))
    return out


# ----------------------------------------------------------------------
# Agregaciones
# ----------------------------------------------------------------------

def month_aggregates(metrics, report, arr):
    """Serie mensual global (para gráficos): prompts, horas de agente,
    tareas, P50/P90 exactos desde las series, y volumen de API del reporte."""
    out = {}
    months = sorted(report["monthly"].keys())
    for ym in months:
        d = metrics["daily"]
        days = [day for day in d if day.startswith(ym)]
        prompts = sum(d[x]["prompts"] for x in days)
        agent_s = sum(d[x]["agent_s"] for x in days)
        first = bisect_left(metrics["days"], f"{ym}-01")
        last = bisect_right(metrics["days"], f"{ym}-31") - 1
        secs = sorted(arr["s"][i] for i, d in enumerate(arr["day"])
                      if first <= d <= last) if last >= first else []
        span = sum(arr["sp"][i] for i, d in enumerate(arr["day"])
                   if first <= d <= last) if last >= first else 0
        out[ym] = {
            "ym": ym, "label": month_label(ym),
            "prompts": prompts,
            "active_days": len(days),
            "prompts_per_day": round(prompts / len(days), 1) if days else None,
            "agent_h": round(agent_s / 3600, 1),
            "span_h": round(span / 3600, 1),
            "tasks": len(secs),
            "p50_s": round(pctl(secs, .5), 1) if secs else None,
            "p90_s": round(pctl(secs, .9), 1) if secs else None,
            "interactions": (report["monthly"].get(ym) or {}).get("interactions"),
        }
    return out


def repo_window(repos, months_window, cfg_meta):
    """Repos creados y mantenidos DENTRO de la ventana.

    - creado: primer commit cae en la ventana;
    - mantenido: > min_commit_days días con commit en la ventana y commits en
      >= min_returning_months meses distintos de la ventana (el "vuelve").
    """
    lo, hi = min(months_window), max(months_window)
    min_days = int(cfg_meta.get("min_commit_days") or 5)
    min_months = int(cfg_meta.get("min_returning_months") or 2)
    created = sum(1 for r in repos if lo <= r["first_commit"][:7] <= hi)
    maintained, returning = 0, 0
    for r in repos:
        in_win = {m: n for m, n in r["days_by_month"].items() if lo <= m <= hi}
        if not in_win:
            continue
        if sum(in_win.values()) > min_days:
            maintained += 1
            if len(in_win) >= min_months:
                returning += 1
    return {"created": created, "maintained": maintained,
            "returning": returning}


def window_metrics(metrics, monthly, tasks_arrays, day_ix, models, report,
                   key, label, months_window, repo_meta):
    """Métricas exactas de una ventana: percentiles sobre las tareas."""
    # rango de índices de días de la ventana (las tareas pueden empezar
    # después del inicio del reporte: bisect, no lookup directo)
    days_all = metrics["days"]
    first = bisect_left(days_all, f"{months_window[0]}-01")
    last = bisect_right(days_all, f"{months_window[-1]}-31") - 1
    idx = [i for i, d in enumerate(tasks_arrays["day"]) if first <= d <= last]
    secs = sorted(tasks_arrays["s"][i] for i in idx)
    spans = sum(tasks_arrays["sp"][i] for i in idx)
    flags = [tasks_arrays["f"][i] for i in idx]
    daily = metrics["daily"]
    days = [d for d in metrics["days"] if months_window[0] <= d[:7] <= months_window[-1]]
    active_days = len([d for d in days if daily.get(d, {}).get("prompts")])
    prompts = sum(daily.get(d, {}).get("prompts", 0) for d in days)
    agent_s = sum(daily.get(d, {}).get("agent_s", 0.0) for d in days)
    turns = sum(daily.get(d, {}).get("turns", 0) for d in days)
    n_tasks = len(secs)
    n_abort = sum(1 for f in flags if f & 1)
    n_error = sum(1 for f in flags if f & 2)
    n_tool = sum(1 for f in flags if f & 4)
    n_retry = sum(1 for f in flags if f & 8)
    cal_days = _calendar_days(months_window, metrics)
    rw = repo_window(metrics["repos"], months_window, repo_meta)
    interactions = sum((report["monthly"].get(m) or {}).get("interactions", 0)
                       for m in months_window)
    bulk_threshold = int(repo_meta.get("bulk_day_interactions") or 5000)
    bulk_days = sum(1 for d, v in report["daily"].items()
                    if months_window[0] <= d[:7] <= months_window[-1]
                    and v["interactions"] >= bulk_threshold)
    top_day = max(((d, v["interactions"]) for d, v in report["daily"].items()
                   if months_window[0] <= d[:7] <= months_window[-1]),
                  default=(None, 0), key=lambda x: x[1])
    return {
        "key": key, "label": label, "months": months_window,
        "prompts": prompts,
        "tasks": n_tasks,
        "active_days": active_days,
        "calendar_days": cal_days,
        "prompts_per_active_day": round(prompts / active_days, 1) if active_days else None,
        "prompts_per_calendar_day": round(prompts / cal_days, 1) if cal_days else None,
        "p50_s": round(pctl(secs, .5), 1) if secs else None,
        "p90_s": round(pctl(secs, .9), 1) if secs else None,
        "p99_s": round(pctl(secs, .99), 1) if secs else None,
        "agent_h": round(agent_s / 3600, 1),
        "span_h": round(spans / 3600, 1),
        "agent_h_per_active_day": round(agent_s / 3600 / active_days, 2) if active_days else None,
        "workdays_per_active_day": round(agent_s / 3600 / 8 / active_days, 2) if active_days else None,
        "turns": turns,
        "aborted": n_abort,
        "errors": n_error,
        "tool_errors": n_tool,
        "retries": n_retry,
        "aborts_per_100_tasks": round(100 * n_abort / n_tasks, 2) if n_tasks else None,
        "errors_per_100_tasks": round(100 * n_error / n_tasks, 2) if n_tasks else None,
        "retries_per_100_prompts": round(100 * n_retry / prompts, 2) if prompts else None,
        "repos_created": rw["created"],
        "repos_maintained": rw["maintained"],
        "repos_returning": rw["returning"],
        "repos_total": len(metrics["repos"]),
        "interactions": interactions,
        "bulk_days": bulk_days,
        "peak_day": top_day[0],
        "peak_day_interactions": top_day[1],
        "by_model": _by_model(tasks_arrays, idx, models),
        "monthly": [monthly[m] for m in months_window if m in monthly],
    }


def _calendar_days(months_window, metrics):
    start = metrics["metadata"]["date_range"]["start"]
    end = metrics["metadata"]["date_range"]["end"]
    lo = max(start, f"{months_window[0]}-01")
    y, m = months_window[-1].split("-")
    month_end = date(int(y) + (int(m) == 12), int(m) % 12 + 1, 1).toordinal() - 1
    hi = min(end, date.fromordinal(month_end).isoformat())
    return max(0, date.fromisoformat(hi).toordinal()
               - date.fromisoformat(lo).toordinal() + 1)


def _by_model(tasks_arrays, idx, models, top=8):
    """Tareas, P90 y tasas por modelo dentro de la ventana (el eje del talk:
    pushback por generación de modelo)."""
    buckets = {}
    for i in idx:
        mi = tasks_arrays["model"][i]
        b = buckets.setdefault(mi, {"tasks": 0, "secs": [], "aborts": 0,
                                    "errors": 0, "retries": 0})
        b["tasks"] += 1
        b["secs"].append(tasks_arrays["s"][i])
        f = tasks_arrays["f"][i]
        b["aborts"] += 1 if f & 1 else 0
        b["errors"] += 1 if f & 2 else 0
        b["retries"] += 1 if f & 8 else 0
    rows = []
    for mi, b in sorted(buckets.items(), key=lambda kv: -kv[1]["tasks"])[:top]:
        s = sorted(b["secs"])
        rows.append({
            "model": models[mi] if mi < len(models) else "?",
            "tasks": b["tasks"],
            "p50_s": round(pctl(s, .5), 1),
            "p90_s": round(pctl(s, .9), 1),
            "aborts_per_100": round(100 * b["aborts"] / b["tasks"], 2),
            "errors_per_100": round(100 * b["errors"] / b["tasks"], 2),
            "retries_per_100": round(100 * b["retries"] / b["tasks"], 2),
        })
    return rows


def build_model(metrics, report):
    meta = metrics["metadata"]
    cfg = {}
    if CONFIG.exists():
        cfg = load_json(CONFIG).get("agent_behavior") or {}
    repo_meta = {**cfg, "min_commit_days": meta.get("min_commit_days"),
                 "min_returning_months": meta.get("min_returning_months")}
    day_ix = {"first": {}, "last": {}}
    for i, d in enumerate(metrics["days"]):
        day_ix["first"].setdefault(d[:7], i)
        day_ix["last"][d[:7]] = i
    arr = metrics["tasks"]
    monthly = month_aggregates(metrics, report, arr)
    months = sorted(report["monthly"].keys())
    views = {}
    for key, label, mwin in windows(months):
        views[key] = window_metrics(metrics, monthly, arr, day_ix,
                                    metrics["models"], report, key, label,
                                    mwin, repo_meta)
    return {
        "meta": {
            "generated": meta.get("last_task"),
            "period": meta["date_range"],
            "task_definition": meta["task_definition"],
            "gap_cap_minutes": meta["gap_cap_minutes"],
            "retry_window_minutes": meta["retry_window_minutes"],
            "provenance": meta["provenance"],
            "coverage": meta["coverage"],
            "reasons": meta["reasons"],
            "sources": metrics["sources"],
            "repos_total": len(metrics["repos"]),
            "min_commit_days": repo_meta["min_commit_days"],
            "min_returning_months": repo_meta["min_returning_months"],
            "bulk_day_interactions": repo_meta.get("bulk_day_interactions"),
            "talk": TALK,
        },
        "windows": [{"key": k, "label": lab}
                    for k, lab, _m in windows(sorted(report["monthly"].keys()))],
        "views": views,
    }


# ----------------------------------------------------------------------
# Render
# ----------------------------------------------------------------------

CSS = """
:root { %(vars)s }
%(base)s
body { margin: 0; background: var(--bg); color: var(--fg);
       font-family: var(--font-stack); font-size: 13px; line-height: 1.45; }
header.site { background: var(--navy); color: #fff; padding: 10px 14px; }
header.site h1 { font-size: 20px; margin: 0 0 2px; letter-spacing: .5px; }
header.site .meta { margin: 0; font-size: 11px; color: var(--bg); }
header.site a { color: var(--yellow); }
main { max-width: 960px; margin: 0 auto; padding: 12px 1rem 3rem; }
nav.topbar { display: flex; gap: .6rem; align-items: center; flex-wrap: wrap;
             background: var(--panel); border: 2px outset #fff;
             padding: .5rem .6rem; margin: 10px 0; position: sticky; top: 0; z-index: 5; }
nav.topbar select { font: inherit; min-height: 36px; }
section.block { background: var(--panel); border: 2px outset #fff; padding: .8rem .9rem;
                margin: 0 0 .9rem; }
section.block h2 { font-size: 14px; margin: 0 0 .5rem; color: var(--navy);
                   border-bottom: 1px solid var(--line); padding-bottom: .25rem; }
.cards { display: grid; grid-template-columns: repeat(auto-fit, minmax(200px, 1fr)); gap: .6rem; }
.card { border: 1px solid var(--line); padding: .5rem .6rem; background: #fff; }
.card h3 { font-size: 12px; margin: 0 0 .2rem; color: var(--muted); font-weight: normal; }
.card .val { font-size: 22px; font-weight: bold; color: var(--navy); }
.card .sub { font-size: 11px; color: var(--muted); }
.card .ref { font-size: 11px; color: var(--accent); margin-top: .2rem; }
.na { color: var(--accent); font-style: italic; }
.reason { font-size: 11px; color: var(--muted); margin: .2rem 0 0; }
[data-provenance]::after { font-size: 9px; vertical-align: super; margin-left: 2px;
                           color: var(--muted); }
[data-provenance="derived"]::after { content: "d"; }
[data-provenance="reported"]::after { content: "r"; }
[data-provenance="missing"]::after { content: "n/a"; }
[data-provenance="assumed"]::after { content: "a"; }
#monthly, #by-model, .scroll { overflow-x: auto; }
table { border-collapse: collapse; width: 100%%; margin: .4rem 0; font-size: 12px; }
th, td { border: 1px solid var(--line); padding: .2rem .35rem; text-align: right; }
th:first-child, td:first-child { text-align: left; }
thead th { background: var(--bg); }
tfoot td { font-weight: bold; }
figure { margin: .4rem 0 0; }
figcaption { font-size: 11px; color: var(--muted); }
svg { max-width: 100%%; height: auto; }
footer.site { max-width: 960px; margin: 0 auto; padding: 0 1rem 2rem;
              font-size: 11px; color: var(--muted); }
ul.notes { margin: .3rem 0 .3rem 1.1rem; padding: 0; font-size: 12px; }
ul.notes li { margin: .15rem 0; }
"""

JS = """
(function () {
  "use strict";
  var MODEL = JSON.parse(document.getElementById("agent-model").textContent);
  function esc(s) {
    return String(s).replace(/[&<>"']/g, function (c) {
      return {"&": "&amp;", "<": "&lt;", ">": "&gt;", "'": "&#39;"}[c] || "&quot;";
    });
  }
  function fmtMin(secs) {
    if (secs === null || secs === undefined) return null;
    var m = secs / 60;
    if (m < 1) return Math.round(secs) + " s";
    if (m < 90) return m.toFixed(1).replace(".", ",") + " min";
    return (m / 60).toFixed(1).replace(".", ",") + " h";
  }
  function fmtNum(n) {
    if (n === null || n === undefined) return null;
    return n.toLocaleString("es-AR");
  }
  function card(cfg) {
    var val = cfg.value === null || cfg.value === undefined
      ? '<span class="val na">n/a</span>'
      : '<span class="val">' + esc(cfg.value) + "</span>";
    var reason = (cfg.reason && (cfg.value === null || cfg.value === undefined))
      ? '<p class="reason">' + esc(cfg.reason) + "</p>" : "";
    var sub = cfg.sub ? '<div class="sub">' + esc(cfg.sub) + "</div>" : "";
    var ref = cfg.ref ? '<div class="ref">talk: ' + esc(cfg.ref) + "</div>" : "";
    return '<div class="card" data-provenance="' + esc(cfg.prov || "derived") + '">'
      + "<h3>" + esc(cfg.label) + "</h3>" + val + sub + reason + ref + "</div>";
  }
  function renderKpis(v) {
    var m = MODEL.meta;
    return [
      card({label: "Prompts por día activo", value: v.prompts_per_active_day,
            sub: fmtNum(v.prompts) + " prompts · " + v.active_days + " días con actividad",
            ref: m.talk.prompts_day}),
      card({label: "Duración de tarea P90", value: fmtMin(v.p90_s),
            sub: "P50 " + fmtMin(v.p50_s) + " · P99 " + fmtMin(v.p99_s)
                 + " · " + fmtNum(v.tasks) + " tareas",
            ref: m.talk.p90_task}),
      card({label: "Tiempo activo del agente", value: v.agent_h,
            sub: (v.agent_h_per_active_day === null ? "n/a" : v.agent_h_per_active_day + " h por día activo")
                 + (v.workdays_per_active_day === null ? "" : " · " + v.workdays_per_active_day + " jornadas de 8 h")
                 + " · " + v.span_h + " h de reloj en tarea (incluye esperas)",
            ref: m.talk.agent_time}),
      card({label: "Abortos / errores", value: (v.aborts_per_100_tasks === null ? null : v.aborts_per_100_tasks),
            sub: v.aborted + " abortos · " + v.errors + " errores · " + v.tool_errors + " tool errors",
            ref: m.talk.pushback, prov: "reported"}),
      card({label: "Reintentos tras fallo", value: v.retries_per_100_prompts,
            sub: fmtNum(v.retries) + " de " + fmtNum(v.prompts) + " prompts",
            ref: m.talk.pushback, prov: "derived"}),
      card({label: "Repos creados en la ventana", value: fmtNum(v.repos_created),
            sub: fmtNum(v.repos_total) + " repos en el workspace",
            ref: m.talk.repos_created, prov: "reported"}),
      card({label: "Repos mantenidos", value: fmtNum(v.repos_maintained),
            sub: ">" + m.min_commit_days + " días con commit · "
                 + fmtNum(v.repos_returning) + " vuelven en ≥" + m.min_returning_months + " meses",
            ref: m.talk.repos_maintained, prov: "reported"}),
      card({label: "Escala de automatización", value: fmtNum(v.peak_day_interactions),
            sub: "peak del día " + esc(v.peak_day || "—") + " · "
                 + v.bulk_days + " días ≥" + fmtNum(m.bulk_day_interactions) + " interacciones",
            ref: "miles de issues por noche", prov: "reported"})
    ].join("");
  }
  function renderMonthly(v) {
    var rows = v.monthly.map(function (r) {
      return "<tr><td>" + esc(r.label) + "</td><td>" + fmtNum(r.prompts) + "</td>"
        + "<td>" + (r.prompts_per_day === null ? "—" : r.prompts_per_day) + "</td>"
        + "<td>" + (r.p50_s === null ? "—" : (r.p50_s / 60).toFixed(1)) + "</td>"
        + "<td>" + (r.p90_s === null ? "—" : (r.p90_s / 60).toFixed(1)) + "</td>"
        + "<td>" + r.agent_h + "</td><td>" + r.span_h + "</td><td>" + fmtNum(r.tasks) + "</td>"
        + "<td>" + fmtNum(r.interactions) + "</td></tr>";
    }).join("");
    var tot = "<tr><td>total</td><td>" + fmtNum(v.prompts) + "</td><td>"
      + (v.prompts_per_active_day === null ? "—" : v.prompts_per_active_day)
      + "</td><td>" + (v.p50_s === null ? "—" : (v.p50_s / 60).toFixed(1))
      + "</td><td>" + (v.p90_s === null ? "—" : (v.p90_s / 60).toFixed(1))
      + "</td><td>" + v.agent_h + "</td><td>" + v.span_h + "</td><td>" + fmtNum(v.tasks) + "</td><td>"
      + fmtNum(v.interactions) + "</td></tr>";
    return '<table><thead><tr><th>Mes</th><th>Prompts</th><th>Prompts/día</th>'
      + "<th>P50 (min)</th><th>P90 (min)</th><th>Horas agente</th><th>Horas reloj</th><th>Tareas</th>"
      + "<th>Interacciones API</th></tr></thead><tbody>" + rows + "</tbody><tfoot>"
      + tot + "</tfoot></table>";
  }
  function renderModels(v) {
    if (!v.by_model.length) return '<p class="reason">sin tareas en la ventana</p>';
    var rows = v.by_model.map(function (r) {
      return "<tr><td>" + esc(r.model) + "</td><td>" + fmtNum(r.tasks) + "</td><td>"
        + (r.p90_s === null ? "—" : (r.p90_s / 60).toFixed(1)) + "</td><td>"
        + r.aborts_per_100 + "</td><td>" + r.errors_per_100 + "</td><td>"
        + r.retries_per_100 + "</td></tr>";
    }).join("");
    return '<table><thead><tr><th>Modelo</th><th>Tareas</th><th>P90 (min)</th>'
      + "<th>Abortos/100</th><th>Errores/100</th><th>Reintentos/100</th></tr></thead>"
      + "<tbody>" + rows + "</tbody></table>"
      + '<p class="reason">Solo tareas con turnos atribuibles a un modelo. '
      + "Comparar generaciones: mismo harness, distintas columnas.</p>";
  }
  function barChart(series, valueOf, color, unit) {
    var vals = series.map(valueOf).filter(function (x) { return x !== null; });
    if (!vals.length) return '<p class="reason">sin datos en la ventana</p>';
    var hi = Math.max.apply(null, vals) || 1;
    var w = 720, h = 160, pad = 26, bw = (w - pad * 2) / series.length;
    var bars = series.map(function (r, i) {
      var v = valueOf(r);
      if (v === null) return "";
      var bh = (h - pad * 2) * (v / hi);
      var x = pad + i * bw + bw * 0.12, y = h - pad - bh, bwi = bw * 0.76;
      return '<rect x="' + x.toFixed(1) + '" y="' + y.toFixed(1) + '" width="'
        + bwi.toFixed(1) + '" height="' + Math.max(bh, 1).toFixed(1) + '" fill="'
        + color + '"><title>' + esc(r.label) + ": " + esc(String(Math.round(v * 10) / 10))
        + " " + unit + "</title></rect>"
        + '<text x="' + (x + bwi / 2).toFixed(1) + '" y="' + (h - pad + 12)
        + '" font-size="9" text-anchor="middle" fill="#404040">'
        + esc(r.label) + "</text>";
    }).join("");
    return '<svg viewBox="0 0 ' + w + " " + h + '" role="img">'
      + '<line x1="' + pad + '" y1="' + (h - pad) + '" x2="' + (w - pad)
      + '" y2="' + (h - pad) + '" stroke="#c0c0c0"/>'
      + '<text x="' + pad + '" y="14" font-size="10" fill="#404040">máx '
      + (Math.round(hi * 10) / 10) + " " + unit + "</text>" + bars + "</svg>";
  }
  function renderAll(key) {
    var v = MODEL.views[key];
    if (!v) return;
    document.getElementById("kpis").innerHTML = renderKpis(v);
    document.getElementById("monthly").innerHTML = renderMonthly(v);
    document.getElementById("by-model").innerHTML = renderModels(v);
    document.getElementById("chart-p90").innerHTML =
      barChart(v.monthly, function (r) { return r.p90_s === null ? null : r.p90_s / 60; },
               "#800000", "min");
    document.getElementById("chart-prompts").innerHTML =
      barChart(v.monthly, function (r) { return r.prompts_per_day; }, "#000080",
               "prompts/día");
    document.getElementById("chart-hours").innerHTML =
      barChart(v.monthly, function (r) { return r.agent_h; }, "#008080", "h");
    var url = new URL(window.location.href);
    url.hash = key;
    history.replaceState(null, "", url.toString());
    var r90 = document.getElementById("readout-p90");
    if (r90) r90.textContent = "P90 " + (fmtMin(v.p90_s) || "n/a")
      + " · P50 " + (fmtMin(v.p50_s) || "n/a") + " · " + fmtNum(v.tasks) + " tareas";
    document.getElementById("period-readout").textContent =
      v.label + " · " + v.months[0] + " → " + v.months[v.months.length - 1];
  }
  var sel = document.getElementById("period-select");
  function current() {
    var h = window.location.hash.slice(1);
    if (h && MODEL.views[h]) return h;
    if (sel && MODEL.views[sel.value]) return sel.value;
    return MODEL.windows[0].key;
  }
  if (sel) {
    sel.value = current();
    sel.addEventListener("change", function () { renderAll(sel.value); });
  }
  window.addEventListener("hashchange", function () {
    var k = current();
    if (sel) sel.value = k;
    renderAll(k);
  });
  renderAll(current());
})();
"""


def render(model):
    m = model["meta"]
    opts = "\n".join(
        f'    <option value="{w["key"]}">{w["label"]}</option>'
        for w in model["windows"])
    coverage_rows = "\n".join(
        "<tr><td>{}</td><td>{}</td><td>{}</td><td>{}</td><td>{}</td></tr>".format(
            c["source"],
            "sí" if c["turn_level"] else "no",
            "sí" if c["abort_signal"] else "no",
            "—" if c["sessions"] is None else c["sessions"],
            c.get("note") or "")
        for c in m["coverage"])
    reasons = "\n".join(
        f"<li><strong>{k}:</strong> {v}</li>" for k, v in m["reasons"].items())
    css = CSS % {"vars": "\n".join(f"{k}: {v};" for k, v in site_theme.TOKENS.items()),
                 "base": site_theme.base_css()}
    return f"""<!DOCTYPE html>
<html lang="es">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta name="robots" content="noindex, nofollow">
<title>Comportamiento del agente — coffee</title>
<style>{css}</style>
</head>
<body>
{site_theme.back_link()}
<header class="site">
  <h1>Comportamiento del agente</h1>
  <p class="meta">Métricas del talk de Armin Ronacher sobre datos locales ·
  tareas {m["period"]["start"]} → {m["period"]["end"]} ·
  tope de gap {m["gap_cap_minutes"]} min · ventana de reintento
  {m["retry_window_minutes"]} min</p>
</header>
<main>
<nav class="topbar" aria-label="Período">
  <label for="period-select">Período</label>
  <select id="period-select">
{opts}
  </select>
  <span id="period-readout" aria-live="polite" class="small"></span>
</nav>

<section class="block">
  <h2>Los números del talk, sobre tus datos</h2>
  <div class="cards" id="kpis"></div>
  <p class="reason">Cada cifra lleva provenance: r = reportado por el log,
  d = derivado (tarea reconstruida), n/a = ausente con razón. La referencia
  “talk: …” es el valor que Ronacher mostró, no un objetivo.</p>
</section>

<section class="block">
  <h2>Duración de tarea P90 por mes (min)</h2>
  <figure id="chart-p90"></figure>
  <figcaption>P90 de la ventana elegida: <span id="readout-p90"></span></figcaption>
</section>

<section class="block">
  <h2>Prompts por día activo y horas de agente por mes</h2>
  <figure id="chart-prompts"></figure>
  <figure id="chart-hours"></figure>
  <figcaption>El talk las muestra planas (prompts) y exponenciales (horas):
  acá se recalculan sobre la ventana.</figcaption>
</section>

<section class="block">
  <h2>Detalle mensual de la ventana</h2>
  <div id="monthly"></div>
</section>

<section class="block">
  <h2>Por modelo (la comparación del talk: pushback por generación)</h2>
  <div id="by-model"></div>
</section>

<section class="block">
  <h2>Cobertura por fuente</h2>
  <table>
    <thead><tr><th>Fuente</th><th>Turnos con timestamp</th><th>Señal de aborto</th>
    <th>Sesiones</th><th>Nota</th></tr></thead>
    <tbody>
{coverage_rows}
    </tbody>
  </table>
  <h3>Definición de tarea</h3>
  <p>{m["task_definition"]}</p>
  <h3>Límites declarados</h3>
  <ul class="notes">
{reasons}
    <li><strong>Volumen del reporte:</strong> “interacciones” son llamadas de
    API (incluye tool calls); no se comparan 1:1 con los prompts del talk.</li>
  </ul>
</section>
</main>
<footer class="site">
  <p>coffee · comportamiento del agente · generado por viz-agent.py
  (stdlib-only, SVG inline) · fuente: data/agent_metrics.json</p>
</footer>
<script type="application/json" id="agent-model">{json.dumps(model, ensure_ascii=False, separators=(",", ":"))}</script>
<script>{JS}</script>
</body>
</html>
"""


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    out = OUT
    if "--output" in argv:
        out = Path(argv[argv.index("--output") + 1])
    metrics = load_json(METRICS)
    report = load_json(REPORT)
    model = build_model(metrics, report)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(render(model), encoding="utf-8")
    print(f"escrito: {out.relative_to(ROOT) if out.is_absolute() else out} "
          f"· ventanas: {len(model['views'])}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

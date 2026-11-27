#!/usr/bin/env python3
"""viz-agent.py — dashboard "Comportamiento del agente".
Presenta, con selector de período, las métricas del talk de Armin Ronacher
sobre los datos locales. Pre-calcula TODAS las ventanas (FPA-090) con
percentiles exactos sobre las tareas — el JS solo elige una vista ya
calculada, nunca promedia P90 mensuales — y emite data/agent-dashboard.html
autocontenido (tema compartido vía site_theme). Mismo patrón que viz-fpa.py.
"""
from __future__ import annotations
import json
import sys
from bisect import bisect_left, bisect_right
from collections import namedtuple
from pathlib import Path
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
import site_theme  # noqa: E402
METRICS = ROOT / "data" / "agent_metrics.json"
REPORT = ROOT / "data" / "usage_report_v3.json"
CONFIG = ROOT / "config" / "fpa.json"
OUT = ROOT / "data" / "agent-dashboard.html"
README = ROOT / "README.md"
DOC_BEGIN = "<!-- AGENT-DOCS:BEGIN -->"
DOC_END = "<!-- AGENT-DOCS:END -->"
MESES = ["ene", "feb", "mar", "abr", "may", "jun", "jul", "ago", "sep", "oct", "nov", "dic"]
# Valores de referencia del talk (para el comparador visual).
TALK = {"p90_task": "2,5 min (dic) → 14+ min (últimos modelos)",
        "prompts_day": "~120–190 por día, plano en el año",
        "agent_time": "de <1 jornada a varias jornadas por día (curva exponencial)",
        "repos_created": "141 repos git en el año",
        "repos_maintained": "10–15 mantenidos (commit >5 días y retorno)",
        "pushback": "correcciones y abortos bajan con cada generación de modelo"}
_PCTLS = (("p50_s", .5), ("p90_s", .9), ("p99_s", .99))
_TASK_BITS = ((1, "aborted"), (2, "errors"), (4, "tool_errors"), (8, "retries"))
_MODEL_BITS = ((1, "aborts"), (2, "errors"), (8, "retries"))
def load_json(path): return json.loads(Path(path).read_text(encoding="utf-8"))
def pctl(sorted_vals, p): return None if not sorted_vals else sorted_vals[int(p * (len(sorted_vals) - 1))]
def month_label(ym): return f"{MESES[int(ym.split('-')[1]) - 1]} {ym[:4][2:]}"
def _task_idx(arr, first, last):  # bisect: no lookup directo
    return [i for i, d in enumerate(arr["day"]) if first <= d <= last]
def _pctls(secs, keys=_PCTLS):
    return {k: round(pctl(secs, p), 1) if secs else None for k, p in keys}
def _per_100(num, den): return round(100 * num / den, 2) if den else None
def _flag_counts(flags):
    return {name: sum(1 for f in flags if f & bit) for bit, name in _TASK_BITS}
def _quarter_windows(y, months):
    return [(f"q:{y}Q{q}", f"Q{q} {y[2:]}", qms)
            for q in range(1, 5)
            if (qms := [f"{y}-{m:02d}" for m in range(3 * q - 2, 3 * q + 1)
                        if f"{y}-{m:02d}" in months])]
def _year_windows(months):
    out = []
    for y in sorted({ym[:4] for ym in months}):
        out.append((f"ytd:{y}", f"YTD {y}", [ym for ym in months if ym.startswith(y)]))
        out += _quarter_windows(y, months)
    return out
def _range_windows(months, known):
    """Todo rango contiguo de >= 2 meses aún sin key propia."""
    return [(f"range:{w[0]}..{w[-1]}", f"{month_label(w[0])} → {month_label(w[-1])}", w)
            for i in range(len(months)) for j in range(i + 2, len(months) + 1)
            if (w := months[i:j]) and f"range:{w[0]}..{w[-1]}" not in known]
def windows(months):
    out = [("all", "Todo el período", list(months))]; out += _year_windows(months)
    out += [(f"month:{ym}", month_label(ym), [ym]) for ym in months]
    out += _range_windows(months, {k for k, *_ in out})
    return out
def _month_row(metrics, report, arr, ym):
    """Serie de un mes (para gráficos): prompts, horas, tareas, P50/P90 exactos."""
    daily = metrics["daily"]; days = [d for d in daily if d.startswith(ym)]
    first = bisect_left(metrics["days"], f"{ym}-01"); last = bisect_right(metrics["days"], f"{ym}-31") - 1
    idx = _task_idx(arr, first, last); secs = sorted(arr["s"][i] for i in idx); span = sum(arr["sp"][i] for i in idx)
    prompts = sum(daily[d]["prompts"] for d in days); agent_s = sum(daily[d]["agent_s"] for d in days)
    return {"ym": ym, "label": month_label(ym), "prompts": prompts, "active_days": len(days),
            "prompts_per_day": round(prompts / len(days), 1) if days else None,
            "agent_h": round(agent_s / 3600, 1), "span_h": round(span / 3600, 1),
            "tasks": len(secs), **_pctls(secs, _PCTLS[:2]),
            "interactions": (report["monthly"].get(ym) or {}).get("interactions")}
def month_aggregates(metrics, report, arr):
    """Serie mensual global (gráficos), mes por mes desde las series exactas."""
    daily = metrics["daily"]
    return {ym: _month_row(metrics, report, arr, ym) for ym in sorted(report["monthly"])}
def repo_window(repos, months_window, cfg_meta):
    """Repos creados y mantenidos DENTRO de la ventana: creado = primer commit en la
    ventana; mantenido = > min_commit_days días con commit en >= min_returning_months
    meses distintos de la ventana (el "vuelve")."""
    lo, hi = min(months_window), max(months_window)
    min_days = int(cfg_meta.get("min_commit_days") or 5); min_months = int(cfg_meta.get("min_returning_months") or 2); created = sum(1 for r in repos if lo <= r["first_commit"][:7] <= hi)
    stats = [_repo_stats(r, lo, hi, (min_days, min_months)) for r in repos]
    return {"created": created, "maintained": sum(s[0] for s in stats),
            "returning": sum(s[1] for s in stats)}
def _repo_stats(r, lo, hi, mins):
    """(mantenido, vuelve) de un repo dentro de la ventana, o (0, 0)."""
    in_win = {m: n for m, n in r["days_by_month"].items() if lo <= m <= hi}
    if not in_win or sum(in_win.values()) <= mins[0]:
        return (0, 0)
    return (1, 1 if len(in_win) >= mins[1] else 0)
# Datos compartidos por todas las ventanas (evita params>4 en window_metrics).
_WinCtx = namedtuple("_WinCtx", "metrics monthly arr models report repo_meta")
def _window_tasks(ctx, mwin):
    """Índices/secs/spans/flags de las tareas de la ventana."""
    days = ctx.metrics["days"]
    first = bisect_left(days, f"{mwin[0]}-01"); last = bisect_right(days, f"{mwin[-1]}-31") - 1
    idx = _task_idx(ctx.arr, first, last); return {"idx": idx, "secs": sorted(ctx.arr["s"][i] for i in idx),
            "spans": sum(ctx.arr["sp"][i] for i in idx), "flags": [ctx.arr["f"][i] for i in idx]}
def _window_daily(ctx, mwin):
    daily = ctx.metrics["daily"]
    days = [d for d in ctx.metrics["days"] if mwin[0] <= d[:7] <= mwin[-1]]
    active = [d for d in days if daily.get(d, {}).get("prompts")]; return {"active_days": len(active),
            "prompts": sum(daily.get(d, {}).get("prompts", 0) for d in days),
            "agent_s": sum(daily.get(d, {}).get("agent_s", 0.0) for d in days),
            "turns": sum(daily.get(d, {}).get("turns", 0) for d in days)}
def _window_volume(ctx, mwin):
    """Volumen de API del reporte dentro de la ventana."""
    lo, hi = mwin[0], mwin[-1]
    daily = ctx.report["daily"]; bulk = int(ctx.repo_meta.get("bulk_day_interactions") or 5000); top = max(((d, v["interactions"]) for d, v in daily.items() if lo <= d[:7] <= hi),
              default=(None, 0), key=lambda x: x[1])
    return {"interactions": sum((ctx.report["monthly"].get(m) or {}).get("interactions", 0) for m in mwin),
            "bulk_days": sum(1 for d, v in daily.items() if lo <= d[:7] <= hi and v["interactions"] >= bulk),
            "peak_day": top[0], "peak_day_interactions": top[1]}
def window_metrics(ctx, key, label, months_window):
    """Métricas exactas de una ventana: percentiles sobre las tareas."""
    t = _window_tasks(ctx, months_window); daily = _window_daily(ctx, months_window)
    vol = _window_volume(ctx, months_window)
    flags = _flag_counts(t["flags"]); secs, n_tasks = t["secs"], len(t["secs"])
    act = daily["active_days"]
    rw = repo_window(ctx.metrics["repos"], months_window, ctx.repo_meta)
    return {"key": key, "label": label, "months": months_window,
            "prompts": daily["prompts"], "tasks": n_tasks, "active_days": act,
            "prompts_per_active_day": round(daily["prompts"] / act, 1) if act else None, **_pctls(secs),
            "agent_h": round(daily["agent_s"] / 3600, 1), "span_h": round(t["spans"] / 3600, 1),
            "agent_h_per_active_day": round(daily["agent_s"] / 3600 / act, 2) if act else None,
            "workdays_per_active_day": round(daily["agent_s"] / 3600 / 8 / act, 2) if act else None,
            "turns": daily["turns"], **flags,
            "aborts_per_100_tasks": _per_100(flags["aborted"], n_tasks),
            "errors_per_100_tasks": _per_100(flags["errors"], n_tasks),
            "retries_per_100_prompts": _per_100(flags["retries"], daily["prompts"]),
            "repos_created": rw["created"], "repos_maintained": rw["maintained"],
            "repos_returning": rw["returning"], "repos_total": len(ctx.metrics["repos"]),
            "interactions": vol["interactions"], "bulk_days": vol["bulk_days"],
            "peak_day": vol["peak_day"], "peak_day_interactions": vol["peak_day_interactions"],
            "by_model": _by_model(ctx.arr, t["idx"], ctx.models),
            "monthly": [ctx.monthly[m] for m in months_window if m in ctx.monthly]}
def _by_model(tasks_arrays, idx, models, top=8):
    """Tareas, P90 y tasas por modelo en la ventana (pushback por generación)."""
    buckets = {}
    for i in idx:
        b = buckets.setdefault(tasks_arrays["model"][i],
                               {"tasks": 0, "secs": [], "aborts": 0, "errors": 0, "retries": 0})
        b["tasks"] += 1; b["secs"].append(tasks_arrays["s"][i])
        for bit, name in _MODEL_BITS:
            b[name] += 1 if tasks_arrays["f"][i] & bit else 0
    rows = []
    for mi, b in sorted(buckets.items(), key=lambda kv: -kv[1]["tasks"])[:top]:
        rows.append({"model": models[mi] if mi < len(models) else "?",
                     "tasks": b["tasks"], **_pctls(sorted(b["secs"]), _PCTLS[:2]),
                     "aborts_per_100": _per_100(b["aborts"], b["tasks"]),
                     "errors_per_100": _per_100(b["errors"], b["tasks"]),
                     "retries_per_100": _per_100(b["retries"], b["tasks"])})
    return rows
def build_model(metrics, report):
    meta = metrics["metadata"]
    cfg = load_json(CONFIG).get("agent_behavior") or {} if CONFIG.exists() else {}
    repo_meta = {**cfg, "min_commit_days": meta.get("min_commit_days"),
                 "min_returning_months": meta.get("min_returning_months")}
    arr = metrics["tasks"]; monthly = month_aggregates(metrics, report, arr)
    months = sorted(report["monthly"].keys())
    ctx = _WinCtx(metrics, monthly, arr, metrics["models"], report, repo_meta)
    views = {key: window_metrics(ctx, key, label, mwin) for key, label, mwin in windows(months)}
    return {"meta": {"generated": meta.get("last_task"), "period": meta["date_range"],
                     "first_task": meta.get("first_task"), "last_task": meta.get("last_task"),
                     "timezone": meta.get("timezone"), "orphan_turns": meta.get("orphan_turns", 0),
                     "preamble_prompts_skipped": meta.get("preamble_prompts_skipped", 0),
                     "repo_roots_missing": meta.get("repo_roots_missing") or [],
                     "task_definition": meta["task_definition"], "gap_cap_minutes": meta["gap_cap_minutes"],
                     "retry_window_minutes": meta["retry_window_minutes"], "provenance": meta["provenance"],
                     "coverage": meta["coverage"], "reasons": meta["reasons"], "sources": metrics["sources"],
                     "repos_total": len(metrics["repos"]), "min_commit_days": repo_meta["min_commit_days"],
                     "min_returning_months": repo_meta["min_returning_months"],
                     "bulk_day_interactions": repo_meta.get("bulk_day_interactions"), "talk": TALK,
                     "ops": metrics.get("ops")},
            "windows": [{"key": k, "label": lab}
                        for k, lab, _m in windows(sorted(report["monthly"].keys()))],
            "views": views}
CSS = ":root { %(vars)s }\n%(base)s\n"
_CSS_RULES = "body { margin: 0; background: var(--bg); color: var(--fg); font-family: var(--font-stack); font-size: 13px; line-height: 1.45; } header.site { background: var(--navy); color: #fff; padding: 10px 14px; } header.site h1 { font-size: 20px; margin: 0 0 2px; letter-spacing: .5px; } header.site .meta { margin: 0; font-size: 11px; color: var(--bg); } header.site a { color: var(--yellow); } main { max-width: 960px; margin: 0 auto; padding: 12px 1rem 3rem; } nav.topbar { display: flex; gap: .6rem; align-items: center; flex-wrap: wrap; background: var(--panel); border: 2px outset #fff; padding: .5rem .6rem; margin: 10px 0; position: sticky; top: 0; z-index: 5; } nav.topbar select { font: inherit; min-height: 36px; } section.block { background: var(--panel); border: 2px outset #fff; padding: .8rem .9rem; margin: 0 0 .9rem; } section.block h2 { font-size: 14px; margin: 0 0 .5rem; color: var(--navy); border-bottom: 1px solid var(--line); padding-bottom: .25rem; } .cards { display: grid; grid-template-columns: repeat(auto-fit, minmax(200px, 1fr)); gap: .6rem; } .card { border: 1px solid var(--line); padding: .5rem .6rem; background: #fff; } .card h3 { font-size: 12px; margin: 0 0 .2rem; color: var(--muted); font-weight: normal; } .card .val { font-size: 22px; font-weight: bold; color: var(--navy); } .card .sub { font-size: 11px; color: var(--muted); } .card .ref { font-size: 11px; color: var(--accent); margin-top: .2rem; } .na { color: var(--accent); font-style: italic; } .reason { font-size: 11px; color: var(--muted); margin: .2rem 0 0; } [data-provenance]::after { font-size: 9px; vertical-align: super; margin-left: 2px; color: var(--muted); } [data-provenance=\"derived\"]::after { content: \"d\"; } [data-provenance=\"reported\"]::after { content: \"r\"; } [data-provenance=\"missing\"]::after { content: \"n/a\"; } [data-provenance=\"assumed\"]::after { content: \"a\"; } #monthly, #by-model, .scroll { overflow-x: auto; } table { border-collapse: collapse; width: 100%; margin: .4rem 0; font-size: 12px; } th, td { border: 1px solid var(--line); padding: .2rem .35rem; text-align: right; } th:first-child, td:first-child { text-align: left; } thead th { background: var(--bg); } tfoot td { font-weight: bold; } figure { margin: .4rem 0 0; } figcaption { font-size: 11px; color: var(--muted); } svg { max-width: 100%; height: auto; } footer.site { max-width: 960px; margin: 0 auto; padding: 0 1rem 2rem; font-size: 11px; color: var(--muted); } ul.notes { margin: .3rem 0 .3rem 1.1rem; padding: 0; font-size: 12px; } ul.notes li { margin: .15rem 0; }"
JS = """(function () { "use strict"; var MODEL = JSON.parse(document.getElementById("agent-model").textContent); function esc(s) { return String(s).replace(/[&<>"']/g, function (c) { return {"&": "&amp;", "<": "&lt;", ">": "&gt;", "'": "&#39;"}[c] || "&quot;"; }); } function fmtMin(secs) { if (secs === null || secs === undefined) return "\\u2014"; var m = secs / 60; if (m < 1) return Math.round(secs) + " s"; if (m < 90) return m.toFixed(1).replace(".", ",") + " min"; return (m / 60).toFixed(1).replace(".", ",") + " h"; } function fmtNum(n) { if (n === null || n === undefined) return "\\u2014"; return n.toLocaleString("es-AR"); } function fmtDec(n, digits) { if (n === null || n === undefined) return "\\u2014"; return Number(n).toLocaleString("es-AR", {minimumFractionDigits: digits, maximumFractionDigits: digits}); } function joinDot(parts) { return parts.filter(function (x) { return x !== null && x !== undefined && x !== ""; }).join(" \\u00b7 "); } function card(cfg) { var val = cfg.value === null || cfg.value === undefined ? '<span class="val na">n/a</span>' : '<span class="val">' + esc(cfg.value) + "</span>"; var reason = (cfg.reason && (cfg.value === null || cfg.value === undefined)) ? '<p class="reason">' + esc(cfg.reason) + "</p>" : ""; var sub = cfg.sub ? '<div class="sub">' + esc(cfg.sub) + "</div>" : ""; var ref = cfg.ref ? '<div class="ref">talk: ' + esc(cfg.ref) + "</div>" : ""; return '<div class="card" data-provenance="' + esc(cfg.prov || "derived") + '">' + "<h3>" + esc(cfg.label) + "</h3>" + val + sub + reason + ref + "</div>"; } function renderKpis(v) { var m = MODEL.meta; var vacia = v.tasks === 0 ? "sin tareas en la ventana" : null; return [ card({label: "Prompts por día activo", value: v.prompts_per_active_day, sub: joinDot([fmtNum(v.prompts) + " prompts", v.active_days + " días con actividad"]), reason: vacia, ref: m.talk.prompts_day}), card({label: "Duración de tarea P90", value: v.p90_s === null ? null : fmtMin(v.p90_s), sub: joinDot(["P50 " + fmtMin(v.p50_s), "P99 " + fmtMin(v.p99_s), fmtNum(v.tasks) + " tareas"]), reason: vacia, ref: m.talk.p90_task}), card({label: "Tiempo activo del agente", value: fmtDec(v.agent_h, 1), sub: joinDot([ v.agent_h_per_active_day === null ? null : fmtDec(v.agent_h_per_active_day, 2) + " h por día activo", v.workdays_per_active_day === null ? null : fmtDec(v.workdays_per_active_day, 2) + " jornadas de 8 h", fmtDec(v.span_h, 1) + " h de reloj en tarea (incluye esperas)"]), reason: vacia, ref: m.talk.agent_time}), card({label: "Abortos por 100 tareas", value: v.aborts_per_100_tasks, sub: joinDot([v.aborted + " abortos", v.errors + " errores", v.tool_errors + " tool errors", v.aborts_per_100_tasks === null ? null : "solo pi registra abortos (claude-cli no)"]), reason: vacia, ref: m.talk.pushback, prov: "reported"}), card({label: "Reintentos tras fallo por 100", value: v.retries_per_100_prompts, sub: joinDot([fmtNum(v.retries) + " de " + fmtNum(v.prompts) + " prompts", "ventana " + m.retry_window_minutes + " min"]), reason: vacia, ref: m.talk.pushback, prov: "derived"}), card({label: "Repos creados en la ventana", value: fmtNum(v.repos_created), sub: fmtNum(v.repos_total) + " repos en las raíces configuradas", ref: m.talk.repos_created, prov: "reported"}), card({label: "Repos mantenidos", value: fmtNum(v.repos_maintained), sub: joinDot([ ">" + m.min_commit_days + " días con commit en la ventana", fmtNum(v.repos_returning) + " vuelven en \\u2265" + m.min_returning_months + " meses", "definición propia: no es la cadencia personal del talk"]), ref: m.talk.repos_maintained, prov: "reported"}), card({label: "Escala de automatización", value: fmtNum(v.peak_day_interactions), sub: joinDot(["peak del día " + (v.peak_day || "\\u2014"), v.bulk_days + " días \\u2265" + fmtNum(m.bulk_day_interactions) + " interacciones"]), ref: "miles de issues por noche", prov: "reported"}) ].join(""); } function renderMonthly(v) { var rows = v.monthly.map(function (r) { return "<tr><td>" + esc(r.label) + "</td><td>" + fmtNum(r.prompts) + "</td>" + "<td>" + fmtDec(r.prompts_per_day, 1) + "</td>" + "<td>" + fmtDec(r.p50_s === null ? null : r.p50_s / 60, 1) + "</td>" + "<td>" + fmtDec(r.p90_s === null ? null : r.p90_s / 60, 1) + "</td>" + "<td>" + fmtDec(r.agent_h, 1) + "</td><td>" + fmtDec(r.span_h, 1) + "</td>" + "<td>" + fmtNum(r.tasks) + "</td>" + "<td>" + fmtNum(r.interactions) + "</td></tr>"; }).join(""); var tot = "<tr><td>total</td><td>" + fmtNum(v.prompts) + "</td><td>" + fmtDec(v.prompts_per_active_day, 1) + "</td><td>" + fmtDec(v.p50_s === null ? null : v.p50_s / 60, 1) + "</td><td>" + fmtDec(v.p90_s === null ? null : v.p90_s / 60, 1) + "</td><td>" + fmtDec(v.agent_h, 1) + "</td><td>" + fmtDec(v.span_h, 1) + "</td><td>" + fmtNum(v.tasks) + "</td><td>" + fmtNum(v.interactions) + "</td></tr>"; return '<table><thead><tr><th>Mes</th><th>Prompts</th><th>Prompts/día</th>' + "<th>P50 (min)</th><th>P90 (min)</th><th>Horas agente</th><th>Horas reloj</th><th>Tareas</th>" + "<th>Interacciones API</th></tr></thead><tbody>" + rows + "</tbody><tfoot>" + tot + "</tfoot></table>"; } function renderModels(v) { if (!v.by_model.length) return '<p class="reason">sin tareas en la ventana</p>'; var rows = v.by_model.map(function (r) { return "<tr><td>" + esc(r.model) + "</td><td>" + fmtNum(r.tasks) + "</td><td>" + fmtDec(r.p90_s === null ? null : r.p90_s / 60, 1) + "</td><td>" + r.aborts_per_100 + "</td><td>" + r.errors_per_100 + "</td><td>" + r.retries_per_100 + "</td></tr>"; }).join(""); return '<table><thead><tr><th>Modelo</th><th>Tareas</th><th>P90 (min)</th>' + "<th>Abortos/100</th><th>Errores/100</th><th>Reintentos/100</th></tr></thead>" + "<tbody>" + rows + "</tbody></table>" + '<p class="reason">Solo tareas con turnos atribuibles a un modelo. ' + "Comparar generaciones: mismo harness, distintas columnas.</p>"; } function barChart(series, valueOf, color, unit, label) { var vals = series.map(valueOf).filter(function (x) { return x !== null; }); if (!vals.length) return '<p class="reason">sin datos en la ventana</p>'; var hi = Math.max.apply(null, vals) || 1; var w = 720, h = 160, pad = 26, bw = (w - pad * 2) / series.length; var bars = series.map(function (r, i) { var v = valueOf(r); if (v === null) return ""; var bh = (h - pad * 2) * (v / hi); var x = pad + i * bw + bw * 0.12, y = h - pad - bh, bwi = bw * 0.76; return '<rect x="' + x.toFixed(1) + '" y="' + y.toFixed(1) + '" width="' + bwi.toFixed(1) + '" height="' + Math.max(bh, 1).toFixed(1) + '" fill="' + color + '"><title>' + esc(r.label) + ": " + esc(String(Math.round(v * 10) / 10)) + " " + unit + "</title></rect>" + '<text x="' + (x + bwi / 2).toFixed(1) + '" y="' + (h - pad + 12) + '" font-size="9" text-anchor="middle" fill="#404040">' + esc(r.label) + "</text>"; }).join(""); return '<svg viewBox="0 0 ' + w + " " + h + '" role="img" aria-label="' + esc(label + " · " + series[0].label + " a " + series[series.length - 1].label) + '">' + '<line x1="' + pad + '" y1="' + (h - pad) + '" x2="' + (w - pad) + '" y2="' + (h - pad) + '" stroke="#c0c0c0"/>' + '<text x="' + pad + '" y="14" font-size="10" fill="#404040">máx ' + (Math.round(hi * 10) / 10) + " " + unit + "</text>" + bars + "</svg>"; } function renderAll(key) { var v = MODEL.views[key]; if (!v) return; document.getElementById("kpis").innerHTML = renderKpis(v); document.getElementById("monthly").innerHTML = renderMonthly(v); document.getElementById("by-model").innerHTML = renderModels(v); document.getElementById("chart-p90").innerHTML = barChart(v.monthly, function (r) { return r.p90_s === null ? null : r.p90_s / 60; }, "#800000", "min", "Duración de tarea P90 por mes"); document.getElementById("chart-prompts").innerHTML = barChart(v.monthly, function (r) { return r.prompts_per_day; }, "#000080", "prompts/día", "Prompts por día activo por mes"); document.getElementById("chart-hours").innerHTML = barChart(v.monthly, function (r) { return r.agent_h; }, "#008080", "h", "Horas de agente por mes"); var url = new URL(window.location.href); url.hash = key; history.replaceState(null, "", url.toString()); var r90 = document.getElementById("readout-p90"); if (r90) r90.textContent = "P90 " + (fmtMin(v.p90_s) || "n/a") + " · P50 " + (fmtMin(v.p50_s) || "n/a") + " · " + fmtNum(v.tasks) + " tareas"; document.getElementById("period-readout").textContent = v.label + " · " + v.months[0] + " → " + v.months[v.months.length - 1]; } var sel = document.getElementById("period-select"); function current() { var h = window.location.hash.slice(1); if (h && MODEL.views[h]) return h; if (sel && MODEL.views[sel.value]) return sel.value; return MODEL.windows[0].key; } if (sel) { sel.value = current(); sel.addEventListener("change", function () { renderAll(sel.value); }); } window.addEventListener("hashchange", function () { var k = current(); if (sel) sel.value = k; renderAll(k); }); renderAll(current()); })();
"""
def _period_options(wins):
    return "\n".join(f'    <option value="{w["key"]}">{w["label"]}</option>' for w in wins)
def _coverage_rows(coverage):
    def row(c):
        return "<tr><td>{}</td><td>{}</td><td>{}</td><td>{}</td><td>{}</td></tr>".format(
            c["source"], "sí" if c["turn_level"] else "no", "sí" if c["abort_signal"] else "no",
            "—" if c["sessions"] is None else c["sessions"], c.get("note") or "")
    return "\n".join(row(c) for c in coverage)
def _reason_items(reasons):
    return "\n".join(f"<li><strong>{k}:</strong> {v}</li>" for k, v in reasons.items())
def _ops_latency_section(metrics, top=12):
    """Tabla server-side de ops.latency por modelo (sin JS: stats estáticas).
    Solo fuentes con señal de duración aportan filas; sin bloque ops -> n/a."""
    lat = (metrics.get("ops") or {}).get("latency")
    if not lat:
        return '<p class="reason">n/a: bloque ops ausente (correr scripts/agent_metrics_ops.py después del tracker).</p>'
    rows = []
    for source, entry in sorted(lat["sources"].items()):
        for model, st in sorted((entry.get("models") or {}).items(),
                                key=lambda kv: -(kv[1]["n"] or 0)):
            p95 = st.get("p95_ms")
            rows.append((st["n"] or 0, "<tr><td>{}</td><td>{}</td><td>{}</td><td>{}</td><td>{}</td><td>{}</td></tr>".format(
                model, source, st["n"], _num_es_ms(st.get("p50_ms")), _num_es_ms(p95),
                "n/a: {}".format(st["reason"]) if st.get("reason") else f"{st['coverage_pct']}%")))
    rows.sort(key=lambda r: -r[0])
    if not rows:
        return '<p class="reason">n/a: ningún turno con duración.</p>'
    body = "\n".join(r[1] for r in rows[:top])
    return ('<table><thead><tr><th>Modelo</th><th>Fuente</th><th>n</th><th>P50</th><th>P95</th><th>Cobertura</th></tr></thead>'
            f"<tbody>\n{body}\n    </tbody></table>"
            '<p class="reason">Sobre TODA la población de turnos con duración (sin sampling); '
            'n = turnos con duración; cobertura = % de turnos assistant de la celda.</p>')
def _num_es_ms(ms):
    return "—" if ms is None else f"{ms / 1000:,.1f} s".replace(",", "@").replace(".", ",").replace("@", ".")
# Slices del epic coffe-a5v: cada módulo expone render_section(ops) -> HTML
# (una <section class="block">). Import con guard: un slice sin módulo se
# renderiza como n/a, no rompe el dashboard.
OPS_SLICES = [
    ("agent_metrics_ops_fallback", "fallback"),
    ("agent_metrics_ops_quota", "quota_events"),
    ("agent_metrics_ops_completion", "completion+intervention"),
    ("agent_metrics_ops_subscription", "subscription"),
    ("agent_metrics_ops_concurrency", "concurrency/stall/queue_wait"),
]
def _ops_sections(ops):
    parts = ['<section class="block"><h2>Latencia p95 por modelo (ops, población completa)</h2>'
             + _ops_latency_section({"ops": ops or {}}) + "</section>"]
    for mod_name, label in OPS_SLICES:
        try:
            import importlib
            mod = importlib.import_module(mod_name)
            parts.append(mod.render_section(ops or {}))
        except ImportError:
            parts.append(f'<section class="block"><h2>ops: {label}</h2>'
                         f'<p class="reason">n/a: módulo {mod_name} no disponible (epic coffe-a5v).</p></section>')
    return "\n".join(parts)
def _page_css():
    return CSS % {"vars": "\n".join(f"{k}: {v};" for k, v in site_theme.TOKENS.items()),
                  "base": site_theme.base_css() + _CSS_RULES}
_PAGE = """<!DOCTYPE html>
<html lang="es"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1"><meta name="robots" content="noindex, nofollow"><title>Comportamiento del agente — coffee</title><style>{css}</style></head><body>
{back_link}<header class="site"><h1>Comportamiento del agente</h1><p class="meta">Métricas del talk de Armin Ronacher sobre datos locales · tareas {first_task} → {last_task} (log con turnos) · reporte {period_start} → {period_end} · TZ {timezone} · tope de gap {gap_cap_minutes} min · ventana de reintento {retry_window_minutes} min</p></header>
<main><nav class="topbar" aria-label="Período"><label for="period-select">Período</label><select id="period-select">{opts}
  </select><span id="period-readout" aria-live="polite" class="small"></span></nav>
<section class="block"><h2>Los números del talk, sobre tus datos</h2><div class="cards" id="kpis"></div><p class="reason">Cada cifra lleva provenance: r = reportado por el log, d = derivado (tarea reconstruida), n/a = ausente con razón. La referencia “talk: …” es el valor que Ronacher mostró, no un objetivo.</p></section>
<section class="block"><h2>Duración de tarea P90 por mes (min)</h2><figure id="chart-p90"></figure><figcaption>P90 de la ventana elegida: <span id="readout-p90"></span></figcaption></section>
<section class="block"><h2>Prompts por día activo y horas de agente por mes</h2><figure id="chart-prompts"></figure><figure id="chart-hours"></figure><figcaption>El talk las muestra planas (prompts) y exponenciales (horas): acá se recalculan sobre la ventana.</figcaption></section>
<section class="block"><h2>Detalle mensual de la ventana</h2><div id="monthly"></div></section>
<section class="block"><h2>Por modelo (la comparación del talk: pushback por generación)</h2><div id="by-model"></div></section>
{ops_sections}
<section class="block"><h2>Cobertura por fuente</h2><table><thead><tr><th>Fuente</th><th>Turnos con timestamp</th><th>Señal de aborto</th><th>Sesiones</th><th>Nota</th></tr></thead><tbody>
{coverage_rows}
    </tbody></table>
<h3>Definición de tarea</h3><p>{task_definition}</p>
<h3>Límites declarados</h3><ul class="notes">
{reasons}
    <li><strong>Turnos sin prompt previo:</strong> {orphan_turns} (sesiones retomadas: no abren tarea, no se cuentan como prompt).</li>
    <li><strong>Prompts inyectados excluidos (claude-cli):</strong> {preamble_prompts_skipped} preámbulos de reanudación.</li>
    <li><strong>Raíces de repos ausentes:</strong> {repo_roots_missing}.</li>
    <li><strong>Volumen del reporte:</strong> “interacciones” son llamadas de API (incluye tool calls); no se comparan 1:1 con los prompts del talk.</li>
  </ul></section>
</main>
<footer class="site"><p>coffee · comportamiento del agente · generado por viz-agent.py (stdlib-only, SVG inline) · fuente: data/agent_metrics.json</p></footer>
<script type="application/json" id="agent-model">{model_json}</script>
<script>{js}</script>
</body>
</html>
"""
def render(model):
    m = model["meta"]
    return _PAGE.format(css=_page_css(), back_link=site_theme.back_link(),
        first_task=m["first_task"] or "—", last_task=m["last_task"] or "—",
        period_start=m["period"]["start"], period_end=m["period"]["end"],
        timezone=m["timezone"] or "UTC", gap_cap_minutes=m["gap_cap_minutes"],
        retry_window_minutes=m["retry_window_minutes"], opts=_period_options(model["windows"]),
        coverage_rows=_coverage_rows(m["coverage"]), task_definition=m["task_definition"],
        reasons=_reason_items(m["reasons"]), orphan_turns=m["orphan_turns"],
        ops_sections=_ops_sections(m.get("ops")),
        preamble_prompts_skipped=m["preamble_prompts_skipped"],
        repo_roots_missing=", ".join(m["repo_roots_missing"]) or "ninguna", model_json=json.dumps(model, ensure_ascii=False, separators=(",", ":")), js=JS)
def _num_es(value, digits=1): return "n/a" if value is None else f"{value:.{digits}f}".replace(".", ",")
def doc_figures(model):
    """Cifras headline del README, derivadas del modelo (no escritas a mano)."""
    v, m = model["views"]["all"], model["meta"]
    return [("Prompts por día activo", _num_es(v["prompts_per_active_day"], 1)),
            ("Duración de tarea P90", f"{_num_es((v['p90_s'] or 0) / 60, 1)} min"),
            ("Horas de agente por día activo", _num_es(v["agent_h_per_active_day"], 2)),
            ("Repos creados en el período", str(v["repos_created"])), ("Repos mantenidos", str(v["repos_maintained"])),
            ("Ventanas de período", str(len(model["views"]))),
            ("Tope de gap (min)", str(m["gap_cap_minutes"])), ("Ventana de reintento (min)", str(m["retry_window_minutes"])),
            ("Días con commit para mantenido", str(m["min_commit_days"]))]
def docs_block(model):
    return DOC_BEGIN + "\n" + "\n".join(f"- {l}: {v}" for l, v in doc_figures(model)) + "\n" + DOC_END
def update_docs(model, path=README):
    """Refresca el bloque AGENT-DOCS del README con las cifras del modelo."""
    text = Path(path).read_text(encoding="utf-8"); block = docs_block(model)
    if DOC_BEGIN in text and DOC_END in text:
        head, tail = text.split(DOC_BEGIN)[0], text.split(DOC_END, 1)[1]
        Path(path).write_text(head + block + tail, encoding="utf-8")
    else:
        Path(path).write_text(text.rstrip() + "\n\n" + block + "\n", encoding="utf-8")
def _marked_body(text):
    return text.split(DOC_BEGIN, 1)[1].split(DOC_END, 1)[0].strip()
def _docs_diff(current, expected):
    got = [l for l in current.splitlines() if l.strip()]
    want = [l for l in expected.splitlines() if l.strip()]
    return [f"AGENT-DOCS desincronizado: README={g!r} modelo={w!r}"
            for g, w in zip(got, want) if g != w] or ["AGENT-DOCS desincronizado"]
def check_docs(model, path=README):
    """Verifica que el bloque AGENT-DOCS exista y coincida con el modelo."""
    text = Path(path).read_text(encoding="utf-8")
    if DOC_BEGIN not in text or DOC_END not in text:
        return [f"README sin bloque AGENT-DOCS ({DOC_BEGIN} … {DOC_END})"]
    current = _marked_body(text)
    expected = _marked_body(docs_block(model))
    if current == expected:
        return []
    return _docs_diff(current, expected)
def _cli_output(argv, default=OUT):
    return Path(argv[argv.index("--output") + 1]) if "--output" in argv else default
def _docs_exit(model):
    errors = check_docs(model)
    if not errors:
        print("AGENT-DOCS sincronizado"); return 0
    for e in errors[:10]:
        print(f"docs: {e}", file=sys.stderr)
    return 1
def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv); out = _cli_output(argv)
    model = build_model(load_json(METRICS), load_json(REPORT))
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(render(model), encoding="utf-8")
    if "--update-docs" in argv:
        update_docs(model)
        print("AGENT-DOCS actualizado en README.md")
    if "--check-docs" in argv and _docs_exit(model):
        return 1
    print(f"escrito: {out.relative_to(ROOT) if out.is_absolute() else out} "
          f"· ventanas: {len(model['views'])}")
    return 0
if __name__ == "__main__":
    sys.exit(main())

#!/usr/bin/env python3
"""model-cost-estimate-v0.py — estimador rápido de coste por unidad aceptada (spike).

Propósito:
- Responder "¿cuánto cuesta una unidad de trabajo aceptada por modelo?" sin
  implementar los hijos de coffe-dnx (.1–.9): cruza session_spend (ventana +
  proyecto + modelo + coste) con señales *locales* de resultado (git log y
  .beads/issues.jsonl por repo) y con los proxies ya calculados en
  agent_metrics.json.

Responsabilidades:
- lane_for(): separar carriles de coste (real pay-per-token vs notional de
  suscripción) usando la MISMA regla que scripts/usage-tracker.py: una fuente
  está en config/fpa.json → subscriptions si su dinero no sale del ledger.
- assign_units(): acreditar cada evento (bead cerrado / commit) a UNA sola sesión
  — la de inicio más cercano entre las que contienen el evento (CORR-002).
- bounded_window(): acotar sesiones de reloj largo y marcarlas ambiguas
  (CORR-004) en lugar de barrer los commits de las sesiones concurrentes.
- pctl()/wilson_lower()/cluster_bootstrap_ci(): cuantiles, cota inferior de
  Wilson y bootstrap por cluster (repo día) — la sesión está anidada, así que un
  iid bootstrap angosta los intervalos de más (EXCL-002).
- beads_freshness(): distinguir "export viejo" de "no hubo cierres" (EDGE-001).
- build_rows()/aggregate_report(): filas por (modelo, carril) con n, yield,
  p50/p99 y banda de sensibilidad, más las filas declaradas `unattributed`
  (EDGE-002) y el resumen de lo excluido (`mixed`, carril unknown).

Rationale: el reporte ya trae coste por modelo y por sesión; lo que falta es la
clave sesión→resultado. Mientras .3/.4/.5 no existan, la unión se hace contra
git y beads del propio repo porque es gratis y local (sin GITHUB_TOKEN). El
script es deliberadamente descartable: NO es el runner de .7 ni el scorecard de
.8; su salida es una estimación de orden de magnitud con procedencia declarada.

Uso:
  python3 scripts/model-cost-estimate-v0.py run --since 2026-09-01 \
      --unit bead --markdown /tmp/est.md --json /tmp/est.json
"""

import argparse
import datetime as dt
import glob
import json
import os
import random
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
REPORT = REPO / "data" / "usage_report_v3.json"
CONFIG = REPO / "config" / "fpa.json"

# Fuentes que el ledger factura por token (no están en config→subscriptions hoy).
KNOWN_PAY_PER_TOKEN = {
    "openrouter", "zai", "openai", "omp", "kimi-coding", "moonshot",
    "minimax", "mistral", "copilot", "github-copilot",
    "qwen-token-plan-individual", "recursive", "poolside", "nvidia",
}

# Umbrales: provisionales por diseño (EXCL-003). Se declaran en el reporte.
THRESHOLDS = {
    "provisional": True,
    "min_productive": 10,          # muestra mínima de sesiones con ≥1 unidad
    "max_ci_halfwidth_rel": 0.25,  # IC90 relativo a la mediana
    "stale_export_days": 30,       # export de beads más viejo que el último commit
    "max_window_s": 3600,          # ventana de acreditación por sesión
    "slack_s": 600,                # margen posterior al fin de sesión
    "bootstrap_B": 2000,
    "bootstrap_seed": 7,
}

EXCLUDED_MODELS = {"mixed"}


# --- carriles de coste (CORR-001) -------------------------------------------

def lane_for(source, subscriptions):
    """'notional' si la fuente es suscripción (config), 'real' si paga por token."""
    if source in subscriptions:
        return "notional"
    if source in KNOWN_PAY_PER_TOKEN:
        return "real"
    return "unknown"


# --- asignación de unidades (CORR-002) --------------------------------------

def assign_units(events, windows):
    """events: [(clave, ts)]; windows: [{id,start,end}].

    Cada evento se acredita a UNA sola ventana: la de inicio más cercano entre
    las que lo contienen (empate → id menor). Eventos repetidos cuentan una vez.
    """
    out = {}
    ordered = sorted(windows, key=lambda w: w["start"])
    for key, ts in events:
        best = None
        for w in ordered:
            if w["start"] > ts:
                break
            if w["end"] < ts:
                continue
            dist = abs(ts - w["start"])
            if best is None or dist < best[0] or (dist == best[0] and w["id"] < best[1]):
                best = (dist, w["id"])
        out.setdefault(key, best[1] if best else None)
    return out


def bounded_window(start, end, max_span_s):
    """Acota [start, end] a max_span_s desde el inicio; 3er valor = ambigua."""
    if end - start > max_span_s:
        return start, start + max_span_s, True
    return start, end, False


# --- estadística ------------------------------------------------------------

def pctl(values, p):
    """Cuantil con interpolación lineal; None si no hay muestra."""
    vals = sorted(values)
    n = len(vals)
    if not n:
        return None
    k = (n - 1) * p
    f = int(k)
    if f + 1 >= n:
        return float(vals[f])
    return vals[f] + (vals[f + 1] - vals[f]) * (k - f)


def wilson_lower(k, n, z=1.96):
    """Cota inferior del IC95 de una proporción (muestra chica no miente)."""
    if n <= 0:
        return None
    p = k / n
    denom = 1 + z * z / n
    centre = p + z * z / (2 * n)
    margin = z * ((p * (1 - p) / n + z * z / (4 * n * n)) ** 0.5)
    return max(0.0, (centre - margin) / denom)


def cluster_bootstrap_ci(values, clusters, p=0.5, B=2000, seed=7):
    """IC90 de un cuantil remuestreando clusters completos (repo día)."""
    groups = {}
    for v, c in zip(values, clusters):
        groups.setdefault(c, []).append(v)
    if len(groups) < 2:
        return None, None
    keys = list(groups)
    rnd = random.Random(seed)
    meds = []
    for _ in range(B):
        pooled = []
        for _ in range(len(keys)):
            pooled.extend(groups[keys[rnd.randrange(len(keys))]])
        q = pctl(pooled, p)
        if q is not None:
            meds.append(q)
    if len(meds) < 10:
        return None, None
    return pctl(meds, 0.05), pctl(meds, 0.95)


def is_comparable(n_productive, ci_halfwidth_rel):
    """Comparabilidad derivada de la muestra (EXCL-003), no de un corte inventado."""
    if n_productive < THRESHOLDS["min_productive"]:
        return False, f"n_productivas<{THRESHOLDS['min_productive']}"
    if ci_halfwidth_rel is None:
        return False, "ci_indefinido"
    if ci_halfwidth_rel > THRESHOLDS["max_ci_halfwidth_rel"]:
        return False, f"ci_ancho({ci_halfwidth_rel:.2f})"
    return True, "comparable"


# --- frescura del export de beads (EDGE-001) --------------------------------

def beads_freshness(export_mtime, last_commit_ts, stale_after_days):
    """fresh | stale | no-data | unknown — un export viejo NO es 'sin cierres'."""
    if export_mtime is None:
        return "no-data"
    if last_commit_ts is None:
        return "unknown"
    if export_mtime >= last_commit_ts - stale_after_days * 86400:
        return "fresh"
    return "stale"


# --- armado de filas --------------------------------------------------------

def _empty_row(model, lane):
    return {
        "model": model, "lane": lane, "n_sessions": 0, "cost": 0.0,
        "cost_imputed": 0.0, "units": 0, "n_productive": 0, "n_ambiguous": 0,
        "cost_ambiguous": 0.0, "units_ambiguous": 0, "unit_costs": [],
        "unit_costs_strict": [], "unit_clusters": [], "session_costs": [],
    }


def build_rows(sessions, units, projects, subscriptions):
    """Filas por (modelo, carril). `units` ya viene asignado uno-a-uno."""
    units_by_session = {}
    for key, sid in units.items():
        if sid:
            units_by_session[sid] = units_by_session.get(sid, 0) + 1

    rows = {}
    for s in sessions:
        if s["model"] in EXCLUDED_MODELS:
            continue
        if s["project"] not in projects:
            key = ("unattributed", "n/a")
        else:
            key = (s["model"], lane_for(s["source"], subscriptions))
        row = rows.setdefault(key, _empty_row(*key))
        n_units = units_by_session.get(s["session_id"], 0)
        cost = s.get("cost_real")
        cost = 0.0 if cost is None else float(cost)
        row["n_sessions"] += 1
        row["cost"] += cost
        row["cost_imputed"] += float(s.get("cost_effective") or 0.0)
        row["units"] += n_units
        row["session_costs"].append(cost)
        cluster = f"{s['project']}|{s.get('day', '')}"
        if n_units:
            row["n_productive"] += 1
            row["unit_costs"].append(cost / n_units)
            row["unit_clusters"].append(cluster)
            if not s.get("ambiguous"):
                row["unit_costs_strict"].append(cost / n_units)
        if s.get("ambiguous"):
            row["n_ambiguous"] += 1
            row["cost_ambiguous"] += cost
            row["units_ambiguous"] += n_units

    out = []
    for (model, lane), row in rows.items():
        uc = row.pop("unit_costs")
        ucs = row.pop("unit_costs_strict")
        ucl = row.pop("unit_clusters")
        sc = row.pop("session_costs")
        med = pctl(uc, 0.5)
        lo, hi = (cluster_bootstrap_ci(uc, ucl,
                                       B=THRESHOLDS["bootstrap_B"],
                                       seed=THRESHOLDS["bootstrap_seed"])
                  if uc else (None, None))
        halfwidth = None
        if lo is not None and med:
            halfwidth = (hi - lo) / 2 / med
        ok, reason = is_comparable(row["n_productive"], halfwidth) if uc else (False, "sin_unidades")
        row.update({
            "unit": "unit",
            "n_sessions": row["n_sessions"],
            "yield": (row["n_productive"] / row["n_sessions"]) if row["n_sessions"] else None,
            "yield_lb": wilson_lower(row["n_productive"], row["n_sessions"]),
            "p50_unit_cost": med,
            "p50_unit_cost_strict": pctl(ucs, 0.5),
            "n_strict": len(ucs),
            "ci90": [lo, hi],
            "ci_halfwidth_rel": halfwidth,
            "p50_session_cost": pctl(sc, 0.5),
            "p99_session_cost": pctl(sc, 0.99),
            "comparable": ok,
            "comparable_reason": reason,
            "units_ambiguous_share": (row["units_ambiguous"] / row["units"])
            if row["units"] else None,
        })
        out.append(row)
    return out


def aggregate_report(sessions, units, projects, subscriptions, meta):
    rows = build_rows(sessions, units, projects, subscriptions)
    excluded = {"mixed": {"n_sessions": 0, "cost": 0.0}}
    for s in sessions:
        if s["model"] in EXCLUDED_MODELS:
            excluded["mixed"]["n_sessions"] += 1
            excluded["mixed"]["cost"] += float(s.get("cost_real") or 0.0)
    unattributed = [r for r in rows if r["model"] == "unattributed"]
    payload = dict(meta)
    payload.update({
        "unit": meta.get("unit", "unit"),
        "thresholds": THRESHOLDS,
        "dedupe_rule": "uno-a-uno: cada evento a la sesión de inicio más cercano",
        "cost_basis": "cost = cost_real; cost_imputed = cost_effective (solo notional)",
        "excluded": excluded,
        "unattributed": unattributed[0] if unattributed else None,
        "rows": sorted([r for r in rows if r["model"] != "unattributed"],
                       key=lambda r: (r["lane"], -(r["p50_unit_cost"] or 0))),
    })
    return payload


# --- recolección de datos ---------------------------------------------------

def local_day(iso_utc, tz_hours=-3):
    d = dt.datetime.fromisoformat(iso_utc.replace("Z", "+00:00"))
    return (d.astimezone(dt.timezone(dt.timedelta(hours=tz_hours)))).date().isoformat()


def resolve_projects(projects, root):
    """proyecto → ruta del repo. Assert del org (EDGE-003), no partition a ciegas."""
    out = {}
    orgs = {p.name for p in Path(root).iterdir() if p.is_dir()}
    for proj in projects:
        org, _, rest = proj.partition("-")
        if org in orgs and rest:
            cand = Path(root) / org / rest
            if (cand / ".git").is_dir():
                out[proj] = str(cand)
                continue
        hits = [p for p in glob.glob(os.path.join(root, "*", glob.escape(rest)))
                if (Path(p) / ".git").is_dir()] if rest else []
        if len(hits) == 1:
            out[proj] = hits[0]
    return out


def _git(path, *args):
    try:
        r = subprocess.run(["git", "-C", path, *args], capture_output=True, text=True)
        return r.stdout
    except OSError:
        return ""


def commit_times(path):
    out = _git(path, "log", "--no-merges", "--format=%at")
    return sorted(int(x) for x in out.split())


def last_commit_ts(path):
    out = _git(path, "log", "-1", "--format=%at").strip()
    return int(out) if out.isdigit() else None


def beads_closes(path):
    """[(id, ts)] de .beads/issues.jsonl; vacío si el repo no trackea beads."""
    f = Path(path) / ".beads" / "issues.jsonl"
    if not f.is_file():
        return None
    out = []
    for line in f.read_text().splitlines():
        try:
            o = json.loads(line)
        except ValueError:
            continue
        ts = o.get("closed_at")
        if not ts:
            continue
        d = dt.datetime.fromisoformat(ts.replace("Z", "+00:00"))
        out.append((f"{path}|{o.get('id')}", d.timestamp()))
    return sorted(out, key=lambda x: x[1])


def load_sessions(report_path, since=None, until=None, tz_hours=-3):
    rep = json.load(open(report_path))
    subs = json.load(open(CONFIG)).get("subscriptions", {})
    sessions = []
    for r in rep["session_spend"]:
        if r.get("cost_effective") is None:
            continue
        day = local_day(r["start"], tz_hours)
        if since and day < since:
            continue
        if until and day > until:
            continue
        sessions.append({
            "session_id": r["session_id"], "model": r["model"], "source": r["source"],
            "project": r["project"], "day": day,
            "cost_effective": r.get("cost_effective"), "cost_real": r.get("cost_real"),
            "start": dt.datetime.fromisoformat(r["start"].replace("Z", "+00:00")).timestamp(),
            "end": dt.datetime.fromisoformat(r["end"].replace("Z", "+00:00")).timestamp(),
        })
    return sessions, subs, rep.get("metadata", {})


def collect(report_path, root, since, until, unit, tz_hours=-3):
    sessions, subs, rep_meta = load_sessions(report_path, since, until, tz_hours)
    projects = resolve_projects({s["project"] for s in sessions}, root)

    windows = []
    for s in sessions:
        a, b, amb = bounded_window(s["start"], s["end"],
                                   THRESHOLDS["max_window_s"])
        s["ambiguous"] = amb
        windows.append({"id": s["session_id"], "start": a, "end": b + THRESHOLDS["slack_s"]})

    events = []
    excluded_repos = {}
    stale = []
    for proj, path in projects.items():
        if unit == "bead":
            closes = beads_closes(path)
            export = Path(path) / ".beads" / "issues.jsonl"
            fresh = beads_freshness(
                export.stat().st_mtime if export.is_file() else None,
                last_commit_ts(path), THRESHOLDS["stale_export_days"])
            if closes is None:
                excluded_repos[proj] = "sin_beads"
                continue
            if fresh != "fresh":
                stale.append({"project": proj, "freshness": fresh})
                excluded_repos[proj] = f"beads_{fresh}"
                continue
            events.extend(closes)
        else:
            events.extend((f"{path}|{t}", t) for t in commit_times(path))

    units = assign_units(events, windows)
    n_credit = sum(1 for v in units.values() if v)
    spend = sum(float(s.get("cost_real") or 0) for s in sessions)
    resolved_spend = sum(float(s.get("cost_real") or 0) for s in sessions
                         if s["project"] in projects)
    meta = {
        "generated": dt.datetime.now().astimezone().isoformat(timespec="seconds"),
        "window": {"since": since, "until": until, "timezone": tz_hours},
        "unit": unit,
        "report": str(report_path.relative_to(REPO)) if str(report_path).startswith(str(REPO)) else str(report_path),
        "report_date_range": rep_meta.get("date_range"),
        "sessions": len(sessions),
        "cost_real_total": round(spend, 2),
        "coverage": {
            "projects_resolved": len(projects),
            "projects_total": len({s["project"] for s in sessions}),
            "spend_resolved_pct": round(100 * resolved_spend / spend, 1) if spend else None,
        },
        "events": {"total": len(events), "credited": n_credit,
                   "distinct_units": len({k for k, v in units.items() if v}),
                   "credit_rate": round(n_credit / len(events), 3) if events else None},
        "excluded_repos": excluded_repos,
        "stale_beads_repos": stale,
        "note": "estimación de orden de magnitud; no es la cohorte de .9 ni el scorecard de .8",
    }
    return sessions, units, projects, subs, meta


# --- render -----------------------------------------------------------------

def _fmt(v, spec="{:.2f}", dash="n/a"):
    return dash if v is None else spec.format(v)


def render_markdown(payload):
    since = payload["window"]["since"] or "inicio del reporte"
    until = payload["window"]["until"] or "fin del reporte"
    lines = [
        "# Estimador v0 — coste por unidad aceptada (spike coffe-dnx.10)",
        "",
        f"Ventana: {since} → {until} "
        f"(tz {payload['window']['timezone']}) · unidad: `{payload['unit']}` · "
        f"sesiones: {payload['sessions']} · gasto real: ${payload['cost_real_total']}",
        "",
        f"Cobertura: {payload['coverage']['projects_resolved']}/"
        f"{payload['coverage']['projects_total']} proyectos, "
        f"{payload['coverage']['spend_resolved_pct']}% del gasto. "
        f"Créditos: {payload['events']['credited']}/{payload['events']['total']} eventos "
        f"({payload['events']['distinct_units']} unidades distintas). "
        f"Regla: {payload['dedupe_rule']}. Base: {payload['cost_basis']}.",
        "",
        "Carril `real` = pay-per-token (dinero del ledger). Carril `notional` = "
        "suscripción: `cost` es el dinero real desembolsado, `imputado` el valor "
        "equivalente por token. **No se comparan entre sí.**",
        "",
    ]
    comparable = [r for r in payload["rows"] if r["comparable"]]
    if comparable:
        lines.append("## Veredicto (solo filas comparables)")
        lines.append("")
        for r in comparable:
            lines.append(f"- **{r['model']}** ({r['lane']}): "
                         f"${_fmt(r['p50_unit_cost'])}/unidad "
                         f"(estricto ${_fmt(r['p50_unit_cost_strict'])}, n={r['n_strict']}; "
                         f"IC90 {_fmt(r['ci90'][0])}–{_fmt(r['ci90'][1])}), "
                         f"yield LB95 {_fmt(r['yield_lb'], '{:.0%}')}, "
                         f"n={r['n_sessions']}.")
        lines.append("")
    lines += [
        "## Tabla completa (n ≥ 10; el resto queda en el JSON)",
        "",
        "| modelo | carril | n | yield (LB95) | p50 $/unidad | estricto (n) | IC90 | n prod | "
        "p99/p50 | ambigua (unidades) | ¿comparable? |",
        "|---|---|---|---|---|---|---|---|---|---|---|",
    ]
    for r in payload["rows"]:
        if r["n_sessions"] < 10:
            continue
        tail = None
        if r["p50_session_cost"] and r["p99_session_cost"]:
            tail = r["p99_session_cost"] / r["p50_session_cost"]
        ic = "n/a" if r["ci90"][0] is None else f"{r['ci90'][0]:.2f}–{r['ci90'][1]:.2f}"
        lines.append(
            f"| `{r['model']}` | {r['lane']} | {r['n_sessions']} | "
            f"{_fmt(r['yield_lb'], '{:.0%}')} | {_fmt(r['p50_unit_cost'])} | "
            f"{_fmt(r['p50_unit_cost_strict'])} ({r['n_strict']}) | {ic} | "
            f"{r['n_productive']} | {_fmt(tail, '{:.1f}×')} | "
            f"{r['n_ambiguous']} ({_fmt(r['units_ambiguous_share'], '{:.0%}')} de las u.) | "
            f"{'sí' if r['comparable'] else r['comparable_reason']} |")
    omitted = len([r for r in payload["rows"] if r["n_sessions"] < 10])
    if omitted:
        lines += ["", f"{omitted} modelos con n<10 omitidos de la tabla (ver JSON)."]
    un = payload.get("unattributed")
    if un:
        lines += ["", f"Sin atribuir (proyecto sin repo local): {un['n_sessions']} sesiones, "
                      f"${un['cost']:.2f}."]
    ex = payload["excluded"]["mixed"]
    if ex["n_sessions"]:
        lines += [f"Excluido `mixed` (sesiones con varios modelos): {ex['n_sessions']} "
                  f"sesiones, ${ex['cost']:.2f}."]
    if payload["stale_beads_repos"]:
        lines += ["", "Repos con export de beads no fiable (excluidos): "
                  + ", ".join(f"`{s['project']}` ({s['freshness']})"
                              for s in payload["stale_beads_repos"])]
    lines += ["", f"Umbrales provisionales: `{json.dumps(payload['thresholds'])}`", ""]
    return "\n".join(lines)


def cmd_run(args):
    report = Path(args.report).expanduser()
    sessions, units, projects, subs, meta = collect(
        report, os.path.expanduser(args.root), args.since, args.until, args.unit,
        args.tz)
    payload = aggregate_report(sessions, units, projects, subs, meta)
    md = render_markdown(payload)
    if args.markdown:
        Path(args.markdown).write_text(md)
    if args.json:
        Path(args.json).write_text(json.dumps(payload, indent=1, default=str))
    print(md)
    return 0


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    run = sub.add_parser("run", help="genera la estimación")
    run.add_argument("--report", default=str(REPORT))
    run.add_argument("--root", default="~/para/areas/dev/gh")
    run.add_argument("--since", default=None)
    run.add_argument("--until", default=None)
    run.add_argument("--unit", choices=("bead", "commit"), default="bead")
    run.add_argument("--tz", type=int, default=-3)
    run.add_argument("--markdown", default=None)
    run.add_argument("--json", dest="json", default=None)
    run.set_defaults(func=cmd_run)
    args = ap.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())

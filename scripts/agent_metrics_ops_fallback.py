"""Purpose: ops.fallback — eventos de fallback de modelo (epic coffe-a5v,
hijo coffe-8mh) para data/agent_metrics.json.

Responsibilities: contar fallbacks por fuente. omp: registros `model_change`
con `resolvedModelIsFallback` true (método *reported*, convenciones de campo
del extractor usage_omp.py, walker propio: usage-tracker.py no es importable).
pi y claude-cli: proxy *derived* — turno assistant cuyo modelo difiere del
turno assistant anterior DENTRO de la misma sesión (cambio a mitad de tarea;
un cambio en el arranque de una sesión nueva no cuenta). denominador = turnos
assistant de sesiones con >=2 turnos assistant (omp: registros model_change
examinados). events_total suma fuentes; provenance y coverage declaran la
realidad de cada fuente (null-con-razón, nunca 0 fake).

Rationale: omp reporta el fallback explícitamente; pi/claude-cli no escriben
esa señal, así que se deriva del proxy. Reusar load_pi_turns/load_claude_turns
evita un segundo parser; el grouping por session_id impide falsos positivos
en el borde entre sesiones.
"""
from __future__ import annotations

import sys
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from agent_metrics_ops import run_slice  # noqa: E402
from agent_metrics_turns import load_claude_turns, load_pi_turns  # noqa: E402

NO_OMP_DIR_REASON = "no_omp_sessions_dir"
NO_TURNS_REASON = "no_turns_signal"
OMP_SOURCE = "omp"
PROXY_SOURCES = ("pi", "claude-cli")


# ----------------------------------------------------------------------
# omp: reported-resolvedModelIsFallback
# ----------------------------------------------------------------------

def omp_model_changes(sessions_dir):
    """Registros `model_change` de los JSONL de ~/.omp/agent/sessions
    (~/*/**/*.jsonl). Devuelve lista de bools: [resolvedModelIsFallback si
    viene el campo]. Un registro sin el campo no entra al denominador."""
    flags = []
    base = Path(sessions_dir)
    if not base.exists():
        return None
    for path in sorted(base.rglob("*.jsonl")):
        try:
            lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
        except OSError:
            continue
        for line in lines:
            if '"model_change"' not in line:
                continue
            import json  # noqa: PLC0415 — json pesa solo al primer hit
            try:
                rec = json.loads(line)
            except ValueError:
                continue
            if rec.get("type") != "model_change":
                continue
            v = rec.get("resolvedModelIsFallback")
            if isinstance(v, bool):
                flags.append(v)
    return flags


def omp_source(sessions_dir):
    """Celda by_source de omp. Sin directorio → celda presente con razón y
    zeros (nunca fabricada)."""
    flags = omp_model_changes(sessions_dir)
    if flags is None:
        return {"method": "reported-resolvedModelIsFallback", "events": 0,
                "denominator": 0, "rate": None, "reason": NO_OMP_DIR_REASON}
    events = sum(1 for f in flags if f)
    return {"method": "reported-resolvedModelIsFallback", "events": events,
            "denominator": len(flags),
            "rate": round(events / len(flags), 4) if flags else None}


# ----------------------------------------------------------------------
# pi / claude-cli: proxy-mid-task-switch
# ----------------------------------------------------------------------

def proxy_source(turns, source):
    """Celda by_source del proxy para pi/claude-cli. Un evento por turno
    assistant con modelo distinto al assistant anterior de SU sesión
    (grouping por session_id: sin falsos positivos en el borde de sesiones).
    Denominador: turnos assistant de sesiones con >=2 assistant."""
    by_ses = defaultdict(list)
    for t in turns:
        if t.get("role") == "assistant":
            sid = (t.get("ses") or {}).get("session_id")
            by_ses[sid].append(t)
    events = 0
    denom = 0
    for sid, ats in by_ses.items():
        ats.sort(key=lambda t: t.get("ts") or "")
        if len(ats) < 2:
            continue
        denom += len(ats)
        prev = None
        for t in ats:
            if prev is not None and t.get("model") != prev:
                events += 1
            prev = t.get("model")
    cell = {"method": "proxy-mid-task-switch", "events": events,
            "denominator": denom,
            "rate": round(events / denom, 4) if denom else None}
    if not denom:
        cell["reason"] = NO_TURNS_REASON
    return cell


# ----------------------------------------------------------------------
# Bloque ops.fallback
# ----------------------------------------------------------------------

def fallback_block(omp_dir, pi_turns, claude_turns):
    by_source = {OMP_SOURCE: omp_source(omp_dir)}
    for source, turns in (("pi", pi_turns), ("claude-cli", claude_turns)):
        by_source[source] = proxy_source(turns, source)
    return {
        "by_source": by_source,
        "events_total": sum(c["events"] for c in by_source.values()),
        "provenance": {"omp": "reported", "pi": "derived",
                       "claude-cli": "derived"},
        "coverage": _coverage(by_source),
    }


def _coverage(by_source):
    cov = []
    for source, cell in by_source.items():
        denom = cell["denominator"]
        if denom:
            cov.append({"source": source, "coverage_pct": 100.0,
                        "reason": None})
        else:
            cov.append({"source": source, "coverage_pct": 0.0,
                        "reason": cell.get("reason") or NO_TURNS_REASON})
    return cov


# ----------------------------------------------------------------------
# main + viz
# ----------------------------------------------------------------------

def main():
    import json  # noqa: PLC0415

    from agent_metrics_common import OUT  # noqa: PLC0415
    from usage_state import OMP_DIR  # noqa: PLC0415

    def compute(inputs):
        pi_turns, _meta = load_pi_turns()
        claude_turns, _meta = load_claude_turns()
        return fallback_block(OMP_DIR, pi_turns, claude_turns)

    run_slice("fallback", compute)
    fb = json.loads(OUT.read_text(encoding="utf-8"))["ops"]["fallback"]
    parts = ", ".join(f"{s}={c['events']}/{c['denominator']}"
                      for s, c in fb["by_source"].items())
    print(f"ops.fallback: total={fb['events_total']} ({parts})")


def render_section(ops):
    """Sección HTML server-side del dashboard (sin JS)."""
    fb = (ops or {}).get("fallback")
    if fb is None:
        return ('<section class="block"><h2>Fallback de modelo (ops.fallback)</h2>'
                '<p class="reason">n/a: bloque ops.fallback ausente '
                '(correr scripts/agent_metrics_ops_fallback.py).</p></section>')
    rows = []
    for source in (OMP_SOURCE,) + PROXY_SOURCES:
        cell = fb["by_source"].get(source)
        if cell is None:
            continue
        rate = f"{cell['rate'] * 100:.1f}%" if cell["rate"] is not None else "n/a"
        reason = f' <span class="reason">{cell["reason"]}</span>' if cell.get("reason") else ""
        rows.append(f"<tr><td>{source}</td><td>{cell['method']}</td>"
                    f"<td>{cell['events']}</td><td>{cell['denominator']}</td>"
                    f"<td>{rate}{reason}</td></tr>")
    return (f'<section class="block"><h2>Fallback de modelo (ops.fallback)</h2>'
            f'<p>Total de eventos: {fb["events_total"]} '
            f'({", ".join(f"{s}: reported" if fb["provenance"].get(s) == "reported" else f"{s}: derived" for s in fb["by_source"])})</p>'
            '<table><thead><tr><th>Fuente</th><th>Método</th><th>Eventos</th>'
            '<th>Denominador</th><th>Tasa</th></tr></thead><tbody>'
            + "".join(rows) + "</tbody></table></section>")


if __name__ == "__main__":
    main()

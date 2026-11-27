"""Purpose: métricas de continuidad operativa (bloque `ops`) para
data/agent_metrics.json — epic coffe-a5v, hijo coffe-9ia.

Responsibilities: calcular p50/p90/p95 de latencia por fuente/proveedor/modelo
sobre TODA la población de turnos con duración (sin sampling, reemplaza el
muestreo CORR-005 para ops), derivar proveedor con el bloque `model_mapping`
de config/fpa.json (misma convención que usage-report, no un segundo mapeo),
y fusionar el bloque `ops` en el dataset del tracker validando contra el schema.

Rationale: la decisión de compra necesita p95 real; el sampling existente cubre
11.8%. Reusar los loaders de agent_metrics_turns evita un segundo parser; el
bloque `ops` se mergea (no se pisa) para que los hijos siguientes del epic
agreguen sus claves sin reescribir este módulo.

Corrida: después de agent-metrics-tracker.py (el tracker escribe el dataset sin
`ops`; este script lo agrega). El wiring al nightly es del hijo coffe-izd.
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from agent_metrics_common import OUT, ROOT, SCHEMA, load_config  # noqa: E402
from agent_metrics_turns import load_claude_turns, load_pi_turns  # noqa: E402

NO_DURATION_REASON = "no_duration_signal"
LATENCY_PCTLS = (0.50, 0.90, 0.95)


# ----------------------------------------------------------------------
# Utilidades puras
# ----------------------------------------------------------------------

def percentile(sorted_vals, q):
    """Percentil q (0..1) con interpolación lineal; [] → None."""
    if not sorted_vals:
        return None
    if len(sorted_vals) == 1:
        return float(sorted_vals[0])
    pos = (len(sorted_vals) - 1) * q
    lo = int(pos)
    frac = pos - lo
    if lo + 1 >= len(sorted_vals):
        return float(sorted_vals[-1])
    return float(sorted_vals[lo]) + frac * float(sorted_vals[lo + 1] - sorted_vals[lo])


def latency_stats(durs_ms, turns_total):
    """Stats de latencia para una celda (fuente×modelo o proveedor).

    durs_ms: duraciones en ms de los turnos CON señal; turns_total: todos los
    turnos assistant de la celda (con y sin señal) → coverage_pct.
    Sin señal en absoluto → null con razón (nunca 0 fake).
    """
    base = {"n": len(durs_ms),
            **{f"p{int(q * 100)}_ms": percentile(sorted(durs_ms), q) for q in LATENCY_PCTLS},
            "turns_total": turns_total,
            "coverage_pct": round(100.0 * len(durs_ms) / turns_total, 1) if turns_total else 0.0}
    if not durs_ms:
        base["reason"] = NO_DURATION_REASON
    return base


def compile_mapping(entries):
    """Compila el bloque model_mapping (config o fallback) → [(regex, family,
    version)] ordenado por priority. Fail loud ante entradas malformadas:
    un mapeo roto clasificaría todo a 'other' en silencio."""
    import usage_state  # noqa: PLC0415 — misma fuente que usage-report

    if entries is None:
        return list(usage_state._MODEL_MAPPING)
    return usage_state._compile_model_mapping(entries)


def provider_for(model_id, mapping):
    """Familia (proveedor) del model_id: primera entrada por priority ascendente
    cuyo pattern matchea; sin match → 'other' (convención del model_mapping)."""
    for pat, family, _ver in mapping:
        if pat.search(model_id or ""):
            return family
    return "other"


def merge_ops(doc, ops_slice):
    """Fusiona un slice (ej. {'latency': ...}) en doc['ops'] sin pisar las
    claves que otros hijos del epic ya escribieron."""
    doc.setdefault("ops", {}).update(ops_slice)
    return doc


# ----------------------------------------------------------------------
# Bloque ops.latency
# ----------------------------------------------------------------------

def _assistant_turns(turns):
    return [t for t in turns if t.get("role") == "assistant"]


def latency_block(pi_turns, claude_turns, mapping):
    """ops.latency completo: por fuente (pi, claude-cli) y roll-up por
    proveedor (familia del model_mapping) sobre TODA la población."""
    sources = {}
    provider_durs = {}
    provider_totals = {}

    for source, turns in (("pi", pi_turns), ("claude-cli", claude_turns)):
        assistants = _assistant_turns(turns)
        by_model = {}
        durs_all = []
        for t in assistants:
            model = t.get("model") or "unknown"
            cell = by_model.setdefault(model, {"durs": [], "total": 0})
            cell["total"] += 1
            dur_s = t.get("dur_s")
            if isinstance(dur_s, (int, float)) and dur_s >= 0:
                cell["durs"].append(float(dur_s) * 1000.0)
        models_out = {}
        for model, cell in sorted(by_model.items()):
            stats = latency_stats(cell["durs"], cell["total"])
            models_out[model] = stats
            if stats["n"]:
                durs_all.extend(cell["durs"])
                family = provider_for(model, mapping)
                provider_durs.setdefault(family, []).extend(cell["durs"])
                provider_totals[family] = provider_totals.get(family, 0) + cell["total"]
        entry = {
            "turns_total": len(assistants),
            "turns_with_duration": len(durs_all),
            "coverage_pct": round(100.0 * len(durs_all) / len(assistants), 1) if assistants else 0.0,
            "models": models_out,
        }
        if assistants and not durs_all:
            entry["reason"] = NO_DURATION_REASON
        sources[source] = entry

    providers = {}
    for family in sorted(provider_durs):
        stats = latency_stats(provider_durs[family], 0)
        # coverage del proveedor: sobre los turnos assistant de ese proveedor
        stats["coverage_pct"] = round(
            100.0 * len(provider_durs[family]) / provider_totals[family], 1
        ) if provider_totals[family] else 0.0
        stats.pop("reason", None)
        stats["turns_total"] = provider_totals[family]
        providers[family] = stats

    return {
        "sources": sources,
        "providers": providers,
        "provenance": {"latency": "measured"},
        "coverage": [{"source": s, "coverage_pct": v["coverage_pct"],
                      "reason": v.get("reason")} for s, v in sources.items()],
    }


# ----------------------------------------------------------------------
# CLI
# ----------------------------------------------------------------------

def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    out = Path(argv[argv.index("--output") + 1]) if "--output" in argv else OUT
    cfg = load_config()
    mapping = compile_mapping((cfg.get("model_mapping") or {}).get("entries"))
    pi_turns, _pi_meta = load_pi_turns()
    claude_turns, _cl_meta = load_claude_turns()

    doc = json.loads(out.read_text(encoding="utf-8"))
    merge_ops(doc, {"latency": latency_block(pi_turns, claude_turns, mapping)})

    from schema_validate import validate_against_schema  # noqa: PLC0415
    errors = validate_against_schema(doc, json.loads(SCHEMA.read_text(encoding="utf-8")))
    if errors:
        for e in errors[:10]:
            print(f"schema: {e}", file=sys.stderr)
        return 1
    out.write_text(json.dumps(doc, ensure_ascii=False, indent=2) + "\n",
                   encoding="utf-8")
    lat = doc["ops"]["latency"]
    print(f"ops.latency: fuentes={len(lat['sources'])} proveedores={len(lat['providers'])} "
          f"· cobertura: " + ", ".join(f"{c['source']}={c['coverage_pct']}%"
                                       for c in lat["coverage"]))
    return 0


if __name__ == "__main__":
    sys.exit(main())

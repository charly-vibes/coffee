#!/usr/bin/env python3
"""Scorecard: cohort, reglas y task rows (extraído de usage-tracker.py).

Parte de la descomposición de scripts/usage-tracker.py (coffe-kav.1):
cada módulo conserva el código original verbatim; los nombres compartidos
viajan por _link() (mismo patrón que viz_fpa_common) y el estado mutable
por usage_state.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import usage_state

_SCORECARD_TASK_REJECTED_BITS = 1 | 2
SCORECARD_WINDOWS = ("todo", "ytd", "mes")


def _scorecard_window_filters(interactions):
    """Ventanas de período del scorecard derivadas del corpus (FPA-008:
    sin data no hay ventanas fantasma). todo = todo el corpus; ytd = desde
    el 1-ene del último año con datos; mes = último mes con datos."""
    if not interactions:
        return {}
    last = max(r["timestamp"] for r in interactions)[:10]
    ym = last[:7]
    ytd = last[:4] + "-01-01"
    return {
        "todo": (None, None),
        "ytd": (ytd, None),
        "mes": (None, ym),
    }


def _in_scorecard_window(ts, since, until_prefix):
    d = ts[:10]
    if since and d < since:
        return False
    if until_prefix and d[:7] != until_prefix:
        return False
    return True


def _scorecard_cache_factor(family, version, when=None):
    """cache_factor = cache_read_rate / input_rate del pricing VIGENTE en
    config (nunca un 1.0 silencioso — el bug que motiva el ticket: con 90%
    de cache_read a ~0.1× del input, $/Mtok sobre tokens totales invierte
    el ranking). Devuelve (factor, reason, provenance) — provenance
    reported con tarifa explícita, assumed con fallback default_rates;
    sin tarifa → (None, razón, None): FPA-008."""
    rates = _pricing_for(when).get(family, {}).get(version)
    if rates is None:
        # misma convención que estimate_cost(): fallback a default_rates
        # del config — explícito (provenance assumed), nunca 1.0 silencioso
        dr = DEFAULT_RATES
        if dr.get("input"):
            if dr.get("cache_read"):
                return (round(dr["cache_read"] / dr["input"], 8), None, "assumed")
            return (0.1, "cache_read sin tarifa en default_rates — fallback "
                         "documentado 0.1× input", "assumed")
        return None, (f"sin tarifa en model_pricing para {family}/{version} "
                      f"(config {CONFIG_SOURCE or 'builtin-fallback'})"), None
    cr = rates.get("cache_read")
    if cr is None:
        # misma convención que estimate_cost(): cache_read a 0.1× del input
        return 0.1, ("cache_read sin tarifa explícita en model_pricing — "
                     f"fallback documentado 0.1× input ({family}/{version})"), "assumed"
    return round(cr / rates["input"], 8), None, "reported"


def _scorecard_task_row(i, tasks, days, models):
    m_ix = tasks["model"][i]
    raw = models[m_ix] if isinstance(m_ix, int) and m_ix < len(models) else "?"
    fam, ver = model_details(raw)
    d_ix = tasks["day"][i]
    day = days[d_ix] if isinstance(d_ix, int) and d_ix < len(days) else None
    return (fam, ver), (
        day,
        float(tasks["cost"][i] or 0.0),
        not (int(tasks["f"][i] or 0) & _SCORECARD_TASK_REJECTED_BITS),
    )


def _scorecard_task_rows(tasks, days, models):
    n = min(len(tasks[k]) for k in ("day", "model", "f", "cost"))
    out = defaultdict(list)
    for i in range(n):
        key, row = _scorecard_task_row(i, tasks, days, models)
        out[key].append(row)
    return dict(out), None


def _scorecard_task_load():
    """(artefacto, reason|None) del agent_metrics.json columnar."""
    path = SCORECARD_AGENT_METRICS_PATH
    if not path.exists():
        return {}, f"unavailable: {path} no encontrado (artefacto coffe-dnx.3)"
    try:
        with open(path) as f:
            am = json.load(f)
    except (OSError, json.JSONDecodeError) as exc:
        return {}, f"unavailable: {path} ilegible ({exc})"
    tasks = am.get("tasks") or {}
    need = ("day", "model", "f", "cost")
    if any(k not in tasks for k in need):
        return {}, ("unavailable: agent_metrics.json sin columnas de tareas "
                    f"{need} (artefacto coffe-dnx.3 incompleto)")
    return (tasks, am.get("days") or [], am.get("models") or []), None


def _scorecard_task_costs():
    """coffe-dnx.3: coste por tarea y por tarea aceptada, por
    (family, version). Lee el artefacto columnar de agent_metrics.json
    (tasks: day/s/model/f/cost). Aceptada = f sin bits aborted|error
    (proxy — la señal positiva de aceptación no existe aún). Devuelve
    ({(fam, ver): [(day, cost, accepted), ...]}, reason|None)."""
    load, reason = _scorecard_task_load()
    if reason is not None:
        return {}, reason
    tasks, days, models = load
    return _scorecard_task_rows(tasks, days, models)


def _rule_insufficient_ref(todo_entries, cohort, rule):
    """Veredicto datos_insuficientes si falta la referencia; None si está."""
    ref_name = cohort.get("reference")
    cpat = {e["model"]: ((e.get("data") or {}).get("cost_per_accepted_task"))
            for e in todo_entries}
    if ref_name in cpat and cpat.get(ref_name) is not None:
        return None
    reason = (f"referencia {ref_name} sin cost_per_accepted_task "
              "(datos_insuficientes: la comparación no puede calibrarse)")
    per_model = {m: {"verdict": "datos_insuficientes", "reason": reason,
                     "ratio_vs_reference": None}
                 for m in cohort.get("models", [])}
    return "datos_insuficientes", reason, per_model


def _rule_model_verdicts(todo_entries, cohort, rule):
    """(per_model, any_cambiar) por modelo con la referencia calibrada."""
    ref_name = cohort.get("reference")
    thresholds = rule.get("thresholds") or {}
    cambiar_max = thresholds.get("cambiar_max_ratio_vs_reference", 0.8)
    cpat = {e["model"]: ((e.get("data") or {}).get("cost_per_accepted_task"))
            for e in todo_entries}
    ref_cost = cpat[ref_name]
    per_model = {}
    any_cambiar = False
    for m in cohort.get("models", []):
        entry, flagged = _verdict_entry(m, cpat.get(m),
                                        (ref_name, ref_cost, cambiar_max))
        per_model[m] = entry
        any_cambiar = any_cambiar or flagged
    return cpat, per_model, any_cambiar


def _scorecard_rule_verdict(todo_entries, cohort, rule):
    """Veredicto según model_decision_rule sobre cost_per_accepted_task de
    la ventana 'todo'. Per-model: cambiar si ratio <= cambiar_max_ratio;
    mantener si ratio > cambiar_max_ratio; datos_insuficientes si falta
    cost_per_accepted_task propio o de la referencia. El veredicto global
    es datos_insuficientes si CUALQUIER modelo de la cohorte lo está
    (ticket coffe-dnx.8), luego cambiar si algún subject lo es."""
    verdict = _rule_insufficient_ref(todo_entries, cohort, rule)
    if verdict:
        return verdict
    cpat, per_model, any_cambiar = _rule_model_verdicts(todo_entries, cohort, rule)
    if any(v is None for v in cpat.values()):
        return ("datos_insuficientes",
                "falta cost_per_accepted_task en la cohorte "
                "(datos_insuficientes: la regla exige la cohorte completa)",
                per_model)
    if any_cambiar:
        return "cambiar", "al menos un subject de la cohorte cumple el umbral cambiar", per_model
    return "mantener", "ningún subject cumple el umbral cambiar", per_model


def _verdict_entry(m, cost, ref):
    """({verdict, reason, ratio_vs_reference}, any_cambiar) de 1 modelo.
    ref = (ref_name, ref_cost, cambiar_max)."""
    ref_name, ref_cost, cambiar_max = ref
    if cost is None:
        return ({"verdict": "datos_insuficientes",
                 "reason": "sin cost_per_accepted_task en la cohorte "
                           "(cruce task×modelo no joinable o sin tareas)",
                 "ratio_vs_reference": None}, False)
    ratio = round(cost / ref_cost, 4) if ref_cost else None
    if ratio is None:
        return {"verdict": "datos_insuficientes",
                "reason": "referencia con cost_per_accepted_task 0",
                "ratio_vs_reference": None}, False
    if m == ref_name:
        return {"verdict": "mantener",
                "reason": "referencia frontier: no participa del "
                          "ranking (techo comparativo)",
                "ratio_vs_reference": 1.0}, False
    if ratio <= cambiar_max:
        return ({"verdict": "cambiar",
                 "reason": (f"cost_per_accepted_task <= {cambiar_max}× la "
                            f"referencia ({ref_name})"),
                 "ratio_vs_reference": ratio}, True)
    return ({"verdict": "mantener",
             "reason": (f"cost_per_accepted_task > {cambiar_max}× la "
                        f"referencia ({ref_name})"),
             "ratio_vs_reference": ratio}, False)

usage_state._link(globals())

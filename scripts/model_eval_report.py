"""model_eval_report.py — agregación y validación del reporte (coffe-dnx.7).

Funciones puras (sin IO): aggregate() por modelo y clase, validate_report()
para el auto-chequeo de provenance. Cargado dinámicamente por model-eval.py,
que funciona standalone (scripts/ no es paquete) y re-exporta estos símbolos.
"""

from __future__ import annotations

CLASSES = ("bugfix", "feature", "docs", "tests")

VALID_PROVENANCE = ("measured", "estimated", "judged")


# Plantilla de cifra con provenance (todo número del reporte pasa por acá).
def figure(value, provenance):
    return {"value": value, "provenance": provenance}


def _mean_cost(runs):
    """Promedio de coste con provenance agregada: cualquier estimated contamina."""
    vals = [r["cost_usd"] for r in runs if r.get("cost_usd") is not None]
    if not vals:
        return None
    prov = "estimated" if any(v["provenance"] != "measured" for v in vals) else "measured"
    return figure(sum(v["value"] for v in vals) / len(vals), prov)


def _entry_means(entry, judged):
    """Media de coste y latencia sobre las corridas juzgadas, si hay datos."""
    mc = _mean_cost(judged)
    if mc:
        entry["mean_cost_usd"] = mc
    lat = [r["latency_s"]["value"] for r in judged if r.get("latency_s")]
    if lat:
        entry["mean_latency_s"] = figure(sum(lat) / len(lat), "measured")


def _entry_blind(entry, cls_runs):
    """Declara el ciego roto solo si alguna corrida de la clase lleva judged_by."""
    blind_broken = [r["judged_by"] for r in cls_runs if r.get("judged_by")]
    if blind_broken:
        entry["judged_by"] = sorted(set(blind_broken))


def _class_entry(cls_runs):
    """Entrada de reporte para una clase; las corridas sin veredicto NO puntúan."""
    judged = [r for r in cls_runs if r.get("verdict") in ("accepted", "rejected")]
    accepted = [r for r in judged if r["verdict"] == "accepted"]
    entry = {
        "runs_recorded": figure(len(cls_runs), "measured"),
        "runs_without_verdict": figure(len(cls_runs) - len(judged), "measured"),
        "judged": figure(len(judged), "measured"),
        "accepted": figure(len(accepted), "measured"),
        "rejected": figure(len(judged) - len(accepted), "measured"),
        "acceptance_rate": figure(len(accepted) / len(judged) if judged else None, "judged"),
    }
    _entry_means(entry, judged)
    _entry_blind(entry, cls_runs)
    return entry


def aggregate(runs, tasks_by_class):
    """Agrega corridas con veredicto por clase; las sin veredicto NO puntúan."""
    return {
        cls: _class_entry([r for r in runs if r["task_class"] == cls])
        for cls in CLASSES
    }


def _check_figure(node, path, errors):
    """Una cifra válida lleva provenance medida|estimated|judged y un value."""
    if "provenance" not in node or node["provenance"] not in VALID_PROVENANCE:
        errors.append(f"{path}: cifra sin provenance válida")
    if "value" not in node:
        errors.append(f"{path}: provenance sin value")


def _walk(node, path, errors):
    """Recorre el reporte buscando cada cifra y le exige provenance."""
    if isinstance(node, dict):
        if "value" in node or "provenance" in node:
            _check_figure(node, path, errors)
            return
        for k, v in node.items():
            _walk(v, f"{path}.{k}", errors)
    elif isinstance(node, list):
        for i, v in enumerate(node):
            _walk(v, f"{path}[{i}]", errors)


def validate_report(report):
    """Auto-chequeo: cada cifra lleva provenance válida; incomplete declarado."""
    errors = []
    _walk(report, "report", errors)
    if "incomplete" not in report:
        errors.append("report.incomplete: falta la declaración de cohorte incompleta")
    for model, mr in report.get("models", {}).items():
        if mr["incomplete"] and report.get("cohort_average"):
            errors.append(f"{model}: cohorte incompleta pero se promedió")
    return errors

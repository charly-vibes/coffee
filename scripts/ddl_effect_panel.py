"""Máquina de panel repo × mes (parte de ddl-effect-tracker)."""
from __future__ import annotations

# ---------------------------------------------------------------------------
# Panel repo × mes (design D2)
# ---------------------------------------------------------------------------

# Campos de métrica admitidos en una fila de monthly[] (D2 + n visibles de
# recovery y CI — spec 'recuperación con censura visible' y denominadores).
PANEL_METRICS = (
    "sessions", "interactions",
    "errors_api_per_100", "errors_terapia_per_100",
    "recovery_p50_interactions", "recovery_censored_rate", "recovery_n",
    "session_commit_latency_p50_h",
    "ci_fail_rate", "runs_total", "runs_failed",
    "revert_ratio", "fix_ratio", "test_commit_ratio",
    "ah_check_findings", "dose",
)

# Claves de emisor que no son métricas del panel: provenance y audit metadata
# (latency_n/clamped_negative de commit_latency, dose_detail es per-repo,
# no per-mes).
_PANEL_META_KEYS = frozenset(
    {"provenance", "reason", "dose_detail", "latency_n", "clamped_negative"})

_PANEL_TAGS = frozenset({"reported", "derived", "cached", "missing"})


def _field_tag(prov, field):
    """Tag de provenance: {campo: tag} o tag escalar del emisor."""
    return prov.get(field) if isinstance(prov, dict) else prov


def _collect_reason(reason, field, reasons):
    """Reason: {campo: texto} (si el campo tiene razón) o texto del emisor."""
    if reason is None:
        return
    if isinstance(reason, dict):
        if field in reason:
            reasons[field] = reason[field]
    else:
        reasons[field] = reason


def _merge_emitter(name, em, acc):
    """Valida y mezcla un emisor en la celda; duplicados/campos unknown fallan.

    acc: {"repo", "month", "metrics", "src", "reasons"} acumuladores de celda.
    """
    repo, month = acc["repo"], acc["month"]
    metrics, src, reasons = acc["metrics"], acc["src"], acc["reasons"]
    prov = em.get("provenance")
    reason = em.get("reason")
    for field in (k for k in em if k not in _PANEL_META_KEYS):
        if field not in PANEL_METRICS:
            raise ValueError(
                f"{repo} {month}: campo '{field}' del emisor '{name}' no "
                "está en PANEL_METRICS")
        if field in metrics:
            raise ValueError(
                f"{repo} {month}: campo '{field}' emitido por más de un "
                "emisor")
        tag = _field_tag(prov, field)
        metrics[field] = None if tag == "missing" else em[field]
        src[field] = tag
        _collect_reason(reason, field, reasons)


def cell_from_emitters(repo, month, emitters):
    """Normaliza los emisores de un (repo, mes) a una celda del panel.

    emitters: {nombre: dict de reducer} — cada dict tiene métricas planas +
    'provenance' ({campo: tag} o tag escalar que aplica a todas las métricas
    del emisor) y opcionalmente 'reason' ({campo: texto} o texto). El tag
    'missing' normaliza el valor a None (nunca 0 fake, p.ej. dose 0.0 sin
    señal). Campos fuera de PANEL_METRICS o duplicados entre emisores fallan
    loud: el schema (coffe-nea) no puede derivar de un panel que mintió.
    """
    metrics, src, reasons = {}, {}, {}
    acc = {"repo": repo, "month": month, "metrics": metrics,
           "src": src, "reasons": reasons}
    for name, em in (emitters or {}).items():
        _merge_emitter(name, em, acc)
    out = {"repo": repo, "month": month}
    out.update(metrics)
    out["src"] = src
    if reasons:
        out["reasons"] = reasons
    return out


def _validate_cell_metrics(cell, metrics, src):
    """Invariantes loud de provenance: tag válido y None ⇔ 'missing'."""
    for field, value in metrics.items():
        tag = src.get(field)
        if tag not in _PANEL_TAGS:
            raise ValueError(
                f"{cell['repo']} {cell['month']}: '{field}' sin tag de "
                f"provenance válido (tiene {tag!r})")
        if (value is None) != (tag == "missing"):
            raise ValueError(
                f"{cell['repo']} {cell['month']}: '{field}' valor {value!r} "
                f"inconsistente con tag {tag!r} (None ⇔ missing)")


def build_panel(cells):
    """Ensambla monthly[] (design D2): filas repo × mes con provenance en toda
    cifra. Invariantes loud: toda métrica lleva tag de _PANEL_TAGS; None ⇔ tag
    'missing' (null con tag real o valor con tag missing mintirían el origen).
    Meses sin señal (todas las métricas None) quedan afuera del array — no
    nulls-fantasma. Salida ordenada por (repo, month), reproducible.
    """
    rows, seen = [], set()
    for cell in cells:
        metrics = {k: cell[k] for k in PANEL_METRICS if k in cell}
        src = cell.get("src", {})
        _validate_cell_metrics(cell, metrics, src)
        if all(v is None for v in metrics.values()):
            continue  # mes sin señal: afuera del array
        key = (cell["repo"], cell["month"])
        if key in seen:
            raise ValueError(
                f"{cell['repo']} {cell['month']}: celda duplicada — el panel "
                "es repo × mes")
        seen.add(key)
        row = {"repo": cell["repo"], "month": cell["month"]}
        row.update(metrics)
        row["src"] = dict(src)
        if cell.get("reasons"):
            row["reasons"] = dict(cell["reasons"])
        rows.append(row)
    rows.sort(key=lambda r: (r["repo"], r["month"]))
    return rows

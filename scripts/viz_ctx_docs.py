#!/usr/bin/env python3
"""
viz_ctx_docs.py — bloque CTX-DOCS del README para viz-ctx.py
(coffe-ruk 3.2)

Purpose: las cifras de contexto del README salen del JSON — contrato
--check-docs entre el reporte context_metrics y el bloque delimitado por
CTX_DOC_BEGIN / CTX_DOC_END.

Responsibilities:
- expected_ctx_figures(report): cifras esperadas con el formato del
  bloque CTX-DOCS (TODO sale del JSON, estilo FPA-143 de viz-fpa).
- check_docs_ctx(report, readme_path): lista de desvíos ([] = ok).

Rationale: bloque propio — el CHECK-DOCS de viz-fpa no admite cifras
extra. Split de viz-ctx.py (coffe-v5f): módulo aparte para que el
archivo principal quede bajo el límite de líneas.
"""

import re
from pathlib import Path

from viz_ctx_model import _clean_sessions
from viz_ctx_stats import _pctl

CTX_DOC_BEGIN = "<!-- CTX-DOCS:BEGIN -->"
CTX_DOC_END = "<!-- CTX-DOCS:END -->"

_UNAVAILABLE_FIGURES = {
    "Sesiones con contexto": "n/a",
    "Excluidas (patológicas)": "n/a",
    "Stubs": "n/a",
    "Pico p50 (tok)": "n/a",
    "Ratio validación": "n/a",
    "Factor chars/token": "n/a",
}


def expected_ctx_figures(report):
    """coffe-ruk (3.2): cifras de contexto con el formato del bloque
    CTX-DOCS del README — TODO sale del JSON (estilo FPA-143 de viz-fpa;
    bloque propio: el CHECK-DOCS de viz-fpa no admite cifras extra)."""
    cm = report.get("context_metrics") or {}
    if not cm.get("available"):
        return _UNAVAILABLE_FIGURES.copy()
    return _available_figures(cm)


def _available_figures(cm):
    """Cifras CTX-DOCS cuando context_metrics está disponible."""
    sessions = cm.get("sessions") or []
    meta = cm.get("meta") or {}
    clean = _clean_sessions(sessions)
    peaks = sorted(s["peak_ctx_est"] for s in clean)
    p50 = _pctl(peaks, 0.5)
    vr = meta.get("validation_ratio_median")
    excluded = meta.get("excluded_pathological",
                        sum(1 for s in sessions
                            if s.get("pathological")))
    return {
        "Sesiones con contexto": str(len(clean)),
        "Excluidas (patológicas)": str(excluded),
        "Stubs": str(meta.get("stubs", 0)),
        "Pico p50 (tok)": f"{p50:.0f}" if p50 else "n/a",
        "Ratio validación": f"{vr:.2f}" if vr else "n/a",
        "Factor chars/token": str(meta.get("chars_per_token")),
    }


def check_docs_ctx(report, readme_path):
    """Las cifras del bloque CTX-DOCS del README deben salir del JSON.
    Devuelve la lista de desvíos ([] = consistente)."""
    try:
        text = Path(readme_path).read_text()
    except OSError as e:
        return [f"no se pudo leer {readme_path}: {e}"]
    if CTX_DOC_BEGIN not in text or CTX_DOC_END not in text:
        return [f"README sin bloque CTX-DOCS (marcadores "
                f"{CTX_DOC_BEGIN} … {CTX_DOC_END})"]
    block = text.split(CTX_DOC_BEGIN, 1)[1].split(CTX_DOC_END, 1)[0]
    return _doc_errors(block, expected_ctx_figures(report))


def _doc_errors(block, expected):
    """Desvíos entre las cifras del bloque README y las del JSON."""
    seen = set()
    errors = []
    for line in block.splitlines():
        m = re.match(r"-\s+([^:]+):\s*(.+)$", line.strip())
        if not m:
            continue
        key, val = m.group(1).strip(), m.group(2).strip()
        seen.add(key)
        exp = expected.get(key)
        if exp is None:
            errors.append(f"cifra desconocida en README: {key}")
        elif val != exp:
            errors.append(f"{key}: README dice {val}, JSON dice {exp}")
    for key in expected:
        if key not in seen:
            errors.append(f"falta cifra en README: {key}")
    return errors

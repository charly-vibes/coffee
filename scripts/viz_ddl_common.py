"""Módulo extraído de viz-ddl.py (decomposición, comportamiento idéntico):
constantes compartidas (vistas, títulos, esqueleto) y helpers de render.
"""
import html
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

DDL_SCHEMA = (Path(__file__).resolve().parent.parent / "specs"
              / "ddl-effect.schema.json")

# deep-link por hash (spec: '6 vistas ... deep-link por hash'); la default es
# la primera — hash inválido ignorado, nunca 404 blanco (patrón FPA-171).
VIEWS = ("adopcion", "dosis-respuesta", "pre-post", "lazo-feedback",
         "errores", "calidad")

VIEW_TITLES = {
    "adopcion": "Línea de adopción",
    "dosis-respuesta": "Dosis-respuesta",
    "pre-post": "Pre/post + DiD",
    "lazo-feedback": "Lazo de feedback",
    "errores": "Errores",
    "calidad": "Calidad",
}

# Reporte vacío (forma del schema v1): lo usa build_html cuando el análisis
# aún no tiene datos y es la base del esqueleto del tracker.
SKELETON_REPORT = {
    "metadata": {"generated_at": None, "seed_bootstrap": 42,
                 "managed_tools": [],
                 "fleet_definition": "repos charly-vibes con actividad en "
                                     "sesiones in-scope"},
    "repos": [],
    "monthly": [],
    "analysis": {"its": [], "did": [], "dose_response": []},
}


def esc(s):
    return html.escape(str(s), quote=True)


def prov_tag(tag):
    """Tag visible de procedencia (reported/derived/cached/missing)."""
    return (f'<span class="prov-tag" title="procedencia: {esc(tag)}">'
            f'{esc(tag)}</span>')


def fig(value_html, tag):
    """Envoltorio de cifra con provenance (patrón FPA-003)."""
    return f'<span class="fig" data-provenance="{esc(tag)}">{value_html}</span>'


def placeholder(view, report):
    """Vista sin implementar todavía (esqueleto coffe-31l)."""
    tiene_datos = bool(report.get("monthly") or report.get("repos"))
    msg = ("datos cargados — la vista llega en un ticket posterior"
           if tiene_datos else "sin datos")
    return (f'<p class="placeholder">Vista “{VIEW_TITLES[view]}": {msg}.'
            f'{prov_tag("missing")}</p>')


def _fmt(x, nd=2):
    return "n/a" if x is None else f"{x:.{nd}f}"


def _estimator_table(headers, rows_html, empty_msg):
    if not rows_html:
        return f'<p class="placeholder">{esc(empty_msg)}</p>'
    head = "".join(f"<th>{esc(h)}</th>" for h in headers)
    body = "".join(rows_html)
    return (f'<table><thead><tr>{head}</tr></thead><tbody>{body}</tbody>'
            f'</table>')


def _monthly_rows(report):
    """Filas de monthly[] ordenadas por (repo, mes) — reproducible."""
    return sorted(report.get("monthly") or [],
                  key=lambda r: (r.get("repo", ""), r.get("month", "")))


def _cell(row, field, nd=2, suffix=""):
    """Celda de panel con provenance (patrón FPA-003): valor con tag de src,
    None con su razón visible — distinto de un 0 real (spec). Dicts (clases
    de error de terapia) se desglosan clave a clave."""
    val = row.get(field)
    tag = (row.get("src") or {}).get(field) or "missing"
    if val is None:
        reason = (row.get("reasons") or {}).get(field)
        return fig("n/a" + (f" ({esc(reason)})" if reason else ""), "missing")
    if isinstance(val, dict):
        if not val:
            return fig("—", tag)  # {} = sin clases, distinto de missing
        inner = ", ".join(f"{esc(k)} {v:.{nd}f}"
                          for k, v in sorted(val.items()))
        return fig(inner, tag)
    return fig(f"{val:.{nd}f}{suffix}", tag)


def its_rows_html(its):
    """Filas ITS (coffe-ktu): delta con IC bootstrap y n visibles; omitidos
    con reason explícito — nunca delta 0 disfrazado (spec)."""
    rows = []
    for e in its:
        label = f"{esc(e.get('repo', '?'))} · {esc(e.get('metric', '?'))}"
        if e.get("delta") is None:
            rows.append(
                f"<tr><td>{label}</td>"
                f"<td colspan='3'>omitido: {esc(e.get('reason', 'sin razón'))}</td>"
                f"</tr>")
            continue
        rows.append(
            f"<tr><td>{label}</td>"
            f"<td>{fig(_fmt(e['delta']), 'derived')}</td>"
            f"<td>IC 95%: {_fmt(e.get('ci_low'))} … {_fmt(e.get('ci_high'))}</td>"
            f"<td>n_pre {e.get('n_pre', '?')} · n_post {e.get('n_post', '?')}</td>"
            f"</tr>")
    return rows


def did_rows_html(did_rows):
    """Filas DiD (coffe-470): punto + IC bootstrap, n de cada celda visible;
    regla dura n<3 → omitido con reason, el punto no se grafica (anti-anécdota)."""
    rows = []
    for e in did_rows:
        label = esc(e.get("metric", "?"))
        if e.get("did") is None:
            rows.append(
                f"<tr><td>{label}</td>"
                f"<td colspan='3'>omitido: {esc(e.get('reason', 'sin razón'))}</td>"
                f"</tr>")
            continue
        rows.append(
            f"<tr><td>{label}</td>"
            f"<td>{fig(_fmt(e['did']), 'derived')}</td>"
            f"<td>IC 95%: {_fmt(e.get('ci_low'))} … {_fmt(e.get('ci_high'))}</td>"
            f"<td>Δ trat {_fmt(e.get('delta_treatment'))} · "
            f"Δ ctrl {_fmt(e.get('delta_control'))}</td>"
            f"<td>n_treatment: {e.get('n_treatment', '?')} · "
            f"n_control: {e.get('n_control', '?')}</td>"
            f"</tr>")
    return rows

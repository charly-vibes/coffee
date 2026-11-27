"""Módulo extraído de viz-fpa.py (decomposición, comportamiento idéntico)."""
import calendar  # noqa: F401
import html  # noqa: F401
import json  # noqa: F401
import re  # noqa: F401
import sys
from datetime import date, datetime  # noqa: F401
from html.parser import HTMLParser  # noqa: F401
from pathlib import Path  # noqa: F401

sys.path.insert(0, str(Path(__file__).resolve().parent))
import fpa_config  # noqa: F401,E402
import schema_validate  # noqa: F401,E402
import site_theme  # noqa: F401,E402




_WINDOWS = ("todo", "ytd", "mes")
_VERDICT_LABEL = {"mantener": "✅ mantener",
                  "cambiar": "🔁 cambiar",
                  "datos_insuficientes": "⚠ datos_insuficientes"}


def _usd4(x):
    """USD con 4 decimales: $ por tarea/tokens son cifras chicas."""
    if x is None:
        return "n/a"
    return "${:,.4f}".format(x)


def _cell(value, prov, fmt, reason=None):
    """Celda con provenance por celda (FPA-003); sin dato ⇒ n/a con razón
    (FPA-008), nunca 0."""
    if value is None:
        return _na_cell(reason or "sin dato en el scorecard")
    return (fig(fmt(value), prov) + prov_tag(prov)
            if prov else fig(fmt(value), "estimated"))


def _latency_cell(lat):
    """p50/p90 con cobertura al lado cuando coverage_pct < 50
    (coffe-dnx.6); None ⇒ n/a con razón."""
    if not isinstance(lat, dict):
        return _na_cell("latencia sin datos en la ventana")
    prov = "reported"
    if lat.get("p50_ms") is None and lat.get("p90_ms") is None:
        return _na_cell("latencia sin datos en la ventana")

    def _s(ms):
        return f"{ms / 1000:.1f}s" if ms is not None else "n/a"

    txt = f"p50 {_s(lat.get('p50_ms'))} · p90 {_s(lat.get('p90_ms'))}"
    cov = lat.get("coverage_pct")
    if cov is not None and cov < 50:
        txt += f" — cobertura {cov}%"
    elif cov is None:
        txt += " — " + _na_cell("cobertura no calculada")
    return fig(txt, prov) + prov_tag(prov)


def _split_cell(sub):
    """Split suscripción vs API (coffe-dnx.8), con provenance."""
    if not isinstance(sub, dict):
        return _na_cell("split suscripción/API sin datos")
    prov = sub.get("provenance", "estimated")
    txt = (f"sub {_usd4(sub.get('subscription'))} · "
           f"api {_usd4(sub.get('api'))}")
    return fig(txt, prov) + prov_tag(prov)


def _nulls_by_field(nulls):
    """nulls[] del scorecard → {campo: razón} (formato flexible)."""
    out = {}
    for n in nulls or []:
        if not isinstance(n, dict):
            continue
        key = n.get("field") or n.get("key") or n.get("name")
        if not key:
            continue
        out[key] = n.get("reason") or "sin dato"
    return out


def _scorecard_metric_cells(data, prov, nulls):
    """Celdas de métricas del scorecard (coffe-dnx.8), con provenance."""
    def c(key, fmt):
        return _cell(data.get(key), prov.get(key), fmt,
                     nulls.get(key, f"{key} sin dato en la ventana"))

    cells = [c("cost_weighted_per_1m", _usd4),
             c("cost_per_task", _usd4),
             c("cost_per_accepted_task", _usd4),
             # prompts/tarea: el scorecard .8 no lo emite (pendiente coffe-dnx.3)
             _na_cell("no emitido por el scorecard (coffe-dnx.3 pendiente)"),
             c("aborts_per_100_turns", lambda x: f"{x:.2f}"),
             c("tool_errors_per_100_turns", lambda x: f"{x:.2f}"),
             _latency_cell(data.get("latency"))]
    cs = data.get("cache_share")
    cells.append(_cell(cs, prov.get("cache_share"),
                       lambda x: f"{x * 100:.1f}%" if cs is not None else "n/a",
                       nulls.get("cache_share")))
    cells.append(c("model_switches", fmt_int))
    cells.append(_split_cell(data.get("subscription_vs_api")))
    return cells


def _scorecard_verdict_cell(verdict_by_model, model):
    """Celda del veredicto de la regla de decisión (coffe-dnx.8)."""
    v = verdict_by_model.get(model) or {}
    ratio = v.get("ratio_vs_reference")
    vtxt = _VERDICT_LABEL.get(v.get("verdict") or "", "n/a")
    if ratio is not None:
        vtxt += f" (ratio {ratio:.2f})"
    return f"<td>{esc_html(vtxt)}</td>"


def _scorecard_row(entry, window, verdict_by_model, reference):
    model = entry.get("model", "?")
    cells = [f"<td>{esc_html(model)}{esc_html(' (referencia)' if model == reference else '')}</td>"]
    data = entry.get("data")
    if data is None:
        reason = entry.get("reason") or entry.get("nulls") or "sin datos"
        cells.append(f'<td colspan="11" class="na">sin datos — '
                     f'{esc_html(reason)}</td>')
        return "<tr>" + "".join(cells) + "</tr>"
    prov = data.get("provenance") or {}
    nulls = _nulls_by_field(data.get("nulls"))
    cells.extend(_scorecard_metric_cells(data, prov, nulls))
    cells.append(_scorecard_verdict_cell(verdict_by_model, model))
    return "<tr>" + "".join(cells) + "</tr>"


def _verdict_banner(sc_meta):
    """Banner del veredicto de la regla de decisión (coffe-dnx.8)."""
    verdict = sc_meta.get("rule_verdict")
    if not verdict:
        return ""
    vtxt = _VERDICT_LABEL.get(verdict, esc_html(verdict))
    reason = sc_meta.get("rule_verdict_reason") or ""
    rule = sc_meta.get("rule") or {}
    return (f'<p class="banner-verdict">Regla de decisión '
            f'(coffe-dnx.8): <strong>{vtxt}</strong> — '
            f'{esc_html(reason)} '
            f'(métrica {esc_html(rule.get("metric") or "n/a")}, '
            f'ventana {esc_html(rule.get("windows") or "todo")}'
            f'{"; umbrales provisionales" if rule.get("provisional") else ""})'
            f'</p>')


def _scorecard_tables(entries, cohort, verdict_by_model, reference):
    """Una tabla por ventana (todo/ytd/mes), ordenada por la cohorte."""
    tables = []
    for window in _WINDOWS:
        window_entries = [e for e in entries if e.get("window") == window]
        order = cohort or [e.get("model") for e in window_entries]
        ordered = sorted(
            window_entries,
            key=lambda e: order.index(e.get("model"))
            if e.get("model") in order else len(order))
        rows = "".join(_scorecard_row(e, window, verdict_by_model, reference)
                       for e in ordered)
        tables.append(
            f'<table class="small"><caption>Scorecard de cohorte — ventana '
            f'{window} (coffe-dnx.8)</caption>'
            '<thead><tr><th>Modelo</th><th>$ ponderado por 1M tokens</th>'
            '<th>$ / tarea</th><th>$ / tarea aceptada</th>'
            '<th>Prompts/tarea</th><th>Abortos/100 turnos</th>'
            '<th>Errores tool/100 turnos</th><th>Latencia p50/p90</th>'
            '<th>Cache share</th><th>Escalaciones (model switches)</th>'
            '<th>Suscripción vs API</th><th>Veredicto regla</th></tr>'
            f'</thead><tbody>{rows}</tbody></table>')
    return tables


def _scorecard_note(sc_meta, reference):
    """Nota metodológica bajo las tablas del scorecard."""
    return (f'<p class="small">$ ponderado usa el cache_factor vigente del '
            f'pricing ({esc_html(sc_meta.get("cache_factor_note") or "")}) '
            f'— la cifra por 1M tokens nunca es el coste sobre tokens '
            f'totales sin ponderar por caché. '
            f'Cohorte: {esc_html(sc_meta.get("cohort_name") or "n/a")} '
            f'(fuente {esc_html(sc_meta.get("cohort_source") or "n/a")}); '
            f'referencia: {esc_html(reference or "n/a")}.</p>')


def models_html(report, cfg):
    """coffe-dnx.6: contenido de la vista Modelos — una tabla por ventana
    (todo/ytd/mes) con las filas de la cohorte, banner del veredicto de la
    regla de decisión y provenance por celda. Datos: model_scorecard[] del
    reporte (coffe-dnx.8); el CSV exportado conserva la provenance porque
    cada celda lleva su tag visible."""
    sc_meta = (report.get("metadata") or {}).get("scorecard") or {}
    entries = report.get("model_scorecard") or []
    cohort = sc_meta.get("models") or []
    reference = sc_meta.get("reference")
    verdict_by_model = sc_meta.get("rule_verdict_per_model") or {}

    banner = _verdict_banner(sc_meta)
    tables = _scorecard_tables(entries, cohort, verdict_by_model, reference)
    if not tables or not entries:
        tables = [f'<p class="small">{_na_cell(
            "model_scorecard[] ausente en el reporte (coffe-dnx.8)")}</p>']

    note = _scorecard_note(sc_meta, reference)
    return (f'<details class="tree" data-tree="models-scorecard" '
            f'id="models-scorecard" open><summary><h2>Modelos: coste y '
            f'resultado por cohorte</h2></summary>{banner}{"".join(tables)}'
            f'{note}</details>')
from viz_fpa_common import _link  # noqa: E402

_link(globals())

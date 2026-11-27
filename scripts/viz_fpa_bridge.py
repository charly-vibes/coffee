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

def build_bridge(prev, cur, prev_meta, cur_meta):
    """FPA-060…067: bridge del coste efectivo prior→seleccionado.

    Con mes parcial usa valores FME y lo etiqueta (FPA-066). Modelos sin
    mes previo usan su rate actual como prior — contribuyen solo a Mix
    (FPA-064). Identidad Volume+Mix+Rate = Δcoste dentro de $0.01 (FPA-065):
    el residuo de redondeo se absorbe en Mix y se verifica con falla ruidosa.
    Sin desglose de coste por modelo → componentes n/a con razón (FPA-008).
    Eje truncado etiquetado cuando |Δ| es pequeño vs los totales (FPA-067).
    """
    sp, sc = _fme_scale(prev_meta), _fme_scale(cur_meta)
    fme = abs(sp - 1.0) > 1e-9 or abs(sc - 1.0) > 1e-9
    pm, cm = _eff_by_model(prev.get("models")), _eff_by_model(cur.get("models"))
    q0, q1, cost0, cost1 = _scaled_totals(prev, cur, sp, sc)

    label = month_label(cur_meta["ym"])
    fme_note = (f" (FME: {month_label(prev_meta['ym'])} ×{sp:.2f} · "
                f"{label} ×{sc:.2f})") if fme else ""
    common = {
        "ym": cur_meta["ym"], "label": label + fme_note, "fme": fme,
        "cost0": round(cost0, 2), "cost1": round(cost1, 2),
        "delta": round(cost1 - cost0, 2),
        "truncated": abs(cost1 - cost0) < 0.1 * max(abs(cost0), abs(cost1), 1e-9),
    }

    # FPA-008: sin coste por modelo no hay rates → PVM no computable
    if _needs_n_a(cost0, cost1, pm, cm):
        common.update({
            "volume": None, "mix": None, "rate": None,
            "identity_residual": None, "axis_label": "eje completo",
            "n_a_reason": ("el reporte no desglosa coste por modelo; el bridge "
                           "requiere rates por modelo (FPA-061…063)"),
        })
        return common

    volume, mix, rate = build_pvm(q0, q1, _by_model_rows(pm, cm, sp, sc))
    _apply_identity(common, (volume, mix, rate), cost1 - cost0)
    return common


def _fme_scale(meta):
    return meta["total_days"] / meta["elapsed"] if meta["partial"] else 1.0


def _eff_by_model(mo):
    out = {}
    for model, v in (mo or {}).items():
        if isinstance(v, dict):
            out[model] = {"q": v.get("interactions", 0),
                          "cost": v.get("cost_effective", 0.0) or 0.0}
        else:  # counters del reporte viejo: sin coste por modelo
            out[model] = {"q": v, "cost": 0.0}
    return out


def _scaled_totals(prev, cur, sp, sc):
    fme = abs(sp - 1.0) > 1e-9 or abs(sc - 1.0) > 1e-9
    q0, q1 = prev["interactions"], cur["interactions"]
    cost0, cost1 = prev["cost_effective"], cur["cost_effective"]
    if fme:
        q0, q1 = q0 * sp, q1 * sc
        cost0, cost1 = cost0 * sp, cost1 * sc
    return q0, q1, cost0, cost1


def _needs_n_a(cost0, cost1, pm, cm):
    return (cost0 and not sum(d["cost"] for d in pm.values())) or \
           (cost1 and not sum(d["cost"] for d in cm.values()))


def _by_model_rows(pm, cm, sp, sc):
    by_model = []
    for m in sorted(set(pm) | set(cm)):
        d0, d1 = pm.get(m, {}), cm.get(m, {})
        # cantidades escaladas al factor FME de su mes (coherentes con Q0/Q1)
        q0_i, c0_i = d0.get("q", 0) * sp, d0.get("cost", 0.0) * sp
        q1_i, c1_i = d1.get("q", 0) * sc, d1.get("cost", 0.0) * sc
        # FPA-064: sin mes previo → rate prior = rate actual (solo Mix)
        p0 = (c0_i / q0_i) if q0_i else ((c1_i / q1_i) if q1_i else 0.0)
        p1 = (c1_i / q1_i) if q1_i else 0.0
        by_model.append((m, q0_i, p0, q1_i, p1))
    return by_model


def _apply_identity(common, vmr, delta):
    """FPA-065: identidad V+M+R = Δ; el residuo de redondeo lo absorbe Mix."""
    volume, mix, rate = vmr
    residual = volume + mix + rate - delta
    mix -= residual  # FPA-065: absorbe el residuo de redondeo en Mix
    residual = volume + mix + rate - delta
    if abs(residual) > 0.01:
        raise ValueError(
            f"identidad del bridge rota (FPA-065): V+M+R = "
            f"{volume + mix + rate:.4f} vs Δ {delta:.4f} (residuo {residual:.4f})")
    common.update({
        "volume": round(volume, 2), "mix": round(mix, 2),
        "rate": round(rate, 2),
        "identity_residual": round(residual, 6),
        "axis_label": ("eje truncado (Δ pequeño vs totales)"
                       if common["truncated"] else "eje completo"),
    })


def _wf_viewbox(base, end, floats):
    """Rango del eje con padding del 8%."""
    lo = min([base, end] + [min(a, z) for a, z in floats])
    hi = max([base, end] + [max(a, z) for a, z in floats])
    span = (hi - lo) or 1.0
    pad = 0.08 * span
    return lo - pad, hi + pad

def _wf_layout(w, nsteps):
    """Ancho de barra y gap para n barras en `w` px."""
    n = nsteps + 2
    bw = min(60.0, (w - 30) / n - 12)
    gap = (w - 20 - n * bw) / (n - 1)
    return bw, gap

def _wf_series(b, geom, lohi, layout):
    """Barras + textos del waterfall (cost₀, V/M/R, cost₁)."""
    steps, base, floats, end = geom
    lo, hi, h = lohi
    x, bw, gap = layout

    def Y(v):
        return h - 24 - (v - lo) / (hi - lo) * (h - 40)

    def rect(x, ys, spec):
        y_a, y_b = ys
        cls, name, val = spec
        top, bot = min(y_a, y_b), max(y_a, y_b)
        label = f"{name}: {_fmt_signed(val)}"
        return (f'<rect x="{x:.1f}" y="{top:.1f}" width="{bw:.1f}" '
                f'height="{max(bot - top, 1.0):.1f}" class="{cls}" '
                f'tabindex="0" role="img" aria-label="{esc_html(label)}" '
                f'data-label="{esc_html(name)}" '
                f'data-value="{esc_html(_fmt_signed(val))}"/>')

    bars, texts = [], []
    bars.append(rect(x, (Y(lo), Y(base)), ("wfb", "cost₀", b["cost0"])))
    texts.append(_wf_text(x + bw / 2, Y(base) - 3, fmt_usd(b["cost0"])))
    x += bw + gap
    for (name, v), (fa, fz) in zip(steps, floats):
        bars.append(rect(x, (Y(fa), Y(fz)),
                         ("wfup" if v >= 0 else "wfdn", name, v)))
        ytxt = Y(max(fa, fz)) - 3
        texts.append(_wf_text(x + bw / 2, ytxt, _fmt_signed(v)))
        x += bw + gap
    bars.append(rect(x, (Y(lo), Y(end)), ("wfb", "cost₁", b["cost1"])))
    texts.append(_wf_text(x + bw / 2, Y(end) - 3, fmt_usd(b["cost1"])))
    return bars, texts

def _wf_axis(b):
    return (f'<text x="12" y="14" class="wfl">{esc_html(b["axis_label"])}</text>'
            if b.get("truncated") else "")

def _wf_aria(b):
    return (f'bridge {esc_html(b["label"] or "")}: '
            f'Volumen {fmt_usd(b["volume"])}, Mix {fmt_usd(b["mix"])}, '
            f'Rate {fmt_usd(b["rate"])}')

def waterfall_svg(b, w=420, h=150):
    """FPA-060: waterfall Volume/Mix/Rate como SVG inline determinista.
    Barras flotantes desde cost₀ hasta cost₁; eje truncado etiquetado
    cuando aplica (FPA-067)."""
    geom = _wf_geom(b)
    steps, base, floats, end = geom
    lo, hi = _wf_viewbox(base, end, floats)
    bw, gap = _wf_layout(w, len(steps))
    bars, texts = _wf_series(b, geom, (lo, hi, h), (12.0, bw, gap))
    labels = _wf_labels(steps, bw, gap, h)
    axis = _wf_axis(b)
    return (f'<svg class="wf chart" width="{w}" height="{h}" viewBox="0 0 {w} {h}" '
            f'role="img" aria-label="{_wf_aria(b)}">'
            f'{axis}{"".join(bars)}{"".join(texts)}{"".join(labels)}</svg>')


def _wf_geom(b):
    steps = [("Volumen", b["volume"]), ("Mix", b["mix"]), ("Rate", b["rate"])]
    base = b["cost0"]
    floats = []
    lvl = base
    for _, v in steps:
        floats.append((lvl, lvl + v))
        lvl += v
    return steps, base, floats, lvl


def _wf_text(x, y, content):
    return (f'<text x="{x:.1f}" y="{y:.1f}" class="wft" '
            f'text-anchor="middle">{content}</text>')


def _wf_labels(steps, bw, gap, h):
    labels = []
    lx = 12.0
    for name in ["cost₀"] + [n for n, _ in steps] + ["cost₁"]:
        labels.append(f'<text x="{lx + bw / 2:.1f}" y="{h - 6:.1f}" class="wfl" '
                      f'text-anchor="middle">{name}</text>')
        lx += bw + gap
    return labels

def _stack_segs(models):
    """Segmentos por modelo; proxy=True si algún modelo no trae desglose."""
    segs, proxy = [], False
    for model, v in models.items():
        if isinstance(v, dict):
            segs.append({"model": model,
                         "cost": v.get("cost_effective", 0.0) or 0.0,
                         "interactions": v.get("interactions", 0)})
        else:
            segs.append({"model": model, "cost": 0.0, "interactions": v})
            proxy = True
    return segs, proxy

def _apply_shares(segs):
    """Asigna shares que suman 100%; devuelve (metric, proxy) o None."""
    total_cost = sum(s["cost"] for s in segs)
    total_inter = sum(s["interactions"] for s in segs)
    if total_cost > 0:
        metric, proxy = "coste efectivo", False
        for s in segs:
            s["share"] = round(s["cost"] / total_cost, 6)
    elif total_inter > 0:
        metric, proxy = "interacciones (proxy)", True
        for s in segs:
            s["share"] = round(s["interactions"] / total_inter, 6)
    else:
        return None
    return metric, proxy

def build_mix_stack(report):
    """FPA-068: mix de modelos por mes como shares que suman 100%.
    Share de coste efectivo cuando hay desglose; con counters (sin coste
    por modelo) → proxy por interacciones, señalado como proxy (FPA-008)."""
    stack = {}
    for meta in build_months(report):
        if not meta["has_data"]:
            continue
        models = report["monthly"][meta["ym"]].get("models") or {}
        segs, proxy = _stack_segs(models)
        shares = _apply_shares(segs)
        if shares is None:
            continue
        metric, proxy = shares
        segs = sorted((s for s in segs if s["share"] > 0),
                      key=lambda s: -s["share"])
        stack[meta["ym"]] = {
            "label": meta["label"], "segments": segs, "proxy": proxy,
            "metric": metric,
            "proxy_reason": ("modelos sin coste desglosado en el reporte; "
                             "shares por interacciones (proxy)" if proxy else None),
        }
    return stack
def build_bridge_section(report):
    """FPA-060/068: pares prior→mes con datos (waterfall por par) + stack
    100% del mix por mes."""
    months = [m for m in build_months(report) if m["has_data"]]
    raw = report["monthly"]
    pairs = {}
    prev = None
    for meta in months:
        if prev is not None:
            b = build_bridge(raw[prev["ym"]], raw[meta["ym"]], prev, meta)
            if not b.get("n_a_reason"):
                b["svg"] = waterfall_svg(b)
            pairs[meta["ym"]] = b
        prev = meta
    return {"provenance": "reported", "pairs": pairs,
            "mix_stack": build_mix_stack(report)}


def _plan_for(cfg, tool, when):
    """Entrada de suscripción activa para `tool` en `when` (date)."""
    for entry in cfg["subscriptions"].get(tool, []):
        start = date.fromisoformat(entry["start"])
        end = date.fromisoformat(entry["end"]) if entry.get("end") else None
        if start <= when and (end is None or when < end):
            return entry
    return None
from viz_fpa_common import _link  # noqa: E402

_link(globals())

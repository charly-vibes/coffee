"""Módulo extraído de viz-fpa.py (decomposición, comportamiento idéntico)."""
import sys
from dataclasses import dataclass
from typing import NamedTuple
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import fpa_config  # noqa: F401,E402
import schema_validate  # noqa: F401,E402
import site_theme  # noqa: F401,E402

def kpi_ingredients(report, cfg):
    """Ingredientes crudos por mes (FPA-017): costes, días, tokens,
    sesiones, top-3, premium, multitasking, outcomes, tokens por tool."""
    sessions_m = report.get("sessions_monthly", {}) or {}
    ing = {}
    for meta in build_months(report):
        if not meta["has_data"]:
            continue
        ing[meta["ym"]] = _month_ingredients(report, cfg, sessions_m, meta)
    return ing

def _hourly_month(report, ym):
    """Interacciones por tool, excluidas sin tokens y multitasking del mes."""
    inter_by_tool, notokens_inter, mt_inter = {}, 0, 0
    for h, hb in report.get("hourly", {}).items():
        if h[:7] != ym:
            continue
        if hb.get("projects_active", 0) >= 2:
            mt_inter += hb["interactions"]
        for tool, ts_ in hb.get("tools", {}).items():
            inter_by_tool[tool] = inter_by_tool.get(tool, 0) + ts_.get("req", 0)
            if (ts_.get("in", 0) + ts_.get("out", 0) + ts_.get("cache_read", 0)
                    + ts_.get("cache_write", 0)) == 0:
                notokens_inter += ts_.get("req", 0)
    return inter_by_tool, notokens_inter, mt_inter

def _premium_models(cfg):
    return [p.lower() for p in cfg.get("premium_models", [])]

def _premium_cost(mo, premium):
    """Coste de modelos premium (lista del config → assumed)."""
    return sum(_bucket(st)[1] for model, st in mo.get("models", {}).items()
               if any(p in model.lower() for p in premium))

def _proj_costs(report, ym):
    """(mapa proyecto→coste, costes ordenados desc) del mes."""
    proj_costs_map = {p: v[ym]["cost_effective"]
                      for p, v in report.get("project_monthly", {}).items()
                      if ym in v}
    return proj_costs_map, sorted(proj_costs_map.values(), reverse=True)
def _token_totals(mo):
    """Totales de tokens del mes: (in, out, cache_read, cache_write)."""
    inp = out_t = cr = cw = 0
    for t in mo.get("tokens_by_model", {}).values():
        inp, out_t = inp + t.get("in", 0), out_t + t.get("out", 0)
        cr, cw = cr + t.get("cache_read", 0), cw + t.get("cache_write", 0)
    return inp, out_t, cr, cw
def _month_ingredients(report, cfg, sessions_m, meta):
    """Ingredientes crudos de un mes con datos (FPA-017)."""
    ym, mo = meta["ym"], report["monthly"][meta["ym"]]
    premium = _premium_models(cfg)
    # FPA-045: tools sin datos de tokens ese mes → exclusión con share
    inter_by_tool, notokens_inter, mt_inter = _hourly_month(report, ym)
    proj_costs_map, proj_costs = _proj_costs(report, ym)
    outc = mo.get("outcomes_by_project") or {}
    premium_cost = _premium_cost(mo, premium)
    inp, out_t, cr, cw = _token_totals(mo)
    sm = sessions_m.get(ym, {})
    return {
        "cost_effective": mo["cost_effective"],
        # coffe-a31.3: cash = ledger (*reported*) o fallback tracker (assumed)
        "cost_cash": meta["cost_cash"], "cash_provenance": meta["cash_provenance"],
        "interactions": mo["interactions"],
        # FPA-041: días efectivos (parciales: elapsed)
        "days": meta["elapsed"], "partial": meta["partial"],
        "sessions": sm.get("total", 0), "sessions_agent": sm.get("with_agent", 0),
        "tokens": {"in": inp, "out": out_t, "cache_read": cr, "cache_write": cw},
        "excluded_interactions": notokens_inter, "interactions_by_tool": inter_by_tool,
        "mt_interactions": mt_inter,
        "top3_cost": sum(proj_costs[:3]), "proj_cost": sum(proj_costs),
        # coffe-hn7: el top-3 de una ventana se computa sobre el agregado
        # del periodo (matchea Pareto/alerta), no sumando top-3 mensuales
        "proj_costs": proj_costs_map, "premium_cost": premium_cost,
        "commits": sum(o.get("commits", 0) or 0 for o in outc.values()),
        "releases": sum(o.get("releases", 0) or 0 for o in outc.values()),
    }
def _pct_change(cur, prev):
    """Delta porcentual; None si no hay base o valor (FPA-042: nunca infinito)."""
    if prev is None or prev == 0 or cur is None:
        return None
    return (cur - prev) / prev

def _sum_ing(ing, window, key):
    return sum(ing[ym].get(key, 0) for ym in window if ym in ing)
def _sum_tokens(ing, window, key):
    return sum(ing[ym]["tokens"].get(key, 0) for ym in window if ym in ing)
def _spark(ing, compute):
    """Serie mensual de un KPI (solo meses con datos) para el sparkline."""
    return [{"ym": ym, "value": round(v, 6)} for ym in sorted(ing) if (v := compute(ing[ym])) is not None]

def _spark_ratio(ing, nk, dk, div=1):
    return _spark(ing, lambda i: i[nk] / (i[dk] / div) if i[dk] else None)

@dataclass
class KpiDelta:
    """Grupo delta/spark del KPI (tasa diaria o Δ% de periodo)."""
    prev: object = None; cur_rate: object = None
    prev_rate: object = None; spark: object = None

@dataclass
class KpiSpec:
    key: str; label: str
    value: object = None
    fmt: object = None
    reason: object = None
    provenance: str = "reported"
    excluded_share: object = None
    delta: KpiDelta = None

def _kpi(spec):
    """Card de KPI normalizada. value None → n/a con razón (FPA-008/042).
    Delta: tasa diaria si viene cur_rate/prev_rate (FPA-041); si no, Δ% del
    valor del periodo vs el previo. Sin periodo previo en el reporte → n/a."""
    d = spec.delta or KpiDelta()
    if d.cur_rate is not None:
        delta, kind = _pct_change(d.cur_rate, d.prev_rate), "daily-rate"
    else:
        delta, kind = _pct_change(spec.value, d.prev), "periodo"
    k = {"key": spec.key, "label": spec.label, "value": spec.value,
         "display": spec.fmt(spec.value) if spec.value is not None else "n/a",
         "provenance": spec.provenance, "reason": spec.reason,
         "delta": None if delta is None else round(delta, 4),
         "delta_display": (f"{'+' if delta >= 0 else ''}{delta * 100:.1f}%" if delta is not None else "n/a"),
         "delta_kind": kind, "spark": d.spark or []}
    if spec.excluded_share:
        k["excluded_share"] = round(spec.excluded_share, 4)
        k["excluded_display"] = f"{spec.excluded_share * 100:.1f}%"
    return k

def _top3_of(ing, window):
    """Coste top-3 de proyectos del agregado de la ventana (coffe-hn7)."""
    acc = {}
    for ym in window:
        for p, c in (ing.get(ym, {}).get("proj_costs") or {}).items():
            acc[p] = acc.get(p, 0.0) + c
    return sum(sorted(acc.values(), reverse=True)[:3])
def kpis_for_window(ing, window, prior):
    """KPIs FPA-030…045 para una ventana, delta vs ventana previa de igual
    longitud + sparkline (FPA-042 n/a con razón; FPA-041 tasas diarias)."""
    if not window:
        return []
    return (_kpi_cost_cards(ing, window, prior) + _kpi_share_cards(ing, window, prior)
            + _kpi_token_cards(ing, window, prior) + _kpi_outcome_cards(ing, window, prior))

def _ratio(n, d):
    return n / d if d else None
def _pos(v):
    return v or None
def _den_reason(d, reason):
    return None if d else reason
def _psum(ing, prior, key):
    return _sum_ing(ing, prior, key) if prior else 0
def _cash_provenance(ing, window):
    # coffe-a31.3: provenance del cash KPI sigue al ledger
    en_ventana = [ing[ym] for ym in window if ym in ing]
    return ("reported" if en_ventana and all(
        i.get("cash_provenance") == "reported" for i in en_ventana) else "assumed")

class _CostCtx(NamedTuple):
    """Ingredientes de las tarjetas de coste; prior = tuplas previas."""
    ing: dict; eff: float; cash: float; inter: float; days: float
    sessions: float; prior: tuple; prov: str

def _cost_ctx(ing, window, prior):
    s = lambda k: _sum_ing(ing, window, k)  # noqa: E731
    p = lambda k: _psum(ing, prior, k)  # noqa: E731
    pr = (p("cost_effective"), p("cost_cash"), p("interactions"),
          p("days"), p("sessions"))
    return _CostCtx(ing, s("cost_effective"), s("cost_cash"), s("interactions"),
                    s("days"), s("sessions"), pr, _cash_provenance(ing, window))

def _cost_card_rows_a(c):
    """Filas (spec-kwargs, delta-kwargs): efectivo, cash y leverage."""
    p_eff, p_cash, _pi, _pd, _ps = c.prior
    return [
        (dict(key="cost_effective", label="Coste efectivo", value=_pos(c.eff), fmt=fmt_usd,
              reason=_den_reason(c.eff, "sin coste efectivo en el periodo")),
         dict(cur_rate=_ratio(c.eff, c.days), prev_rate=_ratio(p_eff, _pd),
              spark=_spark_ratio(c.ing, "cost_effective", "days"))),
        (dict(key="cost_cash", label="Coste cash", value=_pos(c.cash), fmt=fmt_usd,
              reason=_den_reason(c.cash, "sin coste cash en el periodo"), provenance=c.prov),
         dict(cur_rate=_ratio(c.cash, c.days), prev_rate=_ratio(p_cash, _pd),
              spark=_spark_ratio(c.ing, "cost_cash", "days"))),
        (dict(key="leverage", label="Leverage", value=_ratio(c.eff, c.cash),
              fmt=lambda v: f"{v:.1f}×", reason=_den_reason(c.cash, "requiere coste cash > 0"),
              provenance=c.prov),
         dict(prev=_ratio(p_eff, p_cash), spark=_spark_ratio(c.ing, "cost_effective", "cost_cash"))),
    ]

def _cost_card_rows_b(c):
    """Filas (spec-kwargs, delta-kwargs): por 1k y por sesión."""
    p_eff, p_cash, p_inter, _pd, p_sessions = c.prior
    d1k = lambda eff, inter: _ratio(eff, inter / 1000)  # noqa: E731
    den_inter = _den_reason(c.inter, "sin interacciones en el periodo")
    return [
        (dict(key="cost_per_1k_eff", label="Coste efectivo por 1k", value=d1k(c.eff, c.inter),
              fmt=fmt_usd, reason=den_inter),
         dict(prev=d1k(p_eff, p_inter), spark=_spark_ratio(c.ing, "cost_effective", "interactions", 1000))),
        (dict(key="cost_per_1k_cash", label="Coste cash por 1k", value=d1k(c.cash, c.inter),
              fmt=fmt_usd, reason=den_inter, provenance=c.prov),
         dict(prev=d1k(p_cash, p_inter), spark=_spark_ratio(c.ing, "cost_cash", "interactions", 1000))),
        (dict(key="cost_per_session", label="Coste por sesión", value=_ratio(c.eff, c.sessions),
              fmt=fmt_usd, reason=_den_reason(c.sessions, "sin sesiones en el periodo")),
         dict(prev=_ratio(p_eff, p_sessions), spark=_spark_ratio(c.ing, "cost_effective", "sessions"))),
    ]

def _kpi_cost_cards(ing, window, prior):
    """FPA-030/031: costes con delta de tasa diaria (FPA-041)."""
    c = _cost_ctx(ing, window, prior)
    return [_kpi(KpiSpec(delta=KpiDelta(**d), **kw))
            for kw, d in _cost_card_rows_a(c) + _cost_card_rows_b(c)]

def _kpi_share_cards(ing, window, prior):
    """Shares: top-3 (coffe-hn7), premium (config → assumed), autonomous,
    multitasking (FPA-045)."""
    eff, inter = _sum_ing(ing, window, "cost_effective"), _sum_ing(ing, window, "interactions")
    sessions, pcost = _sum_ing(ing, window, "sessions"), _sum_ing(ing, window, "proj_cost")
    p_eff = _psum(ing, prior, "cost_effective")
    p_inter, p_sessions = _psum(ing, prior, "interactions"), _psum(ing, prior, "sessions")
    p_proj = _psum(ing, prior, "proj_cost")
    return [
        _kpi(KpiSpec("top3_concentration", "Concentración top-3",
                     _ratio(_top3_of(ing, window), pcost), lambda v: f"{v * 100:.1f}%",
                     _den_reason(pcost, "sin coste por proyecto en el periodo"),
                     delta=KpiDelta(prev=_ratio(_top3_of(ing, prior) if prior else 0, p_proj),
                                    spark=_spark_ratio(ing, "top3_cost", "proj_cost")))),
        # share premium: la lista de modelos premium viene del config → assumed
        _kpi(KpiSpec("premium_share", "Share premium",
                     _ratio(_sum_ing(ing, window, "premium_cost"), eff),
                     lambda v: f"{v * 100:.1f}%", provenance="assumed",
                     reason=_den_reason(eff, "sin coste efectivo en el periodo"),
                     delta=KpiDelta(prev=_ratio(_psum(ing, prior, "premium_cost"), p_eff),
                                    spark=_spark_ratio(ing, "premium_cost", "cost_effective")))),
        _kpi(KpiSpec("autonomous_share", "Autonomous share",
                     _ratio(_sum_ing(ing, window, "sessions_agent"), sessions),
                     lambda v: f"{v * 100:.1f}%",
                     _den_reason(sessions, "sin sesiones en el periodo"),
                     delta=KpiDelta(prev=_ratio(_psum(ing, prior, "sessions_agent"), p_sessions),
                                    spark=_spark_ratio(ing, "sessions_agent", "sessions")))),
        _kpi(KpiSpec("multitasking_share", "Multitasking",
                     _ratio(_sum_ing(ing, window, "mt_interactions"), inter),
                     lambda v: f"{v * 100:.1f}%",
                     _den_reason(inter, "sin interacciones en el periodo"),
                     delta=KpiDelta(prev=_ratio(_psum(ing, prior, "mt_interactions"), p_inter),
                                    spark=_spark_ratio(ing, "mt_interactions", "interactions")))),
    ]

def _kpi_token_cards(ing, window, prior):
    """FPA-043/045: token KPIs con exclusión de tools sin tokens."""
    tok = {k: _sum_tokens(ing, window, k) for k in ("in", "out", "cache_read", "cache_write")}
    p_tok = {k: _sum_tokens(ing, prior, k) if prior else 0
             for k in ("in", "out", "cache_read", "cache_write")}
    denom = tok["in"] + tok["cache_read"] + tok["cache_write"]
    p_denom = p_tok["in"] + p_tok["cache_read"] + p_tok["cache_write"] if prior else 0
    inter = _sum_ing(ing, window, "interactions")
    exsh = _ratio(_sum_ing(ing, window, "excluded_interactions"), inter)
    return [
        _kpi(KpiSpec("cache_hit_rate", "Cache-hit rate",
                     _ratio(tok["cache_read"], denom), lambda v: f"{v * 100:.1f}%",
                     _den_reason(denom, "sin tokens registrados en el periodo"),
                     excluded_share=exsh,
                     delta=KpiDelta(prev=_ratio(p_tok["cache_read"], p_denom),
                                    spark=_spark(ing, lambda i: (lambda d: i["tokens"]["cache_read"] / d if d else None)(
                                        i["tokens"]["in"] + i["tokens"]["cache_read"] + i["tokens"]["cache_write"]))))),
        _kpi(KpiSpec("out_in_ratio", "Ratio out/in",
                     _ratio(tok["out"], tok["in"]), lambda v: f"{v:.2f}",
                     _den_reason(tok["in"], "sin tokens de input en el periodo"),
                     excluded_share=exsh,
                     delta=KpiDelta(prev=_ratio(p_tok["out"], p_tok["in"]),
                                    spark=_spark(ing, lambda i: i["tokens"]["out"] / i["tokens"]["in"] if i["tokens"]["in"] else None)))),
    ]

def _kpi_outcome_cards(ing, window, prior):
    """FPA-040: outcome KPIs solo donde haya datos."""
    eff, p_eff = _sum_ing(ing, window, "cost_effective"), _psum(ing, prior, "cost_effective")
    out = []
    for key, label, ik in (("cost_per_commit", "Coste por commit", "commits"),
                           ("cost_per_release", "Coste por release", "releases")):
        if not (tot := _sum_ing(ing, window, ik)):
            continue
        p_tot = _psum(ing, prior, ik)
        out.append(_kpi(KpiSpec(key, label, eff / tot, fmt_usd,
                                delta=KpiDelta(prev=(p_eff / p_tot) if prior and p_tot else None,
                                               spark=_spark_ratio(ing, "cost_effective", ik)))))
    return out
from viz_fpa_common import _link  # noqa: E402

_link(globals())

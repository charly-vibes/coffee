#!/usr/bin/env python3
"""Agregación: estado y filas base (extraído de usage-tracker.py).

Parte de la descomposición de scripts/usage-tracker.py (coffe-kav.1):
cada módulo conserva el código original verbatim; los nombres compartidos
viajan por _link() (mismo patrón que viz_fpa_common) y el estado mutable
por usage_state.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import usage_state

class _AggState:
    """Contenedores de acumulación de aggregate() (un lugar, sin copiar)."""

    def __init__(self):
        self.hourly = {}
        self.daily = {}
        self.monthly = {}
        self.by_project = {}
        self.hour_projects = defaultdict(set)  # hora -> proyectos distintos activos
        self.day_projects = defaultdict(set)   # día -> proyectos distintos activos
        self.proj_day = defaultdict(Counter)   # proyecto -> {día: interacciones}
        self.row_kinds = defaultdict(Counter)  # FPA-140: kinds desde filas
        # FPA-011/019: breakdown mensual por tool y modelo
        self.month_tools = defaultdict(lambda: defaultdict(lambda: {
            "interactions": 0, "cost_effective": 0.0, "cost_real": 0.0,
            "models": defaultdict(lambda: {"interactions": 0, "cost_effective": 0.0})}))
        self.month_models = defaultdict(lambda: defaultdict(lambda: {
            "interactions": 0, "cost_effective": 0.0,
            "in": 0, "out": 0, "cache_read": 0, "cache_write": 0}))
        # FPA-012/021: proyecto × mes y proyecto × modelo
        # add-project-cost-token-timeline (d1): tokens por proyecto×mes para
        # la timeline de volumen y coste del dashboard (d2)
        self.project_monthly = defaultdict(lambda: defaultdict(lambda: {
            "interactions": 0, "cost_effective": 0.0, "cost_real": 0.0,
            "tokens": {"in": 0, "out": 0, "cache_read": 0, "cache_write": 0}}))
        # add-session-spend-report: gasto por proyecto × día (sparse, solo
        # días con actividad; misma semántica de coste que los buckets).
        # Aditivo: project_daily (Gantt) y project_monthly no cambian.
        self.project_daily_spend = defaultdict(lambda: defaultdict(lambda: {
            "interactions": 0, "cost_effective": 0.0, "cost_real": 0.0,
            "tokens": {"in": 0, "out": 0, "cache_read": 0, "cache_write": 0}}))
        self.project_models = defaultdict(lambda: defaultdict(lambda: {
            "interactions": 0, "cost_effective": 0.0}))


def _agg_row_kind(st, r):
    kind_key = (r.get("timestamp") or "")[:7]
    if kind_key:
        st.row_kinds.setdefault(kind_key, Counter())[
            r.get("kind", "assistant_turn")] += 1
    return st


def _agg_month_tool(st, m, r, real):
    # FPA-011: tool/model del mes
    mt = st.month_tools[m][r["tool"]]
    mt["interactions"] += 1
    mt["cost_effective"] += r.get("cost_effective", 0) or 0
    mt["cost_real"] += real
    mtm = mt["models"][r["model_raw"]]
    mtm["interactions"] += 1
    mtm["cost_effective"] += r.get("cost_effective", 0) or 0
    return st


def _agg_month_model(st, m, r):
    mm = st.month_models[m][r["model_raw"]]
    mm["interactions"] += 1
    mm["cost_effective"] += r.get("cost_effective", 0) or 0
    mm["in"] += r.get("input_tokens", 0) or 0
    mm["out"] += r.get("output_tokens", 0) or 0
    mm["cache_read"] += r.get("cache_read_tokens", 0) or 0
    mm["cache_write"] += r.get("cache_write_tokens", 0) or 0
    return st


def _agg_project_tokens(bucket, r):
    bucket["tokens"]["in"] += r.get("input_tokens", 0) or 0
    bucket["tokens"]["out"] += r.get("output_tokens", 0) or 0
    bucket["tokens"]["cache_read"] += r.get("cache_read_tokens", 0) or 0
    bucket["tokens"]["cache_write"] += r.get("cache_write_tokens", 0) or 0
    return bucket


def _agg_project_month(st, key, r, real):
    """key = (proj, mes)."""
    proj, m = key
    pmm = st.project_monthly[proj][m]
    pmm["interactions"] += 1
    pmm["cost_effective"] += r.get("cost_effective", 0) or 0
    pmm["cost_real"] += real
    _agg_project_tokens(pmm, r)
    return pmm


def _agg_project_daily_spend(st, key, r, real):
    """key = (proj, día)."""
    proj, d = key
    pds = st.project_daily_spend[proj][d]
    pds["interactions"] += 1
    pds["cost_effective"] += r.get("cost_effective", 0) or 0
    pds["cost_real"] += real
    _agg_project_tokens(pds, r)
    return pds


def _agg_row_bucket(st, key, r, real):
    """Buckets hourly/daily/monthly: crear si faltan y sumar la fila."""
    h, d, m = key
    if h not in st.hourly:
        st.hourly[h] = HourlyBucket()
    if d not in st.daily:
        st.daily[d] = Bucket()
    if m not in st.monthly:
        st.monthly[m] = Bucket()
    st.hourly[h].add(r, real)
    st.daily[d].add(r, real)
    st.monthly[m].add(r, real)
    return st


def _agg_row_projects(st, key, r, real):
    """Proyectos activos por hora/día, bucket por proyecto y breakdowns."""
    h, d, proj = key
    st.hour_projects[h].add(proj)
    st.day_projects[d].add(proj)
    st.proj_day[proj][d] += 1
    if proj not in st.by_project:
        st.by_project[proj] = Bucket()
    st.by_project[proj].add(r, real)
    _agg_month_tool(st, d[:7], r, real)
    _agg_project_month(st, (proj, d[:7]), r, real)
    _agg_project_daily_spend(st, (proj, d), r, real)
    return st


def _agg_row_seen(pp, ts):
    if pp.first_seen is None or ts < pp.first_seen:
        pp.first_seen = ts
    if pp.last_seen is None or ts > pp.last_seen:
        pp.last_seen = ts
    return pp


def _agg_row(st, r):
    h = r["hour"]
    d = h[:10]
    m = d[:7]
    real, _, _ = get_sub_cost(r["tool"], r.get("timestamp", ""),
                              r.get("cost_effective", 0) or 0)

    _agg_row_bucket(st, (h, d, m), r, real)

    proj = clean_proj_name(r.get("project", "unknown"))
    _agg_row_projects(st, (h, d, proj), r, real)
    _agg_month_model(st, m, r)
    pmo = st.project_models[proj][r["model_raw"]]
    pmo["interactions"] += 1
    pmo["cost_effective"] += r.get("cost_effective", 0) or 0
    _agg_row_kind(st, r)
    # first/last seen
    _agg_row_seen(st.by_project[proj], r["timestamp"])
    return st


def _agg_apply_fees(monthly_dicts, sub_fees, monthly, daily):
    # --- Subscription fees ---
    for m_key, fee in sub_fees.items():
        if m_key in monthly:
            monthly[m_key].cost_real += fee
            monthly_dicts[m_key]["cost_real"] = round(monthly[m_key].cost_real, 2)
            monthly_dicts[m_key]["subscription_fees"] = fee
        for d_key, d_data in daily.items():
            if d_key[:7] == m_key:
                d_data.cost_real += fee / 30.0  # prorated roughly
    return monthly_dicts


def _agg_tracker_split(monthly_dicts):
    # --- coffe-a31.2: contabilidad separada tracker vs fee implícito ---
    # La suma mensual de cost_real mezcla la parte tracker (p2p real) con los
    # fees implícitos del calendario; metadata (sumatoria horaria) solo tenía
    # la parte tracker → divergencia 611.62 vs 381.62. Se emiten ambas partes
    # por mes; cost_total_real pasa a ser la suma mensual (una sola cuenta).
    for mo in monthly_dicts.values():
        fee_mes = mo.get("subscription_fees", 0.0)
        mo["cost_real_tracker"] = round(mo["cost_real"] - fee_mes, 2)
        mo["pay_per_token_provenance"] = "assumed"  # puede pasar a reported abajo
    return monthly_dicts

usage_state._link(globals())

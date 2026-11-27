"""Módulo extraído de viz-fpa.py (decomposición, comportamiento idéntico)."""
import calendar  # noqa: F401
import html  # noqa: F401
import json  # noqa: F401
import re  # noqa: F401
import sys
from typing import NamedTuple
from datetime import date, datetime  # noqa: F401
from html.parser import HTMLParser  # noqa: F401
from pathlib import Path  # noqa: F401

sys.path.insert(0, str(Path(__file__).resolve().parent))
import fpa_config  # noqa: F401,E402
import schema_validate  # noqa: F401,E402
import site_theme  # noqa: F401,E402




def validate_report(report):
    """FPA-006: valida el reporte contra el schema; lista de campos fallidos."""
    schema = json.loads(SCHEMA.read_text())
    return validate_against_schema(report, schema)


# ======================================================================
# F2 (coffe-lat.3): árboles expandibles + KPI strip
# (FPA-020…045, 140–142) — maths puras; el HTML lleva los valores ya
# pre-calculados por periodo y JS solo re-escala (design.md)
# ======================================================================

def _node(key, label):
    """Nodo de árbol: valores por mes (para recompute de periodo) + hijos."""
    return {"key": key, "label": label, "by_month": {}, "children": []}


class NodeVals(NamedTuple):
    """Valores de un mes de un nodo: coste, interacciones, presupuesto."""
    cost: float = 0.0
    interactions: int = 0
    budget: float | None = None


def _nv(cost, interactions, budget=None):
    """Constructor corto de NodeVals para los call sites del módulo."""
    return NodeVals(cost, interactions, budget)


def _node_add(node, ym, vals):
    cost, interactions, budget = vals
    b = node["by_month"].setdefault(ym, {"cost": 0.0, "interactions": 0})
    b["cost"] += cost
    b["interactions"] += interactions
    if budget is not None:
        b["budget"] = b.get("budget", 0.0) + budget


def _cost_of(node, months=None):
    if node.get("totals_only"):  # coffe-0d5: el drill solo tiene el total
        return node["by_month"].get("__total__", {}).get("cost", 0.0)
    if months is None:
        months = node["by_month"].keys()
    return sum(node["by_month"][m]["cost"] for m in months if m in node["by_month"])


def _inter_of(node, months=None):
    if node.get("totals_only"):  # coffe-0d5: el drill solo tiene el total
        return node["by_month"].get("__total__", {}).get("interactions", 0)
    if months is None:
        months = node["by_month"].keys()
    return sum(node["by_month"][m]["interactions"]
               for m in months if m in node["by_month"])


def _bucket(v):
    """Compat: modelos/tools del reporte viejo son Counters (int); los nuevos
    llevan dicts. Devuelve (interactions, cost) según el shape."""
    if isinstance(v, dict):
        return v.get("interactions", 0), v.get("cost_effective", 0.0) or 0.0
    return v, 0.0


def _get_child(node, key):
    """Hijo directo con `key`, o None."""
    return next((c for c in node["children"] if c["key"] == key), None)


def _tool_model_nodes(tn, ym, st):
    """Hijos Model de una tool con los valores del mes."""
    tool = tn["label"]
    for model, ms in (st.get("models", {}) if isinstance(st, dict) else {}).items():
        model_node = _get_child(tn, f"model:{tool}:{model}")
        if model_node is None:
            model_node = _node(f"model:{tool}:{model}", model)
            tn["children"].append(model_node)
        n_model, c_model = _bucket(ms)
        _node_add(model_node, ym, _nv(c_model, n_model))


def _tool_tree_month(tools, root, ym, mo):
    """Un mes con datos → nodos tool + modelos (valores por mes)."""
    for tool, st in mo["tools"].items():
        n, cost = _bucket(st)
        tn = tools.setdefault(tool, _node(f"tool:{tool}", tool))
        _node_add(tn, ym, _nv(cost, n))
        _node_add(root, ym, _nv(cost, n))
        _tool_model_nodes(tn, ym, st)


def build_tool_tree(report):
    """FPA-020: árbol Tool → Model con valores por mes; el selector de periodo
    (FPA-026) recompute sumando meses pre-calculados, sin lógica de negocio."""
    root = _node("tool:__all__", "Todas las tools")
    tools = {}
    for ym, mo in sorted(report["monthly"].items()):
        if mo["interactions"] == 0:  # FPA-017: mes sin datos no aporta
            continue
        _tool_tree_month(tools, root, ym, mo)
    for tn in tools.values():
        tn["children"].sort(key=lambda n: -_cost_of(n))
    root["children"] = sorted(tools.values(), key=lambda n: -_cost_of(n))
    return root


def _time_month_budget(meta, start, cash_budget):
    """Presupuesto pro-rata del mes (coffe-9p4); None fuera de la ventana."""
    if not (start and meta["ym"] >= start and cash_budget):
        return None
    return round(cash_budget * meta["elapsed"] / meta["total_days"], 2)


def _time_tree_month(root, years, meta, budget_cfg):
    """Un mes → Year → Quarter → Month con coste CASH y presupuesto."""
    start, cash_budget = budget_cfg
    ym = meta["ym"]
    y, q = ym[:4], f"Q{(int(ym[5:7]) - 1) // 3 + 1}"
    vals = _nv(meta["cost_cash"], meta["interactions"],
               _time_month_budget(meta, start, cash_budget))
    month_node = _node(f"time:{ym}", month_label(ym))
    _node_add(month_node, ym, vals)
    ynode = years.setdefault(y, _node(f"time:{y}", y))
    qnode = _get_child(ynode, f"time:{y}-{q}")
    if qnode is None:
        qnode = _node(f"time:{y}-{q}", f"{q} {y[2:]}")
        ynode["children"].append(qnode)
    qnode["children"].append(month_node)
    _node_add(qnode, ym, vals)
    _node_add(ynode, ym, vals)


def _tree_rollup(parent, with_budget):
    """Roll-up por construcción: el padre = suma de sus hijos directos."""
    for child in parent["children"]:
        for ym, b in child["by_month"].items():
            _node_add(parent, ym, _nv(b["cost"], b["interactions"],
                                      b.get("budget") if with_budget else None))


def build_time_tree(report, cfg):
    """FPA-020/024: Year → Quarter → Month con coste CASH (el comparable con
    el presupuesto cash; FPA-002: jamás se suma con el efectivo) y
    presupuesto mensual del config (assumed) en cada mes."""
    root = _node("time:__all__", "Todo el periodo")
    start = cfg.get("budgets", {}).get("start_month")
    cash_budget = cfg.get("budgets", {}).get("cash_monthly")
    years = {}
    for meta in build_months(report):
        _time_tree_month(root, years, meta, (start, cash_budget))
    root["children"] = [years[y] for y in sorted(years)]
    # roll-up por construcción: el root es la suma de sus años
    _tree_rollup(root, True)
    return root


def _repo_match(org, p, repos):
    """Link para `p` si cae bajo la org `org` (más larga primero); None si no."""
    if p == org:  # label bare → página de la org
        return {"url": f"https://github.com/{repos['owners'][org]}",
                "private": False}
    if not (p.startswith(org + "-") or p.startswith(org + "/")):
        return None
    repo = p[len(org) + 1:]
    if repo.endswith("-jl"):  # clean_proj_name convierte .jl → -jl
        repo = repo[:-3] + ".jl"
    if f"{org}/{repo}" in (repos.get("no_remote") or []):
        return None
    url = ((repos.get("url_overrides") or {}).get(p)
           or f"https://github.com/{repos['owners'][org]}/{repo}")
    return {"url": url,
            "private": f"{org}/{repo}" in (repos.get("private") or [])}


def repo_link(proj, cfg):
    """coffe-vp8: link al repo GitHub del proyecto, sin red.

    Los flags de visibilidad viven en config/fpa.json (repos): owners mapea
    prefijo de label → org GH; private/no_remote se auditaron con gh api
    (404 = sin remoto público → no se linkea; private=true → 🔒 en el UI).
    Devuelve {'url', 'private'} o None si el proyecto no es linkeable.
    """
    repos = cfg.get("repos") or {}
    owners = repos.get("owners") or {}
    p = proj or ""
    for org in sorted(owners, key=len, reverse=True):
        link = _repo_match(org, p, repos)
        if link is not None:
            return link
    return None


def _pf_child(cats, cfg, proj):
    """(categoría, proyecto) para `proj`, creando nodos si faltan."""
    cat = fpa_config.classify_project(cfg, proj)
    cn = cats.setdefault(cat, _node(f"pf:{cat}", cat))
    proj_node = _get_child(cn, f"pf:{proj}")
    if proj_node is None:
        proj_node = _node(f"pf:{proj}", proj)
        link = repo_link(proj, cfg)  # coffe-vp8: link al repo GH
        if link:
            proj_node["url"] = link["url"]
            proj_node["private"] = link["private"]
        cn["children"].append(proj_node)
    return cn, proj_node


def _pf_months(report, cfg, cats):
    """Valores por mes de cada proyecto → nodos categoría + proyecto."""
    for proj, months in report.get("project_monthly", {}).items():
        cn, proj_node = _pf_child(cats, cfg, proj)
        for ym, v in months.items():
            _node_add(proj_node, ym, _nv(v["cost_effective"], v["interactions"]))
            _node_add(cn, ym, _nv(v["cost_effective"], v["interactions"]))


def _pf_model_drill(report, cat):
    """Drill Project→Model (solo totales del periodo completo) + sort."""
    for p_node in cat["children"]:
        for model, ms in report.get("project_models", {}).get(p_node["label"], {}).items():
            m_node = _node(f"pfm:{p_node['label']}:{model}", model)
            m_node["totals_only"] = True
            m_node["by_month"]["__total__"] = {
                "cost": ms["cost_effective"], "interactions": ms["interactions"]}
            p_node["children"].append(m_node)
    cat["children"].sort(key=lambda n: -_cost_of(n))


def build_portfolio_tree(report, cfg):
    """FPA-018/020/021: Category → Project con drill a Model (totales).
    Con project_monthly (FPA-012) el árbol tiene valores por mes; el drill
    Project→Model solo tiene datos del periodo completo — limitación visible."""
    root = _node("pf:__all__", "Portfolio")
    cats = {}
    _pf_months(report, cfg, cats)
    for cat in cats.values():
        _pf_model_drill(report, cat)
    root["children"] = sorted(cats.values(), key=lambda n: -_cost_of(n))
    _tree_rollup(root, False)
    return root


def check_rollup(node):
    """FPA-025: todo padre = suma de sus hijos dentro de $0.01 y 1 interacción.
    Devuelve violaciones (lista vacía = OK). Verificación recursiva."""
    bad = []
    if node.get("children"):
        c_cost = sum(_cost_of(c) for c in node["children"])
        c_inter = sum(_inter_of(c) for c in node["children"])
        p_cost, p_inter = _cost_of(node), _inter_of(node)
        if abs(p_cost - c_cost) > 0.01:
            bad.append(f"{node['key']}: coste padre {p_cost:.4f} ≠ hijos {c_cost:.4f}")
        if abs(p_inter - c_inter) > 1:
            bad.append(f"{node['key']}: interacciones padre {p_inter} ≠ hijos {c_inter}")
        for c in node["children"]:
            bad.extend(check_rollup(c))
    return bad
from viz_fpa_common import _link  # noqa: E402

_link(globals())

#!/usr/bin/env python3
"""ddl-effect-tracker — valida si el toolset de dulce-de-leche mejora outcomes (v1).

Lee `data/usage_report_v3.json` (agregados por sesión), walk de
`~/.pi/agent/sessions` (recuperación por evento), git de la flota, gh api
(cache), `ddl status` y cache de terapia → `data/ddl_effect.json`
(schema: `specs/ddl-effect.schema.json`, change add-ddl-effectiveness-analysis).

Reducers puros arriba (sin disk I/O, testeados en tests/test_ddl_effect.py);
la capa de extracción (git/gh/walk) llega en tickets posteriores.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

# 13 herramientas del registry gestionado (MANAGED-TOOLS en dulce-de-leche/README.md;
# drift-tested allá por tests/tool_registry_drift.rs — no duplicamos el check aquí).
MANAGED_TOOLS = (
    "wai", "testaruda",          # Core
    "turu", "dont", "ah",        # Recommended
    "pretender", "vampiro", "incitaciones", "bd", "openspec", "specodelic",
    "funkalization", "wild",     # Extension
)
assert len(MANAGED_TOOLS) == 13, "registry gestionado incompleto"

# Repos canónicos de la flota que evalúa este análisis.
FLEET_REPOS = (
    "wai", "testaruda", "turu", "dont", "ah", "pretender", "vampiro",
    "incitaciones", "bd", "openspec", "specodelic", "funkalization", "wild",
    "dulce-de-leche", "coffee",
)
DDL_OUTLIER_REPO = "charly-dulce-de-leche"


# ---------------------------------------------------------------------------
# Reducers puros
# ---------------------------------------------------------------------------

def extract_adoption(commits, uses, registry=MANAGED_TOOLS, repos=FLEET_REPOS,
                     repo_aliases=None):
    """Línea de adopción por repo: primer commit de `.ddl/` + primer uso por tool.

    commits: [{repo, date, files}]; uses: [{repo, tool, date}] — `repo` de uses
    puede ser un cwd crudo; solo matchea vía repo_aliases explícito (sin fuzzy).
    Devuelve {repo: {first_ddl_commit, tools_first_use, provenance}}; sin señal →
    None con provenance "missing" (nunca fecha imputada).
    """
    repo_aliases = repo_aliases or {}
    alias_to_repo = {}
    for repo, aliases in repo_aliases.items():
        for alias in aliases:
            alias_to_repo[alias] = repo

    def _repo_of(key):
        return alias_to_repo.get(key, key)

    out = {
        repo: {
            "first_ddl_commit": None,
            "tools_first_use": {tool: None for tool in registry},
            "provenance": {"first_ddl_commit": "missing",
                           "tools_first_use": "missing"},
        }
        for repo in repos
    }
    first_ddl = {}
    first_use = {}
    for c in commits:
        if not any(".ddl/" in f for f in c["files"]):
            continue
        repo = c["repo"]
        if repo not in first_ddl or c["date"] < first_ddl[repo]:
            first_ddl[repo] = c["date"]
    for u in uses:
        tool = _repo_of_tool(u["tool"], registry)
        if tool is None:
            continue
        repo = _repo_of(u["repo"])
        key = (repo, tool)
        if key not in first_use or u["date"] < first_use[key]:
            first_use[key] = u["date"]
    for repo in repos:
        if repo in first_ddl:
            out[repo]["first_ddl_commit"] = first_ddl[repo]
            out[repo]["provenance"]["first_ddl_commit"] = "reported"
        for tool in registry:
            if (repo, tool) in first_use:
                out[repo]["tools_first_use"][tool] = first_use[(repo, tool)]
                out[repo]["provenance"]["tools_first_use"] = "reported"
    return out


def _repo_of_tool(tool, registry):
    """Un uso cuenta solo si el binario es una herramienta gestionada."""
    return tool if tool in registry else None


def compute_dose(ddl_status, canaries, session_uses, registry=MANAGED_TOOLS,
                 repos=FLEET_REPOS):
    """Dosis por repo: fracción de `registry` presentes y sanas.

    Jerarquía: ddl_status (reported) → canarios git (derived) → usos de sesión
    (derived) → 0.0 con provenance "missing". ddl_status: {repo: {present,
    healthy}} (o None); canaries: {repo: [paths bajo .ddl/]}; session_uses:
    {repo: set(tools)}.
    """
    out = {}
    for repo in repos:
        detail = {"present": [], "healthy": [], "missing": list(registry)}
        prov = "missing"
        dose = 0.0
        status = (ddl_status or {}).get(repo)
        if status:
            healthy = [t for t in registry if t in set(status.get("healthy", []))]
            present = [t for t in registry if t in set(status.get("present", [])) | set(healthy)]
            detail = {"present": present, "healthy": healthy,
                      "missing": [t for t in registry if t not in set(present)]}
            dose = len(healthy) / len(registry)
            prov = "reported"
        else:
            canary_paths = (canaries or {}).get(repo) or []
            via_canary = sorted({p.split("/")[1] for p in canary_paths
                                 if p.startswith(".ddl/") and p.split("/")[1] in registry})
            via_session = sorted((session_uses or {}).get(repo) or set())
            found = via_canary or via_session
            if found:
                detail = {"present": found, "healthy": [],
                          "missing": [t for t in registry if t not in set(found)]}
                dose = len(found) / len(registry)
                prov = "derived"
        out[repo] = {"dose": dose, "dose_detail": detail,
                     "provenance": {"dose": prov}}
    return out


def classify_control(repos_info, outlier=DDL_OUTLIER_REPO):
    """control = ausencia estructural de ddl (sin `.ddl/` y sin topic ddl GitHub).

    NUNCA por dosis 0 (adoptar y migrar a 0 sanas sigue siendo tratamiento).
    repos_info: {repo: {has_ddl, topic_ddl}}; el outlier declarado va marcado.
    """
    out = {}
    for repo, info in repos_info.items():
        is_control = not info["has_ddl"] and not info["topic_ddl"]
        out[repo] = {
            "is_control": is_control,
            "is_outlier": repo == outlier,
        }
    return out


# ---------------------------------------------------------------------------
# main
# ---------------------------------------------------------------------------

def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--output", default="data/ddl_effect.json")
    ap.add_argument("--usage-report", default="data/usage_report_v3.json")
    args = ap.parse_args(argv)
    out_path = Path(args.output)

    # Guard (patrón usage-tracker): sin insumos locales no hay datos reales.
    if not Path(args.usage_report).exists():
        ap.error(
            f"sin insumo {args.usage_report} — el tracker requiere el reporte de "
            "usage; los logs fuente (~/.pi/agent/sessions, git, gh api) no están "
            "versionados (ver README 'Los JSON en data/ son derivados')"
        )

    report = {
        "metadata": {
            "generated_at": None,  # se completa cuando aterrice la capa de extracción
            "seed_bootstrap": 42,
            "managed_tools": list(MANAGED_TOOLS),
            "fleet_definition": "repos charly-vibes con actividad en sesiones in-scope",
        },
        "repos": [],
        "monthly": [],
        "analysis": {"its": [], "did": [], "dose_response": []},
    }
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(report, indent=2, sort_keys=False) + "\n")
    print(f"ddl-effect: esqueleto escrito en {out_path} "
          "(reducers en tickets coffe-51p..; extracción gh/git/walk pendiente)")
    return 0


if __name__ == "__main__":
    sys.exit(main())

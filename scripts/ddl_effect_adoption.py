"""Reducers de adopción y dosis (parte de ddl-effect-tracker)."""
from __future__ import annotations

from ddl_effect_core import FLEET_REPOS, MANAGED_TOOLS

def _alias_to_repo(repo_aliases):
    """{repo: [alias]} → {alias: repo} — matching explícito, sin fuzzy."""
    out = {}
    for repo, aliases in (repo_aliases or {}).items():
        for alias in aliases:
            out[alias] = repo
    return out


def _first_ddl_by_repo(commits):
    """Primer commit que toca `.ddl/` por repo (fecha mínima)."""
    first = {}
    for c in commits:
        if not any(".ddl/" in f for f in c["files"]):
            continue
        repo = c["repo"]
        if repo not in first or c["date"] < first[repo]:
            first[repo] = c["date"]
    return first


def _first_use_by_key(uses, registry, alias_to_repo):
    """Primer uso por (repo, tool); usos de binarios no gestionados se ignoran."""
    first = {}
    for u in uses:
        tool = _repo_of_tool(u["tool"], registry)
        if tool is None:
            continue
        repo = alias_to_repo.get(u["repo"], u["repo"])
        key = (repo, tool)
        if key not in first or u["date"] < first[key]:
            first[key] = u["date"]
    return first


def _adoption_row(first_ddl, first_use, repo, registry):
    """Fila de adopción de un repo; sin señal → None con provenance missing."""
    row = {
        "first_ddl_commit": first_ddl.get(repo),
        "tools_first_use": {tool: first_use.get((repo, tool))
                            for tool in registry},
        "provenance": {"first_ddl_commit": "missing",
                       "tools_first_use": "missing"},
    }
    if repo in first_ddl:
        row["provenance"]["first_ddl_commit"] = "reported"
    if any((repo, tool) in first_use for tool in registry):
        row["provenance"]["tools_first_use"] = "reported"
    return row


def extract_adoption(commits, uses, registry=MANAGED_TOOLS, **opts):
    """Línea de adopción por repo: primer commit de `.ddl/` + primer uso por tool.

    commits: [{repo, date, files}]; uses: [{repo, tool, date}] — `repo` de uses
    puede ser un cwd crudo; solo matchea vía repo_aliases explícito (sin fuzzy).
    opts: repos=FLEET_REPOS, repo_aliases=None. Devuelve {repo:
    {first_ddl_commit, tools_first_use, provenance}}; sin señal → None con
    provenance "missing" (nunca fecha imputada).
    """
    alias_to_repo = _alias_to_repo(opts.get("repo_aliases"))
    first_ddl = _first_ddl_by_repo(commits)
    first_use = _first_use_by_key(uses, registry, alias_to_repo)
    return {repo: _adoption_row(first_ddl, first_use, repo, registry)
            for repo in opts.get("repos", FLEET_REPOS)}


def _repo_of_tool(tool, registry):
    """Un uso cuenta solo si el binario es una herramienta gestionada."""
    return tool if tool in registry else None


def _dose_from_status(status, registry):
    """Dosis reported desde `ddl status`: sanas / registry."""
    healthy = [t for t in registry if t in set(status.get("healthy", []))]
    present = [t for t in registry
               if t in set(status.get("present", [])) | set(healthy)]
    detail = {"present": present, "healthy": healthy,
              "missing": [t for t in registry if t not in set(present)]}
    return len(healthy) / len(registry), detail, "reported"


def _dose_from_derived(canary_paths, session_tools, registry):
    """Dosis derived: canarios git, luego usos de sesión; sin señal → missing."""
    detail = {"present": [], "healthy": [], "missing": list(registry)}
    via_canary = sorted({p.split("/")[1] for p in canary_paths
                         if p.startswith(".ddl/") and p.split("/")[1] in registry})
    found = via_canary or sorted(session_tools)
    if not found:
        return 0.0, detail, "missing"
    detail = {"present": found, "healthy": [],
              "missing": [t for t in registry if t not in set(found)]}
    return len(found) / len(registry), detail, "derived"


def _bind_args(args, opts, names, defaults):
    """Liga argumentos posicionales + kwargs a `names` (forma estable con
    ≤4 parámetros formales). Extra posicional → TypeError."""
    if len(args) > len(names):
        raise TypeError(
            f"se esperaban a lo sumo {len(names)} argumentos posicionales")
    bound = dict(zip(names, args))
    for name in names[len(args):]:
        bound[name] = opts.get(name, defaults.get(name))
    return bound


def compute_dose(*args, **opts):
    """Dosis por repo: fracción de `registry` presentes y sanas.

    Posicionales: ddl_status, canaries, session_uses; kwargs: registry
    (MANAGED_TOOLS), repos (FLEET_REPOS). Jerarquía: ddl_status (reported) →
    canarios git (derived) → usos de sesión (derived) → 0.0 con provenance
    "missing". ddl_status: {repo: {present, healthy}} (o None); canaries:
    {repo: [paths bajo .ddl/]}; session_uses: {repo: set(tools)}.
    """
    b = _bind_args(args, opts,
                   ("ddl_status", "canaries", "session_uses", "registry",
                    "repos"),
                   {"registry": MANAGED_TOOLS, "repos": FLEET_REPOS})
    out = {}
    for repo in b["repos"]:
        status = (b["ddl_status"] or {}).get(repo)
        if status:
            dose, detail, prov = _dose_from_status(status, b["registry"])
        else:
            dose, detail, prov = _dose_from_derived(
                (b["canaries"] or {}).get(repo) or [],
                (b["session_uses"] or {}).get(repo) or set(), b["registry"])
        out[repo] = {"dose": dose, "dose_detail": detail,
                     "provenance": {"dose": prov}}
    return out

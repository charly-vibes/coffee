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

sys.path.insert(0, str(Path(__file__).resolve().parent))
from ddl_effect_adoption import compute_dose, extract_adoption  # noqa: E402,F401
from ddl_effect_analysis import (  # noqa: E402,F401
    did_2x2,
    did_bootstrap,
    dose_response,
    its_pre_post,
    repo_deltas,
    validate_against_schema,
    validate_report,
)
from ddl_effect_assemble import assemble_report  # noqa: E402,F401
from ddl_effect_core import (  # noqa: E402,F401
    DDL_OUTLIER_REPO,
    DDL_SCHEMA,
    FLEET_REPOS,
    MANAGED_TOOLS,
)
from ddl_effect_emitters import ANALYSIS_METRICS  # noqa: E402,F401
from ddl_effect_metrics import (  # noqa: E402,F401
    ci_fail_rate,
    classify_control,
    commit_latency,
    error_rates,
    gh_runs_incremental,
    gh_token,
    git_proxies,
    recovery_metrics,
)
from ddl_effect_panel import PANEL_METRICS, build_panel, cell_from_emitters  # noqa: E402,F401
from ddl_effect_sessions import (  # noqa: E402,F401
    DEFAULT_REPO_DIRS,
    build_alias_map,
    cwd_to_repo,
    events_by_repo_month,
    monthly_dose,
    parse_pi_session,
    uses_rows,
    walk_pi_sessions,
)
from ddl_effect_sources import (  # noqa: E402,F401
    _cache_fresh,
    _load_json_cache,
    _write_json,
    ah_findings_cached,
    datetime_now_iso,
    ddl_status_global,
    gh_runs_fetch,
    gh_topics,
    git_commits,
    git_releases,
    repo_local_path,
    terapia_errors_global,
)

# ---------------------------------------------------------------------------
# main
# ---------------------------------------------------------------------------


def _build_arg_parser():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--output", default="data/ddl_effect.json")
    ap.add_argument("--usage-report", default="data/usage_report_v3.json")
    ap.add_argument("--config", default="config/fpa.json")
    ap.add_argument("--cache-dir", default="data/cache")
    ap.add_argument("--sessions-dir", default=None,
                    help="walk de sesiones pi (default ~/.pi/agent/sessions)")
    ap.add_argument("--validate", metavar="ARCHIVO", default=None,
                    help="valida el JSON dado contra specs/ddl-effect.schema.json "
                         "y sale (0 válido / 1 inválido) sin regenerar")
    return ap


def _validate_file(args):
    """Modo --validate: 0 válido / 1 inválido, sin regenerar."""
    errors = validate_report(json.loads(Path(args.validate).read_text()))
    if errors:
        print("ddl-effect: schema inválido:", file=sys.stderr)
        for e in errors:
            print(f"  {e}", file=sys.stderr)
        return 1
    print(f"ddl-effect: {args.validate} válido contra {DDL_SCHEMA.name}")
    return 0


def _owner_fn(owners):
    """Slug de GitHub por repo: repo_owners trae owner suelto ('charly-vibes')
    o slug completo ('charly-vibes/whisper' cuando el repo canónico y el de
    GitHub no comparten nombre)."""
    def _owner(repo):
        val = owners.get(repo) or owners.get("*")
        return val if "/" in val else f"{val}/{repo}"
    return _owner


def _git_insumos(repo_paths):
    """git de la flota local: commits, releases y presencia de `.ddl/` hoy."""
    git_insumos = {}
    for repo in FLEET_REPOS:
        path = repo_local_path(repo, repo_paths)
        if path is None or not path.exists():
            continue
        commits = git_commits(path)
        if commits is None:
            continue
        git_insumos[repo] = {
            "commits": commits,
            "releases": git_releases(path) or [],
            "has_ddl_now": (path / ".ddl").is_dir(),
        }
    return git_insumos


def _gh_insumos(git_insumos, cache_dir, owner):
    """Runs de gh api por repo local, con cache incremental y write-through."""
    gh_insumos = {}
    for repo in FLEET_REPOS:
        if repo not in git_insumos:  # gh sólo para repos locales (charly-vibes)
            continue
        cache_file = cache_dir / "gh_runs" / f"{repo}.json"
        cache = _load_json_cache(cache_file, None)
        owner_repo = owner(repo)

        def _fetch(_repo, _since, owner_repo=owner_repo):
            runs = gh_runs_fetch(owner_repo)
            if runs is None:  # red muerta/404: degradar, no romper
                raise RuntimeError("gh api sin red o repo_gone")
            return runs

        merged = gh_runs_incremental(repo, _fetch, cache,
                                     now=datetime_now_iso())
        if merged is None:
            continue
        gh_insumos[repo] = {"runs": merged.get("runs") or [],
                            "provenance": merged.get("provenance", "reported")}
        _write_json(cache_file, {"runs": merged.get("runs") or [],
                                 "fetched_until": merged.get("fetched_until")})
    return gh_insumos


def _topics_insumos(git_insumos, cache_dir, owner):
    """Topics ddl por repo local con cache semanal (insumo classify_control)."""
    topics = {}
    topics_cache = cache_dir / "gh_topics.json"
    topics_cache_data = _load_json_cache(topics_cache, {})
    for repo in FLEET_REPOS:
        if repo not in git_insumos:
            continue
        entry = topics_cache_data.get(repo)
        if _cache_fresh({"fetched_until": (entry or {}).get("fetched_until")},
                        datetime_now_iso(), 7):
            topics[repo] = entry
            continue
        res = gh_topics(owner(repo))
        if res is None:
            if entry:
                topics[repo] = {**entry, "provenance": "cached"}
            continue
        entry = {"topic_ddl": "ddl" in (res.get("topics") or []),
                 "provenance": "reported",
                 "created_at": res.get("created_at"),
                 "fetched_until": datetime_now_iso()}
        topics[repo] = entry
        topics_cache_data[repo] = entry
    _write_json(topics_cache, topics_cache_data)
    return topics


def _ah_runner(repo_paths):
    """runner(repo) → (exit, stdout) de `ah check` en el checkout local."""
    import subprocess

    def runner(repo):
        cwd = repo_local_path(repo, repo_paths)
        r = subprocess.run(["ah", "check"], cwd=str(cwd),
                           capture_output=True, text=True, timeout=120)
        return r.returncode, r.stdout

    return runner


def _load_config(args):
    """Config opcional (config/fpa.json) → sección ddl_effect."""
    if not Path(args.config).exists():
        return {}
    config = json.loads(Path(args.config).read_text())
    return config.get("ddl_effect") or {}


def _fleet_insumos(args, ddl_cfg, cache_dir):
    """Sesiones, git, gh, topics, ddl status, terapia y ah → insumos del
    reporte (toda la capa de extracción I/O)."""
    alias_map = build_alias_map(ddl_cfg.get("repo_aliases"))
    repo_paths = ddl_cfg.get("repo_paths") or {}
    sessions = walk_pi_sessions(args.sessions_dir, alias_map)
    print(f"ddl-effect: {len(sessions)} sesiones pi de la flota")
    git_insumos = _git_insumos(repo_paths)
    owner = _owner_fn(ddl_cfg.get("repo_owners") or {"*": "charly-vibes"})
    return {
        "sessions": sessions,
        "git": git_insumos,
        "gh": _gh_insumos(git_insumos, cache_dir, owner),
        "topics": _topics_insumos(git_insumos, cache_dir, owner),
        "ddl_status": ddl_status_global(),
        "terapia": terapia_errors_global(cache_dir / "terapia_errors.json",
                                         ttl_days=7,
                                         now=datetime_now_iso()),
        "ah": ah_findings_cached(
            [r for r in FLEET_REPOS if r in git_insumos],
            cache_dir / "ah_findings.json", ttl_days=7,
            now=datetime_now_iso(), runner=_ah_runner(repo_paths)),
        "now": datetime_now_iso(),
    }


def _write_report(out_path, report, ap):
    """Valida (fail loud), escribe y resume el reporte."""
    errors = validate_report(report)
    if errors:  # fail loud: el tracker no escribe un reporte inválido
        ap.error("reporte inválido contra " + DDL_SCHEMA.name + ": "
                 + "; ".join(errors))
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(report, indent=2, sort_keys=False) + "\n")
    repos_n = len(report["repos"])
    rows_n = len(report["monthly"])
    print(f"ddl-effect: {out_path} — {repos_n} repos, {rows_n} filas de "
          "panel, "
          f"{len(report['analysis']['its'])} ITS, "
          f"{len(report['analysis']['did'])} DiD")


def main(argv=None):
    ap = _build_arg_parser()
    args = ap.parse_args(argv)

    if args.validate:
        return _validate_file(args)

    out_path = Path(args.output)

    # Guard (patrón usage-tracker): sin insumos locales no hay datos reales.
    if not Path(args.usage_report).exists():
        ap.error(
            f"sin insumo {args.usage_report} — el tracker requiere el reporte de "
            "usage; los logs fuente (~/.pi/agent/sessions, git, gh api) no están "
            "versionados (ver README 'Los JSON en data/ son derivados')"
        )
    usage_report = json.loads(Path(args.usage_report).read_text())
    ddl_cfg = _load_config(args)
    cache_dir = Path(args.cache_dir)

    insumos = _fleet_insumos(args, ddl_cfg, cache_dir)
    insumos["usage_report"] = usage_report
    report = assemble_report(insumos)
    _write_report(out_path, report, ap)
    return 0

if __name__ == "__main__":
    sys.exit(main())

"""Extracción git/gh/ddl status/terapia/ah con cache (ddl-effect-tracker)."""
from __future__ import annotations

import json
from pathlib import Path

from ddl_effect_core import FLEET_REPOS
from ddl_effect_metrics import _parse_ts

# Dir local → repo canónico (cuando el nombre del dir no es el repo):
# ah vive en espectacular/, turu en whisper/ (workspace con 3 bins),
# coffee se llamaba coffe antes del rename.
DEFAULT_REPO_DIRS = {"espectacular": "ah", "whisper": "turu",
                     "coffe": "coffee"}
FLEET_GH_ROOT = Path.home() / "para" / "areas" / "dev" / "gh"


def git_commits(repo_path, run=None):
    """git log de un repo de la flota → [{date, message, files}] o None si
    el repo no es local (sin git). run(cmd, cwd) inyectable para tests."""
    if run is None:
        import subprocess
        def run(cmd, cwd):
            r = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True,
                               timeout=120)
            if r.returncode != 0:
                raise RuntimeError(r.stderr.strip() or "git falló")
            return r.stdout
    try:
        out = run(["git", "log", "--all", "--name-only",
                   "--pretty=format:%x1e%H|%cI|%s"], str(repo_path))
    except Exception:  # noqa: BLE001 — repo ausente/dañado → missing
        return None
    commits = []
    for block in out.split("\x1e"):
        lines = [l for l in block.strip("\n").split("\n") if l.strip()]
        if not lines or "|" not in lines[0]:
            continue
        _h, date, message = lines[0].split("|", 2)
        commits.append({"date": date, "message": message, "files": lines[1:]})
    return commits


def git_releases(repo_path, run=None):
    """Fechas de tags = releases (insumo de fix_ratio, D6)."""
    if run is None:
        import subprocess
        def run(cmd, cwd):
            r = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True,
                               timeout=30)
            return r.stdout
    try:
        out = run(["git", "for-each-ref", "refs/tags",
                   "--format=%(creatordate:iso-strict)"], str(repo_path))
    except Exception:  # noqa: BLE001
        return None
    return [l.strip() for l in out.splitlines() if l.strip()]


def repo_local_path(repo, repo_paths=None, root=None):
    """Repo canónico → path local (~/para/areas/dev/gh/charly/<dir> con
    DEFAULT_REPO_DIRS); config ddl_effect.repo_paths sobreescribe. None si
    el repo no es local (bd vive upstream, sin checkout)."""
    paths = dict(DEFAULT_REPO_DIRS)
    for repo_key, p in (repo_paths or {}).items():
        paths[repo_key] = p
    if repo in paths:
        cand = Path(paths[repo])
        if not cand.is_absolute():
            cand = (root or FLEET_GH_ROOT) / "charly" / cand
        return cand
    cand = (root or FLEET_GH_ROOT) / "charly" / repo
    return cand


def _ddl_item_status(item, present, healthy):
    """1 item de `ddl status`: label presente salvo level error; healthy aparte."""
    label = item.get("label")
    if not label:
        return
    level = item.get("level")
    if level != "error":
        present.append(label)
    if level == "healthy":
        healthy.append(label)


def _ddl_status_items(envelope):
    """Secciones del envelope `ddl status --json` → (present, healthy).

    'not installed' (level error) no es presente; 'healthy' también es presente.
    """
    present, healthy = [], []
    for section in (envelope.get("data") or {}).get("sections") or []:
        for item in section.get("items") or []:
            _ddl_item_status(item, present, healthy)
    return present, healthy


def ddl_status_global(run=None):
    """`ddl status --json` → {present, healthy} por herramienta (D3 fuente 1).
    'not installed' (level error) no es presente. Binario ausente → None."""
    if run is None:
        import subprocess
        def run(cmd):
            r = subprocess.run(cmd, capture_output=True, text=True, timeout=60)
            if r.returncode != 0:
                raise RuntimeError(r.stderr.strip() or "ddl status falló")
            return r.stdout
    try:
        envelope = json.loads(run(["ddl", "status", "--json"]))
    except Exception:  # noqa: BLE001
        return None
    present, healthy = _ddl_status_items(envelope)
    return {"present": present, "healthy": healthy}


def gh_runs_fetch(owner_repo, run=None):
    """Runs de GitHub Actions paginados (D4). run(cmd) inyectable. Red
    muerta / repo 404 → None (gh_runs_incremental conserva cache stale)."""
    if run is None:
        import subprocess
        def run(cmd):
            r = subprocess.run(cmd, capture_output=True, text=True, timeout=60)
            if r.returncode != 0:
                raise RuntimeError(r.stderr.strip() or "gh api falló")
            return r.stdout
    runs = []
    page = 1
    while True:
        try:
            out = run(["gh", "api",
                       f"repos/{owner_repo}/actions/runs?per_page=100&page={page}"])
            data = json.loads(out)
        except Exception:  # noqa: BLE001 — sin red/token/repo_gone
            return None
        batch = data.get("workflow_runs") or []
        runs.extend(batch)
        if len(batch) < 100:
            return runs
        page += 1


def gh_topics(owner_repo, run=None):
    """Topics del repo (insumo de classify_control: topic ddl). None sin red."""
    if run is None:
        import subprocess
        def run(cmd):
            r = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
            if r.returncode != 0:
                raise RuntimeError(r.stderr.strip() or "gh api falló")
            return r.stdout
    try:
        data = json.loads(run(["gh", "api", f"repos/{owner_repo}"]))
    except Exception:  # noqa: BLE001
        return None
    return {"topics": data.get("topics") or [],
            "created_at": data.get("created_at")}


def _cache_fresh(cache, now, ttl_days):
    fetched = _parse_ts((cache or {}).get("fetched_until"))
    cur = _parse_ts(now)
    return bool(cache and fetched and cur
                and (cur - fetched).days < ttl_days)


def terapia_errors_global(cache_path, ttl_days=7, now=None, run=None):
    """Errores por clase de terapia (cache data/cache/terapia_errors.json,
    TTL semanal — mismo patrón que gh, design D1). Terapia es una query
    GLOBAL (el CLI no filtra por proyecto): su desglose queda en metadata
    como contexto, no en filas por repo-mes (atribución imposible sin
    inventar). Sin binario → missing con razón, cache stale conservado."""
    cache_path = Path(cache_path)
    cache = _load_json_cache(cache_path, None)
    if _cache_fresh(cache, now, ttl_days):
        return {"data": cache["data"], "provenance": "cached",
                "fetched_until": cache["fetched_until"]}
    if run is None:
        import subprocess
        def run(cmd):
            r = subprocess.run(cmd, capture_output=True, text=True, timeout=300)
            if r.returncode != 0:
                raise RuntimeError(r.stderr.strip() or "terapia falló")
            return r.stdout
    try:
        data = json.loads(run(["terapia", "errors", "--all",
                               "--group-by", "class"]))
    except Exception as e:  # noqa: BLE001
        return {"data": None, "provenance": "missing", "reason": str(e)}
    fetched = now or datetime_now_iso()
    _write_json(cache_path, {"data": data, "fetched_until": fetched})
    return {"data": data, "provenance": "reported", "fetched_until": fetched}


def _load_json_cache(cache_path, default):
    """Cache JSON en disco → objeto; corrupto/ilegible → default."""
    if not cache_path.exists():
        return default
    try:
        return json.loads(cache_path.read_text())
    except (json.JSONDecodeError, OSError):
        return default


def _write_json(path, data):
    """Escritura best-effort de cache JSON (mkdir -p; OSError se ignora)."""
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(data, indent=2) + "\n")
    except OSError:
        pass


def _fresh_ah_entry(runner, repo, now):
    """Entrada de un repo recién corrido: findings o missing con razón."""
    try:
        _exit, stdout = runner(repo)
        env = json.loads(stdout)
        findings = (env.get("data") or {}).get("findings")
    except Exception as _err:  # noqa: BLE001 — sin setup / stdout no-JSON
        findings = None
    entry = {"fetched_until": now or datetime_now_iso()}
    if findings is not None:
        entry.update({"findings": len(findings), "provenance": "reported"})
    else:
        entry.update({"findings": None, "provenance": "missing",
                      "reason": "sin setup espectacular o salida no-JSON"})
    return entry


def ah_findings_cached(repos, cache_path, ttl_days=7, **opts):
    """findings de `ah check` por repo (D6: findings por corrida, serie desde
    cache data/cache/ah_findings.json). Repos sin setup espectacular →
    missing con razón. runner(repo) → (exit, stdout) inyectable."""
    now = opts.get("now")
    runner = opts.get("runner")
    cache_path = Path(cache_path)
    cache = _load_json_cache(cache_path, {})
    if runner is None:
        import subprocess
        def runner(repo):
            r = subprocess.run(["ah", "check"], cwd=repo, capture_output=True,
                               text=True, timeout=120)
            return r.returncode, r.stdout
    out = {}
    for repo in repos:
        entry = cache.get(repo)
        if _cache_fresh({"fetched_until": (entry or {}).get("fetched_until")},
                        now, ttl_days):
            out[repo] = dict(entry)
            out[repo]["provenance"] = "cached"
            continue
        out[repo] = cache[repo] = _fresh_ah_entry(runner, repo, now)
    _write_json(cache_path, cache)
    return out


def datetime_now_iso():
    from datetime import datetime, timezone
    return datetime.now(timezone.utc).isoformat(timespec="seconds")

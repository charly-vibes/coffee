"""Walk de sesiones pi y slicing por repo-mes (ddl-effect-tracker)."""
from __future__ import annotations

import json
from pathlib import Path

from ddl_effect_core import FLEET_REPOS, MANAGED_TOOLS
from ddl_effect_metrics import _parse_ts

# ---------------------------------------------------------------------------
# Extracción (capa de I/O con dependencias inyectables; coffe-q9z)
# ---------------------------------------------------------------------------

# Dir local → repo canónico (cuando el nombre del dir no es el repo):
# ah vive en espectacular/, turu en whisper/ (workspace con 3 bins),
# coffee se llamaba coffe antes del rename.
from ddl_effect_sources import DEFAULT_REPO_DIRS, FLEET_GH_ROOT  # noqa: E402
PATH_MARKER = "/para/areas/dev/gh/"

def build_alias_map(repo_aliases):
    """config ddl_effect.repo_aliases: {repo: [alias]} → {alias: repo}.
    Los alias son explícitos (design Riesgos): sin fuzzy matching. Forma de
    alias: 'org/dir' (la convención de path de la flota) o dir suelto."""
    out = {}
    for repo, aliases in (repo_aliases or {}).items():
        for a in aliases or []:
            out[a] = repo
    return out


def _cwd_path_key(cwd):
    """cwd → clave de mapeo según la convención /para/areas/dev/gh/<org>/<dir>.

    'org/dir' si hay ≥2 segmentos, dir suelto si hay 1; None fuera de la
    convención.
    """
    if PATH_MARKER not in cwd:
        return None
    rest = cwd.split(PATH_MARKER, 1)[1].strip("/").split("/")
    if len(rest) >= 2 and rest[1]:
        return f"{rest[0]}/{rest[1]}"
    if rest and rest[0]:
        return rest[0]
    return None


def cwd_to_repo(cwd, alias_map=None):
    """cwd de sesión → repo canónico de la flota, o None.

    1) alias explícito exacto ('org/dir' derivado de la convención de path,
    o dir suelto); 2) convención /para/areas/dev/gh/<org>/<dir> con mapping
    DEFAULT_REPO_DIRS; fuera de la flota → None (nunca fuzzy).
    """
    if not cwd:
        return None
    key = _cwd_path_key(cwd)
    if key is None:
        return None
    am = dict(DEFAULT_REPO_DIRS)
    am.update(alias_map or {})
    if key in am:
        return am[key]
    dir_name = key.split("/", 1)[1] if "/" in key else key
    if dir_name in am:
        return am[dir_name]
    return dir_name if dir_name in FLEET_REPOS else None


def _bash_tokens(command):
    """Primer token de cada segmento del comando (cd /x && bd list →
    ['cd', 'bd']) — suficiente para detectar binarios gestionados."""
    tokens = []
    for seg in str(command or "").replace("&&", "\n").replace(";", "\n").split("\n"):
        parts = seg.strip().split()
        if parts:
            tokens.append(parts[0])
    return tokens


def _collect_uses_from_call(part, ts, state, registry):
    """1 toolCall → uso directo del binario + usos via bash (primer token)."""
    name = part.get("name") or part.get("toolName")
    if name in registry and name not in state["uses"]:
        state["uses"][name] = ts
    if name == "bash":
        for tok in _bash_tokens((part.get("arguments") or {}).get("command")):
            if tok in registry and tok not in state["uses"]:
                state["uses"][tok] = ts


def _handle_assistant_message(msg, ts, state, registry):
    """Mensaje assistant: marcador de recovery + usos de toolCalls."""
    state["events"].append(("assistant", None, None, ts))
    for part in (msg.get("content") or msg.get("parts") or []):
        if not (isinstance(part, dict)
                and str(part.get("type")) == "toolCall"):
            continue
        _collect_uses_from_call(part, ts, state, registry)


def _handle_message_entry(msg, ts, state, registry):
    """1 mensaje pi → eventos de recovery y usos de binarios gestionados."""
    role = msg.get("role")
    if role == "assistant":
        _handle_assistant_message(msg, ts, state, registry)
    elif role == "toolResult":
        tool = str(msg.get("toolName") or "tool")
        ok = not bool(msg.get("isError"))
        state["events"].append(("result", tool, ok, ts))


def _feed_entry(entry, state, registry):
    """1 entrada JSONL → cwd de sesión o manejo del mensaje."""
    if entry.get("type") == "session":
        if state["cwd"] is None:
            state["cwd"] = entry.get("cwd") or None
        return
    if entry.get("type") != "message":
        return
    _handle_message_entry(entry.get("message", {}) or {},
                          entry.get("timestamp"), state, registry)


def _iter_session_entries(path):
    """JSONL → entradas parseadas; líneas corruptas se omiten."""
    with open(path, encoding="utf-8", errors="replace") as fh:
        for line in fh:
            try:
                yield json.loads(line)
            except json.JSONDecodeError:
                continue


def parse_pi_session(path, registry=MANAGED_TOOLS):
    """1 archivo JSONL de pi → {cwd, events, uses} o None sin señal.

    events: [(kind, tool, ok, ts)] con kind 'assistant' (marcador para
    recovery) o 'result' (toolResult con isError → ok). uses: {tool: ts}
    del PRIMER uso de cada binario gestionado (via bash, token inicial del
    comando; design D3 fuente 3).
    """
    state = {"cwd": None, "events": [], "uses": {}}
    try:
        for entry in _iter_session_entries(path):
            _feed_entry(entry, state, registry)
    except OSError:
        return None
    if not state["events"] and not state["uses"]:
        return None
    return {"cwd": state["cwd"], "events": state["events"],
            "uses": state["uses"]}


def walk_pi_sessions(sessions_dir=None, alias_map=None, fleet=FLEET_REPOS):
    """Walk de ~/.pi/agent/sessions (design D1, patrón coffe-i31): sólo
    sesiones con cwd mapeable a la flota. Sessions sin eventos ni usos se
    descartan (parse_pi_session ya devuelve None para ellas)."""
    out = []
    sessions_dir = Path(sessions_dir or Path.home() / ".pi" / "agent"
                        / "sessions")
    if not sessions_dir.exists():
        return out
    for sd in sorted(p for p in sessions_dir.iterdir() if p.is_dir()):
        for f in sorted(sd.glob("*.jsonl")):
            parsed = parse_pi_session(f)
            if parsed is None:
                continue
            repo = cwd_to_repo(parsed["cwd"], alias_map)
            if repo not in fleet:
                continue
            parsed["repo"] = repo
            out.append(parsed)
    return out


def uses_rows(sessions):
    """{tool: ts} por sesión → [{repo, tool, date}] del primer uso por
    (repo, tool) — insumo de extract_adoption (fuente 3 de dosis, D3)."""
    first = {}
    for s in sessions:
        for tool, ts in (s.get("uses") or {}).items():
            if ts is None:
                continue
            key = (s["repo"], tool)
            cur = first.get(key)
            if cur is None or _parse_ts(ts) < _parse_ts(cur):
                first[key] = ts
    return [{"repo": r, "tool": t, "date": ts}
            for (r, t), ts in sorted(first.items())]


def events_by_repo_month(sessions):
    """Slice del stream de eventos por (repo, mes) para recovery_metrics:
    cada (sesión, mes) mantiene el orden de eventos; grupos vacíos no
    existen (sin eventos en el mes → sin bucket)."""
    buckets = {}
    for s in sessions:
        by_month = {}
        for ev in s.get("events") or []:
            ts = ev[3]
            if not ts:
                continue
            by_month.setdefault(str(ts)[:7], []).append(ev)
        for month, evs in by_month.items():
            buckets.setdefault((s["repo"], month), []).extend(
                [(e[0], e[1], e[2]) for e in evs])
    return buckets


def monthly_dose(repo, month, first_use, registry=MANAGED_TOOLS):
    """Dosis como serie temporal (D3b): fracción de herramientas gestionadas
    con primer uso ≤ mes, derivada hacia atrás desde tools_first_use. El
    snapshot corriente lo valida compute_dose con ddl status (reported)."""
    fu = (first_use or {}).get(repo) or {}
    n = sum(1 for d in fu.values()
            if d and str(d)[:7] <= month)
    return {"dose": n / len(registry), "provenance": "derived"}

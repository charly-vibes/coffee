#!/usr/bin/env python3
"""Extractor de rollouts Codex (extraído de usage-tracker.py).

Parte de la descomposición de scripts/usage-tracker.py (coffe-kav.1):
cada módulo conserva el código original verbatim; los nombres compartidos
viajan por _link() (mismo patrón que viz_fpa_common) y el estado mutable
por usage_state.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import usage_state


def _codex_rollout_file(f, rows, skipped):
    try:
        _parse_codex_rollout(f, rows, skipped)
    except OSError as e:
        if skipped is not None:
            key = f"codex:{f.name}"
            skipped[key] = skipped.get(key, 0) + 1
        elif os.environ.get("TRACKER_DEBUG"):
            print(f"  [skipped codex] {f.name}: {e}", file=sys.stderr)
    return rows


def extract_codex(sessions_dir=None, skipped=None):
    """Extrae de Codex CLI standalone (~/.codex/sessions, coffe-2sy).

    Hasta v4.4 solo entraba Codex-via-Pi (provider openai-codex); las
    sesiones del TUI/CLI eran invisibles. Formato: 1 rollout-*.jsonl = 1
    sesión bajo sessions/YYYY/MM/DD/. Cada event_msg token_count trae
    total_token_usage (acumulativo) y last_token_usage (delta del turno)
    — la fila usa el delta, así el uso acumulado no se doble-cuenta.

    Semántica de tokens: input_tokens de Codex YA incluye los cached
    (verificado con total vs suma de deltas) → fresh = input - cached,
    cache_read = cached (como reporta Anthropic). reasoning_output es
    parte del output. Cache_write siempre 0 en este log.

    Sin cost en el log (como gemini-cli, coffe-8t8): costo estimado
    pay-per-token con rates vigentes por fecha. El precio de los modelos
    gpt-6.x no es conocido → default_rates, provenance *assumed*.

    El filtro es is_charly(cwd) (respeta --filter) y el label es el
    proyecto derivado del cwd vía amp_proj_from_uri (org-repo), igual
    que Amp; fallback "codex-unknown" si el cwd no es derivable.
    """
    rows = []
    sessions_dir = sessions_dir or (CODEX_DIR / "sessions")
    if not sessions_dir.exists():
        return rows
    for day_dir in sorted(sessions_dir.glob("*/*/*")):
        if not day_dir.is_dir():
            continue
        for f in sorted(day_dir.glob("rollout-*.jsonl")):
            _codex_rollout_file(f, rows, skipped)
    return rows


class _CodexRollout:
    """Estado de un rollout: cwd, modelo y turnos (ts, last_token_usage)."""

    def __init__(self):
        self.cwd = None
        self.model = None
        self.turns = []

    def feed(self, entry):
        typ = entry.get("type")
        p = entry.get("payload") or {}
        if typ == "session_meta":
            self.cwd = p.get("cwd") or self.cwd
        elif p.get("type") == "thread_settings_applied":
            self.model = ((p.get("thread_settings") or {}).get("model")) or self.model
        elif p.get("model"):
            # formato viejo (abr-2026): turn_context trae el modelo plano
            self.model = p["model"]
        elif p.get("type") == "token_count":
            self.token_count(entry, p)
        return self

    def token_count(self, entry, p):
        ts = parse_ts(entry.get("timestamp"))
        last = (p.get("info") or {}).get("last_token_usage") or {}
        if ts and last:
            self.turns.append((ts, last))
        return self


def _codex_turn_row(ts, u, ctx):
    fam, ver, model, proj, fname = ctx
    inp = u.get("input_tokens") or 0
    cached = min(u.get("cached_input_tokens") or 0, inp)  # sanity
    fresh = inp - cached
    out = u.get("output_tokens") or 0
    cost = estimate_cost(fam, ver, fresh, out, cached, 0, when=ts.date())
    return {
        "source": "codex", "tool": "codex",
        "model_raw": model,
        "model_family": fam, "model_version": ver,
        "project": proj,
        "timestamp": ts.isoformat(), "hour": hour_key(ts),
        "input_tokens": fresh, "output_tokens": out,
        "cache_read_tokens": cached,
        "cache_write_tokens": u.get("cache_write_input_tokens") or 0,
        "cost_effective": cost,
        "session_file": fname,  # add-session-spend-report
    }


def _codex_rollout_feed(f, roll):
    with open(f) as fh:
        for line in fh:
            entry = _json_line(line)
            if entry is not None:
                roll.feed(entry)


def _codex_rollout_ctx(f, roll):
    proj = amp_proj_from_uri(roll.cwd) or "codex-unknown"
    fam, ver = model_details(roll.model or "unknown")
    return (fam, ver, roll.model or "unknown", proj, f.name)


def _parse_codex_rollout(f, rows, skipped=None):
    """1 rollout → filas por turno (token_count) en `rows`.

    Malformed lines se descartan (best-effort); una línea rota no tira la
    sesión entera. Sin meta (session_meta) o sin token_count → 0 filas.
    """
    roll = _CodexRollout()
    _codex_rollout_feed(f, roll)
    if roll.cwd is None or not roll.turns:
        return rows
    if not is_charly(roll.cwd):  # coffe-2sy: unifica con --filter
        return rows
    ctx = _codex_rollout_ctx(f, roll)
    for ts, u in roll.turns:
        if not in_window(ts):
            continue  # coffe-snj: ventana
        rows.append(_codex_turn_row(ts, u, ctx))
    return rows


_CODEX_PREAMBLE = ("<environment_context", "<turn_context", "<user_instructions",
                   "<skill>", "# AGENTS.md")


class _CodexSessionScan:
    """Escaneo de un rollout → métricas de sesión (coffe-2sy).

    duration_msgs = prompts de usuario reales (se excluyen los preámbulos
    inyectados por el harness: AGENTS.md, environment/turn context, skills
    — best-effort sobre el texto). n_turns = token_count events (turnos
    con uso). has_agent=False: los subagentes son semántica de Claude; el
    alcance se declara en sessions.agent_semantics. La ventana
    --since/--until se aplica en extract_sessions() (filter_sessions).
    """

    def __init__(self):
        self.cwd = None
        self.prompts = 0
        self.turns = 0
        self.first_ts = None
        self.last_ts = None

    def feed(self, entry):
        p = entry.get("payload") or {}
        pt = p.get("type")
        if entry.get("type") == "session_meta":
            self.cwd = p.get("cwd") or self.cwd
        elif pt == "token_count":
            self.stamp(entry, count_turn=True)
        elif pt == "message" and p.get("role") == "user":
            self.user_msg(entry, p)
        return self

    def stamp(self, entry, count_turn=False):
        ts = parse_ts(entry.get("timestamp"))
        if not ts:
            return
        if count_turn:
            self.turns += 1
        if self.first_ts is None:
            self.first_ts = ts
        self.last_ts = ts
        return self

    def user_msg(self, entry, p):
        self.stamp(entry)
        text = "".join(
            c.get("text", "") for c in (p.get("content") or [])
            if isinstance(c, dict))
        if not text.lstrip().startswith(_CODEX_PREAMBLE):
            self.prompts += 1
        return self


def _codex_scan_file(f):
    scan = _CodexSessionScan()
    with open(f) as fh:
        for line in fh:
            entry = _json_line(line)
            if entry is not None:
                scan.feed(entry)
    if scan.cwd is None or not scan.turns:
        return None
    if not is_charly(scan.cwd):
        return None
    return scan


def _codex_session_row(scan):
    return {
        "tool": "codex", "source": "codex_session_files",
        "project": amp_proj_from_uri(scan.cwd) or "codex-unknown",
        "first_ts": (scan.first_ts or scan.last_ts).isoformat()[:10],
        "first_ts_full": (scan.first_ts or scan.last_ts).isoformat(),
        "last_ts_full": scan.last_ts.isoformat() if scan.last_ts else None,
        "duration_msgs": scan.prompts,
        "n_turns": scan.turns,
        "n_tools": 0,
        "has_agent": False,
        "n_skills": None,
        "n_errors": None,
        "n_compactions": None,
    }


def extract_codex_sessions(sessions_dir=None):
    """coffe-2sy: sesiones codex — 1 rollout = 1 sesión.

    duration_msgs = prompts de usuario reales (se excluyen los preámbulos
    inyectados por el harness: AGENTS.md, environment/turn context, skills
    — best-effort sobre el texto). n_turns = token_count events (turnos
    con uso). has_agent=False: los subagentes son semántica de Claude; el
    alcance se declara en sessions.agent_semantics. La ventana
    --since/--until se aplica en extract_sessions() (filter_sessions).
    """
    sessions = []
    sessions_dir = sessions_dir or (CODEX_DIR / "sessions")
    if not sessions_dir.exists():
        return sessions
    for day_dir in sorted(sessions_dir.glob("*/*/*")):
        if not day_dir.is_dir():
            continue
        for f in sorted(day_dir.glob("rollout-*.jsonl")):
            try:
                scan = _codex_scan_file(f)
                if scan is not None:
                    sessions.append(_codex_session_row(scan))
            except (json.JSONDecodeError, OSError, ValueError, TypeError, KeyError) as e:
                _debug_skip(f"codex-session {f.name}: {e}")
    return sessions

usage_state._link(globals())

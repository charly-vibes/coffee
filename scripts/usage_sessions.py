#!/usr/bin/env python3
"""Estadísticas de sesiones por fuente (extraído de usage-tracker.py).

Parte de la descomposición de scripts/usage-tracker.py (coffe-kav.1):
cada módulo conserva el código original verbatim; los nombres compartidos
viajan por _link() (mismo patrón que viz_fpa_common) y el estado mutable
por usage_state.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import usage_state


def _claude_cache_sessions():
    """Sesiones desde el cache de Claude (extract_session_stats step)."""
    sessions = []
    cache_file = CLAUDE_DIR / "dashboard-cache.json"
    if not cache_file.exists():
        return sessions
    cache = json.loads(cache_file.read_text())
    for key, summary in cache.get("entries", {}).items():
        if summary.get("source") != "claude":
            continue
        proj = summary.get("project", "")
        if not is_charly(proj):
            continue
        tools = summary.get("tool_counts", {})
        skills = summary.get("skill_uses", {})
        sessions.append({
            "tool": "claude-cli",  # coffe-i31: provenance por tool
            "source": "claude_cache",
            "project": proj,
            "first_ts": (summary.get("first_ts") or " ")[:10],
            "first_ts_full": summary.get("first_ts"),
            "last_ts_full": summary.get("last_ts"),
            "duration_msgs": summary.get("user_messages", 0),
            "n_turns": len(summary.get("turns", [])),
            "n_tools": len(tools),
            "has_agent": "Agent" in tools,
            "n_skills": sum(skills.values()),
            "n_errors": len(summary.get("api_errors", [])),
            "n_compactions": summary.get("compactions", 0),
        })
    return sessions


def extract_session_stats():
    """Extrae estadísticas de sesión desde el cache de Claude."""
    # coffe-snj: sesiones que se solapan con la ventana --since/--until
    return filter_sessions(_claude_cache_sessions(), SINCE, UNTIL)


class _PiSessionScan:
    """coffe-i31: escaneo de 1 archivo JSONL de Pi → métricas de sesión.

    En pi el reset es /new (no /clear) y el TUI lo intercepta: nunca
    aparece como user message en el JSONL (verificado en la auditoría:
    0 /new en 981 archivos). La señal correcta de sesión/reset es el
    archivo mismo: cada /new abre JSONL nuevo.

    Métricas sin señal en pi (skills/errores) van en None — _session_stats
    las excluye de los totales en vez de contarlas como 0. n_compactions es
    real desde coffe-64z (CORR-003): las entradas type=="compaction"
    existen en los JSONL de Pi desde ~may-2026 (12 en miblioteca, review
    R5); sin entradas == 0, no None.
    has_agent=False: la semántica Agent (subagentes) es de Claude; el
    alcance se declara en sessions.agent_semantics.
    """

    def __init__(self):
        self.msgs = 0
        self.user_prompts = 0
        self.n_compactions = 0
        self.first_ts = None
        self.last_ts = None
        self.first_raw = None
        self.last_raw = None
        self.tools = set()

    def feed(self, entry):
        if entry.get("type") == "compaction":
            self.n_compactions += 1
            return
        if entry.get("type") != "message":
            return
        self.stamp(entry)
        msg = entry.get("message", {}) or {}
        role = msg.get("role")
        if role == "user":
            self.user_prompts += 1
        elif role == "assistant":
            self.assistant(msg)
        return self

    def stamp(self, entry):
        ts = parse_ts(entry.get("timestamp"))
        if ts is None:
            return
        if self.first_ts is None:
            self.first_ts = ts
            self.first_raw = entry.get("timestamp")
        self.last_ts = ts
        self.last_raw = entry.get("timestamp")
        return self

    def assistant(self, msg):
        self.msgs += 1
        if not _pi_has_tool_call(msg):
            return
        for part in (msg.get("parts") or msg.get("content") or []):
            if isinstance(part, dict) and str(part.get("type", "")).startswith("tool"):
                name = part.get("toolName") or part.get("name")
                self.tools.add(str(name) if name else "tool-call")
        return self


def _pi_session_row(proj, scan):
    if scan.first_ts is None:
        return None  # archivo sin mensajes
    return {
        "tool": "pi",
        "source": "pi_session_files",
        "project": proj,
        "first_ts": scan.first_ts.isoformat()[:10],
        "first_ts_full": scan.first_raw,
        "last_ts_full": scan.last_raw,
        "duration_msgs": scan.user_prompts,
        "n_turns": scan.msgs,
        "n_tools": len(scan.tools),
        "has_agent": False,
        "n_skills": None,
        "n_errors": None,
        "n_compactions": scan.n_compactions,
    }


def _pi_session_file(f, proj, sessions):
    try:
        scan = _PiSessionScan()
        with open(f) as fh:
            for line in fh:
                scan.feed(json.loads(line))
        row = _pi_session_row(proj, scan)
        if row is not None:
            sessions.append(row)
    except (json.JSONDecodeError, OSError, ValueError, TypeError, KeyError) as e:
        _debug_skip(f"pi-session {f.name}: {e}")
    return sessions


def extract_pi_sessions(sessions_dir=None):
    """coffe-i31: sesiones pi — 1 archivo JSONL = 1 sesión.

    sessions_dir permite tests con tmpdir (como extract_pi, coffe-8t8).
    La ventana --since/--until se aplica en extract_sessions() (una sesión
    puede solaparse con la ventana — filter_sessions).
    """
    sessions = []
    sessions_dir = sessions_dir or (PI_DIR / "sessions")
    if not sessions_dir.exists():
        return sessions
    for sd in sorted(sessions_dir.iterdir()):
        if not sd.is_dir():
            continue
        proj = sd.name
        if not is_charly(proj):
            continue  # coffe-i31: scope consistente
        for f in sorted(sd.glob("*.jsonl")):
            _pi_session_file(f, proj, sessions)
    return sessions


def _amp_session_hit(f):
    """(ts, proyecto, timestamp crudo) por archivo tocado, o None."""
    try:
        entry = json.loads(f.read_text())
        uri = entry.get("uri", "")
        if not is_charly(uri):
            return None
        ts = parse_ts(entry.get("timestamp"))
        if not ts:
            return None
        raw = entry.get("timestamp")
        # coffe-372: logs amp reales usan epoch ms INT — el crudo
        # se normaliza a ISO (filter_sessions hace [:10]; un int
        # crashea con ventana --since/--until). Strings intactos.
        raw = raw if isinstance(raw, str) else ts.isoformat()
        return (ts, amp_proj_from_uri(uri) or "amp-unknown", raw)
    except (json.JSONDecodeError, OSError, ValueError, TypeError, KeyError):
        return None


def extract_amp_sessions(amp_dir=None):
    """coffe-i31: sesiones amp — 1 dir de file-changes = 1 tarea.

    Amp es un agente autónomo: has_agent=True por definición (se declara
    en sessions.agent_semantics). n_turns = archivos tocados; sin señal
    de skills/errores/compactions (None).
    """
    sessions = []
    amp_dir = amp_dir or (AMP_DIR / "file-changes")
    if not amp_dir.exists():
        return sessions
    for td in sorted(amp_dir.iterdir()):
        if not td.is_dir():
            continue
        hits = sorted(h for h in (_amp_session_hit(f) for f in td.iterdir()) if h)
        if not hits:
            continue
        first, last = hits[0][0], hits[-1][0]
        sessions.append({
            "tool": "amp",
            "source": "amp_file_changes",
            "project": hits[0][1],
            "first_ts": first.isoformat()[:10],
            "first_ts_full": hits[0][2],  # timestamp crudo del log
            "last_ts_full": hits[-1][2],
            "duration_msgs": len(hits),
            "n_turns": len(hits),
            "n_tools": 0,
            "has_agent": True,
            "n_skills": None,
            "n_errors": None,
            "n_compactions": None,
        })
    return sessions


def extract_sessions():
    """coffe-i31: sesiones de todas las tools (claude cache + pi files +
    amp tasks), con la ventana --since/--until aplicada a las nuevas
    fuentes (el claude cache ya la aplica en extract_session_stats)."""
    return (extract_session_stats()
            + filter_sessions(extract_pi_sessions(), SINCE, UNTIL)
            + filter_sessions(extract_amp_sessions(), SINCE, UNTIL)
            + filter_sessions(extract_codex_sessions(), SINCE, UNTIL)  # coffe-2sy
            + filter_sessions(extract_omp_sessions(), SINCE, UNTIL))

usage_state._link(globals())

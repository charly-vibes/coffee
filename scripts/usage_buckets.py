#!/usr/bin/env python3
"""Buckets hourly/daily, session_stats, skills y commands (extraído de usage-tracker.py).

Parte de la descomposición de scripts/usage-tracker.py (coffe-kav.1):
cada módulo conserva el código original verbatim; los nombres compartidos
viajan por _link() (mismo patrón que viz_fpa_common) y el estado mutable
por usage_state.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import usage_state


class Bucket:
    """Acumulador de métricas para un bucket de agregación (día, mes, proyecto).

    Una sola implementación del bloque de acumulación que antes estaba
    copiado 4 veces en aggregate() — el cambio de contabilidad de tokens
    se hace en UN lugar.
    """

    def __init__(self):
        self.interactions = 0
        self.input_tokens = 0
        self.output_tokens = 0
        self.cache_read_tokens = 0
        self.cache_write_tokens = 0
        self.cost_effective = 0.0
        self.cost_real = 0.0
        self.tools = Counter()
        self.models = Counter()
        self.first_seen = None
        self.last_seen = None
        self.skills = Counter()

    def add(self, r, real_cost):
        self.interactions += 1
        self.input_tokens += r.get("input_tokens", 0) or 0
        self.output_tokens += r.get("output_tokens", 0) or 0
        self.cache_read_tokens += r.get("cache_read_tokens", 0) or 0
        self.cache_write_tokens += r.get("cache_write_tokens", 0) or 0
        self.cost_effective += r.get("cost_effective", 0) or 0
        self.cost_real += real_cost
        self.tools[r["tool"]] += 1
        self.models[r["model_raw"]] += 1

    def to_dict(self, detail=False):
        d = {
            "interactions": self.interactions,
            "input_tokens": self.input_tokens,
            "output_tokens": self.output_tokens,
            "cache_read_tokens": self.cache_read_tokens,
            "cache_write_tokens": self.cache_write_tokens,
            "cost_effective": round(self.cost_effective, 2),
            "cost_real": round(self.cost_real, 2),
            "tools": dict(self.tools.most_common()),
            "models": dict(self.models.most_common()),
        }
        if detail:
            d["subscription_fees"] = 0.0
        return d


class HourlyBucket(Bucket):
    """Bucket horario: además lleva stats detalladas por herramienta."""

    def __init__(self):
        super().__init__()
        self.tool_stats = defaultdict(Bucket)

    def add(self, r, real_cost):
        super().add(r, real_cost)
        ts = self.tool_stats[r["tool"]]
        ts.interactions += 1
        ts.input_tokens += r.get("input_tokens", 0) or 0
        ts.output_tokens += r.get("output_tokens", 0) or 0
        ts.cache_read_tokens += r.get("cache_read_tokens", 0) or 0
        ts.cache_write_tokens += r.get("cache_write_tokens", 0) or 0
        ts.cost_effective += r.get("cost_effective", 0) or 0
        ts.cost_real += real_cost
        return self

    def to_dict(self):
        d = super().to_dict()
        d["tools"] = {t: {
            "req": b.interactions, "in": b.input_tokens, "out": b.output_tokens,
            "cache_read": b.cache_read_tokens, "cache_write": b.cache_write_tokens,
            "cost_eff": round(b.cost_effective, 8), "cost_real": round(b.cost_real, 8),
        } for t, b in self.tool_stats.items()}
        return d

def _length_bucket(n):
    for cap, label in _LENGTH_BUCKETS:
        if n <= cap:
            return label
    return "500+"


_RESET_SIGNALS = {
    "claude-cli": lambda bt, commands: (
        "/clear en history.jsonl (solo claude)", commands.get("/clear", 0)),
    "pi": lambda bt, commands: (
        "archivos de sesión pi (1 archivo = 1 /new)", bt["total"]),
    "omp": lambda bt, commands: (
        "archivos de sesión omp (1 archivo = 1 sesión)", bt["total"]),
}


def _reset_signal(tool, bt, commands):
    """coffe-i31: resets por tool — señales distintas por harness
    (FPA-117/118 adaptado): claude cuenta /clear del history.jsonl;
    en pi cada /new abre un archivo nuevo (el conteo de archivos de
    sesión ES el conteo de resets); amp es 1 tarea = 1 corrida."""
    fn = _RESET_SIGNALS.get(tool)
    if fn is None:
        return f"tareas {tool} (1 dir = 1 corrida)", bt["total"]
    return fn(bt, commands)


def _fold_session(stats, s):
    n = s["n_turns"]
    stats["length_distribution"][_length_bucket(n)] += 1
    if s.get("has_agent"):
        stats["with_agent"] += 1
    # coffe-i31: None = sin señal (no 0) — excluido del total
    stats["total_api_errors"] += s.get("n_errors") or 0
    stats["total_compactions"] += s.get("n_compactions") or 0
    stats["avg_turns"] += n
    stats["avg_tools"] += s.get("n_tools") or 0
    if s.get("n_skills") is not None:
        stats["avg_skills"] += s["n_skills"]
    bt = stats["by_tool"][s.get("tool", "claude-cli")]
    bt["total"] += 1
    if s.get("has_agent"):
        bt["with_agent"] += 1
    return (n, s["duration_msgs"], s["first_ts"], s["project"])


def _session_stats(sessions, commands=None):
    """Estadísticas de sesiones (largos, autonomía, promedios, top).

    coffe-i31: multi-tool con provenance — by_tool (totals/with_agent/
    resets por tool) y agent_semantics declarando el alcance. Los campos
    sin señal en una tool (pi/amp: skills/errores/compactions) van en
    None y se excluyen de los totales, no se cuentan como 0.
    """
    stats = {
        "total_sessions": len(sessions),
        "length_distribution": Counter(),
        "with_agent": 0,
        "total_api_errors": 0,
        "total_compactions": 0,
        "avg_turns": 0,
        "avg_tools": 0,
        "avg_skills": 0,
        "agent_semantics": (
            "claude-cli: sesión con herramienta Agent (subagentes); "
            "amp: autónomo por definición (has_agent=True); "
            "pi: sin señal de subagentes (has_agent=False); "
            "omp: sin señal de subagentes (has_agent=False)"),
        "by_tool": defaultdict(lambda: {
            "total": 0, "with_agent": 0,
            "resets": {"signal": "", "count": 0}}),
    }
    longest = [_fold_session(stats, s) for s in sessions]
    if sessions:
        n = len(sessions)
        stats["avg_turns"] /= n
        stats["avg_tools"] /= n
        n_sk = sum(1 for s in sessions if s.get("n_skills") is not None)
        stats["avg_skills"] = stats["avg_skills"] / n_sk if n_sk else 0
    commands = commands or Counter()
    for tool, bt in stats["by_tool"].items():
        signal, count = _reset_signal(tool, bt, commands)
        bt["resets"] = {"signal": signal, "count": count}
    stats["by_tool"] = dict(stats["by_tool"])
    longest.sort(key=lambda x: -x[0])
    stats["top_longest_by_turns"] = [
        {"turns": t, "msgs": m, "date": d, "project": clean_proj_name(p)}
        for t, m, d, p in longest[:10]
    ]
    return stats


def _skills_from_cache(by_skill_total, skills_by_project):
    """Skills del cache de Claude (dashboard-cache.json)."""
    cache_file = CLAUDE_DIR / "dashboard-cache.json"
    if not cache_file.exists():
        return by_skill_total
    cache = json.loads(cache_file.read_text())
    for key, summary in cache.get("entries", {}).items():
        if summary.get("source") != "claude":
            continue
        proj = summary.get("project", "")
        if not is_charly(proj):
            continue
        proj_clean = clean_proj_name(proj)
        for skill, count in summary.get("skill_uses", {}).items():
            by_skill_total[skill] += count
            skills_by_project[proj_clean][skill] += count
    return by_skill_total


def _command_of(display):
    """Comando slash de un display del history ('/cmd args...'), o None."""
    stripped = display.strip()
    if not (stripped.startswith("/") and len(stripped) > 2):
        return None
    cmd = stripped.split()[0]
    return cmd if 2 <= len(cmd) <= 30 else None


def _commands_from_history(commands):
    """Comandos slash del history.jsonl (solo proyectos en scope)."""
    hist_file = CLAUDE_DIR / "history.jsonl"
    if not hist_file.exists():
        return commands
    with open(hist_file) as f:
        for line in f:
            try:
                entry = json.loads(line)
            except json.JSONDecodeError:
                continue
            if not is_charly(entry.get("project", "")):
                continue
            cmd = _command_of(entry.get("display", ""))
            if cmd:
                commands[cmd] += 1
    return commands


def collect_skills_and_commands():
    """Extrae skills (cache de Claude) y comandos slash (history.jsonl).

    Vive FUERA de aggregate() para que la agregación sea pura y testeable:
    los resultados se inyectan como parámetros.
    """
    by_skill_total = Counter()
    skills_by_project = defaultdict(Counter)  # proyecto limpio -> skill -> count
    _skills_from_cache(by_skill_total, skills_by_project)
    commands = Counter()
    _commands_from_history(commands)
    return by_skill_total, skills_by_project, commands

usage_state._link(globals())

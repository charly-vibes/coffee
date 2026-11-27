#!/usr/bin/env python3
"""
test_tracker_codex.py — extract_codex: rollouts de Codex CLI standalone (coffe-2sy)

Cubre el alcance del ticket coffe-2sy:
- extract_codex(): ingesta de ~/.codex/sessions/YYYY/MM/DD/rollout-*.jsonl
  (Codex CLI standalone, no vía Pi). Antes solo entraba Codex-via-Pi
  (provider openai-codex); las sesiones del TUI/CLI no se medían.
  1 rollout = 1 sesión; filas por token_count con last_token_usage como
  delta del turno (total_token_usage es acumulativo).
- input_tokens de Codex incluye los cached: fresh = input - cached
  (semántica Claude/Anthropic, para no doble contar en costo/energía).
- Filtro is_charly(cwd) unificado con --filter; proyecto derivado del cwd
  vía amp_proj_from_uri (org-repo, mismo munging que las demás fuentes).
- extract_codex_sessions(): 1 rollout = 1 sesión (like pi/amp).
- model_details: gpt-6.1-sol → ("codex", "gpt-6.1") (los modelos gpt-5.x
  del fallback no cubren los actuales).

Estilo stdlib-only, fixtures falsos en tmpdir como test_tracker_amp.py.
"""

import importlib.util
import json
import tempfile
import unittest
from datetime import date
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
TRACKER_PATH = REPO / "scripts" / "usage-tracker.py"


def load_tracker():
    spec = importlib.util.spec_from_file_location("usage_tracker_codex", TRACKER_PATH)
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


ut = load_tracker()


# ============================ fixtures ============================

DEFAULT_TS = {"meta": "2026-10-06T16:39:00.158Z",
              "settings": "2026-10-06T16:39:01.000Z"}


def rollout_line(kind, data, ts=None):
    """Fábrica única de eventos de rollout (fixtures coffe-2sy).

    kind: 'meta' | 'settings' | 'tokens' | 'turn_context' | 'user_msg';
    data son los campos que varían por evento y ts su timestamp
    (default por kind para meta/settings).
    """
    if ts is None:
        ts = DEFAULT_TS[kind]
    if kind == "meta":
        line_type = "session_meta"
        payload = {"id": "01a", "timestamp": ts, "cwd": data["cwd"],
                   "originator": "codex-tui", "cli_version": "0.160.1"}
    elif kind == "settings":
        line_type = "event_msg"
        payload = {"type": "thread_settings_applied", "thread_id": "01a",
                   "thread_settings": {"model": data["model"]}}
    elif kind == "tokens":
        line_type = "event_msg"
        inp, cached, out = data["toks"]
        usage = {"input_tokens": inp, "cached_input_tokens": cached,
                 "cache_write_input_tokens": 0, "output_tokens": out,
                 "reasoning_output_tokens": data["reasoning"],
                 "total_tokens": inp + out}
        payload = {"type": "token_count",
                   "info": {"total_token_usage": usage,
                            "last_token_usage": usage}}
    elif kind == "turn_context":
        # formato viejo (abr-2026): sin thread_settings_applied, el modelo
        # viaja en response_item turn_context
        line_type = "response_item"
        payload = {"turn_id": "t1", "cwd": data["cwd"],
                   "model": data["model"]}
    else:  # user_msg
        line_type = "response_item"
        payload = {"type": "message", "role": "user",
                   "content": [{"type": "input_text", "text": data["text"]}]}
    return {"type": line_type, "timestamp": ts, "payload": payload}


def write_rollout(tmp, lines, day="2026/10/06",
                  name="rollout-2026-10-06T16-39-00-01a.jsonl"):
    d = tmp / "sessions" / day
    d.mkdir(parents=True, exist_ok=True)
    p = d / name
    p.write_text("\n".join(json.dumps(x) for x in lines) + "\n")
    return p


def rollout_basico(tmp, cwd, cached=0, reasoning=0):
    """Rollout mínimo estándar: meta + settings + 1 token_count gpt-6.1-sol."""
    write_rollout(tmp, [
        rollout_line("meta", {"cwd": cwd}),
        rollout_line("settings", {"model": "gpt-6.1-sol"}),
        rollout_line("tokens", {"toks": (100, cached, 10), "reasoning": reasoning}, ts="2026-10-06T16:39:36.146Z"),
    ])


CWD_REPLY = "/var/home/sasha/para/areas/dev/gh/sk/REPLy.jl"
CWD_COFFEE = "/var/home/sasha/para/areas/dev/gh/charly/coffee"
CWD_FUERA = "/var/home/alguien/proyecto"


# ============================ model_details ============================

class TestModelDetailsGpt6(unittest.TestCase):
    """coffe-2sy: los modelos actuales de Codex no son gpt-5.x."""

    # (model_raw, (familia, versión)) esperados — tabla coffe-2sy
    CASOS_GPT = [
        ("gpt-6.1-sol", ("codex", "gpt-6.1")),
        # gpt-6-luna etc.: familia codex (no "other"), version genérica
        ("gpt-6-luna", ("codex", "gpt-6")),
        ("gpt-5.3", ("codex", "gpt-5.3")),
    ]

    def test_model_details_gpt(self):
        for raw, esperado in self.CASOS_GPT:
            with self.subTest(model_raw=raw):
                self.assertEqual(ut.model_details(raw), esperado)


# ============================ extract_codex ============================

class CodexTmpBase(unittest.TestCase):
    """Aísla ~/.codex en un tmpdir y preserva los globales del módulo."""

    def setUp(self):
        self._old = (ut.CHARLY_FILTER, ut.CODEX_DIR, ut.SINCE, ut.UNTIL)
        ut.SINCE = ut.UNTIL = None
        self.tmp = tempfile.TemporaryDirectory()
        ut.CODEX_DIR = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()
        (ut.CHARLY_FILTER, ut.CODEX_DIR, ut.SINCE, ut.UNTIL) = self._old


class TestExtractCodex(CodexTmpBase):
    """Filas por turno; tokens fresh/cached; proyecto por cwd."""

    def test_una_fila_por_token_count(self):
        write_rollout(Path(self.tmp.name), [
            rollout_line("meta", {"cwd": CWD_REPLY}),
            rollout_line("settings", {"model": "gpt-6.1-sol"}),
            rollout_line("tokens", {"toks": (100, 80, 10), "reasoning": 0}, ts="2026-10-06T16:39:36.146Z"),
            rollout_line("tokens", {"toks": (130, 120, 15), "reasoning": 0}, ts="2026-10-06T16:39:51.657Z"),
        ])
        rows = ut.extract_codex()
        self.assertEqual(len(rows), 2)

    def test_fresh_vs_cached(self):
        # input de codex incluye cached: fresh = input - cached
        write_rollout(Path(self.tmp.name), [
            rollout_line("meta", {"cwd": CWD_REPLY}),
            rollout_line("settings", {"model": "gpt-6.1-sol"}),
            rollout_line("tokens", {"toks": (100, 80, 10), "reasoning": 4}, ts="2026-10-06T16:39:36.146Z"),
        ])
        r = ut.extract_codex()[0]
        self.assertEqual(r["input_tokens"], 20)
        self.assertEqual(r["cache_read_tokens"], 80)
        self.assertEqual(r["cache_write_tokens"], 0)
        self.assertEqual(r["output_tokens"], 10)

    def test_modelo_y_fuente(self):
        rollout_basico(Path(self.tmp.name), CWD_REPLY)
        r = ut.extract_codex()[0]
        self.assertEqual(r["source"], "codex")
        self.assertEqual(r["tool"], "codex")
        self.assertEqual(r["model_raw"], "gpt-6.1-sol")
        self.assertEqual(r["model_family"], "codex")
        self.assertEqual(r["model_version"], "gpt-6.1")

    def test_proyecto_desde_cwd(self):
        rollout_basico(Path(self.tmp.name), CWD_REPLY)
        self.assertEqual(ut.extract_codex()[0]["project"], "sk-REPLy-jl")

    def test_costo_estimado_pay_per_token(self):
        # sin cost en el log → estimación (como gemini-cli, coffe-8t8)
        rollout_basico(Path(self.tmp.name), CWD_REPLY, cached=80)
        r = ut.extract_codex()[0]
        self.assertGreater(r["cost_effective"], 0)

    def test_cwd_fuera_de_charly_filtrado(self):
        rollout_basico(Path(self.tmp.name), CWD_FUERA)
        self.assertEqual(ut.extract_codex(), [])

    def test_filter_all_acepta_cwd_fuera(self):
        ut.CHARLY_FILTER = False
        rollout_basico(Path(self.tmp.name), CWD_FUERA)
        self.assertEqual(len(ut.extract_codex()), 1)

    def test_modelo_de_turn_context_formato_viejo(self):
        write_rollout(Path(self.tmp.name), [
            rollout_line("meta", {"cwd": CWD_REPLY}),
            rollout_line("turn_context", {"model": "gpt-5.4", "cwd": CWD_REPLY}, ts="2026-04-06T19:55:24.000Z"),
            rollout_line("tokens", {"toks": (100, 0, 10), "reasoning": 0}, ts="2026-04-06T19:56:00.000Z"),
        ])
        r = ut.extract_codex()[0]
        self.assertEqual(r["model_raw"], "gpt-5.4")
        self.assertEqual(r["model_version"], "gpt-5.4")

    def test_ventana_since_until(self):
        ut.SINCE, ut.UNTIL = date(2026, 10, 6), date(2026, 10, 6)
        write_rollout(Path(self.tmp.name), [
            rollout_line("meta", {"cwd": CWD_REPLY}),
            rollout_line("settings", {"model": "gpt-6.1-sol"}),
            rollout_line("tokens", {"toks": (100, 0, 10), "reasoning": 0}, ts="2026-10-06T16:39:36.146Z"),
            rollout_line("tokens", {"toks": (50, 0, 5), "reasoning": 0}, ts="2026-10-09T10:00:00.000Z"),  # fuera de ventana
        ])
        rows = ut.extract_codex()
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["input_tokens"], 100)

    def test_dir_inexistente_da_vacio(self):
        self.assertEqual(ut.extract_codex(), [])

    def test_linea_malformada_no_crashea(self):
        d = Path(self.tmp.name) / "sessions" / "2026/10/06"
        d.mkdir(parents=True)
        (d / "rollout-2026-10-06T16-39-00-01a.jsonl").write_text(
            "{no es json}\n" + json.dumps(rollout_line("meta", {"cwd": CWD_COFFEE})) + "\n"
            + json.dumps(rollout_line("tokens", {"toks": (10, 0, 2), "reasoning": 0}, ts="2026-10-06T16:39:36.146Z")) + "\n")
        rows = ut.extract_codex()
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["project"], "charly-coffee")

    def test_uso_acumulado_no_doble_contado(self):
        # sanity del diseño: usar last_token_usage (delta), no total_token_usage
        write_rollout(Path(self.tmp.name), [
            rollout_line("meta", {"cwd": CWD_REPLY}),
            rollout_line("settings", {"model": "gpt-6.1-sol"}),
            rollout_line("tokens", {"toks": (1000, 900, 100), "reasoning": 0}, ts="2026-10-06T16:39:36.146Z"),
            rollout_line("tokens", {"toks": (2000, 1900, 110), "reasoning": 0}, ts="2026-10-06T16:39:51.657Z"),
        ])
        rows = ut.extract_codex()
        total_fresh = sum(r["input_tokens"] for r in rows)
        self.assertEqual(total_fresh, 100 + 100)  # no 1000+2000


# ============================ extract_codex_sessions ============================

class TestExtractCodexSessions(CodexTmpBase):
    """1 rollout = 1 sesión; prompts de usuario reales (sin preámbulos)."""

    def test_una_sesion_por_rollout(self):
        write_rollout(Path(self.tmp.name), [
            rollout_line("meta", {"cwd": CWD_REPLY}),
            rollout_line("settings", {"model": "gpt-6.1-sol"}),
            rollout_line("user_msg", {"text": "# AGENTS.md instructions for " + CWD_REPLY}, ts="2026-10-06T16:39:29.706Z"),
            rollout_line("user_msg", {"text": "lets do a round of evaluation"}, ts="2026-10-06T16:39:29.740Z"),
            rollout_line("tokens", {"toks": (100, 0, 10), "reasoning": 0}, ts="2026-10-06T16:39:36.146Z"),
            rollout_line("tokens", {"toks": (150, 100, 20), "reasoning": 0}, ts="2026-10-06T16:52:21.801Z"),
        ])
        s = ut.extract_codex_sessions()
        self.assertEqual(len(s), 1)
        self.assertEqual(s[0]["tool"], "codex")
        self.assertEqual(s[0]["project"], "sk-REPLy-jl")
        self.assertEqual(s[0]["first_ts"], "2026-10-06")
        self.assertEqual(s[0]["duration_msgs"], 1)  # el AGENTS.md no es prompt real
        self.assertEqual(s[0]["n_turns"], 2)
        self.assertFalse(s[0]["has_agent"])

    def test_fuera_de_charly_filtrado(self):
        write_rollout(Path(self.tmp.name), [
            rollout_line("meta", {"cwd": CWD_FUERA}),
            rollout_line("user_msg", {"text": "hi"}, ts="2026-10-06T16:39:29.740Z"),
            rollout_line("tokens", {"toks": (100, 0, 10), "reasoning": 0}, ts="2026-10-06T16:39:36.146Z"),
        ])
        self.assertEqual(ut.extract_codex_sessions(), [])

    def test_dir_inexistente_da_vacio(self):
        self.assertEqual(ut.extract_codex_sessions(), [])


if __name__ == "__main__":
    unittest.main()

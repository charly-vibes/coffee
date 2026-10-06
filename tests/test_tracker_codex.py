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

def meta_line(cwd, ts="2026-10-06T16:39:00.158Z", sid="01a"):
    return {"type": "session_meta", "timestamp": ts,
            "payload": {"id": sid, "timestamp": ts, "cwd": cwd,
                        "originator": "codex-tui", "cli_version": "0.160.1"}}


def settings_line(model, ts="2026-10-06T16:39:01.000Z"):
    return {"type": "event_msg", "timestamp": ts,
            "payload": {"type": "thread_settings_applied", "thread_id": "01a",
                        "thread_settings": {"model": model}}}


def tok_line(ts, inp, cached, out, reasoning=0):
    usage = {"input_tokens": inp, "cached_input_tokens": cached,
             "cache_write_input_tokens": 0, "output_tokens": out,
             "reasoning_output_tokens": reasoning,
             "total_tokens": inp + out}
    return {"type": "event_msg", "timestamp": ts,
            "payload": {"type": "token_count",
                        "info": {"total_token_usage": usage,
                                 "last_token_usage": usage}}}


def turn_context_line(ts, model, cwd):
    # formato viejo (abr-2026): sin thread_settings_applied, el modelo viaja
    # en response_item turn_context (payload.type None, payload.model)
    return {"type": "response_item", "timestamp": ts,
            "payload": {"turn_id": "t1", "cwd": cwd, "model": model} }


def user_msg_line(ts, text):
    return {"type": "response_item", "timestamp": ts,
            "payload": {"type": "message", "role": "user",
                        "content": [{"type": "input_text", "text": text}]}}


def write_rollout(tmp, lines, day="2026/10/06",
                  name="rollout-2026-10-06T16-39-00-01a.jsonl"):
    d = tmp / "sessions" / day
    d.mkdir(parents=True, exist_ok=True)
    p = d / name
    p.write_text("\n".join(json.dumps(x) for x in lines) + "\n")
    return p


CWD_REPLY = "/var/home/sasha/para/areas/dev/gh/sk/REPLy.jl"
CWD_COFFEE = "/var/home/sasha/para/areas/dev/gh/charly/coffee"
CWD_FUERA = "/var/home/alguien/proyecto"


# ============================ model_details ============================

class TestModelDetailsGpt6(unittest.TestCase):
    """coffe-2sy: los modelos actuales de Codex no son gpt-5.x."""

    def test_gpt_6_1_sol(self):
        self.assertEqual(ut.model_details("gpt-6.1-sol"), ("codex", "gpt-6.1"))

    def test_gpt_6_variante_catchall(self):
        # gpt-6-luna etc.: familia codex (no "other"), version genérica
        fam, ver = ut.model_details("gpt-6-luna")
        self.assertEqual(fam, "codex")

    def test_gpt_5_x_sigue(self):
        self.assertEqual(ut.model_details("gpt-5.3"), ("codex", "gpt-5.3"))


# ============================ extract_codex ============================

class TestExtractCodex(unittest.TestCase):
    """Filas por turno; tokens fresh/cached; proyecto por cwd."""

    def setUp(self):
        self._old = (ut.CHARLY_FILTER, ut.CODEX_DIR, ut.SINCE, ut.UNTIL)
        ut.SINCE = ut.UNTIL = None
        self.tmp = tempfile.TemporaryDirectory()
        ut.CODEX_DIR = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()
        (ut.CHARLY_FILTER, ut.CODEX_DIR, ut.SINCE, ut.UNTIL) = self._old

    def test_una_fila_por_token_count(self):
        write_rollout(Path(self.tmp.name), [
            meta_line(CWD_REPLY),
            settings_line("gpt-6.1-sol"),
            tok_line("2026-10-06T16:39:36.146Z", 100, 80, 10),
            tok_line("2026-10-06T16:39:51.657Z", 130, 120, 15),
        ])
        rows = ut.extract_codex()
        self.assertEqual(len(rows), 2)

    def test_fresh_vs_cached(self):
        # input de codex incluye cached: fresh = input - cached
        write_rollout(Path(self.tmp.name), [
            meta_line(CWD_REPLY),
            settings_line("gpt-6.1-sol"),
            tok_line("2026-10-06T16:39:36.146Z", 100, 80, 10, reasoning=4),
        ])
        r = ut.extract_codex()[0]
        self.assertEqual(r["input_tokens"], 20)
        self.assertEqual(r["cache_read_tokens"], 80)
        self.assertEqual(r["cache_write_tokens"], 0)
        self.assertEqual(r["output_tokens"], 10)

    def test_modelo_y_fuente(self):
        write_rollout(Path(self.tmp.name), [
            meta_line(CWD_REPLY),
            settings_line("gpt-6.1-sol"),
            tok_line("2026-10-06T16:39:36.146Z", 100, 0, 10),
        ])
        r = ut.extract_codex()[0]
        self.assertEqual(r["source"], "codex")
        self.assertEqual(r["tool"], "codex")
        self.assertEqual(r["model_raw"], "gpt-6.1-sol")
        self.assertEqual(r["model_family"], "codex")
        self.assertEqual(r["model_version"], "gpt-6.1")

    def test_proyecto_desde_cwd(self):
        write_rollout(Path(self.tmp.name), [
            meta_line(CWD_REPLY),
            settings_line("gpt-6.1-sol"),
            tok_line("2026-10-06T16:39:36.146Z", 100, 0, 10),
        ])
        self.assertEqual(ut.extract_codex()[0]["project"], "sk-REPLy-jl")

    def test_costo_estimado_pay_per_token(self):
        # sin cost en el log → estimación (como gemini-cli, coffe-8t8)
        write_rollout(Path(self.tmp.name), [
            meta_line(CWD_REPLY),
            settings_line("gpt-6.1-sol"),
            tok_line("2026-10-06T16:39:36.146Z", 100, 80, 10),
        ])
        r = ut.extract_codex()[0]
        self.assertGreater(r["cost_effective"], 0)

    def test_cwd_fuera_de_charly_filtrado(self):
        write_rollout(Path(self.tmp.name), [
            meta_line(CWD_FUERA),
            settings_line("gpt-6.1-sol"),
            tok_line("2026-10-06T16:39:36.146Z", 100, 0, 10),
        ])
        self.assertEqual(ut.extract_codex(), [])

    def test_filter_all_acepta_cwd_fuera(self):
        ut.CHARLY_FILTER = False
        write_rollout(Path(self.tmp.name), [
            meta_line(CWD_FUERA),
            settings_line("gpt-6.1-sol"),
            tok_line("2026-10-06T16:39:36.146Z", 100, 0, 10),
        ])
        self.assertEqual(len(ut.extract_codex()), 1)

    def test_modelo_de_turn_context_formato_viejo(self):
        write_rollout(Path(self.tmp.name), [
            meta_line(CWD_REPLY),
            turn_context_line("2026-04-06T19:55:24.000Z", "gpt-5.4", CWD_REPLY),
            tok_line("2026-04-06T19:56:00.000Z", 100, 0, 10),
        ])
        r = ut.extract_codex()[0]
        self.assertEqual(r["model_raw"], "gpt-5.4")
        self.assertEqual(r["model_version"], "gpt-5.4")

    def test_ventana_since_until(self):
        ut.SINCE, ut.UNTIL = date(2026, 10, 6), date(2026, 10, 6)
        write_rollout(Path(self.tmp.name), [
            meta_line(CWD_REPLY),
            settings_line("gpt-6.1-sol"),
            tok_line("2026-10-06T16:39:36.146Z", 100, 0, 10),
            tok_line("2026-10-09T10:00:00.000Z", 50, 0, 5),  # fuera de ventana
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
            "{no es json}\n" + json.dumps(meta_line(CWD_COFFEE)) + "\n"
            + json.dumps(tok_line("2026-10-06T16:39:36.146Z", 10, 0, 2)) + "\n")
        rows = ut.extract_codex()
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["project"], "charly-coffee")

    def test_uso_acumulado_no_doble_contado(self):
        # sanity del diseño: usar last_token_usage (delta), no total_token_usage
        write_rollout(Path(self.tmp.name), [
            meta_line(CWD_REPLY),
            settings_line("gpt-6.1-sol"),
            tok_line("2026-10-06T16:39:36.146Z", 1000, 900, 100),
            tok_line("2026-10-06T16:39:51.657Z", 2000, 1900, 110),
        ])
        rows = ut.extract_codex()
        total_fresh = sum(r["input_tokens"] for r in rows)
        self.assertEqual(total_fresh, 100 + 100)  # no 1000+2000


# ============================ extract_codex_sessions ============================

class TestExtractCodexSessions(unittest.TestCase):
    """1 rollout = 1 sesión; prompts de usuario reales (sin preámbulos)."""

    def setUp(self):
        self._old = (ut.CHARLY_FILTER, ut.CODEX_DIR, ut.SINCE, ut.UNTIL)
        ut.SINCE = ut.UNTIL = None
        self.tmp = tempfile.TemporaryDirectory()
        ut.CODEX_DIR = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()
        (ut.CHARLY_FILTER, ut.CODEX_DIR, ut.SINCE, ut.UNTIL) = self._old

    def test_una_sesion_por_rollout(self):
        write_rollout(Path(self.tmp.name), [
            meta_line(CWD_REPLY),
            settings_line("gpt-6.1-sol"),
            user_msg_line("2026-10-06T16:39:29.706Z",
                          "# AGENTS.md instructions for " + CWD_REPLY),
            user_msg_line("2026-10-06T16:39:29.740Z",
                          "lets do a round of evaluation"),
            tok_line("2026-10-06T16:39:36.146Z", 100, 0, 10),
            tok_line("2026-10-06T16:52:21.801Z", 150, 100, 20),
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
            meta_line(CWD_FUERA),
            user_msg_line("2026-10-06T16:39:29.740Z", "hi"),
            tok_line("2026-10-06T16:39:36.146Z", 100, 0, 10),
        ])
        self.assertEqual(ut.extract_codex_sessions(), [])

    def test_dir_inexistente_da_vacio(self):
        self.assertEqual(ut.extract_codex_sessions(), [])


if __name__ == "__main__":
    unittest.main()

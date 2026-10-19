"""coffe-i31: sesiones multi-tool con provenance.

Antes del fix, extract_session_stats() solo leía el cache de Claude
(source=='claude'): 557 sesiones pi quedaban fuera y /clear (FPA-117/118)
era una métrica Claude-only sin nota de scope. Ahora:

- pi: 1 archivo JSONL = 1 sesión (cada /new abre archivo nuevo; el TUI
  intercepta /new, nunca aparece como user message).
- amp: 1 dir de file-changes = 1 tarea autónoma.
- by_tool: totals, with_agent y resets con señal por tool.
"""
import importlib.util
import json
import sys
import tempfile
import unittest
from collections import Counter
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]


def _load(name, relpath):
    spec = importlib.util.spec_from_file_location(name, REPO / relpath)
    mod = importlib.util.module_from_spec(spec)
    sys.path.insert(0, str(REPO / "scripts"))
    spec.loader.exec_module(mod)
    return mod


ut = _load("usage_tracker_i31", "scripts/usage-tracker.py")
viz = _load("viz_fpa_i31", "scripts/viz-fpa.py")


def row(ts="2026-05-10T14:23:00+00:00", project="charly-coffee", tool="claude-cli",
        model_raw="claude-sonnet-4-6", **kw):
    """Row sintético con el shape exacto que producen los extractores."""
    base = {
        "source": "test", "tool": tool, "model_raw": model_raw,
        "model_family": "claude", "model_version": "sonnet-4.6",
        "project": project, "timestamp": ts,
        "hour": ut.hour_key(ut.parse_ts(ts)),
        "input_tokens": 1000, "output_tokens": 500,
        "cache_read_tokens": 0, "cache_write_tokens": 0,
        "cost_effective": 0.01,
    }
    base.update(kw)
    return base


ut.row = row  # los tests llaman ut.row


def _pi_msg(ts, role, parts=None, usage=None, provider=None):
    msg = {"role": role, "content": parts or []}
    if usage is not None:
        msg["usage"] = usage
    e = {"type": "message", "timestamp": ts, "message": msg}
    if provider is not None:
        e["provider"] = provider
    return e


def _write_jsonl(path, entries):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(json.dumps(e) for e in entries) + "\n")


def _pi_compaction(ts):
    return {"type": "compaction", "timestamp": ts, "tokensBefore": 100000,
            "fromHook": False}


def _pi_project(tmp, name):
    """Escribe una sesión pi de 5 mensajes (2 user, 3 assistant, 1 tool)."""
    d = Path(tmp) / name
    _write_jsonl(d / "s1.jsonl", [
        _pi_msg("2026-05-10T14:23:00.000Z", "user",
                [{"type": "text", "text": "hola"}]),
        _pi_msg("2026-05-10T14:23:05.000Z", "assistant"),
        _pi_msg("2026-05-10T14:24:00.000Z", "assistant",
                [{"type": "tool-call", "toolName": "bash"}],
                provider="openrouter"),
        _pi_msg("2026-05-10T14:25:00.000Z", "user",
                [{"type": "text", "text": "otra"}]),
        _pi_msg("2026-05-10T14:26:00.000Z", "assistant"),
    ])


class TestExtractPiSessions(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmp.name)

    def tearDown(self):
        self._tmp.cleanup()

    def test_un_archivo_es_una_sesion(self):
        # 2 archivos en el proyecto = 2 sesiones (aunque haya muchos mensajes)
        _pi_project(self.tmp, "charly-coffee")
        _write_jsonl(self.tmp / "charly-coffee" / "s2.jsonl", [
            _pi_msg("2026-05-11T09:00:00.000Z", "user"),
            _pi_msg("2026-05-11T09:01:00.000Z", "assistant"),
        ])
        sessions = ut.extract_pi_sessions(sessions_dir=self.tmp)
        self.assertEqual(len(sessions), 2)

    def test_provenance_tool_source(self):
        _pi_project(self.tmp, "charly-coffee")
        s = ut.extract_pi_sessions(sessions_dir=self.tmp)[0]
        self.assertEqual(s["tool"], "pi")
        self.assertEqual(s["source"], "pi_session_files")

    def test_turns_msgs_y_tools(self):
        _pi_project(self.tmp, "charly-coffee")
        s = ut.extract_pi_sessions(sessions_dir=self.tmp)[0]
        self.assertEqual(s["n_turns"], 3)          # 3 assistant messages
        self.assertEqual(s["duration_msgs"], 2)    # 2 user prompts
        self.assertEqual(s["n_tools"], 1)          # bash (distinto)
        self.assertEqual(s["project"], "charly-coffee")

    def test_timestamps_first_last(self):
        _pi_project(self.tmp, "charly-coffee")
        s = ut.extract_pi_sessions(sessions_dir=self.tmp)[0]
        self.assertEqual(s["first_ts"], "2026-05-10")
        self.assertEqual(s["first_ts_full"], "2026-05-10T14:23:00.000Z")
        self.assertEqual(s["last_ts_full"], "2026-05-10T14:26:00.000Z")

    def test_metricas_claude_only_son_none(self):
        # pi no tiene señal de skills/errores: None (no 0, no inventado) —
        # _session_stats los excluye de los totales. n_compactions ya no es
        # None: CORR-003 (coffe-64z) — la señal existe en los JSONL desde
        # ~may-2026, sin entradas compaction == 0 real.
        _pi_project(self.tmp, "charly-coffee")
        s = ut.extract_pi_sessions(sessions_dir=self.tmp)[0]
        self.assertIsNone(s["n_skills"])
        self.assertIsNone(s["n_errors"])
        self.assertEqual(0, s["n_compactions"])
        # sin señal de subagentes (semántica Agent es de Claude)
        self.assertFalse(s["has_agent"])

    def test_n_compactions_real_desde_jsonl(self):
        """CORR-003: entradas type==compaction del JSONL se cuentan
        (12 en el archivo miblioteca, verificado en el review R5)."""
        _pi_project(self.tmp, "charly-coffee")
        _write_jsonl(self.tmp / "charly-coffee" / "s2.jsonl", [
            _pi_msg("2026-05-11T09:00:00.000Z", "user"),
            {"type": "compaction", "timestamp": "2026-05-11T09:05:00.000Z",
             "tokensBefore": 90000, "fromHook": False},
            _pi_msg("2026-05-11T09:06:00.000Z", "assistant"),
            {"type": "compaction", "timestamp": "2026-05-11T09:10:00.000Z",
             "tokensBefore": 120000, "fromHook": False},
            _pi_msg("2026-05-11T09:11:00.000Z", "assistant"),
        ])
        sessions = ut.extract_pi_sessions(sessions_dir=self.tmp)
        s2 = [s for s in sessions
              if s["first_ts"] == "2026-05-11"][0]
        self.assertEqual(2, s2["n_compactions"])
        # la sesión sin compactions queda en 0 real (no None)
        s1 = [s for s in sessions if s["first_ts"] == "2026-05-10"][0]
        self.assertEqual(0, s1["n_compactions"])

    def test_fuera_de_scope_excluida(self):
        _pi_project(self.tmp, "charly-coffee")
        _pi_project(self.tmp, "acme-otro")
        sessions = ut.extract_pi_sessions(sessions_dir=self.tmp)
        self.assertEqual([s["project"] for s in sessions], ["charly-coffee"])

    def test_archivo_vacio_o_ilegible_sin_sesion(self):
        _pi_project(self.tmp, "charly-coffee")
        (self.tmp / "charly-coffee" / "vacio.jsonl").write_text("")
        (self.tmp / "charly-coffee" / "roto.jsonl").write_text("{no-json\n")
        sessions = ut.extract_pi_sessions(sessions_dir=self.tmp)
        self.assertEqual(len(sessions), 1)


class TestExtractAmpSessions(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmp.name)

    def tearDown(self):
        self._tmp.cleanup()

    def _task(self, name, files):
        for fname, (uri, ts) in files.items():
            p = self.tmp / name / fname
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(json.dumps({"uri": uri, "timestamp": ts}))

    def test_un_dir_es_una_sesion(self):
        self._task("t1", {
            "a.json": ("file:///home/sasha/para/areas/dev/gh/charly/coffee/x.py",
                       "2026-05-12T10:00:00Z"),
            "b.json": ("file:///home/sasha/para/areas/dev/gh/charly/coffee/y.py",
                       "2026-05-12T10:30:00Z"),
        })
        sessions = ut.extract_amp_sessions(amp_dir=self.tmp)
        self.assertEqual(len(sessions), 1)
        s = sessions[0]
        self.assertEqual(s["tool"], "amp")
        self.assertEqual(s["source"], "amp_file_changes")
        self.assertEqual(s["project"], "charly-coffee")
        self.assertEqual(s["n_turns"], 2)
        self.assertTrue(s["has_agent"])  # amp es autónomo por definición
        self.assertEqual(s["first_ts_full"], "2026-05-12T10:00:00Z")
        self.assertEqual(s["last_ts_full"], "2026-05-12T10:30:00Z")

    def test_dir_vacio_sin_sesion(self):
        (self.tmp / "vacio").mkdir()
        self.assertEqual(ut.extract_amp_sessions(amp_dir=self.tmp), [])

    def test_fuera_de_scope_excluida(self):
        self._task("t1", {
            "a.json": ("file:///tmp/acme/otro/x.py", "2026-05-12T10:00:00Z"),
        })
        self.assertEqual(ut.extract_amp_sessions(amp_dir=self.tmp), [])

    def test_timestamp_epoch_int_normalizado(self):
        """coffe-372: logs amp reales usan epoch ms INT — el ts crudo se
        normaliza a ISO (filter_sessions hace [:10] sobre el campo; un int
        crashea con TypeError con --since/--until)."""
        self._task("t1", {
            "a.json": ("file:///home/sasha/para/areas/dev/gh/charly/coffee/x.py",
                       1766492001723),
            "b.json": ("file:///home/sasha/para/areas/dev/gh/charly/coffee/y.py",
                       1766492585074),
        })
        sessions = ut.extract_amp_sessions(amp_dir=self.tmp)
        self.assertEqual(len(sessions), 1)
        s = sessions[0]
        self.assertIsInstance(s["first_ts_full"], str)
        self.assertIsInstance(s["last_ts_full"], str)
        # y la ventana ya no crashea (el gate que la destapó)
        kept = ut.filter_sessions(sessions, until=ut.parse_ts(
            "2026-09-23T00:00:00+00:00").date())
        self.assertEqual(len(kept), 1)


class TestSessionStatsByTool(unittest.TestCase):
    """by_tool: totals, with_agent y resets con señal por tool."""

    def _sessions(self):
        claude = {"tool": "claude-cli", "project": "charly-coffee",
                  "first_ts": "2026-05-10", "duration_msgs": 10,
                  "n_turns": 5, "n_tools": 2, "n_skills": 1,
                  "n_errors": 2, "n_compactions": 1, "has_agent": True}
        pi = {"tool": "pi", "source": "pi_session_files",
              "project": "charly-coffee", "first_ts": "2026-05-11",
              "first_ts_full": "2026-05-11T09:00:00.000Z",
              "last_ts_full": "2026-05-11T10:00:00.000Z",
              "duration_msgs": 2, "n_turns": 3, "n_tools": 1,
              "n_skills": None, "n_errors": None, "n_compactions": None,
              "has_agent": False}
        amp = {"tool": "amp", "source": "amp_file_changes",
               "project": "charly-coffee", "first_ts": "2026-05-12",
               "first_ts_full": "2026-05-12T10:00:00Z",
               "last_ts_full": "2026-05-12T11:00:00Z",
               "duration_msgs": 1, "n_turns": 2, "n_tools": 0,
               "n_skills": None, "n_errors": None, "n_compactions": None,
               "has_agent": True}
        return [claude, pi, amp]

    def test_by_tool_totals(self):
        stats = ut._session_stats(self._sessions())
        bt = stats["by_tool"]
        self.assertEqual(bt["claude-cli"]["total"], 1)
        self.assertEqual(bt["pi"]["total"], 1)
        self.assertEqual(bt["amp"]["total"], 1)
        self.assertEqual(bt["claude-cli"]["with_agent"], 1)
        self.assertEqual(bt["pi"]["with_agent"], 0)
        self.assertEqual(bt["amp"]["with_agent"], 1)
        self.assertEqual(stats["total_sessions"], 3)

    def test_api_errors_solo_claude(self):
        # pi/amp no tienen señal: None NO se cuenta como 0 en los totales
        stats = ut._session_stats(self._sessions())
        self.assertEqual(stats["total_api_errors"], 2)
        self.assertEqual(stats["total_compactions"], 1)

    def test_resets_por_tool(self):
        commands = Counter({"/clear": 7})
        stats = ut._session_stats(self._sessions(), commands=commands)
        r_claude = stats["by_tool"]["claude-cli"]["resets"]
        self.assertEqual(r_claude["count"], 7)
        self.assertIn("history.jsonl", r_claude["signal"])
        r_pi = stats["by_tool"]["pi"]["resets"]
        self.assertEqual(r_pi["count"], 1)
        self.assertIn("archivo", r_pi["signal"])  # 1 archivo = 1 /new
        r_amp = stats["by_tool"]["amp"]["resets"]
        self.assertEqual(r_amp["count"], 1)

    def test_agent_semantics_declarado(self):
        stats = ut._session_stats(self._sessions())
        self.assertIn("claude", stats["agent_semantics"].lower())
        self.assertIn("pi", stats["agent_semantics"].lower())

    def test_aggregate_emite_by_tool(self):
        rows = [ut.row(ts="2026-05-10T14:23:00+00:00", project="charly-coffee")]
        report = ut.aggregate(rows, self._sessions(),
                              Counter(), {}, Counter({"/clear": 7}))
        self.assertIn("by_tool", report["sessions"])
        self.assertIn("agent_semantics", report["sessions"])
        # sessions_monthly ahora incluye los meses de pi/amp
        self.assertIn("2026-05", report["sessions_monthly"])
        self.assertEqual(report["sessions_monthly"]["2026-05"]["total"], 3)


class TestVizSessionsScope(unittest.TestCase):
    """El dashboard declara el scope de /clear y el share por tool."""

    def _report(self):
        sessions = TestSessionStatsByTool()._sessions()
        rows = [ut.row(ts="2026-05-10T14:23:00+00:00", project="charly-coffee")]
        return ut.aggregate(rows, sessions, Counter(), {},
                            Counter({"/clear": 7}))

    def test_clear_per_100_denominador_claude(self):
        usage = viz.build_usage_sessions(self._report(), {})
        # 7 clears / 1 sesión claude (NO el total global de 3)
        self.assertAlmostEqual(usage["clear_per_100"], 700.0)
        # con ratio computable no hay razón de n/a; el scope lo declara el HTML
        self.assertIsNone(usage["clear_scope_reason"])

    def test_clear_per_100_na_sin_sesiones_claude(self):
        report = self._report()
        report["sessions"]["by_tool"] = {
            k: v for k, v in report["sessions"]["by_tool"].items()
            if k != "claude-cli"}
        usage = viz.build_usage_sessions(report, {})
        self.assertIsNone(usage["clear_per_100"])
        self.assertIn("claude", usage["clear_scope_reason"].lower())

    def test_resets_by_tool_al_dashboard(self):
        usage = viz.build_usage_sessions(self._report(), {})
        self.assertEqual(usage["resets_by_tool"]["pi"]["count"], 1)
        self.assertEqual(usage["resets_by_tool"]["claude-cli"]["count"], 7)

    def test_agent_share_por_tool(self):
        usage = viz.build_agent_share(self._report())
        bt = usage["by_tool_shares"]
        self.assertAlmostEqual(bt["claude-cli"]["share"], 1.0)
        self.assertAlmostEqual(bt["amp"]["share"], 1.0)
        self.assertAlmostEqual(bt["pi"]["share"], 0.0)
        # el share global mezcla semánticas → declarado
        self.assertIn("agent_semantics", usage)

    def test_sessions_html_declara_scope(self):
        usage = viz.build_usage_patterns(self._report(), {})
        html_txt = viz.sessions_html(usage)
        self.assertIn("claude", html_txt.lower())       # scope de /clear
        self.assertIn("Resets", html_txt)               # resets por tool


if __name__ == "__main__":
    unittest.main()

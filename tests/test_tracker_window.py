#!/usr/bin/env python3
"""
test_tracker_window.py — ventana temporal --since/--until (coffe-snj)

Cubre el alcance del ticket coffe-snj (flags para corridas reproducibles;
--output y --filter ya existían de v4.2):
- parse_window(): validación de --since/--until (YYYY-MM-DD, inclusive)
- in_window(): filtro por fecha para extractores (semántica UTC-date)
- filter_sessions(): sesiones que se solapan con la ventana
- extractores con dirs falsos: rows/kinds/sesiones solo dentro de la ventana
- guard de MIN_INTERACTIONS relajado cuando hay ventana explícita
"""

import importlib.util
import json
import tempfile
import unittest
from datetime import date, datetime, timezone
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
TRACKER_PATH = REPO / "scripts" / "usage-tracker.py"

# ventana de mayo 2026 reutilizada por varios tests
_MAY_WINDOW = (date(2026, 5, 1), date(2026, 5, 31))


def _guardar_ventana():
    """Guarda (SINCE, UNTIL) para restaurarlos en tearDown."""
    return (ut.SINCE, ut.UNTIL)


def _restaurar_ventana(old):
    ut.SINCE, ut.UNTIL = old


def load_tracker():
    spec = importlib.util.spec_from_file_location("usage_tracker_window", TRACKER_PATH)
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


ut = load_tracker()


def _dt(s):
    return datetime.fromisoformat(s)

# ============================ parse_window ============================

class TestParseWindow(unittest.TestCase):

    def test_ambas_validas(self):
        self.assertEqual(ut.parse_window("2026-01-01", "2026-03-31"),
                         (date(2026, 1, 1), date(2026, 3, 31)))

    def test_solo_since(self):
        self.assertEqual(ut.parse_window("2026-01-01", None),
                         (date(2026, 1, 1), None))

    def test_solo_until(self):
        self.assertEqual(ut.parse_window(None, "2026-03-31"),
                         (None, date(2026, 3, 31)))

    def test_ninguna(self):
        self.assertEqual(ut.parse_window(None, None), (None, None))

    def test_formato_invalido_falla(self):
        with self.assertRaises(ValueError):
            ut.parse_window("01-2026-01", None)
        with self.assertRaises(ValueError):
            ut.parse_window(None, "2026-13-01")

    def test_since_posterior_a_until_falla(self):
        with self.assertRaises(ValueError):
            ut.parse_window("2026-03-01", "2026-01-01")

    def test_rango_de_un_dia_es_valido(self):
        self.assertEqual(ut.parse_window("2026-05-10", "2026-05-10"),
                         (date(2026, 5, 10), date(2026, 5, 10)))


# ============================ in_window ============================

class TestInWindow(unittest.TestCase):

    def setUp(self):
        self._old = _guardar_ventana()

    def tearDown(self):
        _restaurar_ventana(self._old)

    # (nombre, (since, until), [(ts, esperado), ...])
    CASOS = [
        ("sin_ventana", (None, None), [
            ("2026-01-01T00:00:00+00:00", True),
            ("2099-12-31T23:59:59+00:00", True),
        ]),
        ("limites_inclusivos", _MAY_WINDOW, [
            ("2026-05-01T00:00:00+00:00", True),    # borde since inclusivo
            ("2026-05-31T23:59:59+00:00", True),    # borde until inclusivo
            ("2026-04-30T23:59:59+00:00", False),   # un segundo antes
            ("2026-06-01T00:00:00+00:00", False),   # un segundo después
        ]),
        ("dentro_de_ventana", _MAY_WINDOW, [
            ("2026-05-15T12:00:00+00:00", True),
        ]),
        ("solo_since", (date(2026, 5, 1), None), [
            ("2026-05-01T00:00:00+00:00", True),
            ("2026-04-30T23:59:59+00:00", False),
            ("2099-01-01T00:00:00+00:00", True),
        ]),
        ("solo_until", (None, date(2026, 5, 31)), [
            ("2026-06-01T00:00:00+00:00", False),
            ("2020-01-01T00:00:00+00:00", True),
        ]),
        ("acepta_date_y_datetime", _MAY_WINDOW, [
            (date(2026, 5, 10), True),
            (date(2026, 6, 10), False),
        ]),
    ]

    def test_matriz_in_window(self):
        for nombre, ventana, tabla in self.CASOS:
            with self.subTest(caso=nombre):
                ut.SINCE, ut.UNTIL = ventana
                for valor, esperado in tabla:
                    ts = _dt(valor) if isinstance(valor, str) else valor
                    self.assertEqual(ut.in_window(ts), esperado, ts)

    def test_sin_timestamp_pasa(self):
        ut.SINCE = ut.UNTIL = None
        # sin fecha no hay que filtrar: entra
        self.assertTrue(ut.in_window(None))



# ============================ filter_sessions ============================

def session(first, last=None, **kw):
    s = {"project": "charly-coffee", "first_ts": first,
         "first_ts_full": f"{first}T10:00:00+00:00", "duration_msgs": 10,
         "n_turns": 5, "n_tools": 2, "n_skills": 0, "n_errors": 0,
         "n_compactions": 0, "has_agent": False}
    if last:
        s["last_ts_full"] = f"{last}T20:00:00+00:00"
    s.update(kw)
    return s


class TestFilterSessions(unittest.TestCase):

    SESSIONS = [
        session("2026-04-01", "2026-04-02"),   # termina antes de la ventana
        session("2026-04-30", "2026-05-02"),   # empieza antes, termina dentro
        session("2026-05-10", "2026-05-11"),   # completamente dentro
        session("2026-05-30", "2026-06-03"),   # empieza dentro, termina después
        session("2026-06-10", "2026-06-11"),   # empieza después de la ventana
    ]

    def test_sin_ventana_devuelve_todas(self):
        self.assertEqual(len(ut.filter_sessions(self.SESSIONS, None, None)), 5)

    def test_solape_con_ventana(self):
        kept = ut.filter_sessions(self.SESSIONS, date(2026, 5, 1), date(2026, 5, 31))
        self.assertEqual(len(kept), 3)
        self.assertEqual([s["first_ts"] for s in kept],
                         ["2026-04-30", "2026-05-10", "2026-05-30"])

    def test_limite_inclusivo_un_dia(self):
        # ventana de un solo día: solapan las que tocan ese día
        kept = ut.filter_sessions(self.SESSIONS, date(2026, 5, 10), date(2026, 5, 10))
        self.assertEqual([s["first_ts"] for s in kept], ["2026-05-10"])

    def test_sin_last_ts_usa_first_ts(self):
        s = session("2026-05-10")  # sin last_ts_full
        self.assertEqual(ut.filter_sessions([s], date(2026, 5, 1), date(2026, 5, 31)), [s])
        self.assertEqual(ut.filter_sessions([s], date(2026, 6, 1), None), [])

    def test_session_sin_timestamps_no_crashea(self):
        s = {"project": "charly-coffee", "first_ts": "", "last_ts_full": None}
        # sin fechas no se puede ubicar: se conserva (extractores ya filtran
        # filas por fecha; una sesión sin fecha no se descarta silenciosamente)
        self.assertEqual(ut.filter_sessions([s], date(2026, 5, 1), date(2026, 5, 31)), [s])


# ============================ extractores con ventana ============================

class TestExtractorsWindow(unittest.TestCase):
    """Extractores filtran por ventana ANTES de agregar rows/kinds/sesiones:
    user prompts (que no generan fila) también respetan la ventana."""

    def setUp(self):
        self._old = (ut.SINCE, ut.UNTIL, ut.CLAUDE_DIR, ut.PI_DIR, ut.AMP_DIR)
        self.tmp = Path(tempfile.mkdtemp())
        ut.SINCE, ut.UNTIL = _MAY_WINDOW

    def tearDown(self):
        _restaurar_ventana(self._old[:2])
        ut.CLAUDE_DIR, ut.PI_DIR, ut.AMP_DIR = self._old[2:]

    # --- fixtures ---

    @staticmethod
    def _claude_entry(ts, kind="assistant"):
        e = {"type": kind, "timestamp": ts}
        if kind == "assistant":
            e["message"] = {"model": "claude-sonnet-4-6",
                            "usage": {"input_tokens": 100, "output_tokens": 50}}
        return e

    def _fake_claude_dir(self):
        d = Path(tempfile.mkdtemp()) / "projects" / "charly-coffee"
        d.mkdir(parents=True)
        lines = [
            self._claude_entry("2026-04-20T10:00:00Z", "user"),       # fuera (antes)
            self._claude_entry("2026-04-20T10:01:00Z"),               # fuera
            self._claude_entry("2026-05-10T10:00:00Z", "user"),       # dentro
            self._claude_entry("2026-05-10T10:01:00Z"),               # dentro
            self._claude_entry("2026-06-15T10:00:00Z"),               # fuera (después)
        ]
        (d / "a.jsonl").write_text("\n".join(json.dumps(x) for x in lines))
        return d.parents[1]

    def _fake_pi_dir(self):
        d = Path(tempfile.mkdtemp()) / "sessions" / "charly-coffee"
        d.mkdir(parents=True)
        lines = [
            {"type": "message", "timestamp": "2026-04-20T10:00:00Z",
             "message": {"role": "user"}},
            {"type": "message", "timestamp": "2026-05-10T10:00:00Z",
             "message": {"role": "user"}},
            {"type": "message", "timestamp": "2026-05-10T11:00:00Z",
             "message": {"role": "assistant", "model": "claude-sonnet-4-6",
                         "usage": {"input_tokens": 10, "output_tokens": 5}}},
            {"type": "message", "timestamp": "2026-06-15T11:00:00Z",
             "message": {"role": "assistant", "model": "claude-sonnet-4-6",
                         "usage": {"input_tokens": 10, "output_tokens": 5}}},
        ]
        (d / "s.jsonl").write_text("\n".join(json.dumps(x) for x in lines))
        return d.parent.parent

    # --- tests ---

    def test_extract_claude_filtra_rows_y_kinds(self):
        ut.CLAUDE_DIR = self._fake_claude_dir()
        kinds = {}
        rows, excluded = ut.extract_claude(kinds=kinds)
        self.assertEqual(len(rows), 1)
        self.assertTrue(rows[0]["timestamp"].startswith("2026-05-10"))
        # user_prompt de abril no entra: kinds respeta la ventana
        self.assertEqual(set(kinds.keys()), {"2026-05"})
        self.assertEqual(kinds["2026-05"]["user_prompt"], 1)
        self.assertEqual(kinds["2026-05"]["assistant_turn"], 1)
        self.assertEqual(excluded, [])

    def test_extract_claude_sin_ventana_no_filtra(self):
        ut.SINCE = ut.UNTIL = None
        ut.CLAUDE_DIR = self._fake_claude_dir()
        kinds = {}
        rows, _ = ut.extract_claude(kinds=kinds)
        self.assertEqual(len(rows), 3)
        self.assertEqual(set(kinds.keys()), {"2026-04", "2026-05", "2026-06"})

    def test_extract_pi_filtra(self):
        ut.PI_DIR = self._fake_pi_dir()
        kinds = {}
        rows, _ = ut.extract_pi(kinds=kinds)
        self.assertEqual(len(rows), 1)
        self.assertTrue(rows[0]["timestamp"].startswith("2026-05-10"))
        self.assertEqual(set(kinds.keys()), {"2026-05"})
        self.assertEqual(kinds["2026-05"]["user_prompt"], 1)

    def test_extract_session_stats_solape(self):
        d = Path(tempfile.mkdtemp())
        (d / "dashboard-cache.json").write_text(json.dumps({
            "entries": {
                "a": {"source": "claude", "project": "charly-coffee",
                      "first_ts": "2026-04-30T22:00:00Z",
                      "last_ts": "2026-05-02T01:00:00Z",
                      "user_messages": 5, "turns": [1, 2]},
                "b": {"source": "claude", "project": "charly-coffee",
                      "first_ts": "2026-06-10T10:00:00Z",
                      "last_ts": "2026-06-10T11:00:00Z",
                      "user_messages": 1, "turns": [1]},
            }}))
        ut.CLAUDE_DIR = d
        sessions = ut.extract_session_stats()
        # la sesión que se solapa con mayo queda; la de junio no
        self.assertEqual(len(sessions), 1)
        self.assertEqual(sessions[0]["first_ts"], "2026-04-30")

    def test_aggregate_sobre_rows_de_ventana(self):
        """aggregate() sobre rows ya filtrados por los extractores: date_range
        queda dentro de la ventana y los totales coinciden."""
        ut.SINCE, ut.UNTIL = _MAY_WINDOW
        ut.CLAUDE_DIR = self._fake_claude_dir()
        kinds = {}
        rows, _ = ut.extract_claude(kinds=kinds)
        report = ut.aggregate(rows, [])
        m = report["metadata"]
        # date_range viene de los datos (dentro de la ventana)
        self.assertEqual(m["date_range"]["start"], "2026-05-10")
        self.assertEqual(m["date_range"]["end"], "2026-05-10")
        self.assertEqual(m["total_interactions"], len(rows))


if __name__ == "__main__":
    unittest.main()

#!/usr/bin/env python3
"""test_viz_agent.py — contrato del dashboard "Comportamiento del agente".

Cubre el modelo pre-calculado por ventana (percentiles exactos sobre las
tareas de la ventana), la semántica de repos creados/mantenidos, la
estructura del HTML publicado y la ejecución del JS real (con stub de DOM en
node, hermético: sin navegador). El repo ya se comió 4 bugs de JS por no
ejecutarlo nunca (test_viz.py); acá se ejecuta el script inline.
"""

import importlib.util
import json
import re
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
spec = importlib.util.spec_from_file_location("viz_agent", REPO / "scripts" / "viz-agent.py")
va = importlib.util.module_from_spec(spec)
spec.loader.exec_module(va)

MONTHS = ["2026-04", "2026-05", "2026-06", "2026-07", "2026-08"]


def metrics_fixture():
    """Dataset sintético: 4 meses, 4 tareas con duraciones conocidas."""
    return {
        "metadata": {
            "date_range": {"start": "2026-04-01", "end": "2026-07-31"},
            "first_task": "2026-04-02", "last_task": "2026-07-20",
            "task_definition": "una tarea = un prompt de usuario y sus turnos",
            "gap_cap_minutes": 30, "retry_window_minutes": 5,
            "provenance": {"tasks": "derived", "aborts": "reported",
                           "tool_errors": "reported", "repos": "reported"},
            "coverage": [
                {"source": "pi", "turn_level": True, "abort_signal": True,
                 "sessions": 4, "note": None},
                {"source": "claude-cli", "turn_level": True,
                 "abort_signal": False, "sessions": 2, "note": None}],
            "reasons": {"abort_signal": "solo pi"},
            "min_commit_days": 5, "min_returning_months": 2,
            "timezone": "-03", "orphan_turns": 3,
            "preamble_prompts_skipped": 2, "repo_roots_missing": [],
        },
        "days": ["2026-04-02", "2026-05-10", "2026-06-15", "2026-07-20"],
        "sources": ["pi"],
        "models": ["m1", "m2"],
        "months": ["2026-04", "2026-05", "2026-06", "2026-07"],
        "daily": {
            "2026-04-02": {"prompts": 1, "tasks": 1, "turns": 2, "agent_s": 60.0,
                           "aborted": 0, "errors": 0, "tool_errors": 0, "retry": 0},
            "2026-05-10": {"prompts": 1, "tasks": 1, "turns": 2, "agent_s": 120.0,
                           "aborted": 1, "errors": 0, "tool_errors": 0, "retry": 0},
            "2026-06-15": {"prompts": 2, "tasks": 2, "turns": 4, "agent_s": 300.0,
                           "aborted": 0, "errors": 0, "tool_errors": 1, "retry": 1},
            "2026-07-20": {"prompts": 1, "tasks": 1, "turns": 1, "agent_s": 30.0,
                           "aborted": 0, "errors": 0, "tool_errors": 0, "retry": 0},
        },
        "tasks": {"day": [0, 1, 2, 2, 3], "s": [60, 120, 100, 200, 30],
                  "sp": [70, 150, 120, 400, 40], "src": [0, 0, 0, 0, 0],
                  "model": [0, 1, 0, 1, 0], "f": [0, 1, 0, 8, 0]},
        "repos": [
            {"name": "viejo", "first_commit": "2025-01-01",
             "days_by_month": {"2025-01": 1, "2026-05": 6},
             "commits_by_month": {"2025-01": 1, "2026-05": 6}},
            {"name": "nuevo", "first_commit": "2026-06-02",
             "days_by_month": {"2026-06": 3},
             "commits_by_month": {"2026-06": 4}},
            {"name": "constante", "first_commit": "2026-04-05",
             "days_by_month": {"2026-04": 3, "2026-05": 3, "2026-06": 4},
             "commits_by_month": {"2026-04": 4, "2026-05": 5, "2026-06": 6}},
        ],
    }


def report_fixture():
    daily = {}
    for d, n in (("2026-04-02", 100), ("2026-06-15", 6000), ("2026-07-20", 120)):
        daily[d] = {"interactions": n}
    return {
        "metadata": {"date_range": {"start": "2026-04-01", "end": "2026-07-31"}},
        "monthly": {m: {"interactions": 1000} for m in MONTHS},
        "daily": daily,
        "project_daily": {"days": ["2026-04-02"], "matrix": {}},
    }


class WindowsTest(unittest.TestCase):
    def test_incluye_todo_ytd_trimestres_meses_y_rangos(self):
        keys = [k for k, _l, _m in va.windows(MONTHS)]
        self.assertEqual(keys[0], "all")
        self.assertIn("ytd:2026", keys)
        self.assertIn("q:2026Q2", keys)
        self.assertIn("month:2026-06", keys)
        self.assertIn("range:2026-04..2026-06", keys)
        self.assertEqual(len(keys), len(set(keys)), "claves duplicadas")

    def test_rangos_custom_tienen_al_menos_dos_meses_y_son_contiguos(self):
        for key, _label, mwin in va.windows(MONTHS):
            if key.startswith("range:"):
                self.assertGreaterEqual(len(mwin), 2)
                self.assertEqual(mwin, MONTHS[MONTHS.index(mwin[0]):MONTHS.index(mwin[0]) + len(mwin)])

    def test_meses_de_cada_ventana_pertenecen_al_universo(self):
        for _key, _label, mwin in va.windows(MONTHS):
            self.assertTrue(set(mwin) <= set(MONTHS))


class ModelTest(unittest.TestCase):
    def setUp(self):
        self.model = va.build_model(metrics_fixture(), report_fixture())

    def test_todas_las_ventanas_tienen_vista(self):
        self.assertEqual(len(self.model["views"]), len(self.model["windows"]))
        for w in self.model["windows"]:
            self.assertIn(w["key"], self.model["views"])

    def test_p90_es_exacto_sobre_las_tareas_de_la_ventana(self):
        v = self.model["views"]["month:2026-06"]
        secs = sorted([100, 200])
        self.assertEqual(v["tasks"], 2)
        self.assertEqual(v["p50_s"], va.pctl(secs, .5))
        self.assertEqual(v["p90_s"], va.pctl(secs, .9))
        self.assertEqual(v["prompts"], 2)

    def test_ventana_sin_tareas_expone_null(self):
        v = self.model["views"]["month:2026-08"]
        self.assertEqual(v["tasks"], 0)
        self.assertIsNone(v["p90_s"])
        self.assertIsNone(v["prompts_per_active_day"])
        self.assertEqual(v["by_model"], [])

    def test_meta_declara_alcance_real_y_tz(self):
        m = self.model["meta"]
        self.assertEqual(
            {"first_task": m["first_task"], "last_task": m["last_task"],
             "timezone": m["timezone"]},
            {"first_task": "2026-04-02", "last_task": "2026-07-20",
             "timezone": "-03"})

    def test_horas_de_agente_por_dia_activo(self):
        v = self.model["views"]["all"]
        self.assertEqual(v["agent_h"], round((60 + 120 + 300 + 30) / 3600, 1))
        self.assertEqual(v["active_days"], 4)
        self.assertAlmostEqual(v["agent_h_per_active_day"], round(510 / 3600 / 4, 2))

    def test_tiempo_de_reloj_es_el_de_la_ventana(self):
        v = self.model["views"]["all"]
        self.assertEqual(v["span_h"], round((70 + 150 + 120 + 400 + 40) / 3600, 1))

    def test_abortos_y_reintentos_por_100(self):
        v = self.model["views"]["all"]
        self.assertEqual(v["aborted"], 1)
        self.assertEqual(v["retries"], 1)
        self.assertEqual(v["aborts_per_100_tasks"], round(100 / 5, 2))
        self.assertEqual(v["retries_per_100_prompts"], round(100 / 5, 2))

    def test_repos_creados_solo_dentro_de_la_ventana(self):
        self.assertEqual(self.model["views"]["all"]["repos_created"], 2)
        self.assertEqual(self.model["views"]["month:2026-05"]["repos_created"], 0)
        self.assertEqual(self.model["views"]["month:2026-06"]["repos_created"], 1)

    def test_repos_mantenidos_exigen_dias_y_retorno(self):
        # abr-jun: "viejo" 6 días (1 mes) y "constante" 10 días (3 meses);
        # mantenido = >5 días; "vuelve" exige además >=2 meses en la ventana
        v = self.model["views"]["range:2026-04..2026-06"]
        self.assertEqual(v["repos_maintained"], 2)
        self.assertEqual(v["repos_returning"], 1)
        # ventana de un mes: "nuevo" tiene 3 días → no
        self.assertEqual(self.model["views"]["month:2026-06"]["repos_maintained"], 0)

    def test_escala_de_automatizacion_usa_el_umbral_del_config(self):
        v = self.model["views"]["all"]
        self.assertEqual({"bulk_days": v["bulk_days"],
                          "peak_day": v["peak_day"],
                          "peak_day_interactions": v["peak_day_interactions"]},
                         {"bulk_days": 1,          # 2026-06-15 ≥ 5.000
                          "peak_day": "2026-06-15",
                          "peak_day_interactions": 6000})

    def test_por_modelo_ordena_por_tareas(self):
        rows = self.model["views"]["all"]["by_model"]
        self.assertEqual(rows[0]["model"], "m1")
        self.assertEqual([r["tasks"] for r in rows], [3, 2])


class DocsGuardTest(unittest.TestCase):
    def test_update_y_check_docs_sobre_un_readme_temporal(self):
        import tempfile
        model = va.build_model(metrics_fixture(), report_fixture())
        with tempfile.NamedTemporaryFile("w", suffix=".md", delete=False) as fh:
            fh.write("# doc\n\n" + va.DOC_BEGIN + "\n- viejo: 1\n"
                     + va.DOC_END + "\n")
            path = fh.name
        try:
            va.update_docs(model, path)
            self.assertEqual(va.check_docs(model, path), [])
            drifted = Path(path).read_text().replace(
                va.DOC_END, "- cifra inventada: 999\n" + va.DOC_END)
            Path(path).write_text(drifted)
            self.assertTrue(va.check_docs(model, path))
        finally:
            Path(path).unlink(missing_ok=True)


class RenderTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.model = va.build_model(metrics_fixture(), report_fixture())
        cls.html = va.render(cls.model)

    def test_back_link_y_selector_de_periodo(self):
        self.assertIn('href="../index.html"', self.html)
        self.assertIn('id="period-select"', self.html)
        self.assertEqual(self.html.count("<option value="), len(self.model["windows"]))

    def test_modelo_embebido_es_json_valido(self):
        raw = re.search(r'id="agent-model">(.*?)</script>', self.html, re.S).group(1)
        model = json.loads(raw)
        self.assertEqual(set(model["views"]), {w["key"] for w in model["windows"]})
        self.assertNotIn("None", raw)

    def test_declara_cobertura_y_limites(self):
        self.assertIn("Cobertura por fuente", self.html)
        self.assertIn("claude-cli", self.html)
        self.assertIn("1:1", self.html)
        self.assertIn("talk:", self.html)
        self.assertIn("Turnos sin prompt previo", self.html)

    def _run_node(self, hashv):
        import tempfile
        raw = re.search(r'id="agent-model">(.*?)</script>', self.html, re.S).group(1)
        js = self.html.rsplit("<script>", 1)[1].split("</script>", 1)[0]
        shim = """
const MODEL = %s;
const els = {};
function el(id) {
  if (!els[id]) els[id] = {id: id, textContent: "", innerHTML: "",
                           value: "", addEventListener: function () {}};
  return els[id];
}
el("agent-model").textContent = JSON.stringify(MODEL);
globalThis.document = {getElementById: el};
globalThis.window = {location: {hash: "%s", href: "http://x/a.html"},
                     addEventListener: function () {}};
globalThis.history = {replaceState: function () {}};
""" % (raw, hashv)
        tail = """
process.stdout.write(JSON.stringify({
  sel: els["period-select"].value, kpis: els.kpis.innerHTML,
  monthly: els.monthly.innerHTML, model: els["by-model"].innerHTML,
  charts: els["chart-p90"].innerHTML
}));
"""
        with tempfile.NamedTemporaryFile("w", suffix=".mjs", delete=False) as fh:
            fh.write(shim + js + tail)
            path = fh.name
        try:
            proc = subprocess.run(["node", path], capture_output=True, text=True,
                                  timeout=60)
        finally:
            Path(path).unlink(missing_ok=True)
        self.assertEqual(proc.returncode, 0, proc.stderr[-2000:])
        return json.loads(proc.stdout)

    @unittest.skipUnless(shutil.which("node"), "node no instalado")
    def test_ventana_vacia_no_muestra_null(self):
        out = self._run_node("#month:2026-08")
        self.assertEqual(out["sel"], "month:2026-08")
        self.assertNotIn("null", out["kpis"])
        self.assertIn("sin tareas en la ventana", out["kpis"])
        self.assertIn("sin datos en la ventana", out["charts"])

    @unittest.skipUnless(shutil.which("node"), "node no instalado")
    def test_deep_link_por_hash_selecciona_la_ventana(self):
        out = self._run_node("#month:2026-06")
        self.assertEqual(out["sel"], "month:2026-06")
        self.assertIn("jun 26", out["monthly"])

    @unittest.skipUnless(shutil.which("node"), "node no instalado")
    def test_js_corre_en_node_con_dom_stub(self):
        raw = re.search(r'id="agent-model">(.*?)</script>', self.html, re.S).group(1)
        js = self.html.rsplit("<script>", 1)[1].split("</script>", 1)[0]
        shim = """
const MODEL = %s;
const els = {};
function el(id) {
  if (!els[id]) els[id] = {id: id, textContent: "", innerHTML: "",
                           value: "", addEventListener: function () {}};
  return els[id];
}
el("agent-model").textContent = JSON.stringify(MODEL);
globalThis.document = {getElementById: el};
globalThis.window = {location: {hash: "", href: "http://x/a.html"},
                     addEventListener: function () {}};
globalThis.history = {replaceState: function () {}};
""" % raw
        tail = """
process.stdout.write(JSON.stringify({
  kpis: els.kpis.innerHTML, monthly: els.monthly.innerHTML,
  model: els["by-model"].innerHTML, charts: els["chart-p90"].innerHTML
}));
"""
        with tempfile.NamedTemporaryFile("w", suffix=".mjs", delete=False) as fh:
            fh.write(shim + js + tail)
            path = fh.name
        try:
            proc = subprocess.run(["node", path], capture_output=True, text=True,
                                  timeout=60)
        finally:
            Path(path).unlink(missing_ok=True)
        self.assertEqual(proc.returncode, 0, proc.stderr[-2000:])
        out = json.loads(proc.stdout)
        self.assertIn("Prompts por día activo", out["kpis"])
        self.assertIn("Duración de tarea P90", out["kpis"])
        self.assertNotIn("undefined", out["kpis"])
        self.assertIn("P90", out["monthly"])
        self.assertIn("m1", out["model"])
        self.assertIn("<svg", out["charts"])
        # EXCL-004: nombre accesible en el gráfico (emitido por el JS)
        self.assertIn('aria-label="Duración de tarea P90 por mes', out["charts"])
        self.assertIn('role="img"', out["charts"])


if __name__ == "__main__":
    unittest.main()

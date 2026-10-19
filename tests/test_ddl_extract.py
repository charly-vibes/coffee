#!/usr/bin/env python3
"""Tests de la capa de extracción de scripts/ddl-effect-tracker.py (coffe-q9z).

La I/O es inyectable (run/runner fn, tmpdir): los reducers siguen siendo
puros y testeados en test_ddl_effect.py; acá se cubre el wiring que los
alimenta (walk pi, git, gh api, ddl status, terapia, ah, ensamblado).
"""
import importlib.util
import json
import sys
import tempfile
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
_spec = importlib.util.spec_from_file_location(
    "ddl_effect_tracker", REPO / "scripts" / "ddl-effect-tracker.py"
)
ddl = importlib.util.module_from_spec(_spec)
sys.modules["ddl_effect_tracker"] = ddl
_spec.loader.exec_module(ddl)


def _write_session(tmp, cwd, messages, fname="s1.jsonl"):
    d = tmp / cwd.replace("/", "-")
    d.mkdir(parents=True, exist_ok=True)
    lines = [json.dumps({"type": "session", "version": 3, "id": "x",
                         "timestamp": "2026-03-01T10:00:00.000Z",
                         "cwd": cwd})]
    lines += [json.dumps(m) for m in messages]
    p = d / fname
    p.write_text("\n".join(lines) + "\n")
    return p


class CwdToRepoTest(unittest.TestCase):
    def test_convencion_path_y_flota(self):
        self.assertEqual(ddl.cwd_to_repo(
            "/var/home/sasha/para/areas/dev/gh/charly/coffee"), "coffee")
        self.assertEqual(ddl.cwd_to_repo(
            "/var/home/sasha/para/areas/dev/gh/charly/dont"), "dont")

    def test_defaults_de_maquina(self):
        # repos cuyo dir local no coincide con el canónico
        self.assertEqual(ddl.cwd_to_repo(
            "/var/home/sasha/para/areas/dev/gh/charly/espectacular"), "ah")
        self.assertEqual(ddl.cwd_to_repo(
            "/var/home/sasha/para/areas/dev/gh/charly/whisper"), "turu")
        self.assertEqual(ddl.cwd_to_repo(
            "/var/home/sasha/para/areas/dev/gh/charly/coffe"), "coffee")

    def test_alias_explicito_exacto_sin_fuzzy(self):
        m = ddl.build_alias_map({"wai": ["charly/wai-git-epic"]})
        self.assertEqual(ddl.cwd_to_repo(
            "/var/home/sasha/para/areas/dev/gh/charly/wai-git-epic",
            alias_map=m), "wai")
        # sin alias, el worktree no matchea nada (sin fuzzy)
        self.assertIsNone(ddl.cwd_to_repo(
            "/var/home/sasha/para/areas/dev/gh/charly/wai-git-epic"))

    def test_no_flota_y_cwd_ajeno(self):
        self.assertIsNone(ddl.cwd_to_repo(
            "/var/home/sasha/para/areas/dev/gh/charly/microdancing"))
        self.assertIsNone(ddl.cwd_to_repo("/tmp"))
        self.assertIsNone(ddl.cwd_to_repo(None))


class ParsePiSessionTest(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())

    def test_cwd_eventos_y_usos_gestionados(self):
        msgs = [
            # llamada bash con bd: uso gestionado (parse de comando)
            {"type": "message", "timestamp": "2026-03-01T10:00:01.000Z",
             "message": {"role": "assistant", "content": [
                 {"type": "toolCall", "id": "c1", "name": "bash",
                  "arguments": {"command": "cd /x && bd list"}}]}},
            # resultado del bash: error de tool
            {"type": "message", "timestamp": "2026-03-01T10:00:02.000Z",
             "message": {"role": "toolResult", "toolCallId": "c1",
                         "toolName": "bash", "isError": True,
                         "content": []}},
            # retry exitoso
            {"type": "message", "timestamp": "2026-03-01T10:00:03.000Z",
             "message": {"role": "toolResult", "toolCallId": "c2",
                         "toolName": "bash", "isError": False,
                         "content": []}},
            {"type": "message", "timestamp": "2026-03-01T10:00:04.000Z",
             "message": {"role": "assistant", "content": [
                 {"type": "text", "text": "listo"}]}},
        ]
        p = _write_session(self.tmp,
                           "/var/home/sasha/para/areas/dev/gh/charly/coffee",
                           msgs)
        s = ddl.parse_pi_session(p)
        self.assertEqual(s["cwd"],
                         "/var/home/sasha/para/areas/dev/gh/charly/coffee")
        kinds = [(e[0], e[1], e[2]) for e in s["events"]]
        self.assertEqual(kinds, [("assistant", None, None),
                                 ("result", "bash", False),
                                 ("result", "bash", True),
                                 ("assistant", None, None)])
        self.assertEqual(s["uses"], {"bd": "2026-03-01T10:00:01.000Z"})

    def test_file_sin_header_o_vacio(self):
        p = self.tmp / "empty.jsonl"
        p.write_text("{}\n")  # sin type session ni mensajes
        self.assertIsNone(ddl.parse_pi_session(p))


class WalkAndBucketTest(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())

    def _mk(self, cwd, msgs, fname="s.jsonl"):
        return _write_session(self.tmp, cwd, msgs, fname)

    def test_uses_rows_primer_uso(self):
        a = [{"type": "message", "timestamp": "2026-03-05T10:00:00.000Z",
              "message": {"role": "assistant", "content": [
                  {"type": "toolCall", "id": "c", "name": "bash",
                   "arguments": {"command": "wai status"}}]}}]
        b = [{"type": "message", "timestamp": "2026-03-01T10:00:00.000Z",
              "message": {"role": "assistant", "content": [
                  {"type": "toolCall", "id": "c", "name": "bash",
                   "arguments": {"command": "wai sync"}}]}}]
        self._mk("/var/home/sasha/para/areas/dev/gh/charly/coffee", a, "a.jsonl")
        self._mk("/var/home/sasha/para/areas/dev/gh/charly/coffee", b, "b.jsonl")
        self._mk("/var/home/sasha/para/areas/dev/gh/charly/microdancing",
                 a, "c.jsonl")
        sessions = ddl.walk_pi_sessions(self.tmp)
        self.assertEqual(len(sessions), 2)  # microdancing fuera de flota
        rows = ddl.uses_rows(sessions)
        self.assertEqual(rows, [{"repo": "coffee", "tool": "wai",
                                 "date": "2026-03-01T10:00:00.000Z"}])

    def test_events_by_repo_month_slice_por_mes(self):
        msgs = [
            {"type": "message", "timestamp": "2026-03-30T23:00:00.000Z",
             "message": {"role": "toolResult", "toolCallId": "c",
                         "toolName": "bash", "isError": True, "content": []}},
            {"type": "message", "timestamp": "2026-04-01T10:00:00.000Z",
             "message": {"role": "toolResult", "toolCallId": "d",
                         "toolName": "bash", "isError": False, "content": []}},
        ]
        self._mk("/var/home/sasha/para/areas/dev/gh/charly/coffee", msgs)
        sessions = ddl.walk_pi_sessions(self.tmp)
        buckets = ddl.events_by_repo_month(sessions)
        self.assertEqual(set(buckets), {("coffee", "2026-03"),
                                        ("coffee", "2026-04")})
        self.assertEqual(buckets[("coffee", "2026-03")],
                         [("result", "bash", False)])
        self.assertEqual(buckets[("coffee", "2026-04")],
                         [("result", "bash", True)])


class MonthlyDoseTest(unittest.TestCase):
    def test_fraccion_derivada_de_first_use(self):
        first_use = {"coffee": {"wai": "2026-03-01", "turu": "2026-05-01"}}
        self.assertEqual(
            ddl.monthly_dose("coffee", "2026-03", first_use)["dose"], 1 / 13)
        self.assertEqual(
            ddl.monthly_dose("coffee", "2026-05", first_use)["dose"], 2 / 13)
        self.assertEqual(
            ddl.monthly_dose("coffee", "2026-01", first_use)["dose"], 0.0)
        self.assertEqual(
            ddl.monthly_dose("coffee", "2026-05", first_use)["provenance"],
            "derived")


class GitExtractionTest(unittest.TestCase):
    def test_git_commits_parse_bloques(self):
        out = ("H1|2026-03-01T10:00:00-03:00|fix: algo\n"
               ".ddl/config.json\nREADME.md\n"
               "\x1e"
               "H2|2026-03-02T10:00:00-03:00|revert \"fix: algo\"\n"
               "notes.md\n\x1e")
        commits = ddl.git_commits("/fake/repo", run=lambda cmd, cwd: out)
        self.assertEqual(len(commits), 2)
        self.assertEqual(commits[0]["date"], "2026-03-01T10:00:00-03:00")
        self.assertEqual(commits[0]["files"], [".ddl/config.json", "README.md"])
        self.assertEqual(commits[1]["message"], 'revert "fix: algo"')

    def test_git_commits_repo_ausente(self):
        def boom(cmd, cwd):
            raise FileNotFoundError("no git")
        self.assertIsNone(ddl.git_commits("/no/existe", run=boom))

    def test_git_releases_tags(self):
        out = "2026-03-01T00:00:00-03:00\n2026-04-01T00:00:00-03:00\n"
        self.assertEqual(len(ddl.git_releases("/fake", run=lambda c, cwd: out)), 2)
        self.assertEqual(ddl.git_releases("/fake", run=lambda c, cwd: ""), [])


class GhExtractionTest(unittest.TestCase):
    def test_gh_runs_fetch_paginado(self):
        page_full = json.dumps({"workflow_runs": [
            {"id": i, "status": "completed", "conclusion": "success",
             "created_at": "2026-03-01T00:00:00Z", "head_branch": "main"}
            for i in range(100)]})
        page_last = json.dumps({"workflow_runs": [
            {"id": 999, "status": "completed", "conclusion": "failure",
             "created_at": "2026-03-02T00:00:00Z", "head_branch": "main"}]})
        pages = [page_full, page_last]

        def run(cmd):
            return pages.pop(0)

        runs = ddl.gh_runs_fetch("charly-vibes/coffee", run=run)
        self.assertEqual(len(runs), 101)

    def test_gh_runs_fetch_red_muerta(self):
        def boom(cmd, cwd):
            raise RuntimeError("sin red")
        self.assertIsNone(ddl.gh_runs_fetch("charly-vibes/coffee", run=boom))


class DdlStatusTest(unittest.TestCase):
    def test_parse_envelope(self):
        envelope = json.dumps({"ok": True, "data": {"sections": [
            {"items": [
                {"label": "wai", "level": "healthy", "value": "v1"},
                {"label": "funkalization", "level": "error",
                 "value": "not installed"}]}]}})
        st = ddl.ddl_status_global(run=lambda cmd: envelope)
        self.assertIn("wai", st["healthy"])
        self.assertIn("wai", st["present"])
        # 'not installed' no es presente (dosis reported lo excluye)
        self.assertNotIn("funkalization", st["present"])
        self.assertNotIn("funkalization", st["healthy"])

    def test_binario_ausente(self):
        def boom(cmd):
            raise FileNotFoundError()
        self.assertIsNone(ddl.ddl_status_global(run=boom))


class TerapiaCacheTest(unittest.TestCase):
    def test_fresco_y_stale(self):
        with tempfile.TemporaryDirectory() as d:
            cache = Path(d) / "terapia_errors.json"
            calls = []

            def run(cmd):
                calls.append(cmd)
                return json.dumps({"exit_nonzero": 7, "edit_mismatch": 2})

            now = "2026-04-10T00:00:00Z"
            out = ddl.terapia_errors_global(cache, ttl_days=7, now=now, run=run)
            self.assertEqual(out["data"], {"exit_nonzero": 7,
                                           "edit_mismatch": 2})
            self.assertEqual(out["provenance"], "reported")
            self.assertEqual(len(calls), 1)
            # dentro del TTL: sin red/run
            out2 = ddl.terapia_errors_global(cache, ttl_days=7, now=now,
                                             run=run)
            self.assertEqual(out2["provenance"], "cached")
            self.assertEqual(len(calls), 1)
            # stale → re-corrida
            out3 = ddl.terapia_errors_global(
                cache, ttl_days=7, now="2026-05-01T00:00:00Z", run=run)
            self.assertEqual(out3["provenance"], "reported")
            self.assertEqual(len(calls), 2)

    def test_terapia_ausente(self):
        def boom(cmd):
            raise RuntimeError()
        out = ddl.terapia_errors_global(Path("/tmp/nunca-t.json"), ttl_days=7,
                                        now="2026-04-10T00:00:00Z", run=boom)
        self.assertIsNone(out["data"])
        self.assertEqual(out["provenance"], "missing")


class AhFindingsTest(unittest.TestCase):
    def test_cache_y_sin_setup(self):
        with tempfile.TemporaryDirectory() as d:
            cache = Path(d) / "ah_findings.json"
            calls = []

            def runner(repo):
                calls.append(repo)
                if repo == "coffee":
                    return (0, json.dumps({"ok": True, "data": {
                        "findings": [{"kind": "x"}, {"kind": "y"}]}}))
                # repo sin setup espectacular: ah no devuelve findings
                return (1, json.dumps({"ok": False}))

            now = "2026-04-10T00:00:00Z"
            out = ddl.ah_findings_cached(["coffee", "dont"], cache,
                                         ttl_days=7, now=now, runner=runner)
            self.assertEqual(out["coffee"]["findings"], 2)
            self.assertEqual(out["coffee"]["provenance"], "reported")
            self.assertIsNone(out["dont"]["findings"])
            self.assertEqual(out["dont"]["provenance"], "missing")
            # segunda corrida dentro del TTL: sin llamadas
            out2 = ddl.ah_findings_cached(["coffee", "dont"], cache,
                                          ttl_days=7, now=now, runner=runner)
            self.assertEqual(out2["coffee"]["provenance"], "cached")
            self.assertEqual(len(calls), 2)


class AssembleReportTest(unittest.TestCase):
    """Ensamblado end-to-end con insumos sintéticos (sin I/O)."""

    def _insumos(self):
        git_coffee = [
            {"repo": "coffee", "date": "2026-03-05T10:00:00Z",
             "message": "ddl init", "files": [".ddl/config.json"]},
            {"repo": "coffee", "date": "2026-03-10T10:00:00Z",
             "message": "fix: bug", "files": ["src/x.py"]},
            {"repo": "coffee", "date": "2026-03-11T10:00:00Z",
             "message": "test: caso", "files": ["tests/test_x.py"]},
        ]
        sessions = [
            # formato de walk_pi_sessions: events con ts, uses de gestionados
            {"repo": "coffee", "uses": {"wai": "2026-03-05T09:00:00Z"},
             "events": [("assistant", None, None, "2026-03-20T10:00:00Z"),
                        ("result", "bash", False, "2026-03-20T10:00:01Z"),
                        ("result", "bash", True, "2026-03-20T10:00:02Z")]},
            {"repo": "dont", "uses": {}, "events": []},
        ]
        return {
            "usage_report": {
                "project_monthly": {
                    "charly-coffee": {"2026-03": {"interactions": 100}},
                    "charly-dont": {"2026-03": {"interactions": 50}},
                }},
            "sessions": sessions,
            "git": {"coffee": {"commits": git_coffee, "releases": [],
                               "has_ddl_now": True}},
            "gh": {"coffee": {"runs": [
                {"id": 1, "status": "completed", "conclusion": "success",
                 "created_at": "2026-03-01T00:00:00Z", "head_branch": "main"},
                {"id": 2, "status": "completed", "conclusion": "failure",
                 "created_at": "2026-03-02T00:00:00Z", "head_branch": "main"}],
                "provenance": "cached"}},
            "topics": {"coffee": {"topic_ddl": True, "provenance": "cached"},
                       "dont": {"topic_ddl": False, "provenance": "cached"}},
            "ddl_status": {"present": list(ddl.MANAGED_TOOLS),
                           "healthy": list(ddl.MANAGED_TOOLS)},
            "terapia": None,
            "ah": {"coffee": {"findings": 2, "provenance": "cached",
                              "fetched_until": "2026-03-25T00:00:00Z"}},
            "now": "2026-03-25T12:00:00Z",
        }

    def test_panel_repos_y_analysis(self):
        rep = ddl.assemble_report(self._insumos())
        errs = ddl.validate_report(rep)
        self.assertEqual(errs, [], "; ".join(errs))

        # panel
        rows = {(r["repo"], r["month"]): r for r in rep["monthly"]}
        coffee = rows[("coffee", "2026-03")]
        self.assertEqual(coffee["interactions"], 100)
        self.assertEqual(coffee["src"]["interactions"], "reported")
        self.assertIsNone(coffee["errors_api_per_100"])
        self.assertEqual(coffee["src"]["errors_api_per_100"], "missing")
        # recovery: 1 error con éxito después → p50 0 (retry inmediato)
        self.assertEqual(coffee["recovery_p50_interactions"], 0)
        self.assertEqual(coffee["src"]["recovery_p50_interactions"], "derived")
        self.assertEqual(coffee["ci_fail_rate"], 0.5)  # 1 failure / 2 runs
        # gh prov cached en el fixture → el tag hereda cached (D7)
        self.assertEqual(coffee["src"]["ci_fail_rate"], "cached")
        # latencia: sesión 2026-03-20, commit posterior 2026-03-10 NO cuenta
        # (previo) → sin pares → None con missing
        self.assertIsNone(coffee["session_commit_latency_p50_h"])
        self.assertEqual(coffee["src"]["session_commit_latency_p50_h"],
                         "missing")
        # git proxies: fix_ratio sin releases → missing; test_commit_ratio 1/3
        self.assertIsNone(coffee["fix_ratio"])
        self.assertEqual(coffee["src"]["fix_ratio"], "missing")
        self.assertEqual(coffee["test_commit_ratio"], 1 / 3)
        self.assertEqual(coffee["ah_check_findings"], 2)
        self.assertEqual(coffee["src"]["ah_check_findings"], "cached")
        # dosis derivada del mes: wai adoptado 2026-03-05 → 1/13
        self.assertAlmostEqual(coffee["dose"], 1 / 13)
        self.assertEqual(coffee["src"]["dose"], "derived")

        # dont: solo usage y sessions (sin git/gh/ah)
        dont = rows[("dont", "2026-03")]
        self.assertEqual(dont["interactions"], 50)
        self.assertEqual(dont["src"]["interactions"], "reported")
        self.assertNotIn("ci_fail_rate", dont)  # sin señal: campo afuera
        self.assertAlmostEqual(dont["dose"], 0.0)  # sin usos → 0.0 derivada

        # meses sin señal afuera
        self.assertNotIn(("coffee", "2026-02"), rows)

        # repos[]
        by_repo = {r["repo"]: r for r in rep["repos"]}
        coffee_r = by_repo["coffee"]
        self.assertFalse(coffee_r["is_control"])
        self.assertEqual(coffee_r["adoption"]["first_ddl_commit"],
                         "2026-03-05T10:00:00Z")
        self.assertAlmostEqual(coffee_r["dose"], 1.0)  # ddl status reporta
        dont_r = by_repo["dont"]
        self.assertTrue(dont_r["is_control"])
        # sin .ddl, sin usos de gestionados → 0.0 con provenance missing
        self.assertAlmostEqual(dont_r["dose"], 0.0)
        self.assertEqual(dont_r["dose_provenance"], "missing")

        # analysis: estructuras presentes y honestas (datos chicos → razones)
        self.assertIn("its", rep["analysis"])
        self.assertIn("did", rep["analysis"])
        self.assertIn("dose_response", rep["analysis"])
        its_coffee = [e for e in rep["analysis"]["its"]
                      if e["repo"] == "coffee"]
        self.assertTrue(its_coffee)  # ventana pre insuficiente con razón
        self.assertIsNone(its_coffee[0]["delta"])

    def test_ci_cached_hereda_tag_de_cache_gh(self):
        """D7: runs del cache gh → src.ci_fail_rate = cached, no reported."""
        insumos = self._insumos()
        insumos["gh"]["coffee"]["provenance"] = "cached"
        rep = ddl.assemble_report(insumos)
        row = next(r for r in rep["monthly"]
                   if r["repo"] == "coffee" and r["month"] == "2026-03")
        self.assertEqual(row["src"]["ci_fail_rate"], "cached")
        self.assertEqual(row["src"]["runs_total"], "cached")

    def test_mes_parcial_y_metadata(self):
        insumos = self._insumos()
        rep = ddl.assemble_report(insumos)
        self.assertIn("generated_at", rep["metadata"])
        self.assertEqual(rep["metadata"]["seed_bootstrap"], 42)
        self.assertEqual(len(rep["metadata"]["managed_tools"]), 13)
        # provenance de flota: repos con y sin git
        prov = rep["metadata"]["provenance"]
        self.assertIn("fleet", prov)
        self.assertIn("coffee", prov["fleet"])


if __name__ == "__main__":
    unittest.main(verbosity=1)

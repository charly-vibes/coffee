#!/usr/bin/env python3
"""Tests de proxies de git/error rates/gh (cache, CI, auth) en ddl-effect-tracker.py.

Solo stdlib. Sin disk I/O: los reducers reciben estructuras, no paths
(mismo estilo que test_tracker.py con aggregate()).
"""
import importlib.util
import sys
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
_spec = importlib.util.spec_from_file_location(
    "ddl_effect_tracker", REPO / "scripts" / "ddl-effect-tracker.py"
)
ddl = importlib.util.module_from_spec(_spec)
sys.modules["ddl_effect_tracker"] = ddl
_spec.loader.exec_module(ddl)


class GitProxiesTest(unittest.TestCase):
    COMMITS = [
        {"date": "2026-04-01T10:00:00Z", "message": "feat: a", "files": ["src/x.py"]},
        {"date": "2026-04-02T10:00:00Z", "message": "fix: b", "files": ["src/y.py"]},
        {"date": "2026-04-03T10:00:00Z", "message": "Revert \"feat: a\"", "files": ["src/x.py"]},
        {"date": "2026-04-04T10:00:00Z", "message": "test: c", "files": ["tests/test_x.py"]},
        {"date": "2026-04-05T10:00:00Z", "message": "docs: d", "files": ["docs/x.md"]},
    ]

    def test_tres_proxies_sobre_mes_con_datos(self):
        out = ddl.git_proxies(self.COMMITS, releases=["2026-04-06T00:00:00Z"])
        self.assertEqual(out["revert_ratio"], 1 / 5)
        self.assertEqual(out["fix_ratio"], 1 / 1)      # 1 fix / 1 release
        self.assertEqual(out["test_commit_ratio"], 1 / 5)  # test: explicito
        self.assertEqual(out["provenance"]["revert_ratio"], "reported")

    def test_archivos_de_test_cuentan_sin_prefijo(self):
        commits = self.COMMITS + [
            {"date": "2026-04-07T10:00:00Z", "message": "wip",
             "files": ["tests/test_y.py"]},
        ]
        out = ddl.git_proxies(commits, releases=[])
        self.assertEqual(out["test_commit_ratio"], 2 / 6)

    def test_denominador_cero_es_null_con_missing(self):
        out = ddl.git_proxies([], releases=[])
        self.assertIsNone(out["revert_ratio"])
        self.assertIsNone(out["fix_ratio"])
        self.assertEqual(out["provenance"]["fix_ratio"], "missing")

    def test_release_cero_con_fixes_presentes(self):
        out = ddl.git_proxies(self.COMMITS, releases=[])
        self.assertIsNone(out["fix_ratio"])
        self.assertEqual(out["provenance"]["fix_ratio"], "missing")


class ErrorRatesTest(unittest.TestCase):
    def test_api_por_100_con_denominador(self):
        out = ddl.error_rates(interactions=1000, api_errors=3, terapia={"bash": 2})
        self.assertEqual(out["errors_api_per_100"], 0.3)
        self.assertEqual(out["interactions"], 1000)  # denominador en el row
        self.assertEqual(out["errors_terapia_per_100"]["bash"], 0.2)

    def test_denominador_cero_todas_null(self):
        out = ddl.error_rates(interactions=0, api_errors=0, terapia={})
        self.assertIsNone(out["errors_api_per_100"])
        self.assertIsNone(out["errors_terapia_per_100"])
        self.assertEqual(out["provenance"]["errors_api_per_100"], "missing")

    def test_terapia_vacio_con_interacciones_no_es_null(self):
        out = ddl.error_rates(interactions=500, api_errors=0, terapia={})
        self.assertEqual(out["errors_api_per_100"], 0.0)
        self.assertEqual(out["errors_terapia_per_100"], {})


class GhCacheTest(unittest.TestCase):
    RUN_A = {"id": 1, "created_at": "2026-04-01T10:00:00Z",
             "conclusion": "success", "status": "completed",
             "head_branch": "main"}
    RUN_B = {"id": 2, "created_at": "2026-04-05T10:00:00Z",
             "conclusion": "failure", "status": "completed",
             "head_branch": "main"}

    def test_cache_frio_descarga_todo(self):
        seen = []
        def fetch(repo, since):
            seen.append(since)
            return [self.RUN_A, self.RUN_B]
        out = ddl.gh_runs_incremental("wai", fetch, cache=None,
                                      now="2026-04-10T00:00:00Z")
        self.assertIsNone(seen[0])           # sin cache: sin marca since
        self.assertEqual(len(out["runs"]), 2)
        self.assertEqual(out["fetched_until"], "2026-04-05T10:00:00Z")
        self.assertEqual(out["provenance"], "reported")

    def test_cache_tibio_no_llama_red(self):
        def fetch(repo, since):
            raise AssertionError("no debió tocar la red dentro del TTL")
        cache = {"runs": [self.RUN_A], "fetched_until": "2026-04-08T00:00:00Z"}
        out = ddl.gh_runs_incremental("wai", fetch, cache=cache,
                                      ttl_days=7, now="2026-04-10T00:00:00Z")
        self.assertEqual(len(out["runs"]), 1)
        self.assertEqual(out["provenance"], "cached")

    def test_cache_stale_refetch_incremental(self):
        seen = []
        def fetch(repo, since):
            seen.append(since)
            return [self.RUN_B]  # solo el run nuevo
        cache = {"runs": [self.RUN_A], "fetched_until": "2026-04-02T00:00:00Z"}
        out = ddl.gh_runs_incremental("wai", fetch, cache=cache, ttl_days=0,
                                      now="2026-04-10T00:00:00Z")
        self.assertEqual(seen[0], "2026-04-02T00:00:00Z")  # solo lo nuevo
        self.assertEqual(len(out["runs"]), 2)              # merge sin duplicar
        self.assertEqual(out["fetched_until"], "2026-04-05T10:00:00Z")

    def test_red_muerta_conserva_cache_stale_con_missing(self):
        def fetch(repo, since):
            raise ConnectionError("no network")
        cache = {"runs": [self.RUN_A], "fetched_until": "2026-04-02T00:00:00Z"}
        out = ddl.gh_runs_incremental("wai", fetch, cache=cache, ttl_days=0,
                                      now="2026-04-10T00:00:00Z")
        self.assertEqual(len(out["runs"]), 1)  # cache conservado
        self.assertEqual(out["provenance"], "missing")
        self.assertIn("reason", out)

    def test_repo_gone_404(self):
        class NotFound(Exception):
            pass
        def fetch(repo, since):
            raise NotFound("404")
        out = ddl.gh_runs_incremental("ghost", fetch, cache=None,
                                      now="2026-04-10T00:00:00Z")
        self.assertEqual(out["provenance"], "missing")
        self.assertEqual(out["reason"], "repo_gone")


class CiFailRateTest(unittest.TestCase):
    def test_fallidos_sobre_completados(self):
        runs = [
            {"conclusion": "success", "status": "completed", "head_branch": "main",
             "created_at": "2026-04-01T10:00:00Z"},
            {"conclusion": "failure", "status": "completed", "head_branch": "main",
             "created_at": "2026-04-02T10:00:00Z"},
            {"conclusion": None, "status": "in_progress", "head_branch": "main",
             "created_at": "2026-04-03T10:00:00Z"},
        ]
        out = ddl.ci_fail_rate(runs, month="2026-04")
        self.assertEqual(out["ci_fail_rate"], 0.5)  # 1 failure / 2 completed

    def test_dependabot_excluido(self):
        runs = [
            {"conclusion": "failure", "status": "completed",
             "head_branch": "dependabot/npm/y", "created_at": "2026-04-01T10:00:00Z"},
            {"conclusion": "success", "status": "completed",
             "head_branch": "main", "created_at": "2026-04-02T10:00:00Z"},
        ]
        out = ddl.ci_fail_rate(runs, month="2026-04")
        self.assertEqual(out["ci_fail_rate"], 0.0)
        self.assertEqual(out["runs_total"], 1)

    def test_mes_filtrado(self):
        runs = [
            {"conclusion": "failure", "status": "completed", "head_branch": "main",
             "created_at": "2026-03-01T10:00:00Z"},
            {"conclusion": "success", "status": "completed", "head_branch": "main",
             "created_at": "2026-04-01T10:00:00Z"},
        ]
        out = ddl.ci_fail_rate(runs, month="2026-04")
        self.assertEqual(out["runs_total"], 1)

    def test_sin_runs_null_con_missing(self):
        out = ddl.ci_fail_rate([], month="2026-04")
        self.assertIsNone(out["ci_fail_rate"])
        self.assertEqual(out["provenance"], "missing")


class GhAuthTest(unittest.TestCase):
    def test_env_token_gana(self):
        token, src = ddl.gh_token(env={"GH_TOKEN": "tok-env"},
                                  gh_auth=lambda: "tok-cli")
        self.assertEqual(token, "tok-env")
        self.assertEqual(src, "GH_TOKEN")

    def test_fallback_gh_auth_token(self):
        token, src = ddl.gh_token(env={}, gh_auth=lambda: "tok-cli")
        self.assertEqual(token, "tok-cli")
        self.assertEqual(src, "gh auth token")

    def test_sin_credencial_es_none_con_razon(self):
        token, src = ddl.gh_token(env={}, gh_auth=lambda: None)
        self.assertIsNone(token)
        self.assertIsNone(src)

    def test_gh_auth_fallando_no_explota(self):
        def boom():
            raise FileNotFoundError("gh no instalado")
        token, src = ddl.gh_token(env={}, gh_auth=boom)
        self.assertIsNone(token)
        self.assertIsNone(src)


if __name__ == "__main__":
    unittest.main(verbosity=1)

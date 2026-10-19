#!/usr/bin/env python3
"""Tests de reducers puros de scripts/ddl-effect-tracker.py (add-ddl-effectiveness-analysis).

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

REGISTRY = ddl.MANAGED_TOOLS


class ExtractAdoptionTest(unittest.TestCase):
    def test_repo_con_commit_y_primer_uso(self):
        commits = [
            {"repo": "wai", "date": "2026-03-01", "files": ["README.md"]},
            {"repo": "wai", "date": "2026-02-01", "files": [".ddl/config.json"]},
        ]
        uses = [{"repo": "wai", "tool": "wai", "date": "2026-02-15"}]
        out = ddl.extract_adoption(commits, uses, REGISTRY)
        self.assertEqual(out["wai"]["first_ddl_commit"], "2026-02-01")
        self.assertEqual(out["wai"]["tools_first_use"]["wai"], "2026-02-15")

    def test_repo_sin_senal_nulo_con_provenance(self):
        out = ddl.extract_adoption([], [], REGISTRY, repos=["ghost"])
        self.assertIsNone(out["ghost"]["first_ddl_commit"])
        self.assertIsNone(out["ghost"]["tools_first_use"]["wai"])
        self.assertEqual(out["ghost"]["provenance"]["first_ddl_commit"], "missing")

    def test_primer_ddl_commit_es_el_mas_antiguo(self):
        commits = [
            {"repo": "turu", "date": "2026-05-01", "files": [".ddl/config.json"]},
            {"repo": "turu", "date": "2026-01-01", "files": [".ddl/x", "src/main.py"]},
        ]
        out = ddl.extract_adoption(commits, [], REGISTRY)
        self.assertEqual(out["turu"]["first_ddl_commit"], "2026-01-01")

    def test_repo_aliases_match_exacto_sin_fuzzy(self):
        commits = []
        uses = [
            {"repo": "/home/sasha/para/areas/dev/gh/charly/coffee", "tool": "wai",
             "date": "2026-04-01"},
        ]
        out = ddl.extract_adoption(
            commits, uses, REGISTRY, repos=["coffee"],
            repo_aliases={"coffee": ["/home/sasha/para/areas/dev/gh/charly/coffee"]},
        )
        self.assertEqual(out["coffee"]["tools_first_use"]["wai"], "2026-04-01")
        # sin alias, el cwd largo no matchea nada (sin fuzzy matching)
        out2 = ddl.extract_adoption(commits, uses, REGISTRY, repos=["coffee"])
        self.assertIsNone(out2["coffee"]["tools_first_use"]["wai"])

    def test_herramienta_no_gestionada_se_ignora(self):
        uses = [{"repo": "wai", "tool": "vim", "date": "2026-02-15"}]
        out = ddl.extract_adoption([], uses, REGISTRY, repos=["wai"])
        self.assertNotIn("vim", out["wai"]["tools_first_use"])
        self.assertIsNone(out["wai"]["tools_first_use"]["wai"])

    def test_primer_uso_repite_conteo_y_toma_el_minimo(self):
        uses = [
            {"repo": "dont", "tool": "dont", "date": "2026-06-10"},
            {"repo": "dont", "tool": "dont", "date": "2026-06-01"},
        ]
        out = ddl.extract_adoption([], uses, REGISTRY, repos=["dont"])
        self.assertEqual(out["dont"]["tools_first_use"]["dont"], "2026-06-01")

    def test_uses_de_repo_fuera_de_flota_ignorados(self):
        uses = [{"repo": "otro-proyecto", "tool": "wai", "date": "2026-06-01"}]
        out = ddl.extract_adoption([], uses, REGISTRY, repos=["wai"])
        self.assertIsNone(out["wai"]["tools_first_use"]["wai"])


class RecoveryMetricsTest(unittest.TestCase):
    # convención de eventos: ("assistant",) | ("result", tool, ok)

    def test_recuperacion_cuenta_asistentes_hasta_exito(self):
        sessions = [[
            ("result", "bash", False),   # error
            ("assistant",),
            ("assistant",),
            ("result", "bash", True),    # éxito del mismo tool
        ]]
        out = ddl.recovery_metrics(sessions)
        self.assertEqual(out["recovery_p50_interactions"], 2)
        self.assertEqual(out["recovery_censored_rate"], 0.0)
        self.assertEqual(out["recovery_n"], 1)

    def test_error_censurado_por_fin_de_sesion(self):
        sessions = [[("result", "bash", False), ("assistant",)]]
        out = ddl.recovery_metrics(sessions)
        self.assertIsNone(out["recovery_p50_interactions"])
        self.assertEqual(out["recovery_censored_rate"], 1.0)

    def test_exito_de_otro_tool_no_resuelve(self):
        sessions = [[
            ("result", "bash", False),
            ("result", "read", True),
            ("result", "bash", True),
        ]]
        out = ddl.recovery_metrics(sessions)
        self.assertEqual(out["recovery_p50_interactions"], 0)  # 0 asistentes entre medio
        self.assertEqual(out["recovery_n"], 1)

    def test_dos_errores_resueltos_por_un_exito(self):
        sessions = [[
            ("result", "edit", False),
            ("assistant",),
            ("result", "edit", False),
            ("result", "edit", True),
        ]]
        out = ddl.recovery_metrics(sessions)
        self.assertEqual(out["recovery_p50_interactions"], 0.5)  # mediana de [1, 0]
        self.assertEqual(out["recovery_n"], 2)

    def test_sesion_vacia(self):
        out = ddl.recovery_metrics([[]])
        self.assertIsNone(out["recovery_p50_interactions"])
        self.assertEqual(out["recovery_censored_rate"], 0.0)
        self.assertEqual(out["recovery_n"], 0)

    def test_mediana_sobre_multiples_sesiones(self):
        sessions = [
            [("result", "bash", False), ("result", "bash", True)],   # 0
            [("result", "bash", False), ("assistant",), ("result", "bash", True)],  # 1
            [("result", "bash", False), ("assistant",), ("assistant",),
             ("assistant",), ("result", "bash", True)],             # 3
        ]
        out = ddl.recovery_metrics(sessions)
        self.assertEqual(out["recovery_p50_interactions"], 1)


class CommitLatencyTest(unittest.TestCase):
    def test_primer_commit_posterior_a_sesion(self):
        commits = [
            {"date": "2026-04-01T09:00:00Z", "message": "w: previo"},
            {"date": "2026-04-01T14:30:00Z", "message": "feat: trabajo asistido"},
        ]
        out = ddl.commit_latency("2026-04-01T13:00:00Z", commits)
        self.assertEqual(out["session_commit_latency_p50_h"], 1.5)
        self.assertEqual(out["latency_n"], 1)

    def test_sin_commit_posterior_es_none(self):
        out = ddl.commit_latency("2026-04-01T13:00:00Z",
                                 [{"date": "2026-04-01T09:00:00Z", "message": "x"}])
        self.assertIsNone(out["session_commit_latency_p50_h"])

    def test_sesgo_de_reloj_se_clampea(self):
        # commit "antes" de la sesión por skew → latencia 0, contada
        commits = [{"date": "2026-04-01T12:00:00Z", "message": "fix: a"}]
        out = ddl.commit_latency("2026-04-01T13:00:00Z", commits)
        self.assertEqual(out["session_commit_latency_p50_h"], 0.0)
        self.assertEqual(out["clamped_negative"], 1)

    def test_mediana_sobre_varios_pares(self):
        commits = [
            {"date": "2026-04-01T14:00:00Z", "message": "a"},  # 1h
            {"date": "2026-04-01T16:00:00Z", "message": "b"},  # 3h
        ]
        out = ddl.commit_latency("2026-04-01T13:00:00Z", commits)
        self.assertEqual(out["session_commit_latency_p50_h"], 2.0)


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
    def run(self, result=None):
        return super().run(result)

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


class ComputeDoseTest(unittest.TestCase):
    def test_dosis_desde_ddl_status(self):
        status = {
            "wai": {"present": REGISTRY[:5], "healthy": REGISTRY[:4]},
        }
        out = ddl.compute_dose(status, {}, {}, REGISTRY)
        self.assertEqual(out["wai"]["dose"], 4 / 13)
        self.assertEqual(out["wai"]["dose_detail"]["healthy"], list(REGISTRY[:4]))
        self.assertEqual(out["wai"]["provenance"]["dose"], "reported")

    def test_fallback_canarios_git(self):
        canaries = {"wai": [".ddl/wai/config.json", ".ddl/dont/config.json"]}
        out = ddl.compute_dose(None, canaries, {}, REGISTRY)
        self.assertEqual(out["wai"]["dose"], 2 / 13)
        self.assertEqual(out["wai"]["provenance"]["dose"], "derived")

    def test_fallback_usos_de_sesion(self):
        uses = {"wai": {"turu", "ah"}}
        out = ddl.compute_dose(None, {}, uses, REGISTRY)
        self.assertEqual(out["wai"]["dose"], 2 / 13)
        self.assertEqual(out["wai"]["provenance"]["dose"], "derived")

    def test_sin_toda_senal_es_cero_con_missing(self):
        out = ddl.compute_dose(None, {}, {}, REGISTRY, repos=["ghost"])
        self.assertEqual(out["ghost"]["dose"], 0.0)
        self.assertEqual(out["ghost"]["provenance"]["dose"], "missing")

    def test_ddl_status_gana_sobre_fallback(self):
        status = {"wai": {"present": REGISTRY, "healthy": REGISTRY}}
        canaries = {"wai": [".ddl/wai/config.json"]}
        out = ddl.compute_dose(status, canaries, {}, REGISTRY)
        self.assertEqual(out["wai"]["dose"], 1.0)
        self.assertEqual(out["wai"]["provenance"]["dose"], "reported")


class ClassifyControlTest(unittest.TestCase):
    def test_control_por_ausencia_estructural(self):
        repos = {"ghost": {"has_ddl": False, "topic_ddl": False},
                 "wai": {"has_ddl": True, "topic_ddl": True}}
        out = ddl.classify_control(repos)
        self.assertTrue(out["ghost"]["is_control"])
        self.assertFalse(out["wai"]["is_control"])

    def test_ddl_sin_dosis_no_es_control(self):
        repos = {"wai": {"has_ddl": True, "topic_ddl": True}}
        out = ddl.classify_control(repos)
        self.assertFalse(out["wai"]["is_control"])

    def test_outlier_ddl_marcado(self):
        repos = {"charly-dulce-de-leche": {"has_ddl": True, "topic_ddl": True}}
        out = ddl.classify_control(repos, outlier="charly-dulce-de-leche")
        self.assertTrue(out["charly-dulce-de-leche"]["is_outlier"])
        self.assertFalse(out["charly-dulce-de-leche"]["is_control"])

    def test_clasificacion_reproducible(self):
        repos = {"wai": {"has_ddl": True, "topic_ddl": True},
                 "ghost": {"has_ddl": False, "topic_ddl": None}}
        self.assertEqual(ddl.classify_control(repos), ddl.classify_control(repos))


class PanelTest(unittest.TestCase):
    """4.1 (coffe-w02): ensamblado del panel repo × mes (monthly[])."""

    # --- cell_from_emitters -------------------------------------------------

    def test_scalar_provenance_aplica_a_todos_los_campos(self):
        ci = {"ci_fail_rate": 0.25, "runs_total": 4, "runs_failed": 1,
              "provenance": "reported"}
        row = ddl.cell_from_emitters("wai", "2026-08", {"ci": ci})
        self.assertEqual(row["ci_fail_rate"], 0.25)
        self.assertEqual(row["src"]["ci_fail_rate"], "reported")
        self.assertEqual(row["src"]["runs_total"], "reported")
        self.assertEqual(row["src"]["runs_failed"], "reported")

    def test_dict_provenance_por_campo(self):
        errs = ddl.error_rates(120, 3, {"timeout": 1})
        row = ddl.cell_from_emitters("wai", "2026-08", {"errors": errs})
        self.assertEqual(row["src"]["errors_api_per_100"], "reported")
        self.assertEqual(row["src"]["errors_terapia_per_100"], "reported")
        self.assertEqual(row["src"]["interactions"], "reported")

    def test_tag_missing_normaliza_a_null(self):
        # dose 0.0 con provenance missing nunca llega al panel como 0 fake
        dose = {"dose": 0.0, "dose_detail": {"present": [], "healthy": [],
                                             "missing": list(REGISTRY)},
                "provenance": {"dose": "missing"}}
        row = ddl.cell_from_emitters("ghost", "2026-08", {"dose": dose})
        self.assertIsNone(row["dose"])
        self.assertEqual(row["src"]["dose"], "missing")

    def test_reason_visible_en_la_fila(self):
        ci = {"ci_fail_rate": None, "runs_total": 0, "runs_failed": 0,
              "provenance": "missing", "reason": {"ci_fail_rate": "sin red"}}
        row = ddl.cell_from_emitters("wai", "2026-08", {"ci": ci})
        self.assertEqual(row["reasons"]["ci_fail_rate"], "sin red")

    def test_campo_fuera_del_allowlist_falla_loud(self):
        em = {"karma": 5, "provenance": "reported"}
        with self.assertRaises(ValueError):
            ddl.cell_from_emitters("wai", "2026-08", {"x": em})

    def test_campo_duplicado_entre_emisores_falla_loud(self):
        a = {"dose": 0.5, "provenance": {"dose": "derived"}}
        b = {"dose": 0.6, "provenance": {"dose": "reported"}}
        with self.assertRaises(ValueError):
            ddl.cell_from_emitters("wai", "2026-08", {"a": a, "b": b})

    # --- build_panel --------------------------------------------------------

    def test_mes_sin_senal_afuera_del_array(self):
        cells = [ddl.cell_from_emitters("ghost", "2026-08",
                                        {"dose": {"dose": 0.0, "provenance":
                                                  {"dose": "missing"}}})]
        self.assertEqual(ddl.build_panel(cells), [])

    def test_senal_minima_mantiene_el_mes(self):
        em = {"ah_check_findings": 2, "provenance": "reported"}
        panel = ddl.build_panel([ddl.cell_from_emitters("wai", "2026-08",
                                                        {"ah": em})])
        self.assertEqual(len(panel), 1)
        self.assertEqual(panel[0]["ah_check_findings"], 2)
        self.assertEqual(panel[0]["src"]["ah_check_findings"], "reported")

    def test_null_con_tag_reported_falla_loud(self):
        row = {"repo": "wai", "month": "2026-08", "ci_fail_rate": None,
               "src": {"ci_fail_rate": "reported"}}
        with self.assertRaises(ValueError):
            ddl.build_panel([row])

    def test_valor_con_tag_missing_falla_loud(self):
        row = {"repo": "wai", "month": "2026-08", "revert_ratio": 0.1,
               "src": {"revert_ratio": "missing"}}
        with self.assertRaises(ValueError):
            ddl.build_panel([row])

    def test_cifra_sin_tag_falla_loud(self):
        row = {"repo": "wai", "month": "2026-08", "revert_ratio": 0.1,
               "src": {}}
        with self.assertRaises(ValueError):
            ddl.build_panel([row])

    def test_tag_invalido_falla_loud(self):
        row = {"repo": "wai", "month": "2026-08", "revert_ratio": 0.1,
               "src": {"revert_ratio": "guessed"}}
        with self.assertRaises(ValueError):
            ddl.build_panel([row])

    def test_celda_duplicada_repo_mes_falla_loud(self):
        em = {"ah_check_findings": 1, "provenance": "reported"}
        c = ddl.cell_from_emitters("wai", "2026-08", {"ah": em})
        with self.assertRaises(ValueError):
            ddl.build_panel([c, c])

    def test_orden_determinista_repo_mes(self):
        c1 = ddl.cell_from_emitters("turu", "2026-03",
                                    {"ah": {"ah_check_findings": 1,
                                            "provenance": "reported"}})
        c2 = ddl.cell_from_emitters("wai", "2026-08",
                                    {"ah": {"ah_check_findings": 2,
                                            "provenance": "reported"}})
        c3 = ddl.cell_from_emitters("wai", "2026-02",
                                    {"ah": {"ah_check_findings": 3,
                                            "provenance": "reported"}})
        panel = ddl.build_panel([c2, c1, c3])
        self.assertEqual([(r["repo"], r["month"]) for r in panel],
                         [("turu", "2026-03"), ("wai", "2026-02"),
                          ("wai", "2026-08")])
        self.assertEqual(ddl.build_panel([c3, c2, c1]), panel)

    def test_fila_completa_de_celdas_reales(self):
        errs = ddl.error_rates(120, 3, {"timeout": 1})
        ci = {"ci_fail_rate": None, "runs_total": 0, "runs_failed": 0,
              "provenance": "missing", "reason": {"ci_fail_rate": "sin red"}}
        dose = {"dose": 4 / 13, "provenance": {"dose": "reported"}}
        row = ddl.cell_from_emitters(
            "wai", "2026-08",
            {"errors": errs, "ci": ci, "dose": dose,
             "rec": {"recovery_p50_interactions": 2, "recovery_n": 5,
                     "provenance": "derived"}})
        panel = ddl.build_panel([row])
        self.assertEqual(panel[0]["repo"], "wai")
        self.assertEqual(panel[0]["month"], "2026-08")
        self.assertEqual(panel[0]["dose"], 4 / 13)
        self.assertEqual(panel[0]["src"]["recovery_p50_interactions"],
                         "derived")
        self.assertNotIn("provenance", panel[0])  # solo src
        self.assertNotIn("dose_detail", panel[0])


class ItsTest(unittest.TestCase):
    """4.2 (coffe-ktu): ITS pre/post, media móvil trimestral + IC bootstrap."""

    MONTHS = [f"2026-{m:02d}" for m in range(1, 9)]  # 2026-01..2026-08

    def _rows(self, values):
        return [{"month": m, "ah_check_findings": v}
                for m, v in zip(self.MONTHS, values)]

    def test_delta_pre_post_paso_escalon(self):
        out = ddl.its_pre_post(self._rows([1, 1, 1, 1, 3, 3, 3, 3]),
                               "ah_check_findings", "2026-05")
        self.assertEqual(out["pre_mean"], 1.0)
        self.assertEqual(out["post_mean"], 3.0)
        self.assertEqual(out["delta"], 2.0)
        self.assertEqual(out["n_pre"], 4)
        self.assertEqual(out["n_post"], 4)

    def test_ci_bracketea_delta(self):
        out = ddl.its_pre_post(self._rows([1, 2, 1, 3, 5, 4, 6, 5]),
                               "ah_check_findings", "2026-05", n_boot=1000)
        self.assertLess(out["ci_low"], out["delta"])
        self.assertGreater(out["ci_high"], out["delta"])

    def test_reproducible_con_seed_fija(self):
        rows = self._rows([1, 2, 1, 3, 5, 4, 6, 5])
        a = ddl.its_pre_post(rows, "ah_check_findings", "2026-05")
        b = ddl.its_pre_post(rows, "ah_check_findings", "2026-05")
        self.assertEqual(a, b)

    def test_ventana_pre_insuficiente_omitido_con_marca(self):
        # adopción precede al tracking: 2 meses pre < mínimo 3 → delta None,
        # nunca 0 disfrazado
        out = ddl.its_pre_post(self._rows([1, 1, 3, 3, 3, 3, 3, 3]),
                               "ah_check_findings", "2026-03")
        self.assertIsNone(out["delta"])
        self.assertEqual(out["reason"], "ventana_pre_insuficiente")
        self.assertEqual(out["n_pre"], 2)

    def test_ventana_pre_de_3_meses_si_estima(self):
        # borde: exactamente 3 meses pre es suficiente (D5)
        out = ddl.its_pre_post(self._rows([1, 1, 1, 3, 3, 3, 3, 3]),
                               "ah_check_findings", "2026-04")
        self.assertEqual(out["n_pre"], 3)
        self.assertIsNone(out.get("reason"))
        self.assertEqual(out["delta"], 2.0)

    def test_sin_meses_post_omitido(self):
        # adopción después del último mes de la serie: post vacío
        out = ddl.its_pre_post(self._rows([1, 1, 1, 1, 1, 1, 1, 1]),
                               "ah_check_findings", "2026-09")
        self.assertIsNone(out["delta"])
        self.assertEqual(out["reason"], "sin_meses_post")
        self.assertEqual(out["n_post"], 0)

    def test_post_de_1_mes_sin_ma_omitido(self):
        # adopción en el último mes: hay 1 mes post pero no alcanza para la
        # media móvil trimestral → omitido con razón, no delta crudo disfrazado
        out = ddl.its_pre_post(self._rows([1, 1, 1, 1, 1, 1, 1, 1]),
                               "ah_check_findings", "2026-08")
        self.assertIsNone(out["delta"])
        self.assertEqual(out["reason"], "ventana_ma_trimestral_insuficiente")
        self.assertEqual(out["n_post"], 1)

    def test_meses_con_metrica_none_excluidos(self):
        rows = self._rows([1, None, 1, 1, 3, 3, 3, 3])
        out = ddl.its_pre_post(rows, "ah_check_findings", "2026-05")
        self.assertEqual(out["n_pre"], 3)  # el None de feb no cuenta
        self.assertEqual(out["delta"], 2.0)


class DidTest(unittest.TestCase):
    """4.3 (coffe-dek): DiD 2×2 contra control + regla dura n<3."""

    MONTHS = [f"2026-{m:02d}" for m in range(1, 9)]

    def _rows(self, pre, post):
        vals = list(pre) + list(post)
        return [{"month": m, "ah_check_findings": v}
                for m, v in zip(self.MONTHS, vals)]

    def _repo_deltas(self):
        return ddl.repo_deltas(
            {"wai": self._rows([1, 1, 1, 1], [3, 3, 3, 3]),
             "turu": self._rows([2, 2, 2, 2], [4, 4, 4, 4]),
             "ah": self._rows([0, 0, 0, 0], [2, 2, 2, 2]),
             "dont": self._rows([5, 5, 5, 5], [5, 5, 5, 5]),
             "bd": self._rows([7, 7, 7, 7], [7, 7, 7, 7]),
             "wild": self._rows([9, 9, 9, 9], [9, 9, 9, 9])},
            "ah_check_findings", "2026-05")

    def test_repo_deltas_por_repo(self):
        out = ddl.repo_deltas(
            {"wai": self._rows([1, 1, 1, 1], [3, 3, 3, 3])},
            "ah_check_findings", "2026-05")
        self.assertEqual(out["wai"]["delta"], 2.0)
        self.assertEqual(out["wai"]["pre_mean"], 1.0)
        self.assertEqual(out["wai"]["post_mean"], 3.0)

    def test_repo_sin_post_delta_none(self):
        rows = [{"month": m, "ah_check_findings": 1}
                for m in self.MONTHS[:4]]
        out = ddl.repo_deltas({"ghost": rows}, "ah_check_findings",
                              "2026-05")
        self.assertIsNone(out["ghost"]["delta"])

    def test_did_basico_control_plano(self):
        deltas = self._repo_deltas()
        out = ddl.did_2x2(deltas, control_repos={"dont", "bd", "wild"})
        self.assertEqual(out["did"], 2.0)  # tratamiento +2, control +0
        self.assertEqual(out["n_treatment"], 3)
        self.assertEqual(out["n_control"], 3)

    def test_did_resta_tendencia_global(self):
        # control sube igual que el tratamiento: el efecto se cancela
        deltas = ddl.repo_deltas(
            {"wai": self._rows([1, 1, 1, 1], [3, 3, 3, 3]),
             "turu": self._rows([2, 2, 2, 2], [4, 4, 4, 4]),
             "ah": self._rows([0, 0, 0, 0], [2, 2, 2, 2]),
             "dont": self._rows([1, 1, 1, 1], [3, 3, 3, 3]),
             "bd": self._rows([2, 2, 2, 2], [4, 4, 4, 4]),
             "wild": self._rows([0, 0, 0, 0], [2, 2, 2, 2])},
            "ah_check_findings", "2026-05")
        out = ddl.did_2x2(deltas, control_repos={"dont", "bd", "wild"})
        self.assertEqual(out["did"], 0.0)

    def test_n_insuficiente_tratamiento_marca_el_json(self):
        deltas = ddl.repo_deltas(
            {"wai": self._rows([1, 1, 1, 1], [3, 3, 3, 3]),
             "turu": self._rows([2, 2, 2, 2], [4, 4, 4, 4]),
             "dont": self._rows([5, 5, 5, 5], [5, 5, 5, 5]),
             "bd": self._rows([7, 7, 7, 7], [7, 7, 7, 7]),
             "wild": self._rows([9, 9, 9, 9], [9, 9, 9, 9])},
            "ah_check_findings", "2026-05")
        out = ddl.did_2x2(deltas, control_repos={"dont", "bd", "wild"})
        self.assertIsNone(out["did"])
        self.assertEqual(out["reason"], "n_insuficiente")
        self.assertEqual(out["n_treatment"], 2)  # visible, no oculto
        self.assertEqual(out["n_control"], 3)

    def test_n_insuficiente_control(self):
        deltas = ddl.repo_deltas(
            {"wai": self._rows([1, 1, 1, 1], [3, 3, 3, 3]),
             "turu": self._rows([2, 2, 2, 2], [4, 4, 4, 4]),
             "ah": self._rows([0, 0, 0, 0], [2, 2, 2, 2]),
             "dont": self._rows([5, 5, 5, 5], [5, 5, 5, 5])},
            "ah_check_findings", "2026-05")
        out = ddl.did_2x2(deltas, control_repos={"dont"})
        self.assertIsNone(out["did"])
        self.assertEqual(out["reason"], "n_insuficiente")
        self.assertEqual(out["n_control"], 1)

    def test_repo_sin_delta_no_cuenta_en_n(self):
        deltas = self._repo_deltas()
        deltas["ghost"] = {"pre_mean": 1.0, "post_mean": None, "delta": None}
        out = ddl.did_2x2(deltas, control_repos={"dont", "bd", "wild"})
        self.assertEqual(out["did"], 2.0)
        self.assertEqual(out["n_treatment"], 3)

    def test_borde_n_3_si_estima(self):
        deltas = self._repo_deltas()
        out = ddl.did_2x2(deltas, control_repos={"dont", "bd", "wild"},
                          min_n=3)
        self.assertIsNone(out.get("reason"))
        self.assertEqual(out["did"], 2.0)


if __name__ == "__main__":
    unittest.main(verbosity=1)

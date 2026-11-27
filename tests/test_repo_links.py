#!/usr/bin/env python3
"""
test_repo_links.py — coffe-vp8: enlaces a repos GitHub en el dashboard FP&A.

Cubre:
- repo_link(): mapeo puro label→URL (sin red); owners/private/no_remote/
  url_overrides viven en config/fpa.json. Visible = auditable.
- build_portfolio_tree(): nodos de proyecto llevan url/private; categoría
  y raíz no.
- _tree_rows()/_tree_html(): passthrough y render (ancla + 🔒 para privados),
  espejado Python/JS.
- validate_config(): forma del bloque repos.
"""

import importlib.util
import json
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent


def _load(name, relpath):
    spec = importlib.util.spec_from_file_location(name, REPO / relpath)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


viz = _load("viz_fpa_rl", "scripts/viz-fpa.py")
fpa_config = _load("fpa_config_rl", "scripts/fpa_config.py")

CONFIG = json.loads((REPO / "config" / "fpa.json").read_text())


class TestRepoLink(unittest.TestCase):
    """repo_link(): label de proyecto → {'url', 'private'} o None."""

    def test_charly_url(self):
        link = viz.repo_link("charly-coffee", CONFIG)
        self.assertEqual("https://github.com/charly-vibes/coffee", link["url"])
        self.assertFalse(link["private"])

    def test_urls_por_label(self):
        """label → URL: owner por prefijo (ak-/sk-), casing del repo
        preservado (REPLy.jl, tRAGar)."""
        casos = [("ak-journal", "https://github.com/akielbowicz/journal"),
                 ("sk-REPLy-jl", "https://github.com/sashakile/REPLy.jl"),
                 ("charly-tRAGar", "https://github.com/charly-vibes/tRAGar")]
        for label, url in casos:
            with self.subTest(repo=label):
                self.assertEqual(url, viz.repo_link(label, CONFIG)["url"])

    def test_ak_org_con_dashes(self):
        # repos con guiones: el corte es por prefijo de org, no primer '-'
        link = viz.repo_link("ak-la-vida-privada-de-los-pensamientos", CONFIG)
        self.assertIsNone(link)  # sin remoto público → no se linkea

    def test_private_flag(self):
        link = viz.repo_link("charly-tv", CONFIG)
        self.assertTrue(link["private"])
        self.assertEqual("https://github.com/charly-vibes/tv", link["url"])

    def test_no_remote_sin_link(self):
        self.assertIsNone(viz.repo_link("ak-finanzas", CONFIG))
        self.assertIsNone(viz.repo_link("sk-papers", CONFIG))

    def test_url_override(self):
        link = viz.repo_link("charly-vibes", CONFIG)
        self.assertEqual("https://github.com/charly-vibes/charly-vibes",
                         link["url"])

    def test_bare_org_profile(self):
        self.assertEqual("https://github.com/charly-vibes",
                         viz.repo_link("charly", CONFIG)["url"])
        self.assertEqual("https://github.com/sashakile",
                         viz.repo_link("sk", CONFIG)["url"])

    def test_org_desconocida(self):
        self.assertIsNone(viz.repo_link("proyecto-raro", CONFIG))
        self.assertIsNone(viz.repo_link("", CONFIG))
        self.assertIsNone(viz.repo_link(None, CONFIG))

    def test_sin_bloque_repos(self):
        cfg = dict(CONFIG, repos={})
        self.assertIsNone(viz.repo_link("charly-tv", cfg))


def _mini_portfolio_report():
    """project_monthly mínimo: proyecto privado, ak y bare org."""
    return {
        "metadata": {"date_range": {"start": "2026-06-01", "end": "2026-06-30"},
                     "total_interactions": 10},
        "monthly": {"2026-06": {"cost_effective": 5.0, "cost_cash": 5.0,
                                 "interactions": 10}},
        "project_monthly": {
            "charly-tv": {"2026-06": {"cost_effective": 3.0, "interactions": 6}},
            "ak-journal": {"2026-06": {"cost_effective": 1.0, "interactions": 2}},
            "charly": {"2026-06": {"cost_effective": 1.0, "interactions": 2}},
        },
        "project_models": {},
    }


class TestPortfolioTreeLinks(unittest.TestCase):
    """El árbol Portfolio adjunta url/private solo en nodos de proyecto."""

    @classmethod
    def setUpClass(cls):
        cls.tree = viz.build_portfolio_tree(_mini_portfolio_report(), CONFIG)

    def _project(self, label):
        cat = next(c for c in self.tree["children"])
        nodes = {}
        for c in self.tree["children"]:
            for p in c["children"]:
                nodes[p["label"]] = p
        return nodes[label]

    def test_proyecto_privado(self):
        node = self._project("charly-tv")
        self.assertEqual("https://github.com/charly-vibes/tv", node["url"])
        self.assertTrue(node["private"])

    def test_proyecto_ak(self):
        node = self._project("ak-journal")
        self.assertEqual("https://github.com/akielbowicz/journal", node["url"])
        self.assertFalse(node["private"])

    def test_raiz_y_categoria_sin_url(self):
        self.assertNotIn("url", self.tree)
        for c in self.tree["children"]:
            self.assertNotIn("url", c)

    def test_tree_rows_passthrough(self):
        row = viz._tree_rows(self.tree, ["2026-06"], None,
                             viz._cost_of(self.tree, ["2026-06"]))
        found = {}

        def walk(r):
            if r.get("url"):
                found[r["label"]] = (r["url"], r.get("private"))
            for c in r["children"]:
                walk(c)

        walk(row)
        self.assertEqual("https://github.com/charly-vibes/tv",
                         found["charly-tv"][0])
        self.assertTrue(found["charly-tv"][1])
        self.assertEqual("https://github.com/akielbowicz/journal",
                         found["ak-journal"][0])

    def test_tree_html_anchor_y_lock(self):
        row = viz._tree_rows(self.tree, ["2026-06"], None,
                             viz._cost_of(self.tree, ["2026-06"]))
        html = viz._tree_html(row)
        self.assertIn('href="https://github.com/charly-vibes/tv"', html)
        self.assertIn("🔒", html)
        self.assertIn("repo privado", html)
        self.assertIn('href="https://github.com/akielbowicz/journal"', html)
        # categorías (con hijos) sin ancla
        self.assertNotIn('href="https://github.com/charly-vibes"', html.split(
            "charly-tv")[0])


class TestValidateReposBlock(unittest.TestCase):
    """validate_config(): forma del bloque repos."""

    def test_bloque_real_valido(self):
        errors = fpa_config.validate_config(CONFIG)
        repos_errors = [e for e in errors if "repos" in e]
        self.assertEqual([], repos_errors)

    def test_forma_invalida_del_bloque_da_error(self):
        """Mutaciones del bloque repos que el schema rechaza: override sin
        https (se menciona url_overrides), org privada desconocida (se
        menciona la org) y no_remote sin slash — todas nombran 'repos'."""
        casos = [
            ({"url_overrides": {"x": "http://inseguro"}}, "url_overrides"),
            ({"private": ["zzz/tv"]}, "zzz"),
            ({"no_remote": ["sin-slash"]}, None),
        ]
        for mutacion, mencionar in casos:
            with self.subTest(bloque=mutacion):
                cfg = json.loads(json.dumps(CONFIG))
                cfg["repos"].update(mutacion)
                errors = fpa_config.validate_config(cfg)
                self.assertTrue(any("repos" in e for e in errors),
                                f"sin error de repos para {mutacion}")
                if mencionar:
                    self.assertTrue(
                        any(mencionar in e for e in errors),
                        f"el error no menciona {mencionar}: {errors}")


if __name__ == "__main__":
    unittest.main()

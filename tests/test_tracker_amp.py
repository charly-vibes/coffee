#!/usr/bin/env python3
"""
test_tracker_amp.py — extract_amp: filtro unificado + label limpio (coffe-n35)

Cubre el alcance del ticket coffe-n35:
- amp_proj_from_uri(): derivación de proyecto (org-repo) desde el uri
  file:// de Amp, nombrado igual que clean_proj_name para que la taxonomía
  y project_daily lo crucen con las demás fuentes.
- extract_amp() usa is_charly(uri) en vez del check hardcodeado: con
  --filter all Amp ya no queda filtrado de fábrica.
- El label ya no es "charly/amp-auto" fijo: cada archivo aporta su proyecto
  derivado (fallback "amp-unknown" si el uri no es derivable).

Estilo stdlib-only, fixtures falsos en tmpdir como test_tracker_window.py.
"""

import importlib.util
import json
import tempfile
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
TRACKER_PATH = REPO / "scripts" / "usage-tracker.py"


def load_tracker():
    spec = importlib.util.spec_from_file_location("usage_tracker_amp", TRACKER_PATH)
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


ut = load_tracker()


def _guardar_estado():
    """Guarda los globals que tocan los tests para restaurarlos en tearDown."""
    return (ut.CHARLY_FILTER, ut.AMP_DIR, ut.SINCE, ut.UNTIL)


def _restaurar_estado(old):
    (ut.CHARLY_FILTER, ut.AMP_DIR, ut.SINCE, ut.UNTIL) = old


# ============================ amp_proj_from_uri ============================

class TestAmpProjFromUri(unittest.TestCase):
    """Derivación pura de proyecto desde el uri file:// de Amp."""

    # (nombre, uri, proyecto esperado); la derivación no depende del esquema:
    # basta el marker para/areas/dev/gh. REPLy.jl → REPLy-jl, igual que
    # clean_proj_name (convención repos Julia).
    CASOS = [
        ("repo_charly", "file:///var/home/sasha/para/areas/dev/gh/charly/coffe/a.py",
         "charly-coffee"),
        ("repo_sk_con_sufijo_jl", "file:///var/home/sasha/para/areas/dev/gh/sk/REPLy.jl/x.txt",
         "sk-REPLy-jl"),
        ("path_sin_esquema", "/para/areas/dev/gh/charly/coffe/a.py",
         "charly-coffee"),
    ]

    def test_derivacion_de_proyectos(self):
        for nombre, uri, esperado in self.CASOS:
            with self.subTest(caso=nombre):
                self.assertEqual(ut.amp_proj_from_uri(uri), esperado)

    def test_fuera_de_para_gh_da_none(self):
        self.assertIsNone(
            ut.amp_proj_from_uri("file:///tmp/x/proj/main.py"))

    def test_uri_vacio_da_none(self):
        self.assertIsNone(ut.amp_proj_from_uri(""))


# ============================ extract_amp ============================

class TestExtractAmp(unittest.TestCase):
    """extract_amp respeta --filter (is_charly) y etiqueta por proyecto."""

    def setUp(self):
        self._old = _guardar_estado()
        ut.SINCE = ut.UNTIL = None

    def tearDown(self):
        _restaurar_estado(self._old)

    @staticmethod
    def _entry(uri, ts="2026-05-10T10:00:00+00:00"):
        return dict(uri=uri, timestamp=ts)

    def _fake_amp_dir(self, *entries):
        d = Path(tempfile.mkdtemp()) / "file-changes" / "T-fake"
        d.mkdir(parents=True)
        for i, e in enumerate(entries):
            (d / f"f{i}").write_text(json.dumps(e))
        return d.parents[1]

    def test_filter_charly_excluye_no_charly(self):
        ut.CHARLY_FILTER = True
        ut.AMP_DIR = self._fake_amp_dir(
            self._entry("file:///var/home/sasha/para/areas/dev/gh/charly/coffe/a.py"),
            self._entry("file:///var/home/sasha/para/areas/dev/gh/phorma/site/x.html"))
        rows = ut.extract_amp()
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["project"], "charly-coffee")

    def test_filter_all_incluye_otros_proyectos(self):
        # el bug del ticket: con --filter all Amp seguía filtrado hardcode
        ut.CHARLY_FILTER = False
        ut.AMP_DIR = self._fake_amp_dir(
            self._entry("file:///var/home/sasha/para/areas/dev/gh/phorma/site/x.html"))
        rows = ut.extract_amp()
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["project"], "phorma-site")

    def test_label_limpio_sin_amp_auto(self):
        ut.CHARLY_FILTER = True
        ut.AMP_DIR = self._fake_amp_dir(
            self._entry("file:///var/home/sasha/para/areas/dev/gh/charly/coffe/a.py"))
        rows = ut.extract_amp()
        self.assertNotEqual(rows[0]["project"], "charly/amp-auto")
        self.assertEqual(rows[0]["project"], "charly-coffee")

    def test_uri_no_derivable_caen_en_amp_unknown(self):
        ut.CHARLY_FILTER = False
        ut.AMP_DIR = self._fake_amp_dir(
            self._entry("file:///tmp/otro/script.py"))
        rows = ut.extract_amp()
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["project"], "amp-unknown")

    def test_cada_archivo_aporta_su_propio_proyecto(self):
        ut.CHARLY_FILTER = False
        ut.AMP_DIR = self._fake_amp_dir(
            self._entry("file:///var/home/sasha/para/areas/dev/gh/charly/coffe/a.py",
                        "2026-05-10T10:00:00+00:00"),
            self._entry("file:///var/home/sasha/para/areas/dev/gh/sk/REPLy.jl/b.txt",
                        "2026-05-10T11:00:00+00:00"))
        rows = ut.extract_amp()
        projs = sorted(r["project"] for r in rows)
        self.assertEqual(projs, ["charly-coffee", "sk-REPLy-jl"])

    def test_row_shape_igual_al_resto(self):
        ut.CHARLY_FILTER = True
        ut.AMP_DIR = self._fake_amp_dir(
            self._entry("file:///var/home/sasha/para/areas/dev/gh/charly/coffe/a.py"))
        r = ut.extract_amp()[0]
        self.assertEqual(r["source"], "amp")
        self.assertEqual(r["tool"], "amp")
        self.assertEqual(r["model_family"], "amp")
        self.assertEqual(r["cost_effective"], 0)
        self.assertEqual(r["input_tokens"], 0)

    def test_sin_dir_amp_devuelve_vacio(self):
        ut.AMP_DIR = Path(tempfile.mkdtemp())  # sin file-changes/
        self.assertEqual(ut.extract_amp(), [])


if __name__ == "__main__":
    unittest.main()

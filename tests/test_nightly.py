#!/usr/bin/env python3
"""test_nightly.py — contrato del job nocturno (coffe-tw6, 6.1).

Hermetic: parsea scripts/nightly-update.sh y verifica orden de pasos y el
guard de --no-push (sin correr los trackers reales — esos viven en el
timer systemd y tocan logs vivos). bash -n valida sintaxis.
"""
import subprocess
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
SCRIPT = REPO / "scripts" / "nightly-update.sh"


class NightlyStructureTest(unittest.TestCase):
    """El orden importa: ddl tracker tras usage-tracker, viz tras tracker,
    y --no-push sale antes de tocar git."""

    @classmethod
    def setUpClass(cls):
        cls.text = SCRIPT.read_text()

    def _index(self, needle):
        i = self.text.find(needle)
        self.assertGreaterEqual(i, 0, f"no está en el script: {needle}")
        return i

    def test_sintaxis_bash_valida(self):
        rc = subprocess.run(["bash", "-n", str(SCRIPT)],
                            capture_output=True, text=True)
        self.assertEqual(rc.returncode, 0, rc.stderr)

    def test_ddl_tracker_tras_usage_tracker(self):
        self.assertLess(self._index("scripts/usage-tracker.py"),
                        self._index("scripts/ddl-effect-tracker.py"),
                        "ddl-effect-tracker debe correr tras usage-tracker "
                        "(consume data/usage_report_v3.json)")

    def test_ddl_viz_tras_ddl_tracker(self):
        self.assertLess(self._index("scripts/ddl-effect-tracker.py"),
                        self._index("scripts/viz-ddl.py"),
                        "viz-ddl debe correr tras ddl-effect-tracker "
                        "(el dashboard es derivado, nunca fuente)")

    def test_staged_incluye_data(self):
        # data/ddl_effect.json y data/ddl-dashboard.html entran via 'data'
        self._index('STAGED="data README.md index.html"')

    def test_no_push_sale_antes_de_git(self):
        """El bloque --no-push (log + exit 0) va antes del primer git add."""
        guard = self._index("modo --no-push")
        self.assertLess(guard, self._index("git add"),
                        "--no-push debe salir antes de tocar git")


class NightlyCacheSnapshotTest(unittest.TestCase):
    """coffe-0c8: snapshot diario del cache frágil de Claude ANTES de la regen
    (dashboard-cache.json es fuente única de claude-cli para sesiones cuyo
    jsonl ya no existe, y Claude Code lo trunca sin aviso — coffe-jg3)."""

    @classmethod
    def setUpClass(cls):
        cls.text = SCRIPT.read_text()

    def _index(self, needle):
        i = self.text.find(needle)
        self.assertGreaterEqual(i, 0, f"no está en el script: {needle}")
        return i

    def test_snapshot_antes_de_regen(self):
        self.assertLess(self._index("dashboard-cache.json"),
                        self._index("scripts/usage-tracker.py"),
                        "el snapshot del cache debe correr antes del tracker")

    def test_snapshot_fuera_de_git(self):
        """El cache pesa ~50MB: el destino NO puede ser el repo (data/ entra
        en STAGED). Vive junto a la fuente, en ~/.claude/cache-snapshots/."""
        self.assertNotIn("data/cache-snapshots", self.text)
        self.assertIn("$HOME/.claude/cache-snapshots", self.text)

    def test_snapshot_con_rotacion(self):
        """Rotación: conservar solo los 14 snapshots más recientes."""
        self.assertIn("tail -n +15", self.text)
        self.assertIn("cache-$(date +%Y-%m-%d).json", self.text)

    def test_snapshot_tolerante_a_fallo(self):
        """Si el cache no existe, el nightly sigue (no rompe el timer)."""
        block = self.text[self._index("dashboard-cache.json") - 500:
                          self._index("dashboard-cache.json") + 500]
        self.assertIn('cp -f "$CACHE"', block)


if __name__ == "__main__":
    unittest.main()

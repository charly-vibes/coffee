#!/usr/bin/env python3
"""test_nightly.py — contrato del job nocturno (coffe-tw6, 6.1).

Hermetic: parsea scripts/nightly-update.sh y verifica orden de pasos y el
guard de --no-push (sin correr los trackers reales — esos viven en el timer
systemd y tocan logs vivos). bash -n valida sintaxis.

El snapshot del cache frágil de Claude corre ANTES de la regen (coffe-0c8:
dashboard-cache.json es fuente única de claude-cli para sesiones cuyo jsonl
ya no existe, y Claude Code lo trunca sin aviso — coffe-jg3).
"""
import re
import subprocess
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
SCRIPT = REPO / "scripts" / "nightly-update.sh"


class NightlyContractTest(unittest.TestCase):
    """El orden importa: snapshot del cache antes del tracker, ddl tracker
    tras usage-tracker, viz tras tracker, y --no-push sale antes de tocar
    git; los derivados de data/ entran via STAGED."""

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

    def test_orden_de_pasos_del_nightly(self):
        """Cada paso va antes del que lo consume (en orden):
        - snapshot del cache de Claude antes del tracker (coffe-0c8),
        - ddl-effect-tracker tras usage-tracker (consume
          data/usage_report_v3.json),
        - viz-ddl tras ddl-effect-tracker (dashboard derivado, nunca fuente),
        - el guard --no-push (log + exit 0) antes del primer git add."""
        pares = [
            ("dashboard-cache.json", "scripts/usage-tracker.py",
             "el snapshot del cache debe correr antes del tracker"),
            ("scripts/usage-tracker.py", "scripts/ddl-effect-tracker.py",
             "ddl-effect-tracker debe correr tras usage-tracker "
             "(consume data/usage_report_v3.json)"),
            ("scripts/ddl-effect-tracker.py", "scripts/viz-ddl.py",
             "viz-ddl debe correr tras ddl-effect-tracker "
             "(el dashboard es derivado, nunca fuente)"),
            ("modo --no-push", "git add",
             "--no-push debe salir antes de tocar git"),
        ]
        for antes, despues, msg in pares:
            with self.subTest(paso=f"{antes} -> {despues}"):
                self.assertLess(self._index(antes), self._index(despues), msg)

    def test_staged_incluye_data(self):
        """data/ddl_effect.json y data/ddl-dashboard.html entran via 'data':
        la lista STAGED declara data entre los archivos staged."""
        self._index('STAGED="data README.md index.html"')
        staged = re.search(r'STAGED="([^"]*)"', self.text).group(1)
        self.assertIn("data", staged.split(),
                      f"STAGED sin data entre los staged: {staged!r}")

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

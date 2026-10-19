"""Guards del ledger de cargos reales — data/charges.json.

Epic coffe-a31: Sasha transcriptó las facturas de 5 proveedores el
2026-09-20. El dashboard (cash cost, FPA-013 real) va a consumir este
ledger; estos tests fijan su estructura y los totales IA para detectar
transcripciones rotas antes de que lleguen al dashboard.

Reglas (decisiones Sasha 2026-09-20):
- kinds IA: subscription | credits | refund (api_cycle = factura $0;
  storage queda FUERA del ledger — es gasto de storage, no de IA).
- refund puede ser negativo; ningún otro kind lo es.
"""

import json
import re
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
DATE_RE = re.compile(r"^[0-9]{4}-[0-9]{2}-[0-9]{2}$")

KINDS_IA = {"subscription", "credits", "refund", "api_cycle"}
PROVEEDORES = {"claude-cli", "codex", "amp", "gemini-cli", "openrouter"}

# Totales IA por proveedor (suma de amounts; api_cycle aporta $0).
# Facturas transcriptas 2026-09-20 + recargas openrouter 2026-10 (ledger
# data/charges.json es fuente de verdad; commit c74b914 añadió $360).
TOTALES_IA = {
    "claude-cli": 451.78,
    "codex": 40.0,
    "amp": 275.0,
    "gemini-cli": 119.94,
    "openrouter": 830.0,
}


class TestLedgerEstructura(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.ledger = json.loads(
            (REPO / "data" / "charges.json").read_text())

    def test_proveedores_esperados(self):
        """5 proveedores transcriptos, ni más ni menos."""
        self.assertEqual(PROVEEDORES, set(self.ledger["providers"]))

    def test_moneda_usd(self):
        self.assertEqual("USD", self.ledger["currency"])

    def test_entradas_completas(self):
        """Toda entrada tiene date ISO, amount numérico, kind y note
        (FPA-008: nunca vacío sin razón)."""
        for provider, entries in self.ledger["providers"].items():
            self.assertTrue(entries, f"{provider}: sin entradas")
            for i, entry in enumerate(entries):
                self.assertTrue(
                    DATE_RE.match(str(entry.get("date", ""))),
                    f"{provider}[{i}]: date inválida: {entry.get('date')}",
                )
                self.assertIsInstance(
                    entry.get("amount"), (int, float),
                    f"{provider}[{i}]: amount no numérico",
                )
                self.assertTrue(entry.get("kind"), f"{provider}[{i}]: sin kind")
                self.assertTrue(entry.get("note"), f"{provider}[{i}]: sin note")

    def test_kinds_ia_solamente(self):
        """Solo kinds IA; storage ni otros kinds inventados (decisión
        Sasha: Google One 100 GB queda fuera del cash de IA)."""
        for provider, entries in self.ledger["providers"].items():
            for entry in entries:
                self.assertIn(
                    entry["kind"], KINDS_IA,
                    f"{provider}: kind fuera del vocabulario IA: "
                    f"{entry['kind']}",
                )

    def test_refund_negativo_unico_kind_negativo(self):
        for provider, entries in self.ledger["providers"].items():
            for entry in entries:
                if entry["amount"] < 0:
                    self.assertEqual(
                        "refund", entry["kind"],
                        f"{provider}: monto negativo con kind "
                        f"{entry['kind']}",
                    )
                if entry["kind"] == "refund":
                    self.assertLess(entry["amount"], 0)


class TestLedgerTotales(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.ledger = json.loads(
            (REPO / "data" / "charges.json").read_text())

    def _ia_total(self, provider):
        return round(sum(
            e["amount"] for e in self.ledger["providers"][provider]
        ), 2)

    def test_totales_por_proveedor(self):
        """Sumas IA verificadas contra las facturas transcriptas."""
        for provider, esperado in TOTALES_IA.items():
            self.assertEqual(
                esperado, self._ia_total(provider),
                f"{provider}: total IA no coincide con las facturas",
            )

    def test_total_ia_global(self):
        total = round(sum(
            self._ia_total(p) for p in self.ledger["providers"]
        ), 2)
        self.assertEqual(1716.72, total)

    def test_claude_api_cycle_aporta_cero(self):
        """Las facturas de ciclo API $0 no cambian el cash."""
        claude = self.ledger["providers"]["claude-cli"]
        con_ciclos = round(sum(e["amount"] for e in claude), 2)
        sin_ciclos = round(sum(
            e["amount"] for e in claude if e["kind"] != "api_cycle"), 2)
        self.assertEqual(sin_ciclos, con_ciclos)
        self.assertTrue(any(e["kind"] == "api_cycle" for e in claude))


if __name__ == "__main__":
    unittest.main()

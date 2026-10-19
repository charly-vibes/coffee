#!/usr/bin/env python3
"""
test_tracker_charges.py — coffe-a31.2: el tracker consume el ledger de
cargos reales (data/charges.json) y emite las series *reported* que FPA-082
necesita para reconciliar (CRG-F1).

Emisiones nuevas (aditivas al reporte):
- top-level `charges_real_by_month`: {provider: {YYYY-MM: amount}} — solo
  kinds IA (subscription|credits|refund; api_cycle aporta $0, storage fuera
  del ledger), provenance *reported* (facturas, FPA-003).
- metadata.charges_source: ruta del ledger consumido.
- metadata.charges_total_real: suma IA del ledger (guard contra el total
  transcripto: $1,356.72).
- pay_per_token_charges mensual pasa a *reported* cuando el ledger tiene
  cargas p2p del mes (créditos/reembolsos; FPA-013 deja de ser assumed);
  sigue siendo el estimado assumed si no hay cargas en el mes.
- `charges_reconciliation_by_month`: por tool/mes, cash real del ledger vs
  fee implícito del calendario vs efectivo estimado — insumo de la
  reconciliación FPA-082 (a31.3); los fees implícitos NUNCA son cash cost.
- Divergencia cost_total_real: la suma mensual de cost_real incluyó fees
  implícitos del calendario que metadata (sumatoria horaria) no contaba
  (611.62 vs 381.62); metadata.cost_total_real pasa a ser la suma mensual
  (una sola cuenta) y `cost_real_by_month`/`cost_real_total_tracker` separan
  la parte tracker (p2p real del tracker) de los fees implícitos, que viven
  en subscription_fees_by_month para reconciliación/plan economy.

Los cargos del ledger son dinero pagado (cash): NO se suman al coste
efectivo del tracker ni viceversa (FPA-002, decisión Sasha 2026-09-20).
"""

import importlib.util
import json
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
TRACKER_PATH = REPO / "scripts" / "usage-tracker.py"
LEDGER_PATH = REPO / "data" / "charges.json"

# Totales IA transcriptos (tests/test_charges.py — fuente de verdad;
# recargas openrouter oct 2026 elevan el total de 1356.72 a 1716.72)
TOTAL_IA = 1716.72


def load_tracker():
    spec = importlib.util.spec_from_file_location("usage_tracker_charges",
                                                  TRACKER_PATH)
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


ut = load_tracker()


def row(ts="2026-05-10T14:23:00+00:00", project="charly-coffee",
        tool="claude-cli", model_raw="claude-sonnet-4-6", **kw):
    """Row sintético con el shape exacto que producen los extractores."""
    base = {
        "source": "test", "tool": tool, "model_raw": model_raw,
        "model_family": "claude", "model_version": "sonnet-4.6",
        "project": project, "timestamp": ts,
        "hour": ut.hour_key(ut.parse_ts(ts)),
        "input_tokens": 1000, "output_tokens": 500,
        "cache_read_tokens": 0, "cache_write_tokens": 0,
        "cost_effective": 0.01,
    }
    base.update(kw)
    return base


class TestCargarLedger(unittest.TestCase):
    """Carga y guardas del ledger desde el tracker."""

    def test_ledger_existe(self):
        self.assertTrue(LEDGER_PATH.exists(), "falta data/charges.json")

    def test_cargar_charges_kinds_ia(self):
        """cargar_charges(): solo kinds IA entran; api_cycle aporta $0."""
        by_month, total, _ = ut.cargar_charges(LEDGER_PATH)
        # guardas estructurales
        self.assertIsInstance(by_month, dict)
        esperados = {"claude-cli", "codex", "amp", "gemini-cli", "openrouter"}
        self.assertEqual(esperados, set(by_month))
        # total IA = $1,356.72 (los 2 api_cycle $0 no cambian nada)
        self.assertAlmostEqual(TOTAL_IA, round(total, 2))

    def test_cargar_charges_total_por_proveedor(self):
        by_month, total, _ = ut.cargar_charges(LEDGER_PATH)
        por_prov = {
            p: round(sum(mes.values()), 2)
            for p, mes in by_month.items()
        }
        self.assertAlmostEqual(451.78, por_prov["claude-cli"])
        self.assertAlmostEqual(40.0, por_prov["codex"])
        self.assertAlmostEqual(275.0, por_prov["amp"])
        self.assertAlmostEqual(119.94, por_prov["gemini-cli"])
        self.assertAlmostEqual(830.0, por_prov["openrouter"])

    def test_cargar_charges_meses_clave_yyyy_mm(self):
        """Las claves de mes salen de la fecha del cargo (YYYY-MM)."""
        by_month, _, _ = ut.cargar_charges(LEDGER_PATH)
        claude = by_month["claude-cli"]
        self.assertIn("2026-03", claude)   # Max $100 del 19-mar
        self.assertIn("2026-02", claude)   # créditos + refund −28.22
        # refund del 2026-02-19: −28.22 descuenta del cash del mes
        febrero = round(claude["2026-02"], 2)
        # 10+20+10 (créditos feb) − 28.22 (refund) = 11.78
        self.assertAlmostEqual(11.78, febrero)

    def test_ledger_invalido_falla_loud(self):
        """Ledger roto (kind fuera del vocabulario IA) → error, no silencio."""
        import tempfile
        roto = {"currency": "USD", "providers": {
            "x": [{"date": "2026-01-01", "amount": 5,
                   "kind": "storage", "note": "no-IA"}]}}
        with tempfile.NamedTemporaryFile("w", suffix=".json",
                                         delete=False) as f:
            json.dump(roto, f)
            path = f.name
        with self.assertRaises(ValueError):
            ut.cargar_charges(path)


class TestEmisionReporte(unittest.TestCase):
    """Emisiones nuevas en aggregate() — aditivas, con datos sintéticos."""

    @classmethod
    def setUpClass(cls):
        rows = [
            row(ts="2026-03-25T10:00:00+00:00", tool="claude-cli"),
            row(ts="2026-05-10T14:23:00+00:00", tool="claude-cli"),
            row(ts="2026-06-02T09:00:00+00:00", tool="codex"),
        ]
        cls.report = ut.aggregate(rows, [])

    def test_charges_real_by_month_presente(self):
        self.assertIn("charges_real_by_month", self.report)

    def test_charges_real_by_month_solo_meses_con_cargos(self):
        crm = self.report["charges_real_by_month"]
        # meses con facturas: ene (créditos claude), feb (créditos+refund),
        # mar (claude Max $100 + gemini), abr (claude+codex+gemini),
        # may (claude+codex+gemini+amp)… jun NO tiene facturas
        self.assertNotIn("2026-06", crm.get("codex", {}))
        self.assertNotIn("2026-03", crm.get("codex", {}))
        # claude-cli marzo = Max $100 (factura del 19-mar)
        self.assertAlmostEqual(100.0, crm["claude-cli"]["2026-03"])

    def test_charges_source_y_total_en_metadata(self):
        m = self.report["metadata"]
        self.assertEqual("data/charges.json", m["charges_source"])
        self.assertAlmostEqual(TOTAL_IA, round(m["charges_total_real"], 2))
        self.assertIn("reported", m["charges_provenance"])

    def test_charges_no_contaminan_coste_efectivo(self):
        """FPA-002: cash (ledger) y efectivo (tracker) jamás se suman."""
        m = self.report["metadata"]
        eff = round(sum(mo["cost_effective"] for mo in
                        self.report["monthly"].values()), 2)
        self.assertAlmostEqual(0.03, eff)  # 3 rows × 0.01, sin cargos
        # y el total IA del ledger no apareció dentro del efectivo
        self.assertLess(eff, TOTAL_IA)

    def test_charges_reconciliation_por_tool(self):
        """Insumo FPA-082: cash real vs fee implícito vs efectivo, por tool."""
        rec = self.report["charges_reconciliation_by_month"]
        marzo = rec["2026-03"]["claude-cli"]
        # cash real del ledger (factura 19-mar), provenance reported
        self.assertAlmostEqual(100.0, marzo["charges_real"])
        self.assertEqual("reported", marzo["charges_provenance"])
        # fee implícito del calendario corregido (Max $100 del 19-mar)
        self.assertAlmostEqual(100.0, marzo["subscription_fee_implicit"])
        # el tracker estimó 0.01 efectivo ese mes (row sintético)
        self.assertAlmostEqual(0.01, marzo["cost_effective"])
        # jun: sin facturas y sin suscripción activa (calendario corregido)
        # → real $0 vs implícito $0: el caso FPA-082 que motivó el epic,
        # ahora resuelto y visible con ambos montos
        junio = rec["2026-06"]["codex"]
        self.assertAlmostEqual(0.0, junio["charges_real"])
        self.assertAlmostEqual(0.0, junio["subscription_fee_implicit"])
        # feb claude: créditos+refund reales ($11.78) vs fee implícito $0 —
        # el ledger captura gasto p2p que el calendario no ve (el mes aparece
        # en la reconciliación aunque no haya interacciones del tracker)
        feb = rec["2026-02"]["claude-cli"]
        self.assertAlmostEqual(11.78, feb["charges_real"])
        self.assertAlmostEqual(0.0, feb["subscription_fee_implicit"])
        # jul-2026: solo recargas openrouter (sin interacciones sintéticas) —
        # el mes reconcilia igual, con el fee del calendario (sin suscripción)
        julio = rec["2026-07"]["openrouter"]
        self.assertAlmostEqual(380.0, julio["charges_real"])
        # proveedor sin suscripción ni facturas en el mes: igual aparece
        self.assertIn("openrouter", rec["2026-03"])
        self.assertAlmostEqual(0.0, rec["2026-03"]["openrouter"]["charges_real"])


class TestPayPerTokenReported(unittest.TestCase):
    """FPA-013: con ledger, los créditos ene-feb de claude (y amp/
    openrouter) son cargas pay-per-token REALES → provenance reported."""

    def test_claude_creditos_ene_feb_reported(self):
        rep = ut.aggregate([row(ts="2026-01-15T10:00:00+00:00")], [])
        enero = rep["monthly"]["2026-01"]
        # p2p real de enero (ledger): claude 10+10=$20 + amp $80 = $100
        # — FPA-013 deja de ser assumed/cero
        self.assertAlmostEqual(100.0, enero["pay_per_token_charges"])
        self.assertEqual("reported", enero["pay_per_token_provenance"])
        # y en metadata la nota ya no dice "assumed" a secas
        self.assertIn("reported", rep["metadata"]["pay_per_token_note"])

    def test_mes_sin_cargos_ledger_sigue_assumed(self):
        """Sin cargas p2p del ledger en el mes → estimado del tracker
        (assumed), null no: la cifra existe (FPA-008)."""
        rep = ut.aggregate([row(ts="2026-09-10T10:00:00+00:00")], [])
        sep = rep["monthly"]["2026-09"]
        # sep tiene recargas openrouter reales ($20 + $30 + $60) → reported
        self.assertAlmostEqual(110.0, sep["pay_per_token_charges"])
        self.assertEqual("reported", sep["pay_per_token_provenance"])
        # mes sin cargas p2p en el ledger → estimado del tracker (assumed),
        # null no: la cifra existe (FPA-008)
        rep2 = ut.aggregate([row(ts="2026-04-10T10:00:00+00:00",
                                 tool="codex", model_raw="gpt-5.4",
                                 model_family="codex",
                                 model_version="gpt-5.4")], [])
        abril = rep2["monthly"]["2026-04"]
        self.assertAlmostEqual(0.0, abril["pay_per_token_charges"])
        self.assertEqual("assumed", abril["pay_per_token_provenance"])

    def test_mes_mixto_fee_implicit_no_infla_ppt(self):
        """May 2026 tiene suscripción (fee implícito $100 claude + $20 codex
        + $19.99 gemini) Y cargas p2p reales (amp $60): el ppt reportado es
        solo el ledger, sin fees implícitos mezclados."""
        rep = ut.aggregate([row(ts="2026-05-10T14:23:00+00:00",
                               tool="amp", model_raw="claude-opus-4.7",
                               model_family="claude",
                               model_version="opus-4.7")], [])
        mayo = rep["monthly"]["2026-05"]
        amp = rep["charges_real_by_month"].get("amp", {}).get("2026-05", 0.0)
        self.assertAlmostEqual(10.0, round(amp, 2))  # manual 1,000 credits (20-may)
        self.assertAlmostEqual(10.0, round(mayo["pay_per_token_charges"], 2))
        self.assertEqual("reported", mayo["pay_per_token_provenance"])
        # los fees implícitos viven aparte: en la serie mensual solo aplican
        # a tools con interacciones (semántica calc_subscription_fees), y el
        # desglose por tool/mes completo vive en la reconciliación FPA-082
        rec_mayo = rep["charges_reconciliation_by_month"]["2026-05"]
        self.assertAlmostEqual(20.0, rec_mayo["claude-cli"]["subscription_fee_implicit"])
        self.assertAlmostEqual(20.0, rec_mayo["codex"]["subscription_fee_implicit"])
        self.assertAlmostEqual(19.99, rec_mayo["gemini-cli"]["subscription_fee_implicit"])
        self.assertAlmostEqual(59.99, rec_mayo["gemini-cli"]["fee_month_total"])


class TestDivergenciaCostTotalReal(unittest.TestCase):
    """Divergencia 611.62 vs 381.62: metadata sumaba solo buckets horarios
    (sin fees del calendario), la suma mensual los incluye. Unificación:
    metadata.cost_total_real = suma mensual (una sola cuenta), y los fees
    implícitos quedan identificados aparte (subscription_fees_by_month)."""

    def test_metadata_igual_suma_mensual(self):
        rep = ut.aggregate([row(ts="2026-05-10T14:23:00+00:00")], [])
        suma_mensual = round(sum(mo["cost_real"] for mo in
                                 rep["monthly"].values()), 2)
        self.assertAlmostEqual(suma_mensual,
                               rep["metadata"]["cost_total_real"], places=2)

    def test_desglose_tracker_vs_implicito(self):
        """La parte tracker (p2p real del tracker) queda separada de los fees
        implícitos del calendario — jamás contados dos veces."""
        rep = ut.aggregate([row(ts="2026-05-10T14:23:00+00:00"),
                            row(ts="2026-07-01T10:00:00+00:00")], [])
        m = rep["metadata"]
        tracker = round(m["cost_real_total_tracker"], 2)
        implicito = round(m["subscription_fees"], 2)
        total = round(m["cost_total_real"], 2)
        # sin doble conteo: tracker + implícito = total
        self.assertAlmostEqual(total, round(tracker + implicito, 2), places=2)
        # por mes, misma invariante
        for mes, mo in rep["monthly"].items():
            self.assertAlmostEqual(
                round(mo["cost_real"], 2),
                round(mo["cost_real_tracker"] + mo["subscription_fees"], 2),
                places=2, msg=mes)

    def test_dataset_commiteado_consistente(self):
        """El dataset v3 commiteado queda con metadata ≠ suma mensual (el
        refresh es CRG-F3/coffe-a31.4), pero las claves nuevas ya existen
        en el schema para cuando se regenere."""
        schema = json.loads((REPO / "specs" /
                             "usage-report-v3.schema.json").read_text())
        props_mes = schema["definitions"]["monthlyBucket"]["properties"]
        self.assertIn("pay_per_token_provenance", props_mes)
        self.assertIn("cost_real_tracker", props_mes)
        self.assertIn("charges_real_by_month", schema["properties"])
        self.assertIn("charges_reconciliation_by_month", schema["properties"])


if __name__ == "__main__":
    unittest.main()

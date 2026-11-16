# Tasks: fix-cost-reconciliation

## 1. Reconciliación diaria/mensual

- [ ] 1.1 Test: suma de `daily[].cost_real` = `cost_real_total_tracker`
      (sin fees) y `monthly[].cost_real` = tracker + fee.
- [ ] 1.2 Test abril/mayo: daily ya no incluye `fee/30`; monthly conserva el
      fee una vez.
- [ ] 1.3 Implementar: quitar el prorrateo de fees de los buckets diarios;
      documentar la asimetría.

## 2. Fee asumido vs reportado

- [ ] 2.1 Test mayo 2026: ledger $100 vs config $20 ⇒
      `subscription_fee_reported=100`, `subscription_fee_source="ledger"`,
      `cost_real` usa 100.
- [ ] 2.2 Test sin ledger: source=`config`, fee asumido, provenance assumed.
- [ ] 2.3 Emitir los tres campos por mes y actualizar schema/goldens.

## 3. Alertas y economía

- [ ] 3.1 Test `verify-plan`: sin ledger ⇒ n/a con razón; con ledger ⇒
      múltiplo 9.0× y evidencia de ambas cuotas.
- [ ] 3.2 Implementar la selección ledger/config en `viz-fpa.py`.
- [ ] 3.3 Economía de suscripción: distinguir fee reportado vs asumido.

## 4. Gasto diario y alcance del cash

- [ ] 4.1 Test: la vista declara que `cost_real` no incluye fees y que el
      cash se concilia a nivel mensual.
- [ ] 4.2 Emitir `charges_total_real_period`; documentar ambos totales.
- [ ] 4.3 Actualizar README y regenerar dashboards.

## 5. Verificación

- [ ] 5.1 Suite de tracker, fpa, charges y viz.
- [ ] 5.2 Regenerar `data/usage_report_v3.json`, `data/fpa-dashboard.html`
      y `data/fpa-guide.html`; `--check-docs` verde.
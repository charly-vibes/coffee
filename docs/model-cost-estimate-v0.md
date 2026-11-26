# Estimador v0 — coste por unidad aceptada (spike coffe-dnx.10)

Ventana: 2026-09-01 → fin del reporte (tz -3) · unidad: `bead` · sesiones: 2053 · gasto real: $1375.63

Cobertura: 50/105 proyectos, 94.3% del gasto. Créditos: 2311/4354 eventos (2311 unidades distintas). Regla: uno-a-uno: cada evento a la sesión de inicio más cercano. Base: cost = cost_real; cost_imputed = cost_effective (solo notional).

Carril `real` = pay-per-token (dinero del ledger). Carril `notional` = suscripción: `cost` es el dinero real desembolsado, `imputado` el valor equivalente por token. **No se comparan entre sí.**

## Veredicto (solo filas comparables)

- **z-ai/glm-5.3-flash** (real): $0.09/unidad (estricto $0.06, n=464; IC90 0.08–0.11), yield LB95 43%, n=1537.

## Tabla completa (n ≥ 10; el resto queda en el JSON)

| modelo | carril | n | yield (LB95) | p50 $/unidad | estricto (n) | IC90 | n prod | p99/p50 | ambigua (unidades) | ¿comparable? |
|---|---|---|---|---|---|---|---|---|---|---|
| `gpt-6-luna` | notional | 17 | 36% | 1.61 | 0.72 (6) | 0.84–5.55 | 10 | 15.7× | 8 (48% de las u.) | ci_ancho(1.46) |
| `gpt-6.1-sol` | notional | 43 | 26% | 0.94 | 0.79 (9) | 0.60–2.19 | 17 | 5.6× | 19 (69% de las u.) | ci_ancho(0.84) |
| `kimi-for-coding` | real | 17 | 17% | 1.41 | 0.69 (2) | 0.50–2.95 | 6 | 5.7× | 6 (80% de las u.) | n_productivas<10 |
| `qwen3.8-flash` | real | 25 | 9% | 0.89 | 0.06 (3) | 0.06–5.40 | 5 | 101.0× | 9 (38% de las u.) | n_productivas<10 |
| `MiniMax-M2.7` | real | 12 | 25% | 0.68 | 0.81 (5) | 0.61–0.92 | 6 | 2.2× | 3 (25% de las u.) | n_productivas<10 |
| `deepseek-v4.1-flash-1017` | real | 40 | 24% | 0.32 | 0.07 (6) | 0.24–0.67 | 15 | 6.9× | 30 (52% de las u.) | ci_ancho(0.68) |
| `openrouter/free` | real | 24 | 15% | 0.27 | 0.27 (7) | 0.08–4.19 | 7 | 43.0× | 5 (0% de las u.) | n_productivas<10 |
| `MiniMax-M3.1-Flash` | real | 15 | 36% | 0.13 | 0.07 (4) | 0.05–0.19 | 9 | 2.0× | 9 (25% de las u.) | n_productivas<10 |
| `glm-5.3-flash` | real | 57 | 50% | 0.11 | 0.06 (14) | 0.06–0.26 | 36 | 25.2× | 33 (71% de las u.) | ci_ancho(0.95) |
| `z-ai/glm-5.3-flash` | real | 1537 | 43% | 0.09 | 0.06 (464) | 0.08–0.11 | 706 | 13.8× | 542 (38% de las u.) | sí |
| `moonshotai/kimi-k2.5` | real | 12 | 32% | 0.01 | 0.01 (7) | 0.01–0.02 | 7 | 2.5× | 0 (0% de las u.) | n_productivas<10 |
| `mistralai/mistral-small-3.2-24b-instruct` | real | 10 | 11% | 0.00 | 0.00 (3) | 0.00–0.01 | 3 | 69.0× | 0 (0% de las u.) | n_productivas<10 |

16 modelos con n<10 omitidos de la tabla (ver JSON).

Sin atribuir (proyecto sin repo local): 135 sesiones, $67.86.
Excluido `mixed` (sesiones con varios modelos): 52 sesiones, $132.29.

Repos con export de beads no fiable (excluidos): `sk-titi` (stale), `sk-poco` (stale)

Umbrales provisionales: `{"provisional": true, "min_productive": 10, "max_ci_halfwidth_rel": 0.25, "stale_export_days": 30, "max_window_s": 3600, "slack_s": 600, "bootstrap_B": 2000, "bootstrap_seed": 7}`

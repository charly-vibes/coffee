## 1. Tracker

Tickets: coffe-7gg (1.1) · coffe-sht (1.2) · coffe-5uu (1.3) · coffe-gp8 (1.4) ·
coffe-64z (1.5) · coffe-2xj (1.6+1.7) · coffe-kup (1.8+1.9, bloqueado por todos
los anteriores + coffe-9w0 — suite pre-existente en rojo).

- [x] 1.1 Reducer puro `reduce_context(entries) -> metrics` (no toca disco, mismo
      estilo que `aggregate()`): acumula `turns`, pico/final de contexto por
      **segmento de compactación** (reset del acumulador en cada
      `type=="compaction"`), atribución de chars por fuente (`asst_text`,
      `asst_thinking`, `asst_args`, tool results por `toolName`), `big_results`
      (> `context_thresholds.big_result_chars` de config) — tolera mensajes
      assistant sin `usage` (turnos abortados) contándolos en loss visible
- [ ] 1.2 Atribución por modelo: `models_turns` (turnos por modelo, incluye
      `model_change` mid-sesión) + `model` dominante; `month` desde el filename
      del archivo de sesión (documentado)
- [ ] 1.3 Coste por sesión: sumar `cost.in/out/cacheRead/cacheWrite` por sesión;
      `context_source` (pi vs claude-cli); coste ausente → `null` con razón
      (patrón "sin telemetría", no 0 fake)
- [ ] 1.4 Detección de patologías: sesión con bloque assistant >
      `context_thresholds.pathological_chars` (config) → excluida de percentiles,
      contada en `meta.excluded_pathological` y listable
- [ ] 1.5 Corrección de semántica: docstring de `extract_pi_sessions` ("sin señal
      en pi (compactions)" está desactualizado — las entradas existen desde
      ~may-2026), `n_compactions` real para Pi y `sessions.total_compactions`
      coherente
- [ ] 1.6 Agregados `monthly_model[]` por mes × modelo dominante: p50/p90 del pico,
      % > `context_thresholds.big_session_tokens` (config), tok/turno por fuente,
      spend por componente — sobre el set limpio (`turns > 0`, sin patológicas),
      stubs en `meta.stubs`
- [ ] 1.7 `meta`: `chars_per_token` (assumed, dirección de bias), `validation_ratio_median`,
      umbrales usados
- [ ] 1.8 Tests: `tests/test_tracker_context.py` con líneas JSONL sintéticas
      (sesión normal, con compaction multi-segmento, con `model_change`,
      patológica, claude-cli, sin usage en turnos, multi-mes, stub) + regenerar
      golden (`REGEN_GOLDEN=1`, revisar diff)
- [ ] 1.9 `specs/usage-report-v3.schema.json`: requerir la sección nueva
      (incluyendo nullability del coste) y regenerar/validar

## 2. Dashboard

Tickets: coffe-0tw (2.1+2.2) · coffe-opo (2.3+2.4) · coffe-dv6 (2.5+2.7) ·
coffe-r3c (2.6+2.8) · coffe-ruk (smoke + --check-docs) — cadena secuencial.

- [ ] 2.1 `scripts/viz-ctx.py`: lee `usage_report_v3.json` → pre-calcular en Python
      (histograma, shares, pendientes OLS por modelo, tablas mes × modelo)
- [ ] 2.2 Vista *Distribución*: histograma del pico + banda de percentiles, tags
      *assumed* en el factor chars/4, conteo de excluidas junto al título
- [ ] 2.3 Vista *Composición*: share por fuente global y en sesiones grandes (SVG
      stacked inline, tooltips, leyenda completa en ambas vistas)
- [ ] 2.4 Vista *Tiempo*: mes × modelo (pico mediano, % > umbral, tok/turno) con
      meses parciales marcados como fpa-dashboard
- [ ] 2.5 Vista *Coste*: spend por modelo, split in/out/cache, spend mensual,
      $/sesión; sesiones claude-cli separadas con provenancia *subscription* y
      sin suma combinada; coste null con razón visible
- [ ] 2.6 Vista *Ley de crecimiento*: dispersivo pico vs turnos por modelo con
      pendiente OLS pre-calculada, comparable entre eras
- [ ] 2.7 Vista *Patologías*: outliers listados con tamaño, modelo y razón
- [ ] 2.8 Cross-cutting: deep-link por hash, export CSV/SVG, tema compartido,
      smoke en Chromium vía `tests/test_viz.py` (0 pageerrors, charts renderizan)

## 3. Cierre

Ticket: coffe-v5f (3.3+3.4; 3.5 es la mecánica de cierre de cada sesión).

- [ ] 3.1 `python3 tests/test_tracker.py` y `python3 tests/test_viz.py` verdes
- [ ] 3.2 `--check-docs` de viz-ctx.py: consistencia README ↔ JSON
- [ ] 3.3 README: documentar emisión nueva + dashboard (tabla de scripts y de data/),
      corrección de la nota "sin señal en pi (compactions)", dirección de bias del
      estimador
- [ ] 3.4 `nightly-update.sh`: paso de regeneración del dashboard de contexto,
      midiendo duración extra del walk (1.4GB) — si pesa, `--since`
- [ ] 3.5 `bd close` de los tickets y push

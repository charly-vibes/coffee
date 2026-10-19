# add-ddl-effectiveness-analysis — Tasks

Pipeline por ticket: `TDD → ro5u → fix → commit → next`.

Tickets: coffe-51p (1.1) · coffe-y0y (1.2) · coffe-409 (1.3) · coffe-nwn (2.1) ·
coffe-7rl (2.2) · coffe-md0 (2.3) · coffe-wru (2.4) · coffe-awb (3.1) ·
coffe-ygo (3.2) · coffe-pp0 (3.3) · coffe-w02 (4.1) · coffe-ktu (4.2) ·
coffe-dek (4.3) · coffe-s8j (4.4) · coffe-nea (4.5, depende de coffe-w02) ·
coffe-31l (5.1) · coffe-het (5.2+5.3, depende de coffe-470) · coffe-470 (5.4) ·
coffe-46h (5.5) · coffe-8yf (5.6) · coffe-x1s (5.7) · coffe-tw6 (6.1) ·
coffe-6io (6.2) · coffe-zx5 (6.3+6.4) · coffe-q9z (6.5, bloqueado por
coffe-nea + coffe-pp0).

## 1. Adopción y dosis (insumos locales, sin red)

- [ ] 1.1 (coffe-51p) TDD: reducer puro `extract_adoption(git_log, sessions, registry)` —
  primer commit de `.ddl/`, primer uso por herramienta con cwd match vía
  `repo_aliases`, `null` con provenance cuando no hay señal (sin imputación)
- [ ] 1.2 (coffe-y0y) TDD: reducer puro `compute_dose(ddl_status, canaries, session_uses,
  registry)` — fracción sanas/13, jerarquía de fuentes, `dose_detail` por nombre,
  provenance declara fuente efectiva
- [ ] 1.3 (coffe-409) Clasificación control/tratamiento por ausencia de `.ddl/` + topic
  (nunca por dosis 0) + outlier marcado para `charly-dulce-de-leche`

## 2. Métricas de sesión y git

- [ ] 2.1 (coffe-nwn) TDD: `recovery_metrics(sessions)` — p50 de interacciones
  error→éxito mismo tool sobre el walk de JSONLs de sesiones Pi (el reporte de
  usage no tiene filas por evento), censura por fin de sesión en
  `recovery_censored_rate`, n visible
- [ ] 2.2 (coffe-7rl) TDD: `commit_latency(session_ts, commits)` — latencia sesión→commit
  con heurística documentada (bias declarado)
- [ ] 2.3 (coffe-md0) TDD: proxies git — `revert_ratio`, `fix_ratio`, `test_commit_ratio`
  (parse de conventional commits; fixtures sintéticos)
- [ ] 2.4 (coffe-wru) `errors_api_per_100` + clases de terapia por 100 interacciones,
  denominador siempre en el row (cache terapia con TTL)

## 3. Capa CI (gh api)

- [ ] 3.1 (coffe-awb) TDD: cliente gh con cache incremental `data/cache/gh_runs/<repo>.json`
  (`fetched_until`, refetch solo lo nuevo, TTL semanal)
- [ ] 3.2 (coffe-ygo) TDD: `ci_fail_rate` mensual excluyendo dependabot; sin gh/token → null
  con provenance missing (degradación, nunca 0)
- [ ] 3.3 (coffe-pp0) Autenticación: `GH_TOKEN` env o `gh auth token`; error explícito en
  metadata cuando no hay credencial

## 4. Panel y estimadores

- [ ] 4.1 (coffe-w02) TDD: ensamblado del panel repo × mes (`monthly[]`), meses sin señal
  afuera del array, provenance en toda cifra (reported/derived/cached/missing)
- [ ] 4.2 (coffe-ktu) TDD: ITS pre/post con media móvil trimestral + IC bootstrap (seed fija)
- [ ] 4.3 (coffe-dek) TDD: DiD 2×2 contra control + regla dura n < 3 no graficada
- [ ] 4.4 (coffe-s8j) TDD: dosis-respuesta (Spearman) con n por punto
- [ ] 4.5 (coffe-nea) `specs/ddl-effect.schema.json` + `--validate` (jsonschema opcional)

## 5. Dashboard

- [ ] 5.1 (coffe-31l) `scripts/viz-ddl.py` — esqueleto: tema compartido, tabs + deep-link
  hash, export CSV/SVG, tags de provenancia, `--validate`
- [ ] 5.2+5.3 (coffe-het) Vista línea de adopción (timeline por repo, fechas null visibles)
- [ ] 5.3 Vista dosis-respuesta (scatter con n por punto, celdas n<3 ocultas)
- [ ] 5.4 (coffe-470) Vista pre/post + DiD con IC bootstrap
- [ ] 5.5 (coffe-46h) Vistas lazo de feedback, errores y calidad (4 proxies, sin score)
- [ ] 5.6 (coffe-8yf) Banda fija de disclosure de confusores (no-colapsable)
- [ ] 5.7 (coffe-x1s) Smoke Chromium en `tests/test_viz.py` (0 pageerrors, charts render)

## 6. Integración y docs

- [ ] 6.1 (coffe-tw6) `nightly-update.sh`: tracker tras usage-tracker, viz tras tracker;
  `--no-push` testeable
- [ ] 6.2 (coffe-6io) Análisis #21 en `docs/fpa-analyses-guide.md` (siguiente
  sección numerada de la guía; el "19 análisis" del README ya estaba stale —
  bug pre-existente, coffe-4j9) (4 niveles, español) + grounding en
  `viz_fpa_guide.py` (`--check`)
- [ ] 6.3+6.4 (coffe-zx5) README: layout de `data/ddl_effect.json`, cache gh/terapia, gaps
- [ ] 6.5 (coffe-q9z) Regenerar `data/ddl_effect.json` + `data/ddl-dashboard.html` con datos
  reales, revisar cifras contra insumos antes de commitear

# add-ddl-effectiveness-analysis

## Why

La flota charly-vibes corre sobre el toolset gestionado por `dulce-de-leche` (ddl):
13 herramientas (wai, testaruda, turu, dont, ah, pretender, vampiro, incitaciones,
bd, openspec, specodelic, funkalization, wild) con `ddl init/status/migrate/upgrade`.
Ese toolset tiene una tesis implícita — *instalar y mantener todas las herramientas
mejora la calidad del código y acorta el lazo de feedback* — que hoy nadie midió:
el reporte de coffee captura uso (interacciones, tokens, errores de API por sesión),
pero nada cruza adopción de ddl con outcomes de ingeniería.

La pregunta es escéptica por diseño: cada herramienta es overhead de instalación,
config y mantenimiento. Si el análisis no encuentra señal, eso también es un
resultado accionable (podar el toolset). Si la encuentra, sirve para priorizar
qué herramientas carrear y con qué dosis.

Datos disponibles que hoy nadie junta:
- `usage_report_v3.json`: sesiones, interacciones, `n_errors` por sesión, skills,
  por proyecto (ej. `charly-dulce-de-leche`: 4,566 interacciones).
- `terapia errors`: taxonomía de errores de Pi sessions más rica que el error de API.
- Git de cada repo de la flota: fecha de `.ddl/` (adopción), commits, reverts,
  ratio fix/test vs feat.
- `gh api` (GitHub Actions): tasa de fallo de CI por repo y por mes — **incluida**
  en alcance (decisión 2026-11-30), con cache para no reventar rate limits.
- `ddl status` + registry (`MANAGED-TOOLS`): dosis de cobertura por repo.

## What Changes

1. **Capability nueva `ddl-effect`:** `scripts/ddl-effect-tracker.py` produce
   `data/ddl_effect.json` (schema: `specs/ddl-effect.schema.json`), con una fila
   repo × mes y tres capas de fuente:
   - **Adopción:** fecha de primer commit de `.ddl/` por repo, primer uso de cada
     herramienta gestionada en las sesiones de ese repo (cwd match), dosis =
     fracción de las 13 herramientas presentes y sanas — **como serie temporal**:
     `dose(month)` derivada de fechas de adopción y canarios; `ddl status` es solo
     el snapshot corriente que valida la derivación.
   - **Lazo de feedback:** (a) tiempo de recuperación mediano — interacciones entre
     un error y la siguiente acción exitosa del mismo tipo dentro de la sesión
     (fuente: **walk de JSONLs de sesiones Pi**, mismo patrón que `context_metrics` —
     el reporte de usage solo tiene agregados por sesión, sin eventos; `terapia`
     aporta las clases de error, no las secuencias); (b) latencia sesión→commit;
     (c) tasa de fallo de CI por 100 runs (`gh api`, cache en `data/cache/gh_runs/`).
   - **Errores:** `n_errors`/sesión (del reporte), clases de error de `terapia`
     por 100 interacciones, fallos de CI.
   - **Proxies de calidad** (todos los que propuso la revisión, decisión 2026-11-30):
     ratio de commits revert, ratio fix-commit/post-release, ratio commits de test,
     hallazgos de `ah check` por repo a lo largo del tiempo. Meses con denominador
     0 (sin commits, sin releases) → null con razón, nunca 0.
2. **Metodología en el JSON, no solo en la guía:** el tracker emite, además de los
   agregados, los insumos de (a) serie interrumpida por repo (ventanas pre/post
   adopción), (b) diferencia-en-diferencias contra repos control (charly repos sin
   topic ddl / sin `.ddl/`), y (c) dosis-respuesta (outcome vs fracción de cobertura).
   Los estimadores puntuales van con intervalos bootstrapped (seed fija).
3. **Dashboard:** `scripts/viz-ddl.py` genera `data/ddl-dashboard.html` — HTML
   autocontenido, stdlib-only, SVG inline, tema compartido (`site_theme.py`), tags
   de provenancia y deep-link/export como las vistas existentes. Vistas:
   - *Línea de adopción*: timeline por repo (primer `.ddl/`, primeras herramientas).
   - *Dosis-respuesta*: outcome vs fracción de cobertura, con n visible por punto.
   - *Pre/post*: serie interrumpida por repo y DiD contra control.
   - *Lazo de feedback*: recuperación mediana, sesión→commit, fallo de CI.
   - *Errores*: clases de terapia y errores de API, normalizados por 100 interacciones.
   - *Calidad*: los 4 proxies lado a lado.
4. **Disclosure de confusores obligatoria:** el dashboard y la guía declaran que el
   diseño es asociativo (no causal): madurez del repo, cambio de mezcla de modelos,
   y el confusor no controlable (adopción de ddl correlaciona con el cuidado del
   mantenedor). Ningún claim causal sin reversión natural documentada.
5. **Integración:** `nightly-update.sh` regenera tracker + dashboard (tras el
   reporte de usage y el walk de sesiones); README documenta layout y gaps;
   guía `docs/fpa-analyses-guide.md` suma un análisis nuevo a 4 niveles
   (siguiente sección numerada de la guía: **#21**; el README dice "19 análisis"
   y ya está stale — bug pre-existente, se filea aparte) con grounding verificado.

## Scope

- **Flota como unidad de análisis:** repos charly-vibes con actividad en las sesiones
  in-scope de coffee (filtro charly). El repo `charly-dulce-de-leche` itself entra
  al análisis como repo más (con la advertencia de que es un caso atípico: casi todo
  modelos flash, sin skills — se marca como outlier).
- **CI vía gh api incluida**, con cache on-disk (`data/cache/gh_runs/<repo>.json`,
  TTL semanal) y degradación grácil: sin red/gh, el tracker emite `ci: null` con
  razón en provenance — nunca fake 0.
- **Pi-only** para métricas de sesión (mismo alcance que `add-context-dashboard`);
  Claude/Amp/Codex quedan fuera de recuperación y errores de sesión.
- **No-goal v1:** causalidad (sin experimento ni reversión natural no hay diseño que
  la sostenga), cobertura de tests real (no hay data; solo proxies de commits).

## Impact

- **Affected specs:** `ddl-effect` (capability nueva, ~10 requirements).
  `usage-report` no cambia: el tracker nuevo **lee** `usage_report_v3.json`,
  no re-emite nada (separación de concerns; evita otro golden gigante).
- **Affected code:** `scripts/ddl-effect-tracker.py` (nuevo; insumos:
  `usage_report_v3.json` + walk de `~/.pi/agent/sessions` + git de la flota +
  gh api + `ddl status` + cache de terapia),
  `scripts/viz-ddl.py` (nuevo), `scripts/site_theme.py` (solo consumo),
  `scripts/nightly-update.sh` (2 pasos nuevos), `specs/ddl-effect.schema.json`
  (nuevo), `tests/test_ddl_effect.py` (nuevo, reducers puros con fixtures
  sintéticos de git/gh), `tests/test_viz.py` (smoke Chromium del HTML),
  `docs/fpa-analyses-guide.md` + `scripts/viz_fpa_guide.py` (análisis #22),
  `README.md`, `config/fpa.json` (umbrales y TTL de cache).
- **Compatibilidad:** aditivo total — ningún archivo existente cambia de semántica.
  `data/ddl_effect.json` es derivado como el resto de `data/` (fuente: logs locales
  + git + gh api, con guard en `main()` si no hay nada).
- **Riesgo principal:** gh api rate limit (5000/h autenticado) — mitigado con cache
  incremental por repo (solo correcciones después del último run cacheado).

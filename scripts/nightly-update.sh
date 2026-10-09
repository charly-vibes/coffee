#!/usr/bin/env bash
# coffee — job nocturno: regenera el reporte y los dashboards desde los logs
# locales y, si hay cambios, commitea y pushea (el deploy a Pages sale del
# push vía GitHub Actions). Se corre vía systemd user timer `coffee-nightly`.
#
# Uso:
#   scripts/nightly-update.sh             # regen + commit + push si hay diffs
#   scripts/nightly-update.sh --no-push   # solo regen, sin tocar git (test)
set -euo pipefail

cd "$(dirname "$0")/.."

log() { echo "[coffee-nightly $(date '+%F %T')] $*"; }

NO_PUSH=0
[ "${1:-}" = "--no-push" ] && NO_PUSH=1

log "regenerando reporte + dashboards"
python3 scripts/usage-tracker.py
python3 scripts/viz-gantt.py
python3 scripts/viz-fpa.py
python3 scripts/viz_fpa_guide.py
python3 scripts/viz-index.py

# Sincroniza el bloque CHECK-DOCS del README (FPA-143) con el reporte nuevo
# y verifica consistencia — si algo desincroniza, aborta antes de pushear.
python3 scripts/update-readme-docs.py
python3 scripts/viz-fpa.py --check-docs

if [ "$NO_PUSH" -eq 1 ]; then
    log "modo --no-push: listo, sin tocar git"
    exit 0
fi

# Cambios en datos generados, README e index (paths explícitos, nunca -A).
STAGED="data README.md index.html"
if git status --porcelain -- $STAGED | grep -q .; then
    git add $STAGED
    git commit -m "nightly: regenera reporte y dashboards $(date +%F)"
    git pull --rebase
    git push
    log "pusheado"
else
    log "sin cambios, nada que pushear"
fi
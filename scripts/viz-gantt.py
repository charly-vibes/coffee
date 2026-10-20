#!/usr/bin/env python3
"""
viz-gantt.py — Genera visualización tipo Gantt de actividad por proyecto/día.

Lee data/usage_report_v3.json (sección project_daily) y produce un HTML
autocontenido (sin dependencias externas) en data/gantt-multitasking.html:

  - Fila superior: concurrencia diaria (proyectos distintos activos por día)
  - Matriz proyecto × día: intensidad = interacciones (escala log), con
    toggle a modo presencia (activo/inactivo)
  - Fines de semana sombreados, tooltips con detalle por celda
"""

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import site_theme  # noqa: E402
import viz_fpa_guide as guide_mod  # noqa: E402

GUIDE_MD = Path("docs/fpa-analyses-guide.md")
CONFIG = Path("config/fpa.json")


def marginalia_gantt():
    """Strip de marginalia del Gantt (coffe-gen.4): los análisis mapeados
    a la superficie 'gantt' (8 multitasking, 9 ritmo). Sin guía → ''."""
    if not GUIDE_MD.exists():
        return ""
    text = GUIDE_MD.read_text(encoding="utf-8")
    cfg = json.loads(CONFIG.read_text()) if CONFIG.exists() else {}
    return guide_mod.marginalia_html(guide_mod.load_guide(text), cfg, "gantt")

REPORT = Path("data/usage_report_v3.json")
OUT = Path("data/gantt-multitasking.html")

TEMPLATE = """<!DOCTYPE html>
<html lang="es">
<head>
<meta charset="utf-8">
<link rel="icon" href="data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 16 16'%3E%3Ctext y='13' font-size='13'%3E%E2%98%95%3C/text%3E%3C/svg%3E">
<title>Multitasking — actividad por proyecto/día</title>
<style>
  /* Tema compartido del sitio: scripts/site_theme.py (coffe-gen.2).
     Este generador no define paleta propia; solo su layout específico. */
@@THEME_BASE@@
  body {
    margin: 0; padding: 18px; background: var(--bg); color: var(--fg);
    font-size: 13px; line-height: 1.45;
  }
  .banner {
    background: var(--navy); color: #fff; padding: 10px 14px;
    border: 2px outset #fff; margin-bottom: 10px;
  }
  .banner h1 { font-size: 20px; margin: 0; font-weight: bold; letter-spacing: 1px; }
  .banner .tag { color: #c0c0c0; font-size: 11px; font-style: italic; }
  marquee { background: var(--yellow); color: #000; border: 2px inset #fff;
            font-weight: bold; font-size: 12px; padding: 3px 0; margin-bottom: 14px; }
  .sub { color: var(--muted); margin-bottom: 16px; font-size: 12px; }
  .controls { margin-bottom: 12px; display: flex; gap: 8px; align-items: center; }
  .controls button {
    background: #c0c0c0; color: #000; border: 2px outset #fff;
    padding: 4px 14px; cursor: pointer; font: bold 12px Arial, sans-serif;
  }
  .controls button:active { border-style: inset; }
  .controls button.on { background: var(--navy); color: #fff; border-style: inset; }
  #legend { margin-left: auto; color: #000; display: flex; gap: 4px; align-items: center; font-size: 11px; }
  #legend .sw { width: 22px; height: 12px; display: inline-block; border: 1px solid #000; }
  .wrap { overflow-x: auto; background: #fff; border: 2px inset #fff; padding: 10px; }
  /* filas flex (no CSS grid): el containing block de .label es la fila
     completa, así sticky left:0 funciona a cualquier scrollLeft.
     En grid el sticky se limita al grid-area de la columna de labels
     (190px) y se despega al scrollear más allá — ver tests/test_viz.py */
  .grid { display: block; position: relative; }
  .row { display: flex; width: max-content; height: 20px; }
  .cell { width: 14px; flex: none; margin-right: 1px; }
  .label {
    position: sticky; left: 0; z-index: 2; background: #fff;
    flex: 0 0 190px; height: 20px; line-height: 20px;
    padding-right: 8px; white-space: nowrap; text-align: right;
    font-size: 12px; overflow: hidden; text-overflow: ellipsis;
    border-bottom: 1px solid #e0e0e0;
  }
  .weekend { background: #e8e8e8; }
  .monthtick { font-size: 10px; color: #000; white-space: nowrap; overflow: visible; font-weight: bold; }
  #tip {
    position: fixed; display: none; pointer-events: none; z-index: 10;
    background: #ffffcc; border: 2px outset #fff; padding: 8px 10px;
    font-size: 12px; max-width: 340px; box-shadow: 4px 4px 0 #404040;
  }
  #tip b { color: var(--navy); }
  #tip .o { color: var(--muted); }
  footer { margin-top: 20px; font-size: 11px; color: var(--muted); text-align: center; }
  footer i { font-weight: bold; }
</style>
</head>
<body>
<div class="banner">
  @@BACK@@
  <h1>☢ charly analytics — GRÁFICO DE GANTT MULTITASKING</h1>
  <div class="tag">anexo estadístico · actividad concurrente por proyecto y día</div>
</div>
<marquee scrollamount="4">★★★ NUEVO: modo activo/inactivo ★★★ hasta 12 proyectos simultáneos registrados en una sola hora ★★★ esta página se ve mejor en Netscape Navigator 4.0 a 800×600 ★★★</marquee>
<div class="sub">__SUB__</div>
@@MARGINALIA@@
<div class="controls">
  <button id="b-int" class="on">interacciones</button>
  <button id="b-bin">activo/inactivo</button>
  <div id="legend"></div>
</div>
<div class="wrap"><div id="grid" class="grid"></div><div id="empty" style="display:none;color:var(--muted)">Sin datos: ejecuta primero <code>python scripts/usage-tracker.py</code></div></div>
<div id="tip"></div>
<footer><hr noshade size="2">© 1996–2026 Charly Vibes Analytics S.A. · <i>Best viewed in Netscape Navigator 4.0 at 800×600</i> · 🚧 UNDER CONSTRUCTION 🚧</footer>
<script>
const DATA = __DATA__;
const days = DATA.days, projects = Object.keys(DATA.matrix), M = DATA.matrix;
const grid = document.getElementById('grid');
const tip = document.getElementById('tip');
let mode = 'int';

const NCOL = days.length, NROW = projects.length;
const LABEL_W = 190, CW = 14, CH = 18;
if (!NCOL) { document.getElementById('empty').style.display = 'block'; throw new Error('sin datos'); }

// fecha -> índice; weekend set (parsear como UTC: getUTCDay sobre hora local
// desplaza un día en zonas UTC+, ver CORR-001 del review)
const isWeekend = days.map(d => { const dt = new Date(d + 'T00:00:00Z'); const w = dt.getUTCDay(); return w === 0 || w === 6; });

function dayTotals(i) {
  let tot = 0, nproj = 0;
  for (const p of projects) { const v = M[p][i]; if (v > 0) { tot += v; nproj++; } }
  return { tot, nproj };
}
const dayInfo = days.map((_, i) => dayTotals(i));
const maxCount = Math.max(...projects.flatMap(p => M[p]), 1);
const logMax = Math.log(1 + maxCount);

function colorInt(v) {
  if (!v) return 'transparent';
  const t = Math.log(1 + v) / logMax;
  return `rgba(0,0,128,${0.15 + 0.85 * Math.pow(t, 0.7)})`;
}
function colorBin(v) { return v ? 'rgba(128,0,0,0.9)' : 'transparent'; }
const color = v => mode === 'int' ? colorInt(v) : colorBin(v);
const legendHTML = m => m === 'int'
  ? 'menos ' + [0.2, 0.45, 0.7, 0.95].map(a => `<span class="sw" style="background:rgba(0,0,128,${a})"></span>`).join('') + ' más'
  : '<span class="sw" style="background:rgba(128,0,0,0.9)"></span> activo';

function fmt(n) { return n.toLocaleString('es'); }
function dateEs(d) { return new Date(d + 'T00:00').toLocaleDateString('es', { weekday: 'short', day: 'numeric', month: 'short', year: 'numeric' }); }

const rows = [];
function pushRow(labelHtml, cells) {
  rows.push(`<div class="row"><div class="label">${labelHtml}</div>${cells}</div>`);
}
// fila de ticks de mes
{
  let cells = '';
  for (let i = 0; i < NCOL; i++) {
    const d = days[i];
    const tick = d.endsWith('-01') ? d.slice(0, 7) : (i % 14 === 0 ? d.slice(8) : '');
    cells += `<div class="monthtick${isWeekend[i] ? ' weekend' : ''}" style="width:${CW}px">${tick}</div>`;
  }
  rows.push(`<div class="row" style="height:auto"><div class="label"></div>${cells}</div>`);
}
// fila de concurrencia diaria
{
  let cells = '';
  for (let i = 0; i < NCOL; i++) {
    const { nproj } = dayInfo[i];
    const t = nproj ? 0.15 + 0.85 * Math.min(1, nproj / 12) : 0;
    cells += `<div class="cell${isWeekend[i] ? ' weekend' : ''}" data-day="${i}" data-conc="1" style="background:${nproj ? `rgba(0,128,128,${t})` : 'transparent'}"></div>`;
  }
  pushRow('<span title="concurrencia diaria: proyectos distintos activos por día (la intensidad satura en 12)">→ proyectos/día</span>', cells);
}
// filas por proyecto
for (const p of projects) {
  let cells = '';
  for (let i = 0; i < NCOL; i++) {
    const v = M[p][i];
    cells += `<div class="cell${isWeekend[i] ? ' weekend' : ''}" data-p="${p}" data-i="${i}" style="background:${color(v)}"></div>`;
  }
  pushRow(`<span title="${p}">${p}</span>`, cells);
}
grid.innerHTML = rows.join('');

// leyenda
const lg = document.getElementById('legend');
lg.innerHTML = legendHTML(mode);

// tooltip
document.addEventListener('mousemove', e => {
  const el = e.target.closest('.cell');
  if (!el) { tip.style.display = 'none'; return; }
  let body;
  if (el.dataset.conc) {
    const i = +el.dataset.day, { tot, nproj } = dayInfo[i];
    const act = projects.filter(p => M[p][i] > 0);
    body = `<b>${dateEs(days[i])}</b><br>${nproj} proyectos simultáneos · ${fmt(tot)} interacciones<br><span class="o">${act.join(', ') || 'sin actividad'}</span>`;
  } else {
    const i = +el.dataset.i, v = M[el.dataset.p][i];
    const { nproj } = dayInfo[i];
    body = `<b>${el.dataset.p}</b> — ${dateEs(days[i])}<br>${v ? fmt(v) + ' interacciones' : 'sin actividad'} · <span class="o">${nproj} proyectos ese día</span>`;
  }
  tip.innerHTML = body;
  tip.style.display = 'block';
  const x = Math.max(0, Math.min(e.clientX + 14, innerWidth - 360));
  tip.style.left = x + 'px';
  tip.style.top = (e.clientY + 14) + 'px';
});

// toggle de modo
const bi = document.getElementById('b-int'), bb = document.getElementById('b-bin');
function setMode(m) {
  mode = m;
  bi.classList.toggle('on', m === 'int'); bb.classList.toggle('on', m === 'bin');
  grid.querySelectorAll('.cell[data-p]').forEach(el => {
    el.style.background = color(M[el.dataset.p][+el.dataset.i]);
  });
  if (m === 'int') lg.innerHTML = legendHTML('int');
  else lg.innerHTML = legendHTML('bin');
}
bi.onclick = () => setMode('int');
bb.onclick = () => setMode('bin');
</script>
</body>
</html>
"""

def validate_report(report, schema_path=Path("specs/usage-report-v3.schema.json")):
    """Valida el reporte contra el JSON Schema (opcional: requiere jsonschema)."""
    try:
        import jsonschema
    except ImportError:
        print("(--validate omitido: instala jsonschema para validar el contrato)")
        return
    schema = json.loads(schema_path.read_text())
    jsonschema.validate(report, schema)
    print(f"schema OK ({schema_path})")


def main():
    import sys
    args = [a for a in sys.argv[1:] if a != "--validate"]
    report_path = Path(args[0]) if args else REPORT
    out_path = Path(args[1]) if len(args) > 1 else OUT
    report = json.loads(report_path.read_text())
    if "--validate" in sys.argv:
        validate_report(report)
    pd = report["project_daily"]
    meta = report["metadata"]
    mt = report["multitasking"]

    sub = (f"{meta['total_interactions']:,} interacciones · "
           f"{meta['total_projects']} proyectos · {meta['date_range']['start']} → {meta['date_range']['end']} · "
           f"{mt['hourly']['pct_hours_multitasking']}% de horas con ≥2 proyectos en paralelo")

    html = (TEMPLATE
            .replace("@@THEME_BASE@@", site_theme.base_css())
            .replace("@@BACK@@", site_theme.back_link())
            .replace("@@MARGINALIA@@", marginalia_gantt())
            .replace("__DATA__", json.dumps(pd, ensure_ascii=False))
            .replace("__SUB__", sub))
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(html)
    print(f"OK → {out_path} ({out_path.stat().st_size // 1024} KB, {len(pd['days'])} días × {len(pd['matrix'])} proyectos)")

if __name__ == "__main__":
    main()

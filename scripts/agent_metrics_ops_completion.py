"""Purpose: ops.completion + ops.intervention (epic coffe-a5v, hijo coffe-af2)
para data/agent_metrics.json.

Responsibilities: dos slices derivados.

ops.completion — % de beads terminadas autónomamente sobre el cohort
'unambiguous' de data/task_beads.json. Ambigüedad: bead con n_temporal > 0 o
sin cost_per_bead definido (espejo de ambiguous_units del estimador
docs/model-cost-estimate-v0.json; beads temporales y sin coste no entran al
coste, así que no entran al cohort). started = tamaño del cohort;
autonomous = beads del cohort cuyas tareas atribuidas no llevan flag de error
ni abort (FLAG_ERROR|FLAG_ABORTED); rate = autonomous/started, o null con
razón ('no_unambiguous_beads', 'estimator_stale'). Gate de frescura: si el
`generated` del estimador tiene >30 días vs hoy, rate=null 'estimator_stale'
y el cohort/share se siguen computando.

ops.intervention — intervención humana: aborts (tareas con FLAG_ABORTED) +
recovery_prompts (tareas con FLAG_RETRY) por 100 beads aceptadas (el cohort
unambiguous). Aproximación documentada: recovery_prompts cuenta TODAS las
tareas con FLAG_RETRY; no se verifica que sigan (mismo día, misma sesión) a
una tarea con error — la serie compacta de tasks no expone el orden/vecindad
para ese filtro. per_100_accepted = 100*(aborts+recovery_prompts)/accepted,
null con razón si accepted==0 ('no_accepted_beads') o si el estimador está
stale ('estimator_stale' → accepted_beads=null).

Join elegido (defensible, computable con los datos disponibles):
task_beads.json documenta task = índice en la serie compacta tasks de
agent_metrics (mismo orden que tasks['f']); así que bead → filas
(task_beads) → índices de tarea → flags. Las beads del cohort sin filas de
atribución no se pueden clasificar y NO cuentan como autónomas (quedan en
started); la coverage de agent_metrics.tasks reporta ese hueco. Estimador:
solo se usa su `generated` (frescura); el concepto ambiguous_units ya está
espejado en la definición de ambigüedad, no se recalcula de sus rows.

Rationale: null-con-razón nunca 0 fake; provenance derived para ambos; los
dos slices se computan y validan por separado vía run_slice, compartiendo el
mismo helper de cohort para que accepted_beads de intervention sea
exactamente el started de completion.
"""
from __future__ import annotations

import json
import sys
from collections import defaultdict
from datetime import date, datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from agent_metrics_common import FLAG_ABORTED, FLAG_ERROR, FLAG_RETRY  # noqa: E402
from agent_metrics_ops import run_slice  # noqa: E402

NO_UNAMBIGUOUS_REASON = "no_unambiguous_beads"
STALE_REASON = "estimator_stale"
NO_ACCEPTED_REASON = "no_accepted_beads"
NO_TASKS_SIGNAL = "no_tasks_signal"
NO_ATTRIBUTED_TASKS = "no_attributed_tasks"
ESTIMATOR_SOURCE = "model-cost-estimate-v0"
MAX_ESTIMATOR_AGE_DAYS = 30
ERROR_FLAGS = FLAG_ERROR | FLAG_ABORTED
TASK_BEADS_PATH = Path("data") / "task_beads.json"
ESTIMATOR_PATH = Path("docs") / "model-cost-estimate-v0.json"


def load_task_beads(root):
    """data/task_beads.json: {'beads': [...], 'task_beads': [...], ...}."""
    return json.loads((Path(root) / TASK_BEADS_PATH).read_text(encoding="utf-8"))


def load_estimate(root):
    """docs/model-cost-estimate-v0.json: estimador de coste (frescura)."""
    return json.loads((Path(root) / ESTIMATOR_PATH).read_text(encoding="utf-8"))


def _task_flags(doc):
    """Serie compacta de flags por índice de tarea (tasks['f']); None si no está."""
    f = ((doc or {}).get("tasks") or {}).get("f")
    return f if isinstance(f, list) else None


def _est_generated(est):
    """Fecha 'generated' del estimador (top-level o metadata.generated)."""
    gen = est.get("generated")
    if not gen:
        gen = (est.get("metadata") or {}).get("generated")
    return gen


def estimator_stale(est, today, max_age_days=MAX_ESTIMATOR_AGE_DAYS):
    """True si `generated` falta, no parsea o tiene >max_age_days vs today."""
    gen = _est_generated(est)
    if not gen:
        return True
    try:
        g = datetime.fromisoformat(str(gen).replace("Z", "+00:00"))
    except ValueError:
        return True
    return (date(today.year, today.month, today.day) - g.date()).days > max_age_days


def _pct(part, whole):
    return round(100.0 * part / whole, 1) if whole else 0.0


def _cohort_state(tb, est, flags, today):
    """Estado compartido de completion/intervention sobre task_beads.

    Devuelve {'total','ambiguous','ambiguous_share','started','autonomous',
    'rate','rate_reason','stale','accepted','accepted_reason','classified',
    'attr_rows','attr_in_range'}."""
    beads = tb.get("beads") or []
    by_bead = defaultdict(list)
    for r in tb.get("task_beads") or []:
        by_bead[r.get("bead")].append(r.get("task"))

    total = len(beads)
    ambiguous = 0
    started = 0
    autonomous = 0
    classified = 0
    attr_rows = 0
    attr_in_range = 0

    for b in beads:
        if b.get("cost_per_bead") is None or (b.get("n_temporal") or 0) > 0:
            ambiguous += 1
            continue
        started += 1
        tidx = by_bead.get(b.get("bead"))
        if not tidx:
            continue  # sin filas de atribución: no clasificable
        classified += 1
        attr_rows += len(tidx)
        dirty = False
        for i in tidx:
            if not isinstance(i, int) or i < 0 or not isinstance(flags, list) \
                    or i >= len(flags) or not isinstance(flags[i], int):
                dirty = True  # índice fuera de la serie: sin señal
                continue
            attr_in_range += 1
            if flags[i] & ERROR_FLAGS:
                dirty = True
        if not dirty:
            autonomous += 1

    stale = estimator_stale(est, today)

    rate = round(autonomous / started, 4) if started else None
    rate_reason = None
    if started == 0:
        rate_reason = NO_UNAMBIGUOUS_REASON
    elif stale:
        rate, rate_reason = None, STALE_REASON

    accepted = None
    accepted_reason = STALE_REASON if stale else None
    if not stale:
        accepted = started

    return {"total": total, "ambiguous": ambiguous,
            "ambiguous_share": _pct(ambiguous, total),
            "started": started, "autonomous": autonomous,
            "rate": rate, "rate_reason": rate_reason, "stale": stale,
            "accepted": accepted, "accepted_reason": accepted_reason,
            "classified": classified, "attr_rows": attr_rows,
            "attr_in_range": attr_in_range}


def completion_coverage(st, flags):
    """Coverage por fuente del slice completion."""
    cov = []
    if st["started"]:
        cov.append({"source": "task_beads",
                    "coverage_pct": _pct(st["classified"], st["started"]),
                    "reason": None})
    else:
        cov.append({"source": "task_beads", "coverage_pct": 0.0,
                    "reason": NO_UNAMBIGUOUS_REASON})
    if not isinstance(flags, list):
        cov.append({"source": "agent_metrics.tasks", "coverage_pct": 0.0,
                    "reason": NO_TASKS_SIGNAL})
    elif st["attr_rows"] == 0:
        cov.append({"source": "agent_metrics.tasks", "coverage_pct": 0.0,
                    "reason": NO_ATTRIBUTED_TASKS if st["started"]
                    else NO_UNAMBIGUOUS_REASON})
    else:
        cov.append({"source": "agent_metrics.tasks",
                    "coverage_pct": _pct(st["attr_in_range"], st["attr_rows"]),
                    "reason": None})
    if st["stale"]:
        cov.append({"source": ESTIMATOR_SOURCE, "coverage_pct": 0.0,
                    "reason": STALE_REASON})
    else:
        cov.append({"source": ESTIMATOR_SOURCE, "coverage_pct": 100.0,
                    "reason": None})
    return cov


def completion_block(tb, est, flags, today):
    """ops.completion completo (cohort unambiguous)."""
    st = _cohort_state(tb, est, flags, today)
    block = {
        "started": st["started"],
        "autonomous": st["autonomous"],
        "rate": st["rate"],
        "ambiguous_share": st["ambiguous_share"],
        "cohort": "unambiguous",
        "provenance": {"completion": "derived"},
        "coverage": completion_coverage(st, flags),
    }
    if st["rate_reason"]:
        block["reason"] = st["rate_reason"]
    return block


def intervention_block(st, flags):
    """ops.intervention completo (aborts + retries por 100 aceptadas)."""
    flags = flags if isinstance(flags, list) else []
    aborts = sum(1 for f in flags if isinstance(f, int) and f & FLAG_ABORTED)
    recovery = sum(1 for f in flags if isinstance(f, int) and f & FLAG_RETRY)
    per_100 = None
    reason = None
    accepted = st["accepted"]
    if accepted is None:
        reason = st["accepted_reason"] or NO_ACCEPTED_REASON
    elif accepted == 0:
        reason = NO_ACCEPTED_REASON
    else:
        per_100 = round(100.0 * (aborts + recovery) / accepted, 2)
    block = {
        "aborts": aborts,
        "recovery_prompts": recovery,
        "per_100_accepted": per_100,
        "accepted_beads": accepted,
        "provenance": {"intervention": "derived"},
        "coverage": [{"source": "agent_metrics.tasks",
                      "coverage_pct": 100.0 if isinstance(flags, list) else 0.0,
                      "reason": None if isinstance(flags, list)
                      else NO_TASKS_SIGNAL}],
    }
    if reason:
        block["reason"] = reason
    return block


# ----------------------------------------------------------------------
# CLI
# ----------------------------------------------------------------------

def main():
    from agent_metrics_common import OUT  # noqa: PLC0415
    today = date.today()

    def compute_completion(inputs):
        root = inputs["root"]
        tb = inputs.get("task_beads") or load_task_beads(root)
        est = inputs.get("estimate") or load_estimate(root)
        flags = inputs["flags"] if inputs.get("flags") is not None \
            else _task_flags(inputs["doc"])
        return completion_block(tb, est, flags,
                                inputs.get("today") or today)

    def compute_intervention(inputs):
        root = inputs["root"]
        tb = inputs.get("task_beads") or load_task_beads(root)
        est = inputs.get("estimate") or load_estimate(root)
        flags = inputs["flags"] if inputs.get("flags") is not None \
            else _task_flags(inputs["doc"])
        st = _cohort_state(tb, est, flags, inputs.get("today") or today)
        return intervention_block(st, flags)

    run_slice("completion", compute_completion)
    run_slice("intervention", compute_intervention)

    ops = json.loads(OUT.read_text(encoding="utf-8"))["ops"]
    c = ops["completion"]
    i = ops["intervention"]
    rate = f"{c['rate'] * 100:.1f}%" if c["rate"] is not None else "n/a"
    print(f"ops.completion: cohort=unambiguous started={c['started']} "
          f"autonomous={c['autonomous']} rate={rate} "
          f"ambiguous_share={c['ambiguous_share']}%")
    per_100 = i["per_100_accepted"]
    per_100 = f"{per_100:.2f}" if per_100 is not None else "n/a"
    print(f"ops.intervention: aborts={i['aborts']} "
          f"recovery_prompts={i['recovery_prompts']} "
          f"accepted_beads={i['accepted_beads']} per_100_accepted={per_100}")


# ----------------------------------------------------------------------
# viz
# ----------------------------------------------------------------------

def _fmt_rate(rate, reason=None):
    if rate is None:
        return "n/a" + (f' <span class="reason">{reason}</span>'
                        if reason else "")
    return f"{rate * 100:.1f}%"


def _completion_section(c):
    return ('<section class="block"><h2>Completitud autónoma '
            '(ops.completion, cohort sin ambigüedad)</h2>'
            + '<table><thead><tr><th>Cohorte</th><th>Iniciadas</th>'
              '<th>Autónomas</th><th>Tasa</th><th>Ambigüedad</th></tr></thead>'
              '<tbody><tr><td>unambiguous</td>'
              f'<td>{c["started"]}</td><td>{c["autonomous"]}</td>'
              f'<td>{_fmt_rate(c["rate"], c.get("reason"))}</td>'
              f'<td>{c["ambiguous_share"]}%</td></tr></tbody></table></section>')


def _intervention_section(i):
    per_100 = ("n/a" + (f' <span class="reason">{i["reason"]}</span>'
                        if i.get("reason") else "")
               if i["per_100_accepted"] is None
               else f'{i["per_100_accepted"]:.2f}')
    return ('<section class="block"><h2>Intervención humana '
            '(ops.intervention, por 100 beads aceptadas)</h2>'
            '<table><thead><tr><th>Abortes</th><th>Reintentos</th>'
            '<th>Beads aceptadas</th><th>Por 100 aceptadas</th></tr></thead>'
            f'<tbody><tr><td>{i["aborts"]}</td>'
            f'<td>{i["recovery_prompts"]}</td>'
            f'<td>{i["accepted_beads"] if i["accepted_beads"] is not None else "n/a"}</td>'
            f'<td>{per_100}</td></tr></tbody></table></section>')


def render_section(ops):
    """Secciones HTML server-side del dashboard (sin JS)."""
    ops = ops or {}
    c = ops.get("completion")
    i = ops.get("intervention")
    parts = []
    if c is None:
        parts.append('<section class="block"><h2>Completitud autónoma '
                     '(ops.completion)</h2><p class="reason">n/a: bloque '
                     'ops.completion ausente (correr '
                     'scripts/agent_metrics_ops_completion.py).</p></section>')
    else:
        parts.append(_completion_section(c))
    if i is None:
        parts.append('<section class="block"><h2>Intervención humana '
                     '(ops.intervention)</h2><p class="reason">n/a: bloque '
                     'ops.intervention ausente (correr '
                     'scripts/agent_metrics_ops_completion.py).</p></section>')
    else:
        parts.append(_intervention_section(i))
    return "\n".join(parts)


if __name__ == "__main__":
    main()

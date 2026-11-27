"""Purpose: ops.quota_events — eventos de agotamiento de cuota (coffe-t6e).
Responsibilities: detectar por firmas de error de proveedor en los turnos
assistant de pi (campo `error_message`, hilado desde errorMessage crudo) y
exponer el bloque ops.quota_events validado contra el schema del epic
coffe-a5v, más render_section(ops) para el dashboard server-side.
Rationale: claude-cli no expone señal de cuota en sus registros, así que la
detección es por firmas configuradas (config/fpa.json), con provenance
`derived` y null-con-razón para las fuentes sin señal.

Forma esperada del bloque nuevo en config/fpa.json (NO lo escribe este módulo):

    "quota_signatures": [
        {"provider": "anthropic", "patterns": ["credit balance is too low",
                                               "billing", "rate_limit_quota"]}
    ]

Cada patrón es una subcadena/regex buscada case-insensitive en el
`error_message` del turno. Bloque ausente → pi entra en
sources_without_signature con razón `no_quota_signatures_configured`;
claude-cli siempre entra con `no_quota_signatures_in_source` (y pi también
cuando, con configuración presente, sus errores no casan ninguna firma).
"""
from __future__ import annotations

import re
import sys
from html import escape
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from agent_metrics_ops import run_slice  # noqa: E402
from agent_metrics_turns import load_claude_turns, load_pi_turns  # noqa: E402

NO_QUOTA_SIGNATURES_CONFIGURED = "no_quota_signatures_configured"
NO_QUOTA_SIGNAL_IN_SOURCE = "no_quota_signatures_in_source"
NO_ERROR_MESSAGE_SIGNAL = "no_error_message_signal"

PI_SOURCE = "pi"
CLAUDE_SOURCE = "claude-cli"


def _signature_matchers(entries):
    """Config quota_signatures → [(provider, compiled_regex, pattern_str)]."""
    matchers = []
    for entry in entries or []:
        provider = str(entry.get("provider") or "")
        for pattern in entry.get("patterns") or []:
            matchers.append((provider, re.compile(pattern, re.IGNORECASE),
                             pattern))
    return matchers


def _assistant_turns(turns):
    return [t for t in turns if t.get("role") == "assistant"]


def _events_for_source(source, turns, matchers):
    """Eventos de cuota y cobertura (señal de error) para una fuente."""
    events = []
    examined = 0
    with_signal = 0
    for t in _assistant_turns(turns):
        msg = t.get("error_message")
        examined += 1
        if not isinstance(msg, str) or not msg:
            continue
        with_signal += 1
        for provider, rx, pattern in matchers:
            if rx.search(msg):
                events.append({"ts": t.get("ts") or "", "provider": provider,
                               "signature": pattern, "source": source})
                break
    coverage_pct = round(100.0 * with_signal / examined, 1) if examined else 0.0
    return events, coverage_pct


def quota_block(turns_by_source, quota_signatures):
    """ops.quota_events: events/counts/sources_without_signature/provenance/
    coverage. `turns_by_source` es {'pi': [...], 'claude-cli': [...]}."""
    matchers = _signature_matchers(quota_signatures)
    events = []
    counts = {}
    coverage = []
    pi_unmatched_errors = False
    for source in (PI_SOURCE, CLAUDE_SOURCE):
        src_events, coverage_pct = _events_for_source(
            source, turns_by_source.get(source) or [], matchers)
        events.extend(src_events)
        for ev in src_events:
            counts[ev["provider"]] = counts.get(ev["provider"], 0) + 1
        coverage.append({"source": source, "coverage_pct": coverage_pct,
                         "reason": None if coverage_pct else NO_ERROR_MESSAGE_SIGNAL})
        if source == PI_SOURCE:
            pi_unmatched_errors = bool(coverage_pct) and not src_events

    sources_without = [{"source": CLAUDE_SOURCE,
                        "reason": NO_QUOTA_SIGNAL_IN_SOURCE}]
    if not matchers or pi_unmatched_errors:
        sources_without.append({"source": PI_SOURCE,
                                "reason": NO_QUOTA_SIGNATURES_CONFIGURED})
    return {
        "events": events,
        "counts": counts,
        "sources_without_signature": sources_without,
        "provenance": {"quota_events": "derived"},
        "coverage": coverage,
    }


def compute(inputs):
    """Contrato run_slice: inputs con 'config' y turnos por fuente
    (inputs_extra['turns'] o loaders propios del slice)."""
    turns_by_source = inputs.get("turns")
    if turns_by_source is None:
        pi_turns, _ = load_pi_turns()
        claude_turns, _ = load_claude_turns()
        turns_by_source = {PI_SOURCE: pi_turns, CLAUDE_SOURCE: claude_turns}
    return quota_block(turns_by_source,
                       (inputs.get("config") or {}).get("quota_signatures"))


def main(argv=None):
    pi_turns, _ = load_pi_turns()
    claude_turns, _ = load_claude_turns()
    _doc, block = run_slice("quota_events", compute, inputs_extra={
        "turns": {PI_SOURCE: pi_turns, CLAUDE_SOURCE: claude_turns}})
    counts = ", ".join(f"{k}={v}" for k, v in sorted(block["counts"].items()))
    print(f"ops.quota_events: eventos={len(block['events'])} "
          f"proveedores=[{counts or 'sin eventos'}]")
    return 0


def render_section(ops):
    """Sección HTML server-side del dashboard (sin JS)."""
    block = (ops or {}).get("quota_events")
    if block is None:
        return ('<section class="block"><h2>ops: quota_events</h2>'
                '<p class="reason">n/a: bloque ops.quota_events ausente '
                '(correr scripts/agent_metrics_ops_quota.py).</p></section>')
    events = block.get("events") or []
    rows = []
    for provider, n in sorted((block.get("counts") or {}).items(),
                              key=lambda kv: -kv[1]):
        rows.append(f"<tr><td>{escape(provider)}</td><td>{n}</td></tr>")
    body = "\n".join(rows)
    without = "\n".join(
        f"<li><strong>{escape(s['source'])}:</strong> {escape(s['reason'])}</li>"
        for s in block.get("sources_without_signature") or [])
    return ('<section class="block"><h2>Agotamiento de cuota (ops.quota_events)</h2>'
            f"<p>{len(events)} evento(s) detectado(s) por firmas de error de proveedor.</p>"
            '<table><thead><tr><th>Proveedor</th><th>n</th></tr></thead>'
            f"<tbody>\n{body or '<tr><td colspan=\"2\">—</td></tr>'}\n    </tbody></table>"
            '<p class="reason">Fuentes sin señal de cuota:</p><ul>'
            f"\n{without}\n</ul>"
            '<p class="reason">Detección derivada: firmas configuradas en '
            'config/fpa.json (quota_signatures) contra errorMessage crudo de '
            'pi; claude-cli no expone señal de cuota.</p></section>')


if __name__ == "__main__":
    sys.exit(main())

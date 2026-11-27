#!/usr/bin/env python3
"""
schema_validate.py — validador JSON-Schema (draft-07) subset, stdlib-only.

Única fuente del subconjunto de JSON-Schema que usan los trackers/viz del
repo (extraído de viz-fpa.py FPA-006 en coffe-nea): type, required,
properties, additionalProperties, items, enum, minimum/maximum, pattern,
patternProperties, allOf, $ref, definitions. Devuelve la lista de errores
("$ruta.campo: mensaje") — vacía = válido.

Rationale: `jsonschema` (pip) no está garantizado en el nightly (user-timers
sin PATH completo), así que la validación en producción es stdlib-only; los
tests usan jsonschema real cuando está (ver tests/test_energy.py).
"""
from __future__ import annotations

import re

_TYPES = {
    "object": dict, "array": list, "string": str, "boolean": bool,
    "null": type(None),
}


def _type_ok(doc, t):
    if t == "number":
        return isinstance(doc, (int, float)) and not isinstance(doc, bool)
    if t == "integer":
        return isinstance(doc, int) and not isinstance(doc, bool)
    if t in _TYPES:
        return isinstance(doc, _TYPES[t])
    return False


def _resolve_ref(ref, root):
    node = root
    for part in ref.lstrip("#/").split("/"):
        node = node[part]
    return node


class _Ctx:
    """Estado compartido de una validación (root, errores, profundidad)."""
    __slots__ = ("root", "errors", "depth")

    def __init__(self, root):
        self.root = root
        self.errors = []
        self.depth = 0


def _check_required(doc, schema, path, errors):
    for key in schema.get("required", []):
        if key not in doc:
            errors.append(f"{path}: campo requerido ausente: {key}")


def _pattern_matches(key, patterns):
    return [sub for pat, sub in patterns.items() if re.search(pat, key)]


def _check_properties(doc, schema, ctx, path):
    props = schema.get("properties", {})
    for key, val in doc.items():
        if key in props:
            _validate(val, props[key], ctx, f"{path}.{key}")
            continue
        matched = _pattern_matches(key, schema.get("patternProperties", {}))
        for sub in matched:
            _validate(val, sub, ctx, f"{path}.{key}")
        if not matched and schema.get("additionalProperties", True) is False:
            ctx.errors.append(f"{path}.{key}: campo no permitido")


def _check_items(doc, schema, ctx, path):
    for i, item in enumerate(doc):
        _validate(item, schema["items"], ctx, f"{path}[{i}]")


def _check_enum(doc, schema, path, errors):
    if doc not in schema["enum"]:
        errors.append(f"{path}: {doc!r} no está en {schema['enum']!r}")


def _check_numeric(doc, schema, path, errors):
    if "minimum" in schema and doc < schema["minimum"]:
        errors.append(f"{path}: {doc} < mínimo {schema['minimum']}")
    if "maximum" in schema and doc > schema["maximum"]:
        errors.append(f"{path}: {doc} > máximo {schema['maximum']}")


def _check_pattern(doc, schema, path, errors):
    if not re.search(schema["pattern"], doc):
        errors.append(f"{path}: {doc!r} no matchea {schema['pattern']!r}")


def _check_scalar(doc, schema, path, errors):
    if "enum" in schema:
        _check_enum(doc, schema, path, errors)
    if isinstance(doc, (int, float)) and not isinstance(doc, bool):
        _check_numeric(doc, schema, path, errors)
    if isinstance(doc, str) and "pattern" in schema:
        _check_pattern(doc, schema, path, errors)


def _check_containers(doc, schema, ctx, path):
    if isinstance(doc, dict):
        _check_required(doc, schema, path, ctx.errors)
        _check_properties(doc, schema, ctx, path)
    if isinstance(doc, list) and "items" in schema:
        _check_items(doc, schema, ctx, path)


def _check_type(doc, schema, path, errors):
    """True si el tipo (si acaso declarado) coincide."""
    if "type" not in schema:
        return True
    types = schema["type"] if isinstance(schema["type"], list) else [schema["type"]]
    if not any(_type_ok(doc, t) for t in types):
        errors.append(f"{path}: tipo {type(doc).__name__}, esperaba "
                      f"{'/'.join(types)}")
        return False  # con tipo mal, no seguir bajando
    return True


def _validate(doc, schema, ctx, path):
    """Valida un nodo contra `schema`; acumula errores en ctx.errors."""
    if ctx.depth > 32:  # guardia anti-recursión
        return
    if schema is True:
        return
    if schema is False:
        ctx.errors.append(f"{path}: esquema lo prohíbe")
        return
    ctx.depth += 1
    try:
        if "$ref" in schema:
            _validate(doc, _resolve_ref(schema["$ref"], ctx.root), ctx, path)

        for sub in schema.get("allOf", []):
            _validate(doc, sub, ctx, path)

        if not _check_type(doc, schema, path, ctx.errors):
            return

        _check_scalar(doc, schema, path, ctx.errors)
        _check_containers(doc, schema, ctx, path)
    finally:
        ctx.depth -= 1


def validate_against_schema(doc, schema, root=None):
    """Valida `doc` contra `schema` (subset draft-07); lista de errores."""
    ctx = _Ctx(schema if root is None else root)
    _validate(doc, schema, ctx, "$")
    return ctx.errors

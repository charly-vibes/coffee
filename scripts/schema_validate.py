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


def validate_against_schema(doc, schema, root=None, path="$", errors=None,
                            depth=0):
    """Valida `doc` contra `schema` (subset draft-07); lista de errores."""
    if errors is None:
        errors = []
    if depth > 32:  # guardia anti-recursión
        return errors
    if root is None:
        root = schema
    if schema is True:
        return errors
    if schema is False:
        errors.append(f"{path}: esquema lo prohíbe")
        return errors

    if "$ref" in schema:
        validate_against_schema(doc, _resolve_ref(schema["$ref"], root), root,
                                path, errors, depth + 1)

    for sub in schema.get("allOf", []):
        validate_against_schema(doc, sub, root, path, errors, depth + 1)

    if "type" in schema:
        types = schema["type"] if isinstance(schema["type"], list) else [schema["type"]]
        if not any(_type_ok(doc, t) for t in types):
            errors.append(f"{path}: tipo {type(doc).__name__}, esperaba "
                          f"{'/'.join(types)}")
            return errors  # con tipo mal, no seguir bajando

    if "enum" in schema and doc not in schema["enum"]:
        errors.append(f"{path}: {doc!r} no está en {schema['enum']!r}")

    if isinstance(doc, (int, float)) and not isinstance(doc, bool):
        if "minimum" in schema and doc < schema["minimum"]:
            errors.append(f"{path}: {doc} < mínimo {schema['minimum']}")
        if "maximum" in schema and doc > schema["maximum"]:
            errors.append(f"{path}: {doc} > máximo {schema['maximum']}")

    if isinstance(doc, str) and "pattern" in schema:
        if not re.search(schema["pattern"], doc):
            errors.append(f"{path}: {doc!r} no matchea {schema['pattern']!r}")

    if isinstance(doc, dict):
        for key in schema.get("required", []):
            if key not in doc:
                errors.append(f"{path}: campo requerido ausente: {key}")
        props = schema.get("properties", {})
        for key, val in doc.items():
            if key in props:
                validate_against_schema(val, props[key], root,
                                        f"{path}.{key}", errors, depth + 1)
            else:
                matched = False
                for pat, sub in schema.get("patternProperties", {}).items():
                    if re.search(pat, key):
                        matched = True
                        validate_against_schema(val, sub, root,
                                                f"{path}.{key}", errors,
                                                depth + 1)
                ap = schema.get("additionalProperties", True)
                if not matched and ap is False:
                    errors.append(f"{path}.{key}: campo no permitido")

    if isinstance(doc, list) and "items" in schema:
        for i, item in enumerate(doc):
            validate_against_schema(item, schema["items"], root,
                                    f"{path}[{i}]", errors, depth + 1)

    return errors

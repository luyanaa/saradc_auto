#!/usr/bin/env python3
"""Minimal, dependency-free validator for `spec/spec.schema.json`.

`jsonschema` is not installed in this environment, and the schema is a small
subset of draft 2020-12.  This module implements exactly the keywords the
checked-in schema uses, so a spec is validated against the real schema file
instead of drifting from it: `type`, `enum`, `const`, `oneOf`, `required`,
`properties`, `additionalProperties`, `items`, `minimum`, `maximum`,
`exclusiveMinimum`, `exclusiveMaximum`, and `minLength`.

It is deliberately a subset: adding a keyword to the schema that is not
handled here is a silent hole, so `SUPPORTED` is exported and `unknown_keywords`
flags schema keys this validator would ignore.  `run.py validate` reports
those, which keeps the subset honest.

No dependencies outside the standard library.
"""

from __future__ import annotations

import json
import os

# keywords that carry validation meaning here
SUPPORTED = {
    "type", "enum", "const", "oneOf", "required", "properties",
    "additionalProperties", "items", "minimum", "maximum",
    "exclusiveMinimum", "exclusiveMaximum", "minLength",
}
# annotations and identifiers: valid, no validation effect
ANNOTATION = {
    "$schema", "$id", "title", "description", "default", "examples",
    "properties", "items", "required", "type", "enum", "const", "oneOf",
    "additionalProperties", "minimum", "maximum", "exclusiveMinimum",
    "exclusiveMaximum", "minLength",
}


def _is_type(value, name):
    if name == "object":
        return isinstance(value, dict)
    if name == "array":
        return isinstance(value, list)
    if name == "string":
        return isinstance(value, str)
    if name == "boolean":
        return isinstance(value, bool)
    if name == "integer":
        return isinstance(value, int) and not isinstance(value, bool)
    if name == "number":
        return isinstance(value, (int, float)) and not isinstance(value, bool)
    if name == "null":
        return value is None
    return False


def _validate(value, schema, path, errors):
    if not isinstance(schema, dict):
        return

    if "type" in schema:
        types = schema["type"]
        types = types if isinstance(types, list) else [types]
        if not any(_is_type(value, t) for t in types):
            errors.append(f"{path}: expected {'/'.join(types)}, got "
                          f"{type(value).__name__}")
            return

    if "const" in schema and value != schema["const"]:
        errors.append(f"{path}: expected {schema['const']!r}, got {value!r}")

    if "enum" in schema and value not in schema["enum"]:
        errors.append(f"{path}: {value!r} not in {schema['enum']}")

    if "oneOf" in schema:
        matches = 0
        branch_errors = []
        for branch in schema["oneOf"]:
            errs = []
            _validate(value, branch, path, errs)
            if not errs:
                matches += 1
            else:
                branch_errors.append(errs[0])
        if matches != 1:
            detail = "; ".join(branch_errors[:3])
            errors.append(f"{path}: matched {matches} oneOf branches ({detail})")

    if isinstance(value, str) and "minLength" in schema:
        if len(value) < schema["minLength"]:
            errors.append(f"{path}: shorter than {schema['minLength']} characters")

    if _is_type(value, "number"):
        if "minimum" in schema and value < schema["minimum"]:
            errors.append(f"{path}: {value} < minimum {schema['minimum']}")
        if "maximum" in schema and value > schema["maximum"]:
            errors.append(f"{path}: {value} > maximum {schema['maximum']}")
        if "exclusiveMinimum" in schema and value <= schema["exclusiveMinimum"]:
            errors.append(f"{path}: {value} <= exclusiveMinimum "
                          f"{schema['exclusiveMinimum']}")
        if "exclusiveMaximum" in schema and value >= schema["exclusiveMaximum"]:
            errors.append(f"{path}: {value} >= exclusiveMaximum "
                          f"{schema['exclusiveMaximum']}")

    if isinstance(value, dict):
        for key in schema.get("required", []):
            if key not in value:
                errors.append(f"{path}: missing required '{key}'")
        props = schema.get("properties", {})
        for key, sub in props.items():
            if key in value:
                _validate(value[key], sub, f"{path}.{key}", errors)
        extra = schema.get("additionalProperties", True)
        if extra is False:
            for key in value:
                if key not in props:
                    errors.append(f"{path}: unexpected property '{key}'")
        elif isinstance(extra, dict):
            for key, item in value.items():
                if key not in props:
                    _validate(item, extra, f"{path}.{key}", errors)

    if isinstance(value, list) and "items" in schema:
        for i, item in enumerate(value):
            _validate(item, schema["items"], f"{path}[{i}]", errors)


def validate(spec, schema):
    """Return a list of readable error strings (empty means valid)."""
    errors = []
    _validate(spec, schema, "spec", errors)
    return errors


def unknown_keywords(schema, path="schema"):
    """Schema keys this validator would ignore (should stay empty)."""
    found = []
    if not isinstance(schema, dict):
        return found
    for key, value in schema.items():
        if key not in SUPPORTED and key not in ANNOTATION:
            found.append(f"{path}.{key}")
        if key == "properties" and isinstance(value, dict):
            for name, sub in value.items():
                found.extend(unknown_keywords(sub, f"{path}.{name}"))
        elif key in ("items", "additionalProperties") and isinstance(value, dict):
            found.extend(unknown_keywords(value, f"{path}.{key}"))
        elif key == "oneOf" and isinstance(value, list):
            for i, sub in enumerate(value):
                found.extend(unknown_keywords(sub, f"{path}.oneOf[{i}]"))
    return found


def load_schema(path):
    with open(os.path.expanduser(path), encoding="utf-8") as fh:
        return json.load(fh)


def main(argv):
    import argparse

    here = os.path.dirname(os.path.abspath(__file__))
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--spec", required=True)
    ap.add_argument("--schema", default=os.path.join(here, "spec",
                                                     "spec.schema.json"))
    args = ap.parse_args(argv)

    schema = load_schema(args.schema)
    holes = unknown_keywords(schema)
    if holes:
        print("schema keywords not enforced: " + ", ".join(holes))
    with open(os.path.expanduser(args.spec), encoding="utf-8") as fh:
        spec = json.load(fh)
    errors = validate(spec, schema)
    if errors:
        print(f"{args.spec}: {len(errors)} error(s)")
        for e in errors:
            print("  " + e)
        return 1
    print(f"{args.spec}: ok")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(__import__("sys").argv[1:]))

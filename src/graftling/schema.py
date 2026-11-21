"""Small JSON Schema validator shared by registered tool descriptions.

Only the vocabulary used by the registered schemas is supported, so an
unsupported constraint fails closed instead of silently weakening a schema.
Validation reports every violation of a value at once (555), so a caller can
fix them all before calling again.
"""

from __future__ import annotations

from typing import Any, Mapping


class ValidationErrors(ValueError):
    """Every problem found in one request, each as ``{"path", "reason"}``.

    ``str()`` joins them as ``"<path>: <reason>; ..."``, so a single problem
    reads exactly as before 555. A host may add its own problems and raise or
    report ``ValidationErrors(found.errors + mine)`` to return them in the
    same response.
    """

    def __init__(self, errors: list[dict[str, str]]) -> None:
        """Keep the list and build the readable joined message."""
        self.errors = errors
        super().__init__('; '.join(f"{error['path']}: {error['reason']}" for error in errors))


SUPPORTED = frozenset({
    "type", "properties", "required", "additionalProperties", "items",
    "enum", "const", "minItems", "description", "title", "default",
    "examples", "$schema", "$comment", "anyOf",
})

TYPE_CHECKS = {
    "object":  lambda value: isinstance(value, dict),
    "array":   lambda value: isinstance(value, list),
    "string":  lambda value: isinstance(value, str),
    "boolean": lambda value: isinstance(value, bool),
    "integer": lambda value: isinstance(value, int) and not isinstance(value, bool),
    "number":  lambda value: isinstance(value, (int, float)) and not isinstance(value, bool),
    "null":    lambda value: value is None,
}


def _type_matches(value: Any, schema: Mapping[str, Any]) -> bool:
    """Whether ``value`` has one of the schema's ``type``s; true without ``type``."""
    expected = schema.get("type", [])
    types    = [expected] if isinstance(expected, str) else expected
    return not types or any(TYPE_CHECKS.get(kind, lambda _: False)(value) for kind in types)


def schema_errors(value: Any, schema: Mapping[str, Any], path: str) -> list[dict[str, str]]:
    """Return every violation of ``schema`` by ``value``, in document order.

    Parameters
    ----------
    value
        Decoded JSON value supplied by a model or host.
    schema
        Schema using only ``type``, ``properties``, ``required``,
        ``additionalProperties``, ``items``, ``enum``, ``const``, ``minItems``,
        ``anyOf`` and annotation keywords.
    path
        Dotted location used in each error, for example ``"request"``.

    Returns
    -------
    list of dict
        ``{"path", "reason"}`` per violation; empty when the value is valid.
        Reasons name the schema's expectation, never the supplied value.
    """
    if set(schema) - SUPPORTED:
        return [{"path": path, "reason": "unsupported schema constraint"}]

    # A value of the wrong type has no fields worth checking further.
    if not _type_matches(value, schema):
        expected = schema["type"]
        types    = [expected] if isinstance(expected, str) else expected
        return [{"path": path, "reason": f"expected {' or '.join(types)}"}]

    errors: list[dict[str, str]] = []
    if "anyOf" in schema:
        errors += _any_of_errors(value, schema["anyOf"], path)

    # JSON booleans are distinct from numbers, unlike Python's True == 1.
    def equal(choice: Any) -> bool:
        """Compare the scalar enum/const values used by registered schemas."""
        return value == choice and isinstance(value, bool) == isinstance(choice, bool)

    if "const" in schema and not equal(schema["const"]):
        errors.append({"path": path, "reason": f"expected {schema['const']!r}"})
    if "enum" in schema and not any(equal(choice) for choice in schema["enum"]):
        errors.append({"path": path, "reason": f"expected one of {schema['enum']!r}"})

    if isinstance(value, dict):
        for name in schema.get("required", []):
            if name not in value:
                errors.append({"path": f"{path}.{name}", "reason": "required"})
        properties = schema.get("properties", {})
        additional = schema.get("additionalProperties", True)
        for name, item in value.items():
            if name in properties:
                errors += schema_errors(item, properties[name], f"{path}.{name}")
            elif additional is False:
                errors.append({"path": f"{path}.{name}", "reason": "unexpected parameter"})
            elif isinstance(additional, dict):
                errors += schema_errors(item, additional, f"{path}.{name}")
    if isinstance(value, list):
        if len(value) < schema.get("minItems", 0):
            errors.append({"path": path, "reason": f"expected at least {schema['minItems']} items"})
        for index, item in enumerate(value):
            errors += schema_errors(item, schema.get("items", {}), f"{path}[{index}]")
    return errors


def _any_of_errors(value: Any, alternatives: list, path: str) -> list[dict[str, str]]:
    """Report an unmatched ``anyOf`` readably.

    An alternative whose only complaint is the value's type at ``path`` was
    clearly not meant. When exactly one alternative remains, its own errors
    are the answer; otherwise one error lists each alternative's problems.
    """
    found = [schema_errors(value, alternative, path) for alternative in alternatives]
    if any(not errors for errors in found):
        return []

    meant = [errors for errors, alternative in zip(found, alternatives) if _type_matches(value, alternative)]
    if len(meant) == 1:
        return meant[0]

    described = " | ".join("; ".join(f"{error['path']}: {error['reason']}" for error in errors)
                           for errors in (meant or found))
    return [{"path": path, "reason": f"matches no alternative ({described})"}]


def validate(value: Any, schema: Mapping[str, Any], path: str) -> None:
    """Validate ``value`` against the supported JSON Schema vocabulary.

    Parameters are those of :func:`schema_errors`.

    Raises
    ------
    ValidationErrors
        A ``ValueError`` listing every violation; schemas are never dumped
        into the message.
    """
    errors = schema_errors(value, schema, path)
    if errors:
        raise ValidationErrors(errors)

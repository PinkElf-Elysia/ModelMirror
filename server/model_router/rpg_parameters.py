"""Typed RPG parameter contracts. Pure validation; no I/O or provider inference."""
from __future__ import annotations

from copy import deepcopy
import math

CAPABILITY_FORMAT = "modelmirror.text-parameter-capabilities/2"
CONFIG_FORMAT = "modelmirror.rpg.parameter-config/2"
PURPOSES = ("story", "summary", "memory")
# These are host safety ceilings, not a declaration that a model supports them.
SPECS = {
    "temperature": ("number", 0, 2, ("temperature",)),
    "topP": ("number", 0, 1, ("top_p",)),
    "maxOutputTokens": ("integer", 1, 1_000_000, ("max_tokens", "max_completion_tokens")),
    "topK": ("integer", 0, 1_000_000, ("top_k",)),
    "frequencyPenalty": ("number", -2, 2, ("frequency_penalty",)),
    "presencePenalty": ("number", -2, 2, ("presence_penalty",)),
    "seed": ("integer", -(2**53 - 1), 2**53 - 1, ("seed",)),
    "stop": ("text-list", None, None, ("stop",)),
    "thinking.enabled": ("boolean", None, None, ("reasoning.enabled",)),
    "thinking.effort": ("enum", None, None, ("reasoning.effort", "reasoning_effort")),
    "thinking.budgetTokens": ("integer", 0, 1_000_000, ("reasoning.max_tokens",)),
}
SUPPORTED_WIRE_PARAMETERS = frozenset(
    name for spec in SPECS.values() for wire in spec[3] for name in (wire, wire.split(".")[0])
)
EFFORTS = frozenset(("none", "minimal", "low", "medium", "high", "xhigh", "max"))


class ParameterError(ValueError):
    def __init__(self, suffix: str):
        self.code = "rpg_authoring_parameter_" + suffix
        super().__init__(self.code)


def _exact(value, names):
    if not isinstance(value, dict) or set(value) != set(names):
        raise ValueError("parameter_capabilities_invalid")


def _number(value, low, high, integer=False):
    return (type(value) in (int, float) and low <= value <= high
            and math.isfinite(value) and (not integer or int(value) == value))


def _text(value, maximum):
    return (isinstance(value, str) and 0 < len(value) <= maximum
            and not any(0xD800 <= ord(c) <= 0xDFFF for c in value))


def _value_valid(field, value):
    kind = field["type"]
    if kind in ("number", "integer"):
        return _number(value, field["minimum"], field["maximum"], kind == "integer")
    if kind == "boolean":
        return type(value) is bool
    if kind == "enum":
        return isinstance(value, str) and value in field["options"]
    if kind == "text-list":
        return (isinstance(value, list) and field["minItems"] <= len(value) <= field["maxItems"]
                and all(_text(v, field["maxItemLength"]) for v in value)
                and len(set(value)) == len(value))
    return False


def validate_policy(value):
    """Accept only bounded, declarative conditions and allowlisted wire mappings."""
    _exact(value, ("kind", "fields", "exclusive", "requires", "lessThan", "purposes"))
    if value["kind"] not in ("fixed", "editable"):
        raise ValueError("parameter_capabilities_invalid")
    fields = value["fields"]
    if (not isinstance(fields, dict) or "maxOutputTokens" not in fields
            or not set(fields) <= set(SPECS)):
        raise ValueError("parameter_capabilities_invalid")
    purposes = value["purposes"]
    if (not isinstance(purposes, list) or not 1 <= len(purposes) <= 3
            or any(p not in PURPOSES for p in purposes) or len(set(purposes)) != len(purposes)):
        raise ValueError("parameter_capabilities_invalid")
    wires = set()
    for key, field in fields.items():
        kind, low, high, names = SPECS[key]
        extra = {"number": ("minimum", "maximum"), "integer": ("minimum", "maximum"),
                 "boolean": (), "enum": ("options",),
                 "text-list": ("minItems", "maxItems", "maxItemLength")}[kind]
        _exact(field, ("type", "required", "default", "wireName", *extra))
        if (field["type"] != kind or type(field["required"]) is not bool
                or field["wireName"] not in names or field["wireName"] in wires):
            raise ValueError("parameter_capabilities_invalid")
        wires.add(field["wireName"])
        if kind in ("number", "integer"):
            if (not all(_number(field[k], low, high, kind == "integer") for k in extra)
                    or field["minimum"] > field["maximum"]):
                raise ValueError("parameter_capabilities_invalid")
        elif kind == "enum":
            choices = field["options"]
            if (not isinstance(choices, list) or not 1 <= len(choices) <= len(EFFORTS)
                    or any(not isinstance(c, str) or c not in EFFORTS for c in choices)
                    or len(set(choices)) != len(choices)):
                raise ValueError("parameter_capabilities_invalid")
        elif kind == "text-list":
            if (any(type(field[k]) is not int for k in extra)
                    or not 0 <= field["minItems"] <= field["maxItems"] <= 16
                    or not 1 <= field["maxItemLength"] <= 1024):
                raise ValueError("parameter_capabilities_invalid")
        default = field["default"]
        if not isinstance(default, dict) or default.get("mode") not in ("omit", "value"):
            raise ValueError("parameter_capabilities_invalid")
        _exact(default, ("mode", "value") if default["mode"] == "value" else ("mode",))
        if default["mode"] == "value" and not _value_valid(field, default["value"]):
            raise ValueError("parameter_capabilities_invalid")
        if key == "maxOutputTokens" and not field["required"]:
            raise ValueError("parameter_capabilities_invalid")
        if value["kind"] == "fixed" and (not field["required"] or default["mode"] != "value"):
            raise ValueError("parameter_capabilities_invalid")
    for name, maximum in (("exclusive", 16), ("requires", 32), ("lessThan", 16)):
        if not isinstance(value[name], list) or len(value[name]) > maximum:
            raise ValueError("parameter_capabilities_invalid")
    seen = set()
    for group in value["exclusive"]:
        if (not isinstance(group, list) or not 2 <= len(group) <= len(SPECS)
                or any(not isinstance(k, str) or k not in fields for k in group)
                or len(set(group)) != len(group) or tuple(sorted(group)) in seen):
            raise ValueError("parameter_capabilities_invalid")
        seen.add(tuple(sorted(group)))
    for rule in value["requires"]:
        _exact(rule, ("if", "then"))
        for condition in rule.values():
            if not isinstance(condition, dict) or condition.get("op") not in ("set", "unset", "eq"):
                raise ValueError("parameter_capabilities_invalid")
            _exact(condition, ("field", "op", "value") if condition["op"] == "eq" else ("field", "op"))
            key = condition["field"]
            if not isinstance(key, str) or key not in fields:
                raise ValueError("parameter_capabilities_invalid")
            if condition["op"] == "eq" and not _value_valid(fields[key], condition["value"]):
                raise ValueError("parameter_capabilities_invalid")
    seen = set()
    for pair in value["lessThan"]:
        if (not isinstance(pair, list) or len(pair) != 2
                or any(not isinstance(k, str) or k not in fields
                       or fields[k]["type"] not in ("number", "integer") for k in pair)
                or pair[0] == pair[1] or tuple(pair) in seen):
            raise ValueError("parameter_capabilities_invalid")
        seen.add(tuple(pair))
    return deepcopy(value)


def upgrade_legacy(policy):
    """v1 optional defaults are editor suggestions, NOT values to send implicitly."""
    fields = {}
    for key, field in policy["fields"].items():
        fields[key] = {"type": SPECS[key][0], "minimum": field["minimum"],
                       "maximum": field["maximum"], "required": field["required"],
                       "wireName": field["wireName"],
                       "default": {"mode": "value", "value": field["defaultValue"]}
                       if field["required"] else {"mode": "omit"}}
    return validate_policy({"kind": policy["kind"], "fields": fields,
                            "exclusive": policy["exclusive"], "requires": [],
                            "lessThan": [], "purposes": list(PURPOSES)})


def public_policy(policy):
    result = deepcopy(policy)
    for field in result["fields"].values():
        del field["wireName"]
    return result


def supports_policy(policy, supported):
    if not isinstance(supported, list) or any(not isinstance(p, str) for p in supported):
        return False
    return all(f["wireName"] in supported or f["wireName"].split(".")[0] in supported
               for f in policy["fields"].values())


def _matches(condition, values):
    key = condition["field"]
    if condition["op"] == "unset":
        return key not in values
    return key in values and (condition["op"] == "set" or values[key] == condition["value"])


def resolve_parameters(policy, config, purpose):
    """Resolve to an immutable-by-copy snapshot. Missing keys explicitly mean default."""
    policy = validate_policy(policy)
    purpose = "summary" if purpose == "compression" else purpose
    if purpose not in policy["purposes"]:
        raise ParameterError("purpose")
    if (not isinstance(config, dict) or set(config) != {"format", "values"}
            or config["format"] != CONFIG_FORMAT or not isinstance(config["values"], dict)):
        raise ParameterError("invalid")
    choices, fields = config["values"], policy["fields"]
    if not set(choices) <= set(fields):
        raise ParameterError("unsupported")
    effective, sources = {}, {}
    for key, field in fields.items():
        choice = choices.get(key, {"mode": "default"})
        if not isinstance(choice, dict) or choice.get("mode") not in ("default", "explicit"):
            raise ParameterError("invalid")
        mode = choice["mode"]
        if set(choice) != ({"mode", "value"} if mode == "explicit" else {"mode"}):
            raise ParameterError("invalid")
        default = field["default"]
        if mode == "default" and default["mode"] == "omit":
            if field["required"]:
                raise ParameterError("required")
            sources[key] = {"mode": mode, "resolution": "omitted"}
            continue
        chosen = choice["value"] if mode == "explicit" else default["value"]
        if not _value_valid(field, chosen):
            raise ParameterError("range")
        if policy["kind"] == "fixed" and chosen != default["value"]:
            raise ParameterError("range")
        effective[key] = deepcopy(int(chosen) if field["type"] == "integer" else chosen)
        sources[key] = {"mode": mode, "resolution": "explicit" if mode == "explicit" else "declared-default"}
    if any(sum(k in effective for k in group) > 1 for group in policy["exclusive"]):
        raise ParameterError("conflict")
    if "thinking.effort" in effective and "thinking.budgetTokens" in effective:
        raise ParameterError("conflict")
    if effective.get("thinking.enabled") is False and any(
            k in effective for k in ("thinking.effort", "thinking.budgetTokens")):
        raise ParameterError("conflict")
    for rule in policy["requires"]:
        if _matches(rule["if"], effective) and not _matches(rule["then"], effective):
            raise ParameterError("dependency")
    for left, right in policy["lessThan"]:
        if left in effective and (right not in effective or effective[left] >= effective[right]):
            raise ParameterError("dependency")
    wire = {}
    for key, val in effective.items():
        path = fields[key]["wireName"].split(".")
        if len(path) == 1:
            wire[path[0]] = deepcopy(val)
        else:
            wire.setdefault(path[0], {})[path[1]] = deepcopy(val)
    return {"purpose": purpose, "effectiveParameters": effective, "sources": sources, "wireParameters": wire}

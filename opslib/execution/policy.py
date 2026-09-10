from __future__ import annotations

import json
from pathlib import Path
import re
import tomllib

from .common import ROOT, Blocked, digest

POLICY = ROOT / "execution-policy.toml"
YOLO_LAUNCH = {"permissions": "yolo", "approval_policy": "never", "sandbox": "none"}


def load(path=POLICY):
    raw = Path(path).read_bytes()
    policy = tomllib.loads(raw.decode())
    if type(policy.get("schema_version")) is not int or policy["schema_version"] not in (1, 2) or not policy.get("policy_version"):
        raise Blocked("unsupported policy schema or missing policy version")
    if policy.get("applies_to") != "new-runs-only":
        raise Blocked("policy must apply to new runs only")
    if policy.get("constraints") != {"paid_fallback": False, "automatic_substitution": False,
            "credential_changes": False, "required_observations": True}:
        raise Blocked("policy weakens required economic or observation constraints")
    if policy["schema_version"] == 2 and policy.get("launch") != YOLO_LAUNCH:
        raise Blocked("new-run policy requires YOLO permissions, no approval prompts and no native sandbox")
    if policy["schema_version"] == 1 and "launch" in policy:
        raise Blocked("launch permissions require policy schema v2")
    routes = policy.get("routes", {})
    for name, route in routes.items():
        required = {"role", "task_class", "provider", "model", "effort", "billing", "review", "escalation"}
        if set(route) != required or route["escalation"] not in {*routes, "principal"}:
            raise Blocked(f"invalid route contract: {name}")
    return policy, raw, digest(raw)


def resolve(name, path=POLICY):
    policy, raw, sha = load(path)
    if name not in policy["routes"]:
        raise Blocked(f"unknown route: {name}")
    return {"policy_version": policy["policy_version"], "policy_sha256": sha,
            "launch": policy.get("launch"),
            "route_id": name, **policy["routes"][name]}


def validate_schema(value, schema, at="$", root=None):
    """Strict subset used by our published schemas; unsupported keywords fail closed.

    Avoids adding a dependency to the existing stdlib-only CLI. The schemas
    intentionally use only these draft-2020-12 validation keywords.
    """
    root = root or schema
    allowed = {"$schema", "$id", "title", "description", "$defs", "$ref", "type", "const",
               "enum", "required", "properties", "additionalProperties", "items", "minItems",
               "uniqueItems", "minLength", "pattern", "minimum", "maximum"}
    if set(schema) - allowed:
        raise Blocked(f"unsupported schema keywords at {at}")
    if "$ref" in schema:
        ref = schema["$ref"]
        if not ref.startswith("#/$defs/"):
            raise Blocked("only local schema references are supported")
        return validate_schema(value, root["$defs"][ref.split("/")[-1]], at, root)
    types = schema.get("type")
    types = types if isinstance(types, list) else [types] if types else []
    checks = {"object": lambda x: isinstance(x, dict), "array": lambda x: isinstance(x, list),
              "string": lambda x: isinstance(x, str), "integer": lambda x: type(x) is int,
              "number": lambda x: type(x) in (int, float), "boolean": lambda x: type(x) is bool,
              "null": lambda x: x is None}
    if types and not any(checks[t](value) for t in types):
        raise Blocked(f"{at}: expected {'/'.join(types)}")
    if "const" in schema and (type(value) != type(schema["const"]) or value != schema["const"]):
        raise Blocked(f"{at}: wrong contract version or constant")
    if "enum" in schema and value not in schema["enum"]:
        raise Blocked(f"{at}: unsupported value")
    if isinstance(value, dict):
        if set(schema.get("required", [])) - set(value):
            raise Blocked(f"{at}: missing required fields")
        properties = schema.get("properties", {})
        if schema.get("additionalProperties") is False and set(value) - set(properties):
            raise Blocked(f"{at}: unknown fields")
        for key, item in value.items():
            if key in properties:
                validate_schema(item, properties[key], at + "." + key, root)
    if isinstance(value, list):
        if len(value) < schema.get("minItems", 0):
            raise Blocked(f"{at}: empty required list")
        if schema.get("uniqueItems") and len({json.dumps(x, sort_keys=True) for x in value}) != len(value):
            raise Blocked(f"{at}: duplicate items")
        for i, item in enumerate(value):
            validate_schema(item, schema.get("items", {}), f"{at}[{i}]", root)
    if isinstance(value, str):
        if len(value) < schema.get("minLength", 0) or ("pattern" in schema and not re.search(schema["pattern"], value)):
            raise Blocked(f"{at}: invalid string")
    if type(value) in (int, float):
        if value < schema.get("minimum", float("-inf")) or value > schema.get("maximum", float("inf")):
            raise Blocked(f"{at}: outside runtime bounds")


def validate_packet(packet):
    version = packet.get("schema_version") if isinstance(packet, dict) else None
    if type(version) is not int or version not in (1, 2):
        raise Blocked("unsupported task packet schema")
    schema = json.loads((ROOT / f"schemas/task-packet-v{version}.json").read_text())
    validate_schema(packet, schema)
    scope = packet["scope"]
    for path in scope["write_paths"] + scope["read_paths"]:
        if Path(path).is_absolute() or ".." in Path(path).parts or path in ("", ".", ".git") or ".git" in Path(path).parts:
            raise Blocked("scope paths must be explicit repository-relative paths")
    if packet["role"] in ("reviewer", "diagnostician") and scope["write_paths"]:
        raise Blocked("review and diagnosis require read-only scope")
    if packet["limits"]["corrections"] > 1 or packet["limits"]["depth"] > 3:
        raise Blocked("correction or depth limit exceeded")
    if packet["task_id"] in packet["dependencies"]:
        raise Blocked("task cannot depend on itself")
    return packet


def require_new_run_permissions(packet, route):
    if packet["schema_version"] != 2:
        raise Blocked("new runs require a v2 YOLO packet; retain historical v1 packets unchanged")
    if route.get("launch") != YOLO_LAUNCH or packet["authorization"]["permissions"] != "yolo":
        raise Blocked("new-run packet and policy must request YOLO permissions")

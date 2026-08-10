class_name MatrixOasisGodotRuntime
extends RefCounted

const DEFAULT_STEP_LIMIT := 256
const MAX_STEP_LIMIT := 10000
const MIN_SAFE_INTEGER := -9007199254740991
const MAX_SAFE_INTEGER := 9007199254740991

const SNAPSHOT_FIELDS := [
	"snapshotVersion", "pack", "status", "location", "variables", "stepCount", "stepLimit",
]
const IDENTITY_FIELDS := [
	"format", "formatVersion", "sourceFormat", "sourceFormatVersion", "id",
	"contentVersion", "sourceSha256", "artifactSha256",
]

const FAILURE_DEFINITIONS := {
	"PACK_RUNTIME_PREPARED_PACK_INVALID": "/prepared",
	"PACK_RUNTIME_OPTIONS_INVALID": "/options",
	"PACK_RUNTIME_INVALID_SNAPSHOT": "/snapshot",
	"PACK_RUNTIME_PACK_MISMATCH": "/snapshot/pack",
	"PACK_RUNTIME_SESSION_ENDED": "/snapshot/status",
	"PACK_RUNTIME_STEP_LIMIT": "/snapshot/stepCount",
	"PACK_RUNTIME_ACTION_UNKNOWN": "/actionId",
	"PACK_RUNTIME_ACTION_UNAVAILABLE": "/actionId",
	"PACK_RUNTIME_INTEGER_OVERFLOW": "/snapshot/variables",
	"PACK_GODOT_RUNTIME_INTERNAL_ERROR": "/runtime",
}


static func create_game_session(prepared: Variant, options: Variant = {}) -> Dictionary:
	var context := _prepared_context(prepared)
	if not context["ok"]:
		return context
	var option_result := _capture_options(options)
	if not option_result["ok"]:
		return _failure("PACK_RUNTIME_OPTIONS_INVALID")
	var pack: Dictionary = context["pack"]
	var variables: Array = []
	for variable in pack["variables"]:
		variables.append(variable["initial"])
	var snapshot := _make_snapshot(
		context,
		"active",
		{"kind": "node", "index": pack["entryNodeIndex"]},
		variables,
		0,
		option_result["step_limit"],
	)
	var inspection := _build_inspection(context, snapshot)
	if inspection.is_empty():
		return _failure("PACK_GODOT_RUNTIME_INTERNAL_ERROR")
	return _success({
		"ok": true,
		"snapshot": snapshot,
		"inspection": inspection,
		"emittedCues": _cue_descriptors(pack, pack["nodes"][pack["entryNodeIndex"]]["entryCueIndexes"]),
	})


static func inspect_game_session(prepared: Variant, snapshot_input: Variant) -> Dictionary:
	var context := _prepared_context(prepared)
	if not context["ok"]:
		return context
	var captured := _capture_snapshot(context, snapshot_input)
	if not captured["ok"]:
		return _failure(captured["code"])
	var inspection := _build_inspection(context, captured["snapshot"])
	if inspection.is_empty():
		return _failure("PACK_GODOT_RUNTIME_INTERNAL_ERROR")
	return _success({"ok": true, "inspection": inspection})


static func apply_game_session_action(
	prepared: Variant,
	snapshot_input: Variant,
	action_id: Variant,
) -> Dictionary:
	var context := _prepared_context(prepared)
	if not context["ok"]:
		return context
	var captured := _capture_snapshot(context, snapshot_input)
	if not captured["ok"]:
		return _failure(captured["code"])
	var snapshot: Dictionary = captured["snapshot"]
	if snapshot["status"] == "ended":
		return _failure("PACK_RUNTIME_SESSION_ENDED")
	if snapshot["stepCount"] >= snapshot["stepLimit"]:
		return _failure("PACK_RUNTIME_STEP_LIMIT")
	var pack: Dictionary = context["pack"]
	var source_node: Dictionary = pack["nodes"][snapshot["location"]["index"]]
	var action: Variant = null
	if typeof(action_id) == TYPE_STRING:
		for candidate in source_node["actions"]:
			if candidate["id"] == action_id:
				action = candidate
				break
	if action == null:
		return _failure("PACK_RUNTIME_ACTION_UNKNOWN")
	if not _evaluate_condition(action["when"], snapshot["variables"]):
		return _failure("PACK_RUNTIME_ACTION_UNAVAILABLE")
	var effect_result := _apply_effects(pack, action, snapshot["variables"])
	if not effect_result["ok"]:
		return _failure(effect_result["code"])
	var target_collection: Array = pack["nodes"] if action["target"]["kind"] == "node" else pack["endings"]
	var target: Dictionary = target_collection[action["target"]["index"]]
	var target_cue_indexes: Array = target["entryCueIndexes"] if action["target"]["kind"] == "node" else target["cueIndexes"]
	var emitted_cues: Array = effect_result["emitted_cues"]
	emitted_cues.append_array(_cue_descriptors(pack, target_cue_indexes))
	var step: int = snapshot["stepCount"] + 1
	var next_snapshot := _make_snapshot(
		context,
		"active" if action["target"]["kind"] == "node" else "ended",
		{"kind": action["target"]["kind"], "index": action["target"]["index"]},
		effect_result["variables"],
		step,
		snapshot["stepLimit"],
	)
	var inspection := _build_inspection(context, next_snapshot)
	if inspection.is_empty():
		return _failure("PACK_GODOT_RUNTIME_INTERNAL_ERROR")
	return _success({
		"ok": true,
		"snapshot": next_snapshot,
		"inspection": inspection,
		"transition": {
			"transitionVersion": 1,
			"step": step,
			"from": {
				"kind": "node",
				"index": snapshot["location"]["index"],
				"id": source_node["id"],
			},
			"actionId": action["id"],
			"to": {
				"kind": action["target"]["kind"],
				"index": action["target"]["index"],
				"id": target["id"],
			},
			"emittedCues": emitted_cues,
		},
	})


static func _prepared_context(prepared: Variant) -> Dictionary:
	if not prepared is MatrixOasisPreparedRuntimePack:
		return _failure("PACK_RUNTIME_PREPARED_PACK_INVALID")
	var pack: Dictionary = prepared._copy_pack_for_runtime()
	var receipt: Dictionary = prepared._copy_receipt_for_runtime()
	if not MatrixOasisRuntimePackRules.validate_runtime_pack(pack).get("ok", false):
		return _failure("PACK_RUNTIME_PREPARED_PACK_INVALID")
	if not MatrixOasisRuntimePackRules.validate_receipt(receipt).get("ok", false):
		return _failure("PACK_RUNTIME_PREPARED_PACK_INVALID")
	if receipt["artifact"]["sha256"] != prepared.artifact_sha256():
		return _failure("PACK_RUNTIME_PREPARED_PACK_INVALID")
	var canonical_bytes := JSON.stringify(pack, "", true).to_utf8_buffer()
	if not MatrixOasisStrictJson.parse_bytes(canonical_bytes, "/runtimePack").get("ok", false):
		return _failure("PACK_RUNTIME_PREPARED_PACK_INVALID")
	if canonical_bytes.size() != receipt["artifact"]["byteLength"] or (
		MatrixOasisRuntimeArtifactLoader._sha256_hex(canonical_bytes) != receipt["artifact"]["sha256"]
	):
		return _failure("PACK_RUNTIME_PREPARED_PACK_INVALID")
	return {"ok": true, "pack": pack, "receipt": receipt}


static func _capture_options(options: Variant) -> Dictionary:
	if typeof(options) != TYPE_DICTIONARY:
		return {"ok": false}
	if options.is_empty():
		return {"ok": true, "step_limit": DEFAULT_STEP_LIMIT}
	if not _has_exact_fields(options, ["stepLimit"]):
		return {"ok": false}
	var value: Variant = options["stepLimit"]
	if typeof(value) != TYPE_INT or value < 1 or value > MAX_STEP_LIMIT:
		return {"ok": false}
	return {"ok": true, "step_limit": value}


static func _make_identity(context: Dictionary) -> Dictionary:
	var pack: Dictionary = context["pack"]
	return {
		"format": pack["format"],
		"formatVersion": pack["formatVersion"],
		"sourceFormat": pack["source"]["format"],
		"sourceFormatVersion": pack["source"]["formatVersion"],
		"id": pack["source"]["id"],
		"contentVersion": pack["source"]["contentVersion"],
		"sourceSha256": pack["source"]["canonicalSha256"],
		"artifactSha256": context["receipt"]["artifact"]["sha256"],
	}


static func _make_snapshot(
	context: Dictionary,
	status: String,
	location: Dictionary,
	variables: Array,
	step_count: int,
	step_limit: int,
) -> Dictionary:
	return {
		"snapshotVersion": 1,
		"pack": _make_identity(context),
		"status": status,
		"location": {"kind": location["kind"], "index": location["index"]},
		"variables": variables.duplicate(),
		"stepCount": step_count,
		"stepLimit": step_limit,
	}


static func _capture_snapshot(context: Dictionary, input: Variant) -> Dictionary:
	if not _has_exact_fields(input, SNAPSHOT_FIELDS):
		return {"ok": false, "code": "PACK_RUNTIME_INVALID_SNAPSHOT"}
	if typeof(input["snapshotVersion"]) != TYPE_INT or input["snapshotVersion"] != 1 or (
		not _has_exact_fields(input["pack"], IDENTITY_FIELDS)
	):
		return {"ok": false, "code": "PACK_RUNTIME_INVALID_SNAPSHOT"}
	for field in IDENTITY_FIELDS:
		if typeof(input["pack"][field]) != TYPE_STRING:
			return {"ok": false, "code": "PACK_RUNTIME_INVALID_SNAPSHOT"}
	var expected_identity := _make_identity(context)
	for field in IDENTITY_FIELDS:
		if input["pack"][field] != expected_identity[field]:
			return {"ok": false, "code": "PACK_RUNTIME_PACK_MISMATCH"}
	if typeof(input["status"]) != TYPE_STRING or input["status"] not in ["active", "ended"] or (
		not _has_exact_fields(input["location"], ["kind", "index"])
	):
		return {"ok": false, "code": "PACK_RUNTIME_INVALID_SNAPSHOT"}
	if typeof(input["location"]["kind"]) != TYPE_STRING:
		return {"ok": false, "code": "PACK_RUNTIME_INVALID_SNAPSHOT"}
	if typeof(input["location"]["index"]) != TYPE_INT or input["location"]["index"] < 0:
		return {"ok": false, "code": "PACK_RUNTIME_INVALID_SNAPSHOT"}
	var pack: Dictionary = context["pack"]
	if input["status"] == "active":
		if input["location"]["kind"] != "node" or input["location"]["index"] >= pack["nodes"].size():
			return {"ok": false, "code": "PACK_RUNTIME_INVALID_SNAPSHOT"}
	elif input["location"]["kind"] != "ending" or input["location"]["index"] >= pack["endings"].size():
		return {"ok": false, "code": "PACK_RUNTIME_INVALID_SNAPSHOT"}
	if typeof(input["variables"]) != TYPE_ARRAY or input["variables"].size() != pack["variables"].size():
		return {"ok": false, "code": "PACK_RUNTIME_INVALID_SNAPSHOT"}
	for index in range(pack["variables"].size()):
		if not _valid_variable_value(pack["variables"][index], input["variables"][index]):
			return {"ok": false, "code": "PACK_RUNTIME_INVALID_SNAPSHOT"}
	if typeof(input["stepCount"]) != TYPE_INT or input["stepCount"] < 0:
		return {"ok": false, "code": "PACK_RUNTIME_INVALID_SNAPSHOT"}
	if typeof(input["stepLimit"]) != TYPE_INT or input["stepLimit"] < 1 or input["stepLimit"] > MAX_STEP_LIMIT:
		return {"ok": false, "code": "PACK_RUNTIME_INVALID_SNAPSHOT"}
	if input["stepCount"] > input["stepLimit"]:
		return {"ok": false, "code": "PACK_RUNTIME_INVALID_SNAPSHOT"}
	return {
		"ok": true,
		"snapshot": _make_snapshot(
			context,
			input["status"],
			input["location"],
			input["variables"],
			input["stepCount"],
			input["stepLimit"],
		),
	}


static func _valid_variable_value(variable: Dictionary, value: Variant) -> bool:
	match variable["type"]:
		"boolean":
			return typeof(value) == TYPE_BOOL
		"integer":
			return typeof(value) == TYPE_INT and value >= MIN_SAFE_INTEGER and value <= MAX_SAFE_INTEGER
		"enum":
			return typeof(value) == TYPE_STRING and value in variable["allowedValues"]
	return false


static func _evaluate_condition(condition: Variant, variables: Array) -> bool:
	if condition == null:
		return true
	match condition["op"]:
		"all":
			for child in condition["conditions"]:
				if not _evaluate_condition(child, variables):
					return false
			return true
		"any":
			for child in condition["conditions"]:
				if _evaluate_condition(child, variables):
					return true
			return false
		"not":
			return not _evaluate_condition(condition["condition"], variables)
		"eq":
			return variables[condition["variableIndex"]] == condition["value"]
		"ne":
			return variables[condition["variableIndex"]] != condition["value"]
		"lt":
			return variables[condition["variableIndex"]] < condition["value"]
		"lte":
			return variables[condition["variableIndex"]] <= condition["value"]
		"gt":
			return variables[condition["variableIndex"]] > condition["value"]
		"gte":
			return variables[condition["variableIndex"]] >= condition["value"]
	return false


static func _apply_effects(pack: Dictionary, action: Dictionary, source_variables: Array) -> Dictionary:
	var variables := source_variables.duplicate()
	var emitted_cues: Array = []
	for effect in action["effects"]:
		match effect["op"]:
			"set":
				variables[effect["variableIndex"]] = effect["value"]
			"add":
				var next_value: int = variables[effect["variableIndex"]] + effect["value"]
				if next_value < MIN_SAFE_INTEGER or next_value > MAX_SAFE_INTEGER:
					return {"ok": false, "code": "PACK_RUNTIME_INTEGER_OVERFLOW"}
				variables[effect["variableIndex"]] = next_value
			"emitCue":
				emitted_cues.append(_cue_descriptor(pack, effect["cueIndex"]))
			_:
				return {"ok": false, "code": "PACK_GODOT_RUNTIME_INTERNAL_ERROR"}
	return {"ok": true, "variables": variables, "emitted_cues": emitted_cues}


static func _build_inspection(context: Dictionary, snapshot: Dictionary) -> Dictionary:
	var pack: Dictionary = context["pack"]
	var active: bool = snapshot["status"] == "active"
	var collection: Array = pack["nodes"] if active else pack["endings"]
	var location: Dictionary = collection[snapshot["location"]["index"]]
	var location_entities: Array = _entity_ids(pack, location["entityIndexes"]) if active else []
	var variables: Array = []
	for index in range(pack["variables"].size()):
		variables.append({
			"id": pack["variables"][index]["id"],
			"type": pack["variables"][index]["type"],
			"value": snapshot["variables"][index],
		})
	var actions: Array = []
	if active:
		var can_advance: bool = snapshot["stepCount"] < snapshot["stepLimit"]
		for action in location["actions"]:
			actions.append({
				"id": action["id"],
				"label": action["label"],
				"entityIds": _entity_ids(pack, action["entityIndexes"]),
				"available": can_advance and _evaluate_condition(action["when"], snapshot["variables"]),
			})
	return {
		"inspectionVersion": 1,
		"pack": {
			"format": snapshot["pack"]["format"],
			"formatVersion": snapshot["pack"]["formatVersion"],
			"sourceFormat": snapshot["pack"]["sourceFormat"],
			"sourceFormatVersion": snapshot["pack"]["sourceFormatVersion"],
			"id": snapshot["pack"]["id"],
			"contentVersion": snapshot["pack"]["contentVersion"],
			"sourceSha256": snapshot["pack"]["sourceSha256"],
			"artifactSha256": snapshot["pack"]["artifactSha256"],
			"language": pack["language"],
			"title": pack["title"],
			"summary": pack["summary"],
		},
		"status": snapshot["status"],
		"location": {
			"kind": snapshot["location"]["kind"],
			"index": snapshot["location"]["index"],
			"id": location["id"],
			"title": location["title"],
			"text": location["text"],
			"entityIds": location_entities,
		},
		"variables": variables,
		"actions": actions,
		"stepCount": snapshot["stepCount"],
		"stepLimit": snapshot["stepLimit"],
	}


static func _cue_descriptors(pack: Dictionary, indexes: Array) -> Array:
	var output: Array = []
	for index in indexes:
		output.append(_cue_descriptor(pack, index))
	return output


static func _cue_descriptor(pack: Dictionary, index: int) -> Dictionary:
	var cue: Dictionary = pack["cues"][index]
	return {"id": cue["id"], "channel": cue["channel"], "intent": cue["intent"]}


static func _entity_ids(pack: Dictionary, indexes: Array) -> Array:
	var output: Array = []
	for index in indexes:
		output.append(pack["entities"][index]["id"])
	return output


static func _has_exact_fields(value: Variant, fields: Array) -> bool:
	if typeof(value) != TYPE_DICTIONARY or value.size() != fields.size():
		return false
	for key in value.keys():
		if typeof(key) != TYPE_STRING:
			return false
	for field in fields:
		if not value.has(field):
			return false
	return true


static func _failure(code: String) -> Dictionary:
	if not FAILURE_DEFINITIONS.has(code):
		code = "PACK_GODOT_RUNTIME_INTERNAL_ERROR"
	var diagnostic := {
		"phase": "runtime",
		"severity": "error",
		"code": code,
		"path": FAILURE_DEFINITIONS[code],
		"message": code,
	}
	var result := {"ok": false, "diagnostics": [diagnostic]}
	_deep_read_only(result)
	return result


static func _success(result: Dictionary) -> Dictionary:
	_deep_read_only(result)
	return result


static func _deep_read_only(value: Variant) -> void:
	if typeof(value) == TYPE_DICTIONARY:
		for key in value.keys():
			_deep_read_only(value[key])
		value.make_read_only()
	elif typeof(value) == TYPE_ARRAY:
		for child in value:
			_deep_read_only(child)
		value.make_read_only()

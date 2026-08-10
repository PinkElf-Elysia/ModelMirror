class_name MatrixOasisRuntimePackRules
extends RefCounted

const RUNTIME_FORMAT := "matrix-oasis.runtime-game-pack"
const RUNTIME_VERSION := "0.1.0"
const AUTHORING_FORMAT := "matrix-oasis.authoring-game-pack"
const AUTHORING_VERSION := "0.1.0"
const RECEIPT_FORMAT := "matrix-oasis.runtime-game-pack-receipt"
const CANONICALIZATION := "matrix-oasis.canonical-json/1"
const COMPILER_ID := "@matrix-oasis/game-pack-compiler"
const COMPILER_VERSION := "0.1.0-r3"
const MAX_CONDITION_DEPTH := 16


static func validate_runtime_pack(pack: Dictionary) -> Dictionary:
	var failure := _exact_object(pack, [
		"format", "formatVersion", "canonicalization", "source", "language",
		"title", "summary", "entryNodeIndex", "entities", "variables", "cues",
		"nodes", "endings",
	], "/runtimePack")
	if not failure.is_empty():
		return failure
	if pack["format"] != RUNTIME_FORMAT or pack["formatVersion"] != RUNTIME_VERSION:
		return _failure("schema", "GODOT_RUNTIME_SCHEMA_INVALID", "/runtimePack")
	if pack["canonicalization"] != CANONICALIZATION:
		return _failure("schema", "GODOT_RUNTIME_SCHEMA_INVALID", "/runtimePack/canonicalization")
	failure = _validate_source(pack["source"])
	if not failure.is_empty():
		return failure
	if not _valid_language(pack["language"]):
		return _failure("schema", "GODOT_RUNTIME_SCHEMA_INVALID", "/runtimePack/language")
	if not _valid_prose(pack["title"]):
		return _failure("schema", "GODOT_RUNTIME_SCHEMA_INVALID", "/runtimePack/title")
	if pack["summary"] != null and not _valid_prose(pack["summary"]):
		return _failure("schema", "GODOT_RUNTIME_SCHEMA_INVALID", "/runtimePack/summary")
	if not _valid_index(pack["entryNodeIndex"]):
		return _failure("schema", "GODOT_RUNTIME_SCHEMA_INVALID", "/runtimePack/entryNodeIndex")
	for field in ["entities", "variables", "cues", "nodes", "endings"]:
		if typeof(pack[field]) != TYPE_ARRAY:
			return _failure("schema", "GODOT_RUNTIME_SCHEMA_INVALID", "/runtimePack/%s" % field)
	if pack["nodes"].is_empty() or pack["nodes"].size() > 4096:
		return _failure("schema", "GODOT_RUNTIME_SCHEMA_INVALID", "/runtimePack/nodes")
	if pack["endings"].is_empty():
		return _failure("schema", "GODOT_RUNTIME_SCHEMA_INVALID", "/runtimePack/endings")

	for index in range(pack["entities"].size()):
		failure = _validate_entity(pack["entities"][index], "/runtimePack/entities/%d" % index)
		if not failure.is_empty():
			return failure
	for index in range(pack["variables"].size()):
		failure = _validate_variable(pack["variables"][index], "/runtimePack/variables/%d" % index)
		if not failure.is_empty():
			return failure
	for index in range(pack["cues"].size()):
		failure = _validate_cue(pack["cues"][index], "/runtimePack/cues/%d" % index)
		if not failure.is_empty():
			return failure
	for index in range(pack["nodes"].size()):
		failure = _validate_node(pack["nodes"][index], "/runtimePack/nodes/%d" % index)
		if not failure.is_empty():
			return failure
	for index in range(pack["endings"].size()):
		failure = _validate_ending(pack["endings"][index], "/runtimePack/endings/%d" % index)
		if not failure.is_empty():
			return failure
	return _validate_semantics(pack)


static func validate_receipt(receipt: Dictionary) -> Dictionary:
	var failure := _exact_object(
		receipt,
		["format", "formatVersion", "canonicalization", "compiler", "artifact"],
		"/receipt",
	)
	if not failure.is_empty():
		return failure
	if receipt["format"] != RECEIPT_FORMAT or receipt["formatVersion"] != RUNTIME_VERSION:
		return _failure("schema", "GODOT_RUNTIME_RECEIPT_SCHEMA_INVALID", "/receipt")
	if receipt["canonicalization"] != CANONICALIZATION:
		return _failure("schema", "GODOT_RUNTIME_RECEIPT_SCHEMA_INVALID", "/receipt/canonicalization")
	failure = _exact_object(receipt["compiler"], ["id", "version"], "/receipt/compiler")
	if not failure.is_empty():
		return _receipt_schema_failure("/receipt/compiler")
	if receipt["compiler"]["id"] != COMPILER_ID or receipt["compiler"]["version"] != COMPILER_VERSION:
		return _receipt_schema_failure("/receipt/compiler")
	failure = _exact_object(
		receipt["artifact"],
		["format", "formatVersion", "sha256", "byteLength"],
		"/receipt/artifact",
	)
	if not failure.is_empty():
		return _receipt_schema_failure("/receipt/artifact")
	var artifact: Dictionary = receipt["artifact"]
	if artifact["format"] != RUNTIME_FORMAT or artifact["formatVersion"] != RUNTIME_VERSION:
		return _receipt_schema_failure("/receipt/artifact")
	if not _valid_hash(artifact["sha256"]):
		return _receipt_schema_failure("/receipt/artifact/sha256")
	if typeof(artifact["byteLength"]) != TYPE_INT or artifact["byteLength"] < 1:
		return _receipt_schema_failure("/receipt/artifact/byteLength")
	return {"ok": true}


static func _validate_source(value: Variant) -> Dictionary:
	var failure := _exact_object(
		value,
		["format", "formatVersion", "id", "contentVersion", "canonicalSha256"],
		"/runtimePack/source",
	)
	if not failure.is_empty():
		return failure
	if value["format"] != AUTHORING_FORMAT or value["formatVersion"] != AUTHORING_VERSION:
		return _failure("schema", "GODOT_RUNTIME_SCHEMA_INVALID", "/runtimePack/source")
	if not _valid_id(value["id"]):
		return _failure("schema", "GODOT_RUNTIME_SCHEMA_INVALID", "/runtimePack/source/id")
	if not _valid_content_version(value["contentVersion"]):
		return _failure("schema", "GODOT_RUNTIME_SCHEMA_INVALID", "/runtimePack/source/contentVersion")
	if not _valid_hash(value["canonicalSha256"]):
		return _failure("schema", "GODOT_RUNTIME_SCHEMA_INVALID", "/runtimePack/source/canonicalSha256")
	return {}


static func _validate_entity(value: Variant, path: String) -> Dictionary:
	var failure := _exact_object(value, ["id", "label", "description"], path)
	if not failure.is_empty():
		return failure
	if not _valid_id(value["id"]) or not _valid_prose(value["label"]):
		return _failure("schema", "GODOT_RUNTIME_SCHEMA_INVALID", path)
	if value["description"] != null and not _valid_prose(value["description"]):
		return _failure("schema", "GODOT_RUNTIME_SCHEMA_INVALID", path + "/description")
	return {}


static func _validate_variable(value: Variant, path: String) -> Dictionary:
	if typeof(value) != TYPE_DICTIONARY or not value.has("type"):
		return _failure("schema", "GODOT_RUNTIME_SCHEMA_INVALID", path)
	match value["type"]:
		"boolean":
			if not _exact_object(value, ["id", "type", "initial"], path).is_empty():
				return _failure("schema", "GODOT_RUNTIME_SCHEMA_INVALID", path)
			if not _valid_id(value["id"]) or typeof(value["initial"]) != TYPE_BOOL:
				return _failure("schema", "GODOT_RUNTIME_SCHEMA_INVALID", path)
		"integer":
			if not _exact_object(value, ["id", "type", "initial"], path).is_empty():
				return _failure("schema", "GODOT_RUNTIME_SCHEMA_INVALID", path)
			if not _valid_id(value["id"]) or typeof(value["initial"]) != TYPE_INT:
				return _failure("schema", "GODOT_RUNTIME_SCHEMA_INVALID", path)
		"enum":
			if not _exact_object(value, ["id", "type", "allowedValues", "initial"], path).is_empty():
				return _failure("schema", "GODOT_RUNTIME_SCHEMA_INVALID", path)
			if not _valid_id(value["id"]) or not _valid_id(value["initial"]):
				return _failure("schema", "GODOT_RUNTIME_SCHEMA_INVALID", path)
			if not _valid_unique_id_array(value["allowedValues"]):
				return _failure("schema", "GODOT_RUNTIME_SCHEMA_INVALID", path + "/allowedValues")
		_:
			return _failure("schema", "GODOT_RUNTIME_SCHEMA_INVALID", path + "/type")
	return {}


static func _validate_cue(value: Variant, path: String) -> Dictionary:
	var failure := _exact_object(value, ["id", "channel", "intent"], path)
	if not failure.is_empty():
		return failure
	if not _valid_id(value["id"]) or value["channel"] not in ["visual", "audio", "ui"]:
		return _failure("schema", "GODOT_RUNTIME_SCHEMA_INVALID", path)
	if not _valid_prose(value["intent"]):
		return _failure("schema", "GODOT_RUNTIME_SCHEMA_INVALID", path + "/intent")
	return {}


static func _validate_node(value: Variant, path: String) -> Dictionary:
	var failure := _exact_object(
		value,
		["id", "title", "text", "entityIndexes", "entryCueIndexes", "actions"],
		path,
	)
	if not failure.is_empty():
		return failure
	if not _valid_id(value["id"]) or not _valid_prose(value["title"]):
		return _failure("schema", "GODOT_RUNTIME_SCHEMA_INVALID", path)
	if value["text"] != null and not _valid_prose(value["text"]):
		return _failure("schema", "GODOT_RUNTIME_SCHEMA_INVALID", path + "/text")
	if not _valid_unique_index_array(value["entityIndexes"]):
		return _failure("schema", "GODOT_RUNTIME_SCHEMA_INVALID", path + "/entityIndexes")
	if not _valid_unique_index_array(value["entryCueIndexes"]):
		return _failure("schema", "GODOT_RUNTIME_SCHEMA_INVALID", path + "/entryCueIndexes")
	if typeof(value["actions"]) != TYPE_ARRAY or value["actions"].is_empty() or value["actions"].size() > 64:
		return _failure("schema", "GODOT_RUNTIME_SCHEMA_INVALID", path + "/actions")
	for index in range(value["actions"].size()):
		failure = _validate_action(value["actions"][index], "%s/actions/%d" % [path, index])
		if not failure.is_empty():
			return failure
	return {}


static func _validate_action(value: Variant, path: String) -> Dictionary:
	var failure := _exact_object(
		value,
		["id", "label", "entityIndexes", "when", "effects", "target"],
		path,
	)
	if not failure.is_empty():
		return failure
	if not _valid_id(value["id"]) or not _valid_prose(value["label"]):
		return _failure("schema", "GODOT_RUNTIME_SCHEMA_INVALID", path)
	if not _valid_unique_index_array(value["entityIndexes"]):
		return _failure("schema", "GODOT_RUNTIME_SCHEMA_INVALID", path + "/entityIndexes")
	if value["when"] != null:
		failure = _validate_condition_shape(value["when"], path + "/when")
		if not failure.is_empty():
			return failure
	if typeof(value["effects"]) != TYPE_ARRAY or value["effects"].size() > 32:
		return _failure("schema", "GODOT_RUNTIME_SCHEMA_INVALID", path + "/effects")
	for index in range(value["effects"].size()):
		failure = _validate_effect_shape(value["effects"][index], "%s/effects/%d" % [path, index])
		if not failure.is_empty():
			return failure
	return _validate_target_shape(value["target"], path + "/target")


static func _validate_condition_shape(value: Variant, path: String) -> Dictionary:
	if typeof(value) != TYPE_DICTIONARY or not value.has("op") or typeof(value["op"]) != TYPE_STRING:
		return _failure("schema", "GODOT_RUNTIME_SCHEMA_INVALID", path)
	match value["op"]:
		"all", "any":
			if not _exact_object(value, ["op", "conditions"], path).is_empty():
				return _failure("schema", "GODOT_RUNTIME_SCHEMA_INVALID", path)
			if typeof(value["conditions"]) != TYPE_ARRAY or value["conditions"].is_empty():
				return _failure("schema", "GODOT_RUNTIME_SCHEMA_INVALID", path + "/conditions")
			for index in range(value["conditions"].size()):
				var failure := _validate_condition_shape(value["conditions"][index], "%s/conditions/%d" % [path, index])
				if not failure.is_empty():
					return failure
		"not":
			if not _exact_object(value, ["op", "condition"], path).is_empty():
				return _failure("schema", "GODOT_RUNTIME_SCHEMA_INVALID", path)
			return _validate_condition_shape(value["condition"], path + "/condition")
		"eq", "ne":
			if not _exact_object(value, ["op", "variableIndex", "value"], path).is_empty():
				return _failure("schema", "GODOT_RUNTIME_SCHEMA_INVALID", path)
			if not _valid_index(value["variableIndex"]) or not _valid_scalar(value["value"]):
				return _failure("schema", "GODOT_RUNTIME_SCHEMA_INVALID", path)
		"lt", "lte", "gt", "gte":
			if not _exact_object(value, ["op", "variableIndex", "value"], path).is_empty():
				return _failure("schema", "GODOT_RUNTIME_SCHEMA_INVALID", path)
			if not _valid_index(value["variableIndex"]) or typeof(value["value"]) != TYPE_INT:
				return _failure("schema", "GODOT_RUNTIME_SCHEMA_INVALID", path)
		_:
			return _failure("schema", "GODOT_RUNTIME_SCHEMA_INVALID", path + "/op")
	return {}


static func _validate_effect_shape(value: Variant, path: String) -> Dictionary:
	if typeof(value) != TYPE_DICTIONARY or not value.has("op") or typeof(value["op"]) != TYPE_STRING:
		return _failure("schema", "GODOT_RUNTIME_SCHEMA_INVALID", path)
	match value["op"]:
		"set":
			if not _exact_object(value, ["op", "variableIndex", "value"], path).is_empty():
				return _failure("schema", "GODOT_RUNTIME_SCHEMA_INVALID", path)
			if not _valid_index(value["variableIndex"]) or not _valid_scalar(value["value"]):
				return _failure("schema", "GODOT_RUNTIME_SCHEMA_INVALID", path)
		"add":
			if not _exact_object(value, ["op", "variableIndex", "value"], path).is_empty():
				return _failure("schema", "GODOT_RUNTIME_SCHEMA_INVALID", path)
			if not _valid_index(value["variableIndex"]) or typeof(value["value"]) != TYPE_INT or value["value"] == 0:
				return _failure("schema", "GODOT_RUNTIME_SCHEMA_INVALID", path)
		"emitCue":
			if not _exact_object(value, ["op", "cueIndex"], path).is_empty() or not _valid_index(value["cueIndex"]):
				return _failure("schema", "GODOT_RUNTIME_SCHEMA_INVALID", path)
		_:
			return _failure("schema", "GODOT_RUNTIME_SCHEMA_INVALID", path + "/op")
	return {}


static func _validate_target_shape(value: Variant, path: String) -> Dictionary:
	if not _exact_object(value, ["kind", "index"], path).is_empty():
		return _failure("schema", "GODOT_RUNTIME_SCHEMA_INVALID", path)
	if value["kind"] not in ["node", "ending"] or not _valid_index(value["index"]):
		return _failure("schema", "GODOT_RUNTIME_SCHEMA_INVALID", path)
	return {}


static func _validate_ending(value: Variant, path: String) -> Dictionary:
	var failure := _exact_object(value, ["id", "title", "text", "cueIndexes"], path)
	if not failure.is_empty():
		return failure
	if not _valid_id(value["id"]) or not _valid_prose(value["title"]):
		return _failure("schema", "GODOT_RUNTIME_SCHEMA_INVALID", path)
	if value["text"] != null and not _valid_prose(value["text"]):
		return _failure("schema", "GODOT_RUNTIME_SCHEMA_INVALID", path + "/text")
	if not _valid_unique_index_array(value["cueIndexes"]):
		return _failure("schema", "GODOT_RUNTIME_SCHEMA_INVALID", path + "/cueIndexes")
	return {}


static func _validate_semantics(pack: Dictionary) -> Dictionary:
	var top_ids := {}
	for collection in ["entities", "variables", "cues", "nodes", "endings"]:
		for index in range(pack[collection].size()):
			var identifier: String = pack[collection][index]["id"]
			if top_ids.has(identifier):
				return _failure("semantic", "RUNTIME_PACK_TOP_LEVEL_ID_DUPLICATE", "/runtimePack/%s/%d/id" % [collection, index])
			top_ids[identifier] = true
	for node_index in range(pack["nodes"].size()):
		var action_ids := {}
		for action_index in range(pack["nodes"][node_index]["actions"].size()):
			var action_id: String = pack["nodes"][node_index]["actions"][action_index]["id"]
			if action_ids.has(action_id):
				return _failure("semantic", "RUNTIME_PACK_ACTION_ID_DUPLICATE", "/runtimePack/nodes/%d/actions/%d/id" % [node_index, action_index])
			action_ids[action_id] = true
	if pack["entryNodeIndex"] >= pack["nodes"].size():
		return _failure("semantic", "RUNTIME_PACK_ENTRY_NODE_INDEX_INVALID", "/runtimePack/entryNodeIndex")
	for variable_index in range(pack["variables"].size()):
		var variable: Dictionary = pack["variables"][variable_index]
		if variable["type"] == "enum" and variable["initial"] not in variable["allowedValues"]:
			return _failure("semantic", "RUNTIME_PACK_ENUM_INITIAL_NOT_ALLOWED", "/runtimePack/variables/%d/initial" % variable_index)
	for node_index in range(pack["nodes"].size()):
		var node: Dictionary = pack["nodes"][node_index]
		var failure := _validate_index_references(node["entityIndexes"], pack["entities"], "/runtimePack/nodes/%d/entityIndexes" % node_index, "RUNTIME_PACK_ENTITY_INDEX_INVALID")
		if not failure.is_empty():
			return failure
		failure = _validate_index_references(node["entryCueIndexes"], pack["cues"], "/runtimePack/nodes/%d/entryCueIndexes" % node_index, "RUNTIME_PACK_CUE_INDEX_INVALID")
		if not failure.is_empty():
			return failure
		for action_index in range(node["actions"].size()):
			var action: Dictionary = node["actions"][action_index]
			var action_path := "/runtimePack/nodes/%d/actions/%d" % [node_index, action_index]
			failure = _validate_index_references(action["entityIndexes"], pack["entities"], action_path + "/entityIndexes", "RUNTIME_PACK_ENTITY_INDEX_INVALID")
			if not failure.is_empty():
				return failure
			if action["when"] != null:
				failure = _validate_condition_semantics(action["when"], action_path + "/when", 1, pack["variables"])
				if not failure.is_empty():
					return failure
			for effect_index in range(action["effects"].size()):
				failure = _validate_effect_semantics(action["effects"][effect_index], "%s/effects/%d" % [action_path, effect_index], pack["variables"], pack["cues"])
				if not failure.is_empty():
					return failure
			var target_collection: Array = pack["nodes"] if action["target"]["kind"] == "node" else pack["endings"]
			if action["target"]["index"] >= target_collection.size():
				return _failure("semantic", "RUNTIME_PACK_TARGET_INDEX_INVALID", action_path + "/target/index")
	for ending_index in range(pack["endings"].size()):
		var failure := _validate_index_references(pack["endings"][ending_index]["cueIndexes"], pack["cues"], "/runtimePack/endings/%d/cueIndexes" % ending_index, "RUNTIME_PACK_CUE_INDEX_INVALID")
		if not failure.is_empty():
			return failure
	return _validate_graph(pack)


static func _validate_condition_semantics(condition: Dictionary, path: String, depth: int, variables: Array) -> Dictionary:
	if depth > MAX_CONDITION_DEPTH:
		return _failure("semantic", "RUNTIME_PACK_CONDITION_DEPTH_EXCEEDED", path)
	if condition["op"] in ["all", "any"]:
		for index in range(condition["conditions"].size()):
			var failure := _validate_condition_semantics(condition["conditions"][index], "%s/conditions/%d" % [path, index], depth + 1, variables)
			if not failure.is_empty():
				return failure
		return {}
	if condition["op"] == "not":
		return _validate_condition_semantics(condition["condition"], path + "/condition", depth + 1, variables)
	if condition["variableIndex"] >= variables.size():
		return _failure("semantic", "RUNTIME_PACK_VARIABLE_INDEX_INVALID", path + "/variableIndex")
	var variable: Dictionary = variables[condition["variableIndex"]]
	if condition["op"] in ["lt", "lte", "gt", "gte"]:
		if variable["type"] != "integer":
			return _failure("semantic", "RUNTIME_PACK_CONDITION_VARIABLE_TYPE_MISMATCH", path + "/variableIndex")
		return {}
	return _validate_typed_value(variable, condition["value"], path + "/value", "condition")


static func _validate_effect_semantics(effect: Dictionary, path: String, variables: Array, cues: Array) -> Dictionary:
	if effect["op"] == "emitCue":
		if effect["cueIndex"] >= cues.size():
			return _failure("semantic", "RUNTIME_PACK_CUE_INDEX_INVALID", path + "/cueIndex")
		return {}
	if effect["variableIndex"] >= variables.size():
		return _failure("semantic", "RUNTIME_PACK_VARIABLE_INDEX_INVALID", path + "/variableIndex")
	var variable: Dictionary = variables[effect["variableIndex"]]
	if effect["op"] == "add":
		if variable["type"] != "integer":
			return _failure("semantic", "RUNTIME_PACK_EFFECT_VARIABLE_TYPE_MISMATCH", path + "/variableIndex")
		return {}
	return _validate_typed_value(variable, effect["value"], path + "/value", "effect")


static func _validate_typed_value(variable: Dictionary, value: Variant, path: String, context: String) -> Dictionary:
	if variable["type"] == "boolean" and typeof(value) != TYPE_BOOL:
		return _failure("semantic", "RUNTIME_PACK_%s_VALUE_TYPE_MISMATCH" % context.to_upper(), path)
	if variable["type"] == "integer" and typeof(value) != TYPE_INT:
		return _failure("semantic", "RUNTIME_PACK_%s_VALUE_TYPE_MISMATCH" % context.to_upper(), path)
	if variable["type"] == "enum":
		if typeof(value) != TYPE_STRING:
			return _failure("semantic", "RUNTIME_PACK_%s_VALUE_TYPE_MISMATCH" % context.to_upper(), path)
		if value not in variable["allowedValues"]:
			return _failure("semantic", "RUNTIME_PACK_ENUM_VALUE_NOT_ALLOWED", path)
	return {}


static func _validate_graph(pack: Dictionary) -> Dictionary:
	var adjacency := []
	var reverse := []
	var can_reach_ending := {}
	for node_index in range(pack["nodes"].size()):
		adjacency.append([])
		reverse.append([])
	for node_index in range(pack["nodes"].size()):
		for action in pack["nodes"][node_index]["actions"]:
			if action["target"]["kind"] == "ending":
				can_reach_ending[node_index] = true
			else:
				adjacency[node_index].append(action["target"]["index"])
				reverse[action["target"]["index"]].append(node_index)
	var reachable := {}
	var pending := [pack["entryNodeIndex"]]
	while not pending.is_empty():
		var current: int = pending.pop_front()
		if reachable.has(current):
			continue
		reachable[current] = true
		pending.append_array(adjacency[current])
	pending = can_reach_ending.keys()
	while not pending.is_empty():
		var current: int = pending.pop_front()
		for predecessor in reverse[current]:
			if not can_reach_ending.has(predecessor):
				can_reach_ending[predecessor] = true
				pending.append(predecessor)
	for node_index in range(pack["nodes"].size()):
		if not reachable.has(node_index):
			return _failure("semantic", "RUNTIME_PACK_NODE_UNREACHABLE", "/runtimePack/nodes/%d/id" % node_index)
		if not can_reach_ending.has(node_index):
			return _failure("semantic", "RUNTIME_PACK_NODE_NO_ENDING_PATH", "/runtimePack/nodes/%d/id" % node_index)
	return {"ok": true}


static func _validate_index_references(values: Array, collection: Array, path: String, code: String) -> Dictionary:
	for index in range(values.size()):
		if values[index] >= collection.size():
			return _failure("semantic", code, "%s/%d" % [path, index])
	return {}


static func _exact_object(value: Variant, fields: Array, path: String) -> Dictionary:
	if typeof(value) != TYPE_DICTIONARY or value.size() != fields.size():
		return _failure("schema", "GODOT_RUNTIME_SCHEMA_INVALID", path)
	for field in fields:
		if not value.has(field):
			return _failure("schema", "GODOT_RUNTIME_SCHEMA_INVALID", path)
	return {}


static func _valid_scalar(value: Variant) -> bool:
	return typeof(value) in [TYPE_BOOL, TYPE_INT] or _valid_id(value)


static func _valid_index(value: Variant) -> bool:
	return typeof(value) == TYPE_INT and value >= 0


static func _valid_unique_index_array(value: Variant) -> bool:
	if typeof(value) != TYPE_ARRAY:
		return false
	var seen := {}
	for item in value:
		if not _valid_index(item) or seen.has(item):
			return false
		seen[item] = true
	return true


static func _valid_unique_id_array(value: Variant) -> bool:
	if typeof(value) != TYPE_ARRAY or value.is_empty():
		return false
	var seen := {}
	for item in value:
		if not _valid_id(item) or seen.has(item):
			return false
		seen[item] = true
	return true


static func _valid_id(value: Variant) -> bool:
	if typeof(value) != TYPE_STRING or value.is_empty() or value.length() > 96:
		return false
	var text: String = value
	var previous_hyphen := false
	for index in range(text.length()):
		var code: int = text.unicode_at(index)
		var valid_letter: bool = code >= 0x61 and code <= 0x7a
		var valid_digit: bool = code >= 0x30 and code <= 0x39
		if index == 0 and not valid_letter:
			return false
		if not valid_letter and not valid_digit and code != 0x2d:
			return false
		if code == 0x2d and (previous_hyphen or index == text.length() - 1):
			return false
		previous_hyphen = code == 0x2d
	return true


static func _valid_content_version(value: Variant) -> bool:
	if typeof(value) != TYPE_STRING:
		return false
	var text: String = value
	return not text.is_empty() and text.length() <= 64 and _contains_non_ecmascript_whitespace(text)


static func _valid_language(value: Variant) -> bool:
	if typeof(value) != TYPE_STRING or value.length() < 2 or value.length() > 35:
		return false
	var parts: PackedStringArray = value.split("-", false)
	if parts.is_empty() or parts[0].length() < 2 or parts[0].length() > 3 or not _ascii_letters(parts[0]):
		return false
	for index in range(1, parts.size()):
		if parts[index].length() < 2 or parts[index].length() > 8 or not _ascii_alphanumeric(parts[index]):
			return false
	return true


static func _valid_prose(value: Variant) -> bool:
	if typeof(value) != TYPE_STRING:
		return false
	var text: String = value
	return not text.is_empty() and text.length() <= 4096 and _contains_non_ecmascript_whitespace(text)


static func _contains_non_ecmascript_whitespace(value: String) -> bool:
	for index in range(value.length()):
		var code := value.unicode_at(index)
		var is_whitespace := (
			(code >= 0x09 and code <= 0x0d) or
			code in [0x20, 0xa0, 0x1680, 0x2028, 0x2029, 0x202f, 0x205f, 0x3000, 0xfeff] or
			(code >= 0x2000 and code <= 0x200a)
		)
		if not is_whitespace:
			return true
	return false


static func _valid_hash(value: Variant) -> bool:
	if typeof(value) != TYPE_STRING or value.length() != 64:
		return false
	var text: String = value
	for index in range(text.length()):
		var code: int = text.unicode_at(index)
		if not (code >= 0x30 and code <= 0x39) and not (code >= 0x61 and code <= 0x66):
			return false
	return true


static func _ascii_letters(value: String) -> bool:
	for index in range(value.length()):
		var code := value.unicode_at(index)
		if not (code >= 0x41 and code <= 0x5a) and not (code >= 0x61 and code <= 0x7a):
			return false
	return true


static func _ascii_alphanumeric(value: String) -> bool:
	for index in range(value.length()):
		var code := value.unicode_at(index)
		if not (code >= 0x41 and code <= 0x5a) and not (code >= 0x61 and code <= 0x7a) and not (code >= 0x30 and code <= 0x39):
			return false
	return true


static func _receipt_schema_failure(path: String) -> Dictionary:
	return _failure("schema", "GODOT_RUNTIME_RECEIPT_SCHEMA_INVALID", path)


static func _failure(phase: String, code: String, path: String) -> Dictionary:
	return {
		"ok": false,
		"diagnostics": [{
			"phase": phase,
			"severity": "error",
			"code": code,
			"path": path,
			"message": code,
		}],
	}

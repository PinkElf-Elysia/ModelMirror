class_name MatrixOasisRuntimeSessionTest
extends GdUnitTestSuite


func test_create_binds_identity_variables_entry_cues_and_availability() -> void:
	var prepared := _prepared(_runtime_pack())
	var created := MatrixOasisGodotRuntime.create_game_session(prepared)
	assert_bool(created["ok"]).is_true()
	assert_dict(created["snapshot"]["location"]).is_equal({"kind": "node", "index": 0})
	assert_array(created["snapshot"]["variables"]).is_equal([false, 0, "alpha"])
	assert_int(created["snapshot"]["stepCount"]).is_equal(0)
	assert_int(created["snapshot"]["stepLimit"]).is_equal(256)
	assert_str(created["snapshot"]["pack"]["sourceSha256"]).is_equal("1".repeat(64))
	assert_str(created["snapshot"]["pack"]["artifactSha256"]).is_equal(prepared.artifact_sha256())
	assert_array(_cue_ids(created["emittedCues"])).is_equal(["cue-entry"])
	assert_array(_action_states(created["inspection"])).is_equal([
		["advance", true],
		["blocked", false],
	])
	assert_bool(created.is_read_only()).is_true()
	assert_bool(created["snapshot"].is_read_only()).is_true()
	assert_bool(created["inspection"]["actions"].is_read_only()).is_true()


func test_all_nine_conditions_use_strict_typed_comparisons() -> void:
	var variables := [false, 0, "alpha"]
	var cases := [
		[{"op": "eq", "variableIndex": 0, "value": false}, true],
		[{"op": "ne", "variableIndex": 2, "value": "beta"}, true],
		[{"op": "lt", "variableIndex": 1, "value": 1}, true],
		[{"op": "lte", "variableIndex": 1, "value": 0}, true],
		[{"op": "gt", "variableIndex": 1, "value": -1}, true],
		[{"op": "gte", "variableIndex": 1, "value": 0}, true],
		[{
			"op": "all",
			"conditions": [
				{"op": "eq", "variableIndex": 0, "value": false},
				{"op": "gte", "variableIndex": 1, "value": 0},
			],
		}, true],
		[{
			"op": "any",
			"conditions": [
				{"op": "eq", "variableIndex": 0, "value": true},
				{"op": "ne", "variableIndex": 2, "value": "beta"},
			],
		}, true],
		[{"op": "not", "condition": {"op": "eq", "variableIndex": 0, "value": true}}, true],
	]
	for item in cases:
		assert_bool(MatrixOasisGodotRuntime._evaluate_condition(item[0], variables)).is_equal(item[1])
	assert_bool(MatrixOasisGodotRuntime._evaluate_condition(
		{"op": "gt", "variableIndex": 1, "value": 0},
		variables,
	)).is_false()
	assert_bool(MatrixOasisGodotRuntime._evaluate_condition(
		{"op": "lt", "variableIndex": 1, "value": 0},
		variables,
	)).is_false()


func test_effects_and_target_cues_are_ordered_and_each_call_advances_once() -> void:
	var prepared := _prepared(_runtime_pack())
	var created := MatrixOasisGodotRuntime.create_game_session(prepared)
	var advanced := MatrixOasisGodotRuntime.apply_game_session_action(prepared, created["snapshot"], "advance")
	assert_bool(advanced["ok"]).is_true()
	assert_array(advanced["snapshot"]["variables"]).is_equal([true, 1, "beta"])
	assert_array(_cue_ids(advanced["transition"]["emittedCues"])).is_equal(["cue-step", "cue-step"])
	assert_dict(advanced["transition"]["to"]).is_equal({
		"kind": "node",
		"index": 1,
		"id": "node-finish",
	})
	assert_int(advanced["transition"]["step"]).is_equal(1)

	var ended := MatrixOasisGodotRuntime.apply_game_session_action(prepared, advanced["snapshot"], "finish")
	assert_bool(ended["ok"]).is_true()
	assert_array(ended["snapshot"]["variables"]).is_equal([true, 0, "beta"])
	assert_array(_cue_ids(ended["transition"]["emittedCues"])).is_equal([
		"cue-step", "cue-end", "cue-step",
	])
	assert_str(ended["inspection"]["status"]).is_equal("ended")
	assert_array(ended["inspection"]["actions"]).is_empty()
	assert_str(_failure_code(
		MatrixOasisGodotRuntime.apply_game_session_action(prepared, ended["snapshot"], "finish"),
	)).is_equal("PACK_RUNTIME_SESSION_ENDED")


func test_expected_failures_preserve_static_codes_and_paths() -> void:
	var prepared := _prepared(_runtime_pack())
	assert_str(_failure_code(MatrixOasisGodotRuntime.create_game_session(null))).is_equal(
		"PACK_RUNTIME_PREPARED_PACK_INVALID",
	)
	for options in [null, [], "default", {"stepLimit": 0}, {"stepLimit": 10001}, {"extra": true}]:
		assert_str(_failure_code(MatrixOasisGodotRuntime.create_game_session(prepared, options))).is_equal(
			"PACK_RUNTIME_OPTIONS_INVALID",
		)
	var created := MatrixOasisGodotRuntime.create_game_session(prepared, {"stepLimit": 1})
	assert_str(_failure_code(
		MatrixOasisGodotRuntime.apply_game_session_action(prepared, created["snapshot"], "missing"),
	)).is_equal("PACK_RUNTIME_ACTION_UNKNOWN")
	assert_str(_failure_code(
		MatrixOasisGodotRuntime.apply_game_session_action(prepared, created["snapshot"], "blocked"),
	)).is_equal("PACK_RUNTIME_ACTION_UNAVAILABLE")
	var advanced := MatrixOasisGodotRuntime.apply_game_session_action(prepared, created["snapshot"], "advance")
	assert_str(_failure_code(
		MatrixOasisGodotRuntime.apply_game_session_action(prepared, advanced["snapshot"], "finish"),
	)).is_equal("PACK_RUNTIME_STEP_LIMIT")
	var failure := MatrixOasisGodotRuntime.apply_game_session_action(prepared, created["snapshot"], "missing")
	assert_bool(failure.is_read_only()).is_true()
	assert_bool(failure["diagnostics"].is_read_only()).is_true()
	assert_str(failure["diagnostics"][0]["message"]).is_equal(failure["diagnostics"][0]["code"])


func test_snapshot_validation_copies_input_and_binds_source_and_artifact() -> void:
	var prepared := _prepared(_runtime_pack())
	var created := MatrixOasisGodotRuntime.create_game_session(prepared)
	var snapshot: Dictionary = created["snapshot"].duplicate(true)
	assert_bool(MatrixOasisGodotRuntime.inspect_game_session(prepared, snapshot)["ok"]).is_true()
	var inspected := MatrixOasisGodotRuntime.inspect_game_session(prepared, snapshot)
	snapshot["variables"][1] = 99
	assert_int(inspected["inspection"]["variables"][1]["value"]).is_equal(0)

	for invalid in [
		_created_snapshot_with(created["snapshot"], "extra", true),
		_created_snapshot_with(created["snapshot"], "snapshotVersion", true),
		_created_snapshot_with(created["snapshot"], "variables", ["false", 0, "alpha"]),
		_created_snapshot_with(created["snapshot"], "location", {"kind": "ending", "index": 0}),
		_created_snapshot_with(created["snapshot"], "stepCount", 257),
	]:
		assert_str(_failure_code(MatrixOasisGodotRuntime.inspect_game_session(prepared, invalid))).is_equal(
			"PACK_RUNTIME_INVALID_SNAPSHOT",
		)
	var mismatch: Dictionary = created["snapshot"].duplicate(true)
	mismatch["pack"]["artifactSha256"] = "0".repeat(64)
	assert_str(_failure_code(MatrixOasisGodotRuntime.inspect_game_session(prepared, mismatch))).is_equal(
		"PACK_RUNTIME_PACK_MISMATCH",
	)


func test_prepared_reassignment_cannot_bypass_artifact_identity() -> void:
	var prepared := _prepared(_runtime_pack())
	var replaced_pack: Dictionary = prepared._copy_pack_for_runtime()
	replaced_pack["title"] = "Tampered"
	prepared._runtime_pack = replaced_pack
	assert_str(_failure_code(MatrixOasisGodotRuntime.create_game_session(prepared))).is_equal(
		"PACK_RUNTIME_PREPARED_PACK_INVALID",
	)


func test_positive_and_negative_overflow_are_atomic() -> void:
	for item in [[9007199254740991, 1], [-9007199254740991, -1]]:
		var pack := _runtime_pack()
		pack["variables"][1]["initial"] = item[0]
		pack["nodes"][0]["actions"][0]["when"] = null
		pack["nodes"][0]["actions"][0]["effects"] = [
			{"cueIndex": 1, "op": "emitCue"},
			{"op": "add", "value": item[1], "variableIndex": 1},
		]
		var prepared := _prepared(pack)
		var created := MatrixOasisGodotRuntime.create_game_session(prepared)
		var before := JSON.stringify(created["snapshot"], "", true)
		var overflow := MatrixOasisGodotRuntime.apply_game_session_action(
			prepared,
			created["snapshot"],
			"advance",
		)
		assert_str(_failure_code(overflow)).is_equal("PACK_RUNTIME_INTEGER_OVERFLOW")
		assert_str(JSON.stringify(created["snapshot"], "", true)).is_equal(before)


func test_explicit_loop_stops_at_the_exact_step_limit() -> void:
	var pack := _runtime_pack()
	pack["nodes"][0]["actions"] = [
		{
			"effects": [],
			"entityIndexes": [],
			"id": "loop",
			"label": "Loop",
			"target": {"index": 0, "kind": "node"},
			"when": null,
		},
		{
			"effects": [],
			"entityIndexes": [],
			"id": "exit-loop",
			"label": "Exit",
			"target": {"index": 0, "kind": "ending"},
			"when": null,
		},
	]
	pack["nodes"].remove_at(1)
	var prepared := _prepared(pack)
	var created := MatrixOasisGodotRuntime.create_game_session(prepared, {"stepLimit": 2})
	var first := MatrixOasisGodotRuntime.apply_game_session_action(prepared, created["snapshot"], "loop")
	var second := MatrixOasisGodotRuntime.apply_game_session_action(prepared, first["snapshot"], "loop")
	assert_int(second["snapshot"]["stepCount"]).is_equal(2)
	assert_bool(second["inspection"]["actions"][0]["available"]).is_false()
	assert_str(_failure_code(
		MatrixOasisGodotRuntime.apply_game_session_action(prepared, second["snapshot"], "loop"),
	)).is_equal("PACK_RUNTIME_STEP_LIMIT")


func _prepared(pack: Dictionary) -> MatrixOasisPreparedRuntimePack:
	var runtime_bytes := JSON.stringify(pack, "", true).to_utf8_buffer()
	var receipt := {
		"artifact": {
			"byteLength": runtime_bytes.size(),
			"format": "matrix-oasis.runtime-game-pack",
			"formatVersion": "0.1.0",
			"sha256": MatrixOasisRuntimeArtifactLoader._sha256_hex(runtime_bytes),
		},
		"canonicalization": "matrix-oasis.canonical-json/1",
		"compiler": {
			"id": "@matrix-oasis/game-pack-compiler",
			"version": "0.1.0-r3",
		},
		"format": "matrix-oasis.runtime-game-pack-receipt",
		"formatVersion": "0.1.0",
	}
	var result := MatrixOasisRuntimeArtifactLoader.prepare_from_bytes(
		runtime_bytes,
		JSON.stringify(receipt, "", true).to_utf8_buffer(),
	)
	assert_bool(result["ok"]).is_true()
	return result["prepared"]


func _runtime_pack() -> Dictionary:
	return {
		"canonicalization": "matrix-oasis.canonical-json/1",
		"cues": [
			{"channel": "visual", "id": "cue-entry", "intent": "Enter."},
			{"channel": "ui", "id": "cue-step", "intent": "Step."},
			{"channel": "audio", "id": "cue-end", "intent": "End."},
		],
		"endings": [{
			"cueIndexes": [2, 1],
			"id": "ending-done",
			"text": null,
			"title": "Done",
		}],
		"entities": [{"description": null, "id": "entity-unit", "label": "Unit"}],
		"entryNodeIndex": 0,
		"format": "matrix-oasis.runtime-game-pack",
		"formatVersion": "0.1.0",
		"language": "en",
		"nodes": [
			{
				"actions": [
					{
						"effects": [
							{"op": "set", "value": true, "variableIndex": 0},
							{"op": "add", "value": 1, "variableIndex": 1},
							{"op": "set", "value": "beta", "variableIndex": 2},
							{"cueIndex": 1, "op": "emitCue"},
						],
						"entityIndexes": [0],
						"id": "advance",
						"label": "Advance",
						"target": {"index": 1, "kind": "node"},
						"when": {
							"conditions": [
								{"op": "eq", "value": false, "variableIndex": 0},
								{"op": "gte", "value": 0, "variableIndex": 1},
							],
							"op": "all",
						},
					},
					{
						"effects": [],
						"entityIndexes": [],
						"id": "blocked",
						"label": "Blocked",
						"target": {"index": 0, "kind": "ending"},
						"when": {"op": "eq", "value": true, "variableIndex": 0},
					},
				],
				"entityIndexes": [0],
				"entryCueIndexes": [0],
				"id": "node-start",
				"text": null,
				"title": "Start",
			},
			{
				"actions": [{
					"effects": [
						{"op": "add", "value": -1, "variableIndex": 1},
						{"cueIndex": 1, "op": "emitCue"},
					],
					"entityIndexes": [],
					"id": "finish",
					"label": "Finish",
					"target": {"index": 0, "kind": "ending"},
					"when": {
						"conditions": [
							{"op": "ne", "value": "alpha", "variableIndex": 2},
							{
								"condition": {"op": "lt", "value": 1, "variableIndex": 1},
								"op": "not",
							},
						],
						"op": "any",
					},
				}],
				"entityIndexes": [],
				"entryCueIndexes": [1],
				"id": "node-finish",
				"text": "Finish the unit trace.",
				"title": "Finish",
			},
		],
		"source": {
			"canonicalSha256": "1".repeat(64),
			"contentVersion": "1",
			"format": "matrix-oasis.authoring-game-pack",
			"formatVersion": "0.1.0",
			"id": "runtime-session-unit",
		},
		"summary": null,
		"title": "Runtime session unit",
		"variables": [
			{"id": "flag", "initial": false, "type": "boolean"},
			{"id": "count", "initial": 0, "type": "integer"},
			{"allowedValues": ["alpha", "beta"], "id": "mode", "initial": "alpha", "type": "enum"},
		],
	}


func _cue_ids(cues: Array) -> Array:
	return cues.map(func(cue: Dictionary) -> String: return cue["id"])


func _action_states(inspection: Dictionary) -> Array:
	return inspection["actions"].map(
		func(action: Dictionary) -> Array: return [action["id"], action["available"]],
	)


func _failure_code(result: Dictionary) -> String:
	assert_bool(result["ok"]).is_false()
	assert_int(result["diagnostics"].size()).is_equal(1)
	return result["diagnostics"][0]["code"]


func _created_snapshot_with(snapshot: Dictionary, field: String, value: Variant) -> Dictionary:
	var copy: Dictionary = snapshot.duplicate(true)
	copy[field] = value
	return copy

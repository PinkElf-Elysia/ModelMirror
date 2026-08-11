class_name MatrixOasisPlayableLabTest
extends GdUnitTestSuite

const LAB_SCENE := "res://playable/playable_lab.tscn"


func test_playable_lab_renders_runtime_actions_without_topic_branches() -> void:
	var lab := await _spawn_lab()
	assert_str(lab.pack_title.text).is_equal("Playable fixture")
	assert_str(lab.location_title.text).is_equal("Start")
	assert_str(lab.location_text.text).is_equal("Use a terminal to advance.")
	assert_str(lab.state_label.text).contains("Step 0 / 256")
	assert_int(lab.terminal_grid.get_terminal_count()).is_equal(2)
	assert_str(lab.terminal_grid.get_terminal(0).get_action_id()).is_equal("action-enable")
	assert_bool(lab.terminal_grid.get_terminal(0).is_action_available()).is_true()
	assert_str(lab.terminal_grid.get_terminal(1).get_action_id()).is_equal("action-finish")
	assert_bool(lab.terminal_grid.get_terminal(1).is_action_available()).is_false()
	assert_float(lab.reset_button.custom_minimum_size.y).is_greater_equal(40.0)


func test_runtime_action_refreshes_terminals_ending_and_reset_atomically() -> void:
	var lab := await _spawn_lab()
	var start_transform := lab.player.global_transform
	lab.player.global_position += Vector3(1.0, 0.0, -1.0)
	lab.player.apply_look_delta(Vector2(0.0, 40.0))
	lab.interaction_ray.action_requested.emit("action-enable")
	await await_idle_frame()
	assert_str(lab.status_label.text).is_equal("Action applied")
	assert_str(lab.location_title.text).is_equal("Start")
	assert_str(lab.transition_label.text).contains("node-start")
	assert_str(lab.variables_label.text).contains("flag: true")
	assert_bool(lab.terminal_grid.get_terminal(1).is_action_available()).is_true()
	lab.interaction_ray.action_requested.emit("action-finish")
	await await_idle_frame()
	assert_int(lab.terminal_grid.get_terminal_count()).is_equal(0)
	assert_str(lab.location_title.text).is_equal("Done")
	assert_str(lab.status_label.text).is_equal("Action applied")
	assert_str(lab.prompt_label.text).is_equal("Ending reached · R to reset")
	lab.reset_button.pressed.emit()
	await await_idle_frame()
	assert_int(lab.terminal_grid.get_terminal_count()).is_equal(2)
	assert_str(lab.location_title.text).is_equal("Start")
	assert_str(lab.status_label.text).is_equal("Session reset")
	assert_bool(lab.player.global_transform.is_equal_approx(start_transform)).is_true()
	assert_float(lab.player.head.rotation.x).is_equal_approx(0.0, 0.00001)


func test_disabled_and_unknown_actions_preserve_session_and_world() -> void:
	var lab := await _spawn_lab()
	var snapshot_before := JSON.stringify(lab.snapshot_copy_for_test(), "", true)
	var first_terminal := lab.terminal_grid.get_terminal(0)
	var second_terminal := lab.terminal_grid.get_terminal(1)
	assert_bool(lab._apply_action("action-finish")).is_false()
	assert_str(lab.status_label.text).is_equal("PACK_RUNTIME_ACTION_UNAVAILABLE")
	assert_str(JSON.stringify(lab.snapshot_copy_for_test(), "", true)).is_equal(snapshot_before)
	assert_object(lab.terminal_grid.get_terminal(0)).is_same(first_terminal)
	assert_object(lab.terminal_grid.get_terminal(1)).is_same(second_terminal)
	assert_bool(lab._apply_action("missing-action")).is_false()
	assert_str(lab.status_label.text).is_equal("PACK_RUNTIME_ACTION_UNKNOWN")
	assert_str(JSON.stringify(lab.snapshot_copy_for_test(), "", true)).is_equal(snapshot_before)
	assert_object(lab.terminal_grid.get_terminal(0)).is_same(first_terminal)
	assert_object(lab.terminal_grid.get_terminal(1)).is_same(second_terminal)


func test_focus_feedback_distinguishes_available_disabled_and_empty() -> void:
	var lab := await _spawn_lab()
	lab._on_focus_changed("Enable fixture", true)
	assert_str(lab.prompt_label.text).is_equal("E / Enter · Enable fixture")
	lab._on_focus_changed("Finish fixture", false)
	assert_str(lab.prompt_label.text).is_equal("Unavailable · Finish fixture")
	lab._on_focus_changed("", false)
	assert_str(lab.prompt_label.text).is_equal("Aim at an action terminal")


func _spawn_lab() -> MatrixOasisPlayableLab:
	var packed_scene := load(LAB_SCENE) as PackedScene
	assert_object(packed_scene).is_not_null()
	var lab: MatrixOasisPlayableLab = auto_free(packed_scene.instantiate()) as MatrixOasisPlayableLab
	lab.set_prepared_for_test(_prepared(_runtime_pack()))
	var player: MatrixOasisFirstPersonController = lab.get_node("FirstPersonPlayer") as MatrixOasisFirstPersonController
	player.capture_mouse_on_ready = false
	player.input_enabled = false
	add_child(lab)
	await await_idle_frame()
	return lab


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
			{"channel": "visual", "id": "cue-entry", "intent": "Show entry."},
			{"channel": "ui", "id": "cue-finish", "intent": "Show completion."},
		],
		"endings": [{
			"cueIndexes": [1],
			"id": "ending-done",
			"text": "The fixture reached its ending.",
			"title": "Done",
		}],
		"entities": [],
		"entryNodeIndex": 0,
		"format": "matrix-oasis.runtime-game-pack",
		"formatVersion": "0.1.0",
		"language": "en",
		"nodes": [{
			"actions": [
				{
					"effects": [{"op": "set", "value": true, "variableIndex": 0}],
					"entityIndexes": [],
					"id": "action-enable",
					"label": "Enable fixture",
					"target": {"index": 0, "kind": "node"},
					"when": null,
				},
				{
					"effects": [{"cueIndex": 1, "op": "emitCue"}],
					"entityIndexes": [],
					"id": "action-finish",
					"label": "Finish fixture",
					"target": {"index": 0, "kind": "ending"},
					"when": {"op": "eq", "value": true, "variableIndex": 0},
				},
			],
			"entityIndexes": [],
			"entryCueIndexes": [0],
			"id": "node-start",
			"text": "Use a terminal to advance.",
			"title": "Start",
		}],
		"source": {
			"canonicalSha256": "1".repeat(64),
			"contentVersion": "1",
			"format": "matrix-oasis.authoring-game-pack",
			"formatVersion": "0.1.0",
			"id": "playable-lab-fixture",
		},
		"summary": null,
		"title": "Playable fixture",
		"variables": [{"id": "flag", "initial": false, "type": "boolean"}],
	}

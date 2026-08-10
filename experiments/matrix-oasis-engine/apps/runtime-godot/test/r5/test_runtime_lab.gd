class_name MatrixOasisRuntimeLabTest
extends GdUnitTestSuite

const LAB_SCENE := "res://runtime/runtime_lab.tscn"


func test_lab_renders_pack_content_and_native_action_states() -> void:
	var lab := await _spawn_lab()
	assert_str(lab.pack_title.text).is_equal("Runtime lab fixture")
	assert_str(lab.pack_meta.text).contains("runtime-lab-fixture")
	assert_str(lab.location_title.text).is_equal("Start")
	assert_str(lab.location_text.text).is_equal("Choose a declared action.")
	assert_str(lab.step_label.text).contains("Step 0 / 256")
	assert_object(lab.get_node("Foundation/FoundationMarker")).is_not_null()
	var buttons := _action_buttons(lab)
	assert_int(buttons.size()).is_equal(2)
	assert_bool(buttons[0].disabled).is_false()
	assert_bool(buttons[1].disabled).is_true()
	assert_int(buttons[0].focus_mode).is_equal(Control.FOCUS_ALL)
	assert_float(buttons[0].custom_minimum_size.y).is_greater_equal(44.0)


func test_native_button_signal_advances_once_and_reset_restores_session() -> void:
	var lab := await _spawn_lab()
	var first_button: Button = _action_buttons(lab)[0]
	first_button.pressed.emit()
	await await_idle_frame()
	assert_str(lab.location_title.text).is_equal("Done")
	assert_str(lab.step_label.text).contains("Step 1 / 256")
	assert_str(lab.transition_label.text).contains("action-finish")
	assert_int(lab.cues_list.get_child_count()).is_equal(2)
	lab.reset_button.pressed.emit()
	await await_idle_frame()
	assert_str(lab.location_title.text).is_equal("Start")
	assert_str(lab.step_label.text).contains("Step 0 / 256")
	assert_str(lab.transition_label.text).is_equal("No transition yet.")


func test_disabled_action_and_failed_call_preserve_visible_session() -> void:
	var lab := await _spawn_lab()
	var before := lab.location_title.text
	lab._apply_action("action-blocked")
	assert_str(lab.status_label.text).is_equal("PACK_RUNTIME_ACTION_UNAVAILABLE")
	assert_str(lab.location_title.text).is_equal(before)
	lab._apply_action("missing")
	assert_str(lab.status_label.text).is_equal("PACK_RUNTIME_ACTION_UNKNOWN")
	assert_str(lab.location_title.text).is_equal(before)
	lab._apply_action("action-finish")
	assert_str(lab.status_label.text).is_equal("Action applied")
	assert_bool(lab.status_label.get_theme_color("font_color").is_equal_approx(
		MatrixOasisRuntimeLab.READY_COLOR,
	)).is_true()


func _spawn_lab() -> MatrixOasisRuntimeLab:
	var packed_scene := load(LAB_SCENE) as PackedScene
	assert_object(packed_scene).is_not_null()
	var lab: MatrixOasisRuntimeLab = auto_free(packed_scene.instantiate()) as MatrixOasisRuntimeLab
	lab.set_prepared_for_test(_prepared(_runtime_pack()))
	add_child(lab)
	await await_idle_frame()
	return lab


func _action_buttons(lab: MatrixOasisRuntimeLab) -> Array[Button]:
	var buttons: Array[Button] = []
	for child in lab.actions_list.get_children():
		if child is Button:
			buttons.append(child)
	return buttons


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
					"effects": [{"cueIndex": 1, "op": "emitCue"}],
					"entityIndexes": [],
					"id": "action-finish",
					"label": "Finish fixture",
					"target": {"index": 0, "kind": "ending"},
					"when": null,
				},
				{
					"effects": [],
					"entityIndexes": [],
					"id": "action-blocked",
					"label": "Blocked fixture action",
					"target": {"index": 0, "kind": "ending"},
					"when": {"op": "eq", "value": true, "variableIndex": 0},
				},
			],
			"entityIndexes": [],
			"entryCueIndexes": [0],
			"id": "node-start",
			"text": "Choose a declared action.",
			"title": "Start",
		}],
		"source": {
			"canonicalSha256": "1".repeat(64),
			"contentVersion": "1",
			"format": "matrix-oasis.authoring-game-pack",
			"formatVersion": "0.1.0",
			"id": "runtime-lab-fixture",
		},
		"summary": null,
		"title": "Runtime lab fixture",
		"variables": [{"id": "flag", "initial": false, "type": "boolean"}],
	}

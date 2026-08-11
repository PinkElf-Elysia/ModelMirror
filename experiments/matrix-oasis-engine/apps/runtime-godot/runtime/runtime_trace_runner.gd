extends SceneTree

const TRACE_MARKER := "MATRIX_OASIS_R5_TRACE_JSON:"
const ACTION_ARGUMENT := "--matrix-oasis-trace-action="
const STEP_LIMIT_ARGUMENT := "--matrix-oasis-trace-step-limit="
const MAX_ACTIONS := 64
const MAX_ACTION_ID_LENGTH := 128


func _init() -> void:
	var arguments := _parse_arguments(OS.get_cmdline_user_args())
	if not arguments["ok"]:
		_print_failure("GODOT_TRACE_ARGUMENT_INVALID", 2)
		return
	var loaded := MatrixOasisRuntimeArtifactLoader.load_from_arguments(arguments["artifact_arguments"])
	if not loaded["ok"]:
		print(TRACE_MARKER + JSON.stringify({"ok": false, "diagnostics": loaded["diagnostics"]}, "", true))
		quit(1)
		return
	var prepared: MatrixOasisPreparedRuntimePack = loaded["prepared"]
	var created := MatrixOasisGodotRuntime.create_game_session(prepared, arguments["options"])
	if not created["ok"]:
		print(TRACE_MARKER + JSON.stringify({"ok": false, "diagnostics": created["diagnostics"]}, "", true))
		quit(1)
		return
	var snapshot: Dictionary = created["snapshot"]
	var steps: Array = []
	for action_id in arguments["actions"]:
		var result := MatrixOasisGodotRuntime.apply_game_session_action(prepared, snapshot, action_id)
		steps.append(result)
		if result["ok"]:
			snapshot = result["snapshot"]
	var trace := {
		"traceVersion": 1,
		"created": created,
		"steps": steps,
	}
	print(TRACE_MARKER + JSON.stringify(trace, "", true))
	quit(0)


func _parse_arguments(arguments: PackedStringArray) -> Dictionary:
	var artifact_arguments := PackedStringArray()
	var actions: Array[String] = []
	var step_limit := MatrixOasisGodotRuntime.DEFAULT_STEP_LIMIT
	var has_step_limit := false
	for argument in arguments:
		if argument.begins_with(MatrixOasisRuntimeArtifactLoader.RUNTIME_ARGUMENT) or (
			argument.begins_with(MatrixOasisRuntimeArtifactLoader.RECEIPT_ARGUMENT)
		):
			artifact_arguments.append(argument)
		elif argument.begins_with(ACTION_ARGUMENT):
			var action_id := argument.substr(ACTION_ARGUMENT.length())
			if actions.size() >= MAX_ACTIONS or not _is_safe_action_id(action_id):
				return {"ok": false}
			actions.append(action_id)
		elif argument.begins_with(STEP_LIMIT_ARGUMENT):
			if has_step_limit:
				return {"ok": false}
			var raw_limit := argument.substr(STEP_LIMIT_ARGUMENT.length())
			if not _is_canonical_positive_integer(raw_limit):
				return {"ok": false}
			step_limit = int(raw_limit)
			if step_limit < 1 or step_limit > MatrixOasisGodotRuntime.MAX_STEP_LIMIT:
				return {"ok": false}
			has_step_limit = true
		else:
			return {"ok": false}
	var artifact_paths := MatrixOasisRuntimeArtifactLoader.paths_from_arguments(artifact_arguments)
	if not artifact_paths["ok"]:
		return {"ok": false}
	return {
		"ok": true,
		"artifact_arguments": artifact_arguments,
		"actions": actions,
		"options": {"stepLimit": step_limit},
	}


func _is_safe_action_id(value: String) -> bool:
	if value.is_empty() or value.length() > MAX_ACTION_ID_LENGTH:
		return false
	for index in range(value.length()):
		var code := value.unicode_at(index)
		if not (
			(code >= 48 and code <= 57) or
			(code >= 65 and code <= 90) or
			(code >= 97 and code <= 122) or
			code == 45 or code == 95
		):
			return false
	return true


func _is_canonical_positive_integer(value: String) -> bool:
	if value.is_empty() or value.length() > 5 or (value.length() > 1 and value.begins_with("0")):
		return false
	for index in range(value.length()):
		var code := value.unicode_at(index)
		if code < 48 or code > 57:
			return false
	return true


func _print_failure(code: String, exit_code: int) -> void:
	print(TRACE_MARKER + JSON.stringify({"ok": false, "code": code}, "", true))
	quit(exit_code)

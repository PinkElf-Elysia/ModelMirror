extends SceneTree

const RESULT_MARKER := "MATRIX_OASIS_R5_ADAPTER_JSON:"


func _init() -> void:
	var result := MatrixOasisRuntimeArtifactLoader.load_from_arguments(OS.get_cmdline_user_args())
	if result["ok"]:
		print(RESULT_MARKER + JSON.stringify({"ok": true}))
		quit(0)
		return
	print(RESULT_MARKER + JSON.stringify({"ok": false, "diagnostics": result["diagnostics"]}))
	quit(1)

extends SceneTree

const MARKER := "MATRIX_OASIS_R11_GDGS_IMPORT_READY:"
const RESOURCE_PATH := "res://spatial_fixture/environment.compressed.ply"
const SPLAT_NODE_SCRIPT := preload("res://addons/gdgs/runtime/nodes/gaussian_splat_node.gd")
const COMPOSITOR_EFFECT_SCRIPT := preload("res://addons/gdgs/runtime/compositor/gaussian_compositor_effect.gd")


func _initialize() -> void:
	call_deferred("_probe")


func _probe() -> void:
	var enabled_plugins: PackedStringArray = ProjectSettings.get_setting(
		"editor_plugins/enabled", PackedStringArray()
	)
	if not enabled_plugins.has("res://addons/gdgs/plugin.cfg"):
		quit(1)
		return
	if str(ProjectSettings.get_setting("gdgs/rendering/backend", "")) != "Compute":
		quit(1)
		return
	var gaussian: Resource = load(RESOURCE_PATH)
	if gaussian == null or int(gaussian.get("point_count")) != 3:
		quit(1)
		return
	var splat_node: Node = SPLAT_NODE_SCRIPT.new()
	splat_node.set("gaussian", gaussian)
	var compositor_effect: RefCounted = COMPOSITOR_EFFECT_SCRIPT.new()
	if splat_node.get("gaussian") != gaussian or compositor_effect == null:
		quit(1)
		return
	print(MARKER + JSON.stringify({
		"configuredBackend": "Compute",
		"format": "compressed-ply",
		"pointCount": 3,
		"probeVersion": 1,
	}))
	quit(0)

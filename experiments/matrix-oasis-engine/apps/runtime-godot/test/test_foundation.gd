class_name MatrixOasisFoundationTest
extends GdUnitTestSuite

const BOOTSTRAP_SCENE := "res://scenes/bootstrap.tscn"
const BOOTSTRAP_SCRIPT := "res://scripts/bootstrap.gd"
const READINESS_MARKER := "MATRIX_OASIS_R4_GODOT_FOUNDATION_READY"


func test_project_settings_are_fixed() -> void:
	assert_str(ProjectSettings.get_setting("rendering/renderer/rendering_method")).is_equal("forward_plus")
	assert_int(ProjectSettings.get_setting("display/window/size/viewport_width")).is_equal(960)
	assert_int(ProjectSettings.get_setting("display/window/size/viewport_height")).is_equal(540)
	assert_str(ProjectSettings.get_setting("application/run/main_scene")).is_equal(BOOTSTRAP_SCENE)


func test_bootstrap_scene_loads_with_required_nodes() -> void:
	var packed_scene := load(BOOTSTRAP_SCENE) as PackedScene
	assert_object(packed_scene).is_not_null()
	var instance: Node = auto_free(packed_scene.instantiate()) as Node
	for node_path in [
		"FoundationEnvironment",
		"FoundationGround",
		"FoundationMarker",
		"FoundationLight",
		"FoundationCamera",
	]:
		assert_object(instance.get_node_or_null(node_path)).is_not_null()


func test_bootstrap_contract_exposes_marker_and_smoke_flag() -> void:
	var bootstrap_script := load(BOOTSTRAP_SCRIPT) as GDScript
	assert_object(bootstrap_script).is_not_null()
	assert_str(bootstrap_script.READINESS_MARKER).is_equal(READINESS_MARKER)
	assert_bool(bootstrap_script.is_smoke_requested(PackedStringArray(["--matrix-oasis-smoke"]))).is_true()
	assert_bool(bootstrap_script.is_smoke_requested(PackedStringArray())).is_false()


func test_bootstrap_dependencies_stay_inside_the_project() -> void:
	for dependency in ResourceLoader.get_dependencies(BOOTSTRAP_SCENE):
		assert_bool(dependency.contains("res://")).is_true()

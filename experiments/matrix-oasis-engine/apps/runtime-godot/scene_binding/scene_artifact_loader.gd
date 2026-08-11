class_name MatrixOasisSceneArtifactLoader
extends RefCounted

const SCENE_ARGUMENT := "--matrix-oasis-scene-pack="
const SCENE_MAX_BYTES := 256 * 1024
const ASSET_MAX_BYTES := 32 * 1024 * 1024
const TOTAL_ASSET_MAX_BYTES := 128 * 1024 * 1024
const TOTAL_TRIANGLE_LIMIT := 1000000
const PATH_SEPARATOR := "\u002f"
const KENNEY_TEXTURE_URI := "Textures/colormap.png"
const KENNEY_TEXTURE_BYTE_LENGTH := 8706
const KENNEY_TEXTURE_SHA256 := "0d4947d34ff32acf4a359c7f22ca784e057e7e72f622170a9a77b6fc88fdb70e"
const KENNEY_FIGURINE_SHA256 := "ae0ea82089e66215684b0b2f5a162be9f6c71475085c81c3b80e53abd08b6bd8"
const KENNEY_GLBS := {
	"7dec224fbdd2297524c56fe3b4fa79fe6c5854f4b699a9e2e2c21ce6f008738c": true,
	"ae0ea82089e66215684b0b2f5a162be9f6c71475085c81c3b80e53abd08b6bd8": true,
	"873232210ff286b26bb6bfc371d3c6c96479a5b667f2927de3bcf06b1114d5af": true,
	"538dd97f85473999e1e9fe4758dc48daa85a7eed0be50b30c004702ab848f36c": true,
}


static func load_from_arguments(arguments: PackedStringArray) -> Dictionary:
	var scene_path := ""
	var runtime_arguments := PackedStringArray()
	for argument in arguments:
		if argument.begins_with(SCENE_ARGUMENT):
			if not scene_path.is_empty():
				return _failure("input", "PACK_GODOT_SCENE_INPUT_INVALID", "/scenePack")
			scene_path = argument.substr(SCENE_ARGUMENT.length())
		else:
			runtime_arguments.append(argument)
	if scene_path.is_empty() or not scene_path.is_absolute_path():
		return _failure("input", "PACK_GODOT_SCENE_INPUT_INVALID", "/scenePack")
	var runtime_loaded := MatrixOasisRuntimeArtifactLoader.load_from_arguments(runtime_arguments)
	if not runtime_loaded["ok"]:
		return runtime_loaded
	var scene_loaded := load_from_path(scene_path, runtime_loaded["prepared"])
	if not scene_loaded["ok"]:
		return scene_loaded
	var result := {"ok": true, "prepared_runtime": runtime_loaded["prepared"], "prepared_scene": scene_loaded["prepared"]}
	result.make_read_only()
	return result


static func load_from_path(scene_path: String, prepared_runtime: MatrixOasisPreparedRuntimePack) -> Dictionary:
	if prepared_runtime == null or scene_path.is_empty() or not scene_path.is_absolute_path() or _path_has_link(scene_path):
		return _failure("input", "PACK_GODOT_SCENE_INPUT_INVALID", "/scenePack")
	var read := _read_file(scene_path, SCENE_MAX_BYTES)
	if not read["ok"]:
		return _failure("input", "PACK_GODOT_SCENE_INPUT_INVALID", "/scenePack")
	return prepare_from_bytes(read["bytes"], scene_path.get_base_dir(), prepared_runtime)


static func prepare_from_bytes(scene_bytes: PackedByteArray, scene_directory: String, prepared_runtime: MatrixOasisPreparedRuntimePack) -> Dictionary:
	if prepared_runtime == null or scene_bytes.is_empty() or scene_bytes.size() > SCENE_MAX_BYTES or scene_directory.is_empty() or not scene_directory.is_absolute_path():
		return _failure("input", "PACK_GODOT_SCENE_INPUT_INVALID", "/scenePack")
	var parsed := MatrixOasisStrictJson.parse_bytes(scene_bytes, "/scenePack")
	if not parsed["ok"]:
		var code := "GODOT_RUNTIME_UNSUPPORTED_TEXT" if parsed["diagnostics"][0]["code"] == "GODOT_RUNTIME_UNSUPPORTED_TEXT" else "PACK_GODOT_SCENE_JSON_INVALID"
		return _failure("parse", code, "/scenePack")
	var runtime_pack := prepared_runtime._copy_pack_for_runtime()
	var validation := MatrixOasisScenePackRules.validate(parsed["value"], runtime_pack, prepared_runtime.artifact_sha256())
	if not validation["ok"]:
		return validation
	var loaded_assets := {}
	var total_bytes := 0
	var total_triangles := 0
	var checked_dependencies := {}
	for index in parsed["value"]["assets"].size():
		var asset: Dictionary = parsed["value"]["assets"][index]
		var absolute := scene_directory.path_join(asset["path"]).simplify_path()
		if not _is_contained(scene_directory, absolute) or _path_has_link(absolute):
			return _failure("integrity", "PACK_GODOT_SCENE_ASSET_INVALID", "/scenePack/assets/%d/path" % index)
		var read := _read_file(absolute, ASSET_MAX_BYTES)
		if not read["ok"]:
			return _failure("integrity", "PACK_GODOT_SCENE_ASSET_INVALID", "/scenePack/assets/%d/path" % index)
		var bytes: PackedByteArray = read["bytes"]
		total_bytes += bytes.size()
		var actual_sha256 := _sha256_hex(bytes)
		if total_bytes > TOTAL_ASSET_MAX_BYTES or bytes.size() != asset["byteLength"] or actual_sha256 != asset["sha256"]:
			return _failure("integrity", "PACK_GODOT_SCENE_INTEGRITY_MISMATCH", "/scenePack/assets/%d" % index)
		var allowed_external_uri := KENNEY_TEXTURE_URI if KENNEY_GLBS.has(actual_sha256) else ""
		if not allowed_external_uri.is_empty() and not checked_dependencies.has(allowed_external_uri):
			var dependency := absolute.get_base_dir().path_join(allowed_external_uri).simplify_path()
			if not _is_contained(scene_directory, dependency) or _path_has_link(dependency):
				return _failure("integrity", "PACK_GODOT_SCENE_ASSET_INVALID", "/scenePack/assets/%d/path" % index)
			var dependency_read := _read_file(dependency, KENNEY_TEXTURE_BYTE_LENGTH)
			if not dependency_read["ok"]:
				return _failure("integrity", "PACK_GODOT_SCENE_ASSET_INVALID", "/scenePack/assets/%d/path" % index)
			var dependency_bytes: PackedByteArray = dependency_read["bytes"]
			if dependency_bytes.size() != KENNEY_TEXTURE_BYTE_LENGTH or _sha256_hex(dependency_bytes) != KENNEY_TEXTURE_SHA256:
				return _failure("integrity", "PACK_GODOT_SCENE_INTEGRITY_MISMATCH", "/scenePack/assets/%d/path" % index)
			total_bytes += dependency_bytes.size()
			if total_bytes > TOTAL_ASSET_MAX_BYTES:
				return _failure("integrity", "SCENE_PACK_ASSET_TOTAL_SIZE_LIMIT", "/scenePack/assets")
			checked_dependencies[allowed_external_uri] = true
		var strip_animations := actual_sha256 == KENNEY_FIGURINE_SHA256
		var inspected := MatrixOasisSceneGlbRules.inspect(bytes, allowed_external_uri, strip_animations)
		if not inspected["ok"]:
			return _failure("integrity", inspected["code"], "/scenePack/assets/%d/path" % index)
		total_triangles += inspected["triangles"]
		if total_triangles > TOTAL_TRIANGLE_LIMIT:
			return _failure("integrity", "SCENE_PACK_GLB_TOTAL_TRIANGLE_LIMIT", "/scenePack/assets")
		var materialized := _materialize_glb(bytes, absolute, asset["roles"], strip_animations)
		if not materialized["ok"]:
			return _failure("integrity", "PACK_GODOT_SCENE_ASSET_INVALID", "/scenePack/assets/%d/path" % index)
		loaded_assets[asset["id"]] = materialized["asset"]
	var manifest_sha256 := _sha256_hex(scene_bytes)
	if manifest_sha256.is_empty():
		return _failure("runtime", "PACK_GODOT_SCENE_INTERNAL_ERROR", "/scenePack")
	var result := {"ok": true, "prepared": MatrixOasisPreparedScene.new(parsed["value"], manifest_sha256, prepared_runtime.artifact_sha256(), loaded_assets)}
	result.make_read_only()
	return result


static func _materialize_glb(bytes: PackedByteArray, path: String, roles: Array, strip_animations: bool) -> Dictionary:
	var materialized_bytes := MatrixOasisSceneGlbRules.without_animations(bytes) if strip_animations else bytes
	if materialized_bytes.is_empty():
		return {"ok": false}
	var document := GLTFDocument.new()
	var state := GLTFState.new()
	if document.append_from_buffer(materialized_bytes, path, state) != OK:
		return {"ok": false}
	var root := document.generate_scene(state)
	if root == null or not root.find_children("*", "AnimationPlayer", true, false).is_empty():
		if root != null:
			root.free()
		return {"ok": false}
	var packed: Variant = null
	if "visual" in roles:
		packed = PackedScene.new()
		if (packed as PackedScene).pack(root) != OK:
			root.free()
			return {"ok": false}
	var faces := PackedVector3Array()
	if "collider" in roles:
		faces = _collect_faces(root, Transform3D.IDENTITY)
		if faces.is_empty():
			root.free()
			return {"ok": false}
	root.free()
	var asset := {"visual": packed, "collider_faces": faces}
	return {"ok": true, "asset": asset}


static func _collect_faces(node: Node, parent_transform: Transform3D) -> PackedVector3Array:
	var local_transform := parent_transform
	if node is Node3D:
		local_transform = parent_transform * (node as Node3D).transform
	var faces := PackedVector3Array()
	if node is MeshInstance3D and (node as MeshInstance3D).mesh != null:
		for vertex in (node as MeshInstance3D).mesh.get_faces():
			faces.append(local_transform * vertex)
	for child in node.get_children():
		faces.append_array(_collect_faces(child, local_transform))
	return faces


static func _read_file(path: String, maximum: int) -> Dictionary:
	var file := FileAccess.open(path, FileAccess.READ)
	if file == null:
		return {"ok": false}
	var length := file.get_length()
	if length < 1 or length > maximum:
		return {"ok": false}
	var bytes := file.get_buffer(length)
	return {"ok": bytes.size() == length and file.get_position() == length, "bytes": bytes}


static func _path_has_link(path: String) -> bool:
	var current := path
	while not current.is_empty():
		var parent := current.get_base_dir()
		if parent == current:
			break
		var directory := DirAccess.open(parent)
		if directory == null or directory.is_link(current.get_file()):
			return true
		current = parent
	return false


static func _is_contained(root: String, target: String) -> bool:
	var normalized_root := root.replace("\\", PATH_SEPARATOR).simplify_path().trim_suffix(PATH_SEPARATOR)
	var normalized_target := target.replace("\\", PATH_SEPARATOR).simplify_path()
	return normalized_target.begins_with(normalized_root + PATH_SEPARATOR)


static func _sha256_hex(bytes: PackedByteArray) -> String:
	var context := HashingContext.new()
	if context.start(HashingContext.HASH_SHA256) != OK or context.update(bytes) != OK:
		return ""
	return context.finish().hex_encode()


static func _failure(phase: String, code: String, path: String) -> Dictionary:
	var diagnostic := {"phase": phase, "severity": "error", "code": code, "path": path, "message": code}
	diagnostic.make_read_only()
	var diagnostics := [diagnostic]
	diagnostics.make_read_only()
	var result := {"ok": false, "diagnostics": diagnostics}
	result.make_read_only()
	return result

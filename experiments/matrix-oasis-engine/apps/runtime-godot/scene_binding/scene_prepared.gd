class_name MatrixOasisPreparedScene
extends RefCounted

var _scene_pack: Dictionary
var _manifest_sha256: String
var _runtime_artifact_sha256: String
var _assets: Dictionary


func _init(scene_pack: Dictionary, manifest_sha256_value: String, runtime_artifact_sha256_value: String, assets: Dictionary) -> void:
	_scene_pack = scene_pack.duplicate(true)
	_manifest_sha256 = manifest_sha256_value
	_runtime_artifact_sha256 = runtime_artifact_sha256_value
	_assets = assets.duplicate()
	_make_read_only(_scene_pack)
	for asset_id in _assets:
		var record: Dictionary = _assets[asset_id]
		record.make_read_only()
	_assets.make_read_only()


func manifest_sha256() -> String:
	return _manifest_sha256


func runtime_artifact_sha256() -> String:
	return _runtime_artifact_sha256


func _copy_scene_for_runtime() -> Dictionary:
	return _scene_pack.duplicate(true)


func _asset_ids_for_runtime() -> Array:
	return _assets.keys().duplicate()


func _instantiate_visual_for_runtime(asset_id: String) -> Node:
	if not _assets.has(asset_id):
		return null
	var packed: Variant = _assets[asset_id]["visual"]
	return null if packed == null else (packed as PackedScene).instantiate()


func _collider_faces_for_runtime(asset_id: String) -> PackedVector3Array:
	if not _assets.has(asset_id):
		return PackedVector3Array()
	return (_assets[asset_id]["collider_faces"] as PackedVector3Array).duplicate()


static func _make_read_only(value: Variant) -> void:
	if typeof(value) == TYPE_DICTIONARY:
		for key in value.keys():
			_make_read_only(value[key])
		value.make_read_only()
	elif typeof(value) == TYPE_ARRAY:
		for child in value:
			_make_read_only(child)
		value.make_read_only()

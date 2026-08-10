class_name MatrixOasisPreparedRuntimePack
extends RefCounted

var _runtime_pack: Dictionary
var _receipt: Dictionary
var _artifact_sha256: String


func _init(runtime_pack: Dictionary, receipt: Dictionary) -> void:
	_runtime_pack = runtime_pack.duplicate(true)
	_receipt = receipt.duplicate(true)
	_make_read_only(_runtime_pack)
	_make_read_only(_receipt)
	_artifact_sha256 = _receipt["artifact"]["sha256"]


func _copy_pack_for_runtime() -> Dictionary:
	return _runtime_pack.duplicate(true)


func _copy_receipt_for_runtime() -> Dictionary:
	return _receipt.duplicate(true)


func artifact_sha256() -> String:
	return _artifact_sha256


static func _make_read_only(value: Variant) -> void:
	if typeof(value) == TYPE_DICTIONARY:
		for key in value.keys():
			_make_read_only(value[key])
		value.make_read_only()
	elif typeof(value) == TYPE_ARRAY:
		for child in value:
			_make_read_only(child)
		value.make_read_only()

class_name MatrixOasisRuntimeArtifactLoader
extends RefCounted

const RUNTIME_ARGUMENT := "--matrix-oasis-runtime-pack="
const RECEIPT_ARGUMENT := "--matrix-oasis-runtime-receipt="
const RUNTIME_MAX_BYTES := 16 * 1024 * 1024
const RECEIPT_MAX_BYTES := 16 * 1024


static func load_from_arguments(arguments: PackedStringArray) -> Dictionary:
	var paths := paths_from_arguments(arguments)
	if not paths["ok"]:
		return paths
	return load_from_paths(paths["runtime_path"], paths["receipt_path"])


static func paths_from_arguments(arguments: PackedStringArray) -> Dictionary:
	var runtime_path := ""
	var receipt_path := ""
	for argument in arguments:
		if argument.begins_with(RUNTIME_ARGUMENT):
			if not runtime_path.is_empty():
				return _input_failure("/runtimePack")
			runtime_path = argument.substr(RUNTIME_ARGUMENT.length())
		elif argument.begins_with(RECEIPT_ARGUMENT):
			if not receipt_path.is_empty():
				return _input_failure("/receipt")
			receipt_path = argument.substr(RECEIPT_ARGUMENT.length())
		else:
			return _input_failure("/runtimePack")
	if runtime_path.is_empty() or not runtime_path.is_absolute_path():
		return _input_failure("/runtimePack")
	if receipt_path.is_empty() or not receipt_path.is_absolute_path():
		return _input_failure("/receipt")
	var result := {"ok": true, "runtime_path": runtime_path, "receipt_path": receipt_path}
	result.make_read_only()
	return result


static func load_from_paths(runtime_path: String, receipt_path: String) -> Dictionary:
	var runtime_read := _read_approved_file(runtime_path, RUNTIME_MAX_BYTES, "/runtimePack")
	if not runtime_read["ok"]:
		return runtime_read
	var receipt_read := _read_approved_file(receipt_path, RECEIPT_MAX_BYTES, "/receipt")
	if not receipt_read["ok"]:
		return receipt_read
	return prepare_from_bytes(runtime_read["bytes"], receipt_read["bytes"])


static func prepare_from_bytes(runtime_bytes: PackedByteArray, receipt_bytes: PackedByteArray) -> Dictionary:
	if runtime_bytes.is_empty() or runtime_bytes.size() > RUNTIME_MAX_BYTES:
		return _input_failure("/runtimePack")
	if receipt_bytes.is_empty() or receipt_bytes.size() > RECEIPT_MAX_BYTES:
		return _input_failure("/receipt")
	var runtime_parsed := MatrixOasisStrictJson.parse_bytes(runtime_bytes, "/runtimePack")
	if not runtime_parsed["ok"]:
		return _public_parse_failure(runtime_parsed)
	var receipt_parsed := MatrixOasisStrictJson.parse_bytes(receipt_bytes, "/receipt")
	if not receipt_parsed["ok"]:
		return _public_parse_failure(receipt_parsed)
	var runtime_pack: Dictionary = runtime_parsed["value"]
	var receipt: Dictionary = receipt_parsed["value"]
	var runtime_validation := MatrixOasisRuntimePackRules.validate_runtime_pack(runtime_pack)
	if not runtime_validation["ok"]:
		return _normalize_failure(runtime_validation, "GODOT_RUNTIME_SCHEMA_INVALID" if runtime_validation["diagnostics"][0]["phase"] == "schema" else "GODOT_RUNTIME_SEMANTIC_INVALID")
	var receipt_validation := MatrixOasisRuntimePackRules.validate_receipt(receipt)
	if not receipt_validation["ok"]:
		return _normalize_failure(receipt_validation, "GODOT_RUNTIME_SCHEMA_INVALID")
	if receipt["artifact"]["byteLength"] != runtime_bytes.size():
		return _failure("integrity", "GODOT_RUNTIME_INTEGRITY_MISMATCH", "/receipt/artifact/byteLength")
	var digest := _sha256_hex(runtime_bytes)
	if digest.is_empty() or receipt["artifact"]["sha256"] != digest:
		return _failure("integrity", "GODOT_RUNTIME_INTEGRITY_MISMATCH", "/receipt/artifact/sha256")
	var result := {
		"ok": true,
		"prepared": MatrixOasisPreparedRuntimePack.new(runtime_pack, receipt),
	}
	result.make_read_only()
	return result


static func _read_approved_file(approved_path: String, maximum_bytes: int, document_path: String) -> Dictionary:
	var file := FileAccess.open(approved_path, FileAccess.READ)
	if file == null:
		return _input_failure(document_path)
	var length := file.get_length()
	if length < 1 or length > maximum_bytes:
		return _input_failure(document_path)
	var bytes := file.get_buffer(length)
	if bytes.size() != length or file.get_position() != length:
		return _input_failure(document_path)
	return {"ok": true, "bytes": bytes}


static func _sha256_hex(bytes: PackedByteArray) -> String:
	var context := HashingContext.new()
	if context.start(HashingContext.HASH_SHA256) != OK:
		return ""
	if context.update(bytes) != OK:
		return ""
	return context.finish().hex_encode()


static func _public_parse_failure(result: Dictionary) -> Dictionary:
	var diagnostic: Dictionary = result["diagnostics"][0]
	if diagnostic["code"] == "GODOT_RUNTIME_UNSUPPORTED_TEXT":
		return _failure("parse", "GODOT_RUNTIME_UNSUPPORTED_TEXT", diagnostic["path"])
	return _failure("parse", "GODOT_RUNTIME_JSON_INVALID", diagnostic["path"])


static func _normalize_failure(result: Dictionary, code: String) -> Dictionary:
	var diagnostic: Dictionary = result["diagnostics"][0]
	return _failure(diagnostic["phase"], code, diagnostic["path"])


static func _input_failure(path: String) -> Dictionary:
	return _failure("input", "GODOT_RUNTIME_INPUT_INVALID", path)


static func _failure(phase: String, code: String, path: String) -> Dictionary:
	var diagnostic := {
		"phase": phase,
		"severity": "error",
		"code": code,
		"path": path,
		"message": code,
	}
	diagnostic.make_read_only()
	var diagnostics := [diagnostic]
	diagnostics.make_read_only()
	var result := {
		"ok": false,
		"diagnostics": diagnostics,
	}
	result.make_read_only()
	return result

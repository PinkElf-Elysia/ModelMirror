class_name MatrixOasisSceneGlbRules
extends RefCounted

const GLB_MAGIC := 0x46546c67
const JSON_CHUNK := 0x4e4f534a
const BIN_CHUNK := 0x004e4942
const MAX_NODES := 256
const MAX_MESHES := 64
const MAX_SURFACES := 128
const MAX_TRIANGLES := 250000
const PATH_SEPARATOR := "\u002f"


static func inspect(bytes: PackedByteArray, allowed_external_uri := "", allow_animations := false) -> Dictionary:
	if bytes.size() < 20 or bytes.decode_u32(0) != GLB_MAGIC or bytes.decode_u32(4) != 2 or bytes.decode_u32(8) != bytes.size():
		return _failure("SCENE_PACK_GLB_INVALID")
	var offset := 12
	var document: Variant = null
	var binary_length := -1
	while offset < bytes.size():
		if offset + 8 > bytes.size():
			return _failure("SCENE_PACK_GLB_INVALID")
		var chunk_length := bytes.decode_u32(offset)
		var chunk_type := bytes.decode_u32(offset + 4)
		offset += 8
		if chunk_length % 4 != 0 or offset + chunk_length > bytes.size():
			return _failure("SCENE_PACK_GLB_INVALID")
		var chunk := bytes.slice(offset, offset + chunk_length)
		offset += chunk_length
		if document == null and chunk_type == JSON_CHUNK:
			while not chunk.is_empty() and chunk[chunk.size() - 1] in [0, 0x20]:
				chunk.resize(chunk.size() - 1)
			if chunk.is_empty() or not MatrixOasisStrictJson._is_valid_utf8(chunk):
				return _failure("SCENE_PACK_GLB_INVALID")
			var text := chunk.get_string_from_utf8()
			document = JSON.parse_string(text)
			if typeof(document) != TYPE_DICTIONARY:
				return _failure("SCENE_PACK_GLB_INVALID")
		elif document != null and chunk_type == BIN_CHUNK and binary_length < 0:
			binary_length = chunk_length
		else:
			return _failure("SCENE_PACK_GLB_INVALID")
	if offset != bytes.size() or typeof(document) != TYPE_DICTIONARY:
		return _failure("SCENE_PACK_GLB_INVALID")
	return _inspect_document(document, binary_length, allowed_external_uri, allow_animations)


static func _inspect_document(document: Dictionary, binary_length: int, allowed_external_uri: String, allow_animations: bool) -> Dictionary:
	if typeof(document.get("asset")) != TYPE_DICTIONARY or document["asset"].get("version") != "2.0":
		return _failure("SCENE_PACK_GLB_INVALID")
	for key in ["skins", "cameras"]:
		if document.has(key) and (typeof(document[key]) != TYPE_ARRAY or not document[key].is_empty()):
			return _failure("SCENE_PACK_GLB_FEATURE_UNSUPPORTED")
	if document.has("animations") and (typeof(document["animations"]) != TYPE_ARRAY or (not document["animations"].is_empty() and not allow_animations)):
		return _failure("SCENE_PACK_GLB_FEATURE_UNSUPPORTED")
	if document.has("extensionsRequired") and (typeof(document["extensionsRequired"]) != TYPE_ARRAY or not document["extensionsRequired"].is_empty()):
		return _failure("SCENE_PACK_GLB_FEATURE_UNSUPPORTED")
	var pending: Array = [{"value": document, "pointer": ""}]
	while not pending.is_empty():
		var item: Dictionary = pending.pop_back()
		var current: Variant = item["value"]
		var pointer: String = item["pointer"]
		if typeof(current) == TYPE_DICTIONARY:
			if current.has("uri") and not _is_allowed_image_uri(pointer, current["uri"], allowed_external_uri):
				return _failure("SCENE_PACK_GLB_EXTERNAL_URI")
			if current.has("camera") or current.has("skin") or current.has("KHR_lights_punctual"):
				return _failure("SCENE_PACK_GLB_FEATURE_UNSUPPORTED")
			for key in current:
				pending.append({"value": current[key], "pointer": "%s/%s" % [pointer, key]})
		elif typeof(current) == TYPE_ARRAY:
			for index in current.size():
				pending.append({"value": current[index], "pointer": "%s/%d" % [pointer, index]})
	var buffers: Variant = document.get("buffers", [])
	if typeof(buffers) != TYPE_ARRAY or buffers.size() > 1:
		return _failure("SCENE_PACK_GLB_INVALID")
	if buffers.is_empty() and binary_length >= 0:
		return _failure("SCENE_PACK_GLB_INVALID")
	if buffers.size() == 1:
		if typeof(buffers[0]) != TYPE_DICTIONARY or not _is_json_integer(buffers[0].get("byteLength")):
			return _failure("SCENE_PACK_GLB_INVALID")
		var declared := int(buffers[0]["byteLength"])
		if declared < 0 or binary_length < declared or binary_length - declared > 3:
			return _failure("SCENE_PACK_GLB_INVALID")
	var nodes: Variant = document.get("nodes", [])
	var meshes: Variant = document.get("meshes", [])
	var accessors: Variant = document.get("accessors", [])
	if typeof(nodes) != TYPE_ARRAY or typeof(meshes) != TYPE_ARRAY or typeof(accessors) != TYPE_ARRAY:
		return _failure("SCENE_PACK_GLB_INVALID")
	var surface_count := 0
	var triangle_count := 0
	for mesh in meshes:
		if typeof(mesh) != TYPE_DICTIONARY or typeof(mesh.get("primitives")) != TYPE_ARRAY:
			return _failure("SCENE_PACK_GLB_INVALID")
		for primitive in mesh["primitives"]:
			if typeof(primitive) != TYPE_DICTIONARY or primitive.get("mode", 4) != 4:
				return _failure("SCENE_PACK_GLB_FEATURE_UNSUPPORTED")
			surface_count += 1
			var accessor_index: Variant = primitive.get("indices")
			if accessor_index == null and typeof(primitive.get("attributes")) == TYPE_DICTIONARY:
				accessor_index = primitive["attributes"].get("POSITION")
			if not _is_json_integer(accessor_index):
				return _failure("SCENE_PACK_GLB_INVALID")
			var resolved_index := int(accessor_index)
			if resolved_index < 0 or resolved_index >= accessors.size() or typeof(accessors[resolved_index]) != TYPE_DICTIONARY or not _is_json_integer(accessors[resolved_index].get("count")):
				return _failure("SCENE_PACK_GLB_INVALID")
			var count := int(accessors[resolved_index]["count"])
			if count < 0:
				return _failure("SCENE_PACK_GLB_INVALID")
			triangle_count += count / 3
	if nodes.size() > MAX_NODES or meshes.size() > MAX_MESHES or surface_count > MAX_SURFACES or triangle_count > MAX_TRIANGLES:
		return _failure("SCENE_PACK_GLB_COMPLEXITY_LIMIT")
	return {"ok": true, "triangles": triangle_count}


static func _is_json_integer(value: Variant) -> bool:
	if typeof(value) == TYPE_INT:
		return true
	return typeof(value) == TYPE_FLOAT and is_finite(value) and value == floor(value) and abs(value) <= 9007199254740991.0


static func _is_allowed_image_uri(pointer: String, value: Variant, allowed_external_uri: String) -> bool:
	if allowed_external_uri.is_empty() or typeof(value) != TYPE_STRING or value != allowed_external_uri:
		return false
	var segments := pointer.trim_prefix(PATH_SEPARATOR).split(PATH_SEPARATOR, true)
	return segments.size() == 2 and segments[0] == "images" and segments[1].is_valid_int()


static func without_animations(bytes: PackedByteArray) -> PackedByteArray:
	if bytes.size() < 20 or bytes.decode_u32(0) != GLB_MAGIC or bytes.decode_u32(4) != 2 or bytes.decode_u32(8) != bytes.size():
		return PackedByteArray()
	var json_length := bytes.decode_u32(12)
	if json_length % 4 != 0 or bytes.decode_u32(16) != JSON_CHUNK or 20 + json_length > bytes.size():
		return PackedByteArray()
	var json_bytes := bytes.slice(20, 20 + json_length)
	while not json_bytes.is_empty() and json_bytes[json_bytes.size() - 1] in [0, 0x20]:
		json_bytes.resize(json_bytes.size() - 1)
	if json_bytes.is_empty() or not MatrixOasisStrictJson._is_valid_utf8(json_bytes):
		return PackedByteArray()
	var document: Variant = JSON.parse_string(json_bytes.get_string_from_utf8())
	if typeof(document) != TYPE_DICTIONARY or not document.has("animations") or typeof(document["animations"]) != TYPE_ARRAY:
		return PackedByteArray()
	document.erase("animations")
	var sanitized_json := JSON.stringify(document, "", true).to_utf8_buffer()
	while sanitized_json.size() % 4 != 0:
		sanitized_json.append(0x20)
	var binary_chunk := PackedByteArray()
	var binary_length := 0
	var binary_offset := 20 + json_length
	if binary_offset < bytes.size():
		if binary_offset + 8 > bytes.size() or bytes.decode_u32(binary_offset + 4) != BIN_CHUNK:
			return PackedByteArray()
		binary_length = bytes.decode_u32(binary_offset)
		if binary_length % 4 != 0 or binary_offset + 8 + binary_length != bytes.size():
			return PackedByteArray()
		binary_chunk = bytes.slice(binary_offset + 8, bytes.size())
	var result := PackedByteArray()
	result.resize(20)
	result.encode_u32(0, GLB_MAGIC)
	result.encode_u32(4, 2)
	result.encode_u32(12, sanitized_json.size())
	result.encode_u32(16, JSON_CHUNK)
	result.append_array(sanitized_json)
	if not binary_chunk.is_empty():
		var binary_header_offset := result.size()
		result.resize(result.size() + 8)
		result.encode_u32(binary_header_offset, binary_length)
		result.encode_u32(binary_header_offset + 4, BIN_CHUNK)
		result.append_array(binary_chunk)
	result.encode_u32(8, result.size())
	return result


static func _failure(code: String) -> Dictionary:
	return {"ok": false, "code": code}

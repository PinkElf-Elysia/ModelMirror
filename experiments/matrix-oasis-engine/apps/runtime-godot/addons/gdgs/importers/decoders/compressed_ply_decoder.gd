@tool
extends RefCounted

const BinaryPlyReader = preload("res://addons/gdgs/importers/parsers/binary_ply_reader.gd")
const GaussianResourceBuilder = preload("res://addons/gdgs/importers/builders/gaussian_resource_builder.gd")

const SH_C0 := 0.28209479177387814
const SQRT2 := 1.4142135623730951
const SH_COEFF_COUNT := 48

static func decode(path: String) -> Dictionary:
	var ply := BinaryPlyReader.read(path, true)
	if not ply.get("ok", false):
		return ply

	var chunk_element := BinaryPlyReader.get_element(ply, "chunk")
	var vertex_element := BinaryPlyReader.get_element(ply, "vertex")
	if chunk_element.is_empty() or vertex_element.is_empty():
		return _error(ERR_INVALID_DATA, "Compressed PLY must contain 'chunk' and 'vertex' elements")

	var vertex_map: Dictionary = vertex_element.get("property_map", {})
	for property_name in ["packed_position", "packed_rotation", "packed_scale", "packed_color"]:
		if not vertex_map.has(property_name):
			return _error(ERR_INVALID_DATA, "Compressed PLY is missing '%s'" % property_name)

	var chunk_map: Dictionary = chunk_element.get("property_map", {})
	var chunk_required := [
		"min_x", "min_y", "min_z",
		"max_x", "max_y", "max_z",
		"min_scale_x", "min_scale_y", "min_scale_z",
		"max_scale_x", "max_scale_y", "max_scale_z",
		"min_r", "min_g", "min_b",
		"max_r", "max_g", "max_b"
	]
	for property_name in chunk_required:
		if not chunk_map.has(property_name):
			return _error(ERR_INVALID_DATA, "Compressed PLY chunk metadata is missing '%s'" % property_name)

	var count := int(vertex_element.get("count", 0))
	var expected_chunks := int(ceili(count / 256.0))
	if int(chunk_element.get("count", 0)) < expected_chunks:
		return _error(ERR_INVALID_DATA, "Compressed PLY does not contain enough chunk records")

	var chunk_stride := int(chunk_element.get("stride", 0))
	var chunk_data: PackedByteArray = chunk_element.get("data", PackedByteArray())
	var chunks: Array = []
	chunks.resize(int(chunk_element.get("count", 0)))
	for i in chunks.size():
		var base := i * chunk_stride
		chunks[i] = {
			"min_x": float(_read_property(chunk_data, base, chunk_map, "min_x")),
			"min_y": float(_read_property(chunk_data, base, chunk_map, "min_y")),
			"min_z": float(_read_property(chunk_data, base, chunk_map, "min_z")),
			"max_x": float(_read_property(chunk_data, base, chunk_map, "max_x")),
			"max_y": float(_read_property(chunk_data, base, chunk_map, "max_y")),
			"max_z": float(_read_property(chunk_data, base, chunk_map, "max_z")),
			"min_scale_x": float(_read_property(chunk_data, base, chunk_map, "min_scale_x")),
			"min_scale_y": float(_read_property(chunk_data, base, chunk_map, "min_scale_y")),
			"min_scale_z": float(_read_property(chunk_data, base, chunk_map, "min_scale_z")),
			"max_scale_x": float(_read_property(chunk_data, base, chunk_map, "max_scale_x")),
			"max_scale_y": float(_read_property(chunk_data, base, chunk_map, "max_scale_y")),
			"max_scale_z": float(_read_property(chunk_data, base, chunk_map, "max_scale_z")),
			"min_r": float(_read_property(chunk_data, base, chunk_map, "min_r")),
			"min_g": float(_read_property(chunk_data, base, chunk_map, "min_g")),
			"min_b": float(_read_property(chunk_data, base, chunk_map, "min_b")),
			"max_r": float(_read_property(chunk_data, base, chunk_map, "max_r")),
			"max_g": float(_read_property(chunk_data, base, chunk_map, "max_g")),
			"max_b": float(_read_property(chunk_data, base, chunk_map, "max_b"))
		}

	var sh_element := BinaryPlyReader.get_element(ply, "sh")
	var sh_stride := 0
	var sh_coeffs_per_channel := 0
	var sh_data := PackedByteArray()
	if not sh_element.is_empty():
		var sh_map: Dictionary = sh_element.get("property_map", {})
		sh_coeffs_per_channel = int(sh_map.size() / 3)
		sh_stride = int(sh_element.get("stride", 0))
		sh_data = sh_element.get("data", PackedByteArray())
		if int(sh_element.get("count", 0)) != count:
			return _error(ERR_INVALID_DATA, "Compressed PLY SH element count does not match vertex count")
		if sh_coeffs_per_channel < 0 or sh_coeffs_per_channel > 15:
			return _error(ERR_INVALID_DATA, "Compressed PLY SH payload has an unsupported size")

	var canonical := GaussianResourceBuilder.create_canonical(count)
	var positions: PackedVector3Array = canonical["positions"]
	var scales_linear: PackedVector3Array = canonical["scales_linear"]
	var rotations: Array = canonical["rotations"]
	var opacities: PackedFloat32Array = canonical["opacities"]
	var sh_coeffs: PackedFloat32Array = canonical["sh_coeffs"]

	var vertex_stride := int(vertex_element.get("stride", 0))
	var vertex_data: PackedByteArray = vertex_element.get("data", PackedByteArray())

	for i in count:
		var base := i * vertex_stride
		var chunk: Dictionary = chunks[int(i / 256)]

		var packed_position := int(_read_property(vertex_data, base, vertex_map, "packed_position"))
		var packed_rotation := int(_read_property(vertex_data, base, vertex_map, "packed_rotation"))
		var packed_scale := int(_read_property(vertex_data, base, vertex_map, "packed_scale"))
		var packed_color := int(_read_property(vertex_data, base, vertex_map, "packed_color"))

		var position_norm := _unpack_111011(packed_position)
		positions[i] = Vector3(
			_lerp_range(chunk["min_x"], chunk["max_x"], position_norm.x),
			_lerp_range(chunk["min_y"], chunk["max_y"], position_norm.y),
			_lerp_range(chunk["min_z"], chunk["max_z"], position_norm.z)
		)

		var log_scale_norm := _unpack_111011(packed_scale)
		scales_linear[i] = Vector3(
			exp(_lerp_range(chunk["min_scale_x"], chunk["max_scale_x"], log_scale_norm.x)),
			exp(_lerp_range(chunk["min_scale_y"], chunk["max_scale_y"], log_scale_norm.y)),
			exp(_lerp_range(chunk["min_scale_z"], chunk["max_scale_z"], log_scale_norm.z))
		)

		rotations[i] = _unpack_packed_rotation(packed_rotation)

		var packed_rgba := _unpack_8888(packed_color)
		var dc_r := _lerp_range(chunk["min_r"], chunk["max_r"], packed_rgba.x)
		var dc_g := _lerp_range(chunk["min_g"], chunk["max_g"], packed_rgba.y)
		var dc_b := _lerp_range(chunk["min_b"], chunk["max_b"], packed_rgba.z)

		var sh_offset := i * SH_COEFF_COUNT
		sh_coeffs[sh_offset + 0] = (dc_r - 0.5) / SH_C0
		sh_coeffs[sh_offset + 1] = (dc_g - 0.5) / SH_C0
		sh_coeffs[sh_offset + 2] = (dc_b - 0.5) / SH_C0
		opacities[i] = packed_rgba.w

		if not sh_element.is_empty():
			var sh_base := i * sh_stride
			for coeff_idx in range(sh_coeffs_per_channel):
				var dst := sh_offset + 3 + coeff_idx * 3
				sh_coeffs[dst + 0] = _decode_quantized_sh(sh_data[sh_base + coeff_idx])
				sh_coeffs[dst + 1] = _decode_quantized_sh(sh_data[sh_base + coeff_idx + sh_coeffs_per_channel])
				sh_coeffs[dst + 2] = _decode_quantized_sh(sh_data[sh_base + coeff_idx + sh_coeffs_per_channel * 2])

	return {
		"ok": true,
		"canonical": canonical
	}

static func _read_property(data: PackedByteArray, base: int, property_map: Dictionary, property_name: String) -> Variant:
	var prop: Dictionary = property_map[property_name]
	return BinaryPlyReader.decode_scalar(data, base + int(prop["offset"]), String(prop["type"]))

static func _unpack_111011(value: int) -> Vector3:
	return Vector3(
		float((value >> 21) & 0x7FF) / 2047.0,
		float((value >> 11) & 0x3FF) / 1023.0,
		float(value & 0x7FF) / 2047.0
	)

static func _unpack_8888(value: int) -> Vector4:
	return Vector4(
		float((value >> 24) & 0xFF) / 255.0,
		float((value >> 16) & 0xFF) / 255.0,
		float((value >> 8) & 0xFF) / 255.0,
		float(value & 0xFF) / 255.0
	)

static func _unpack_packed_rotation(value: int) -> Quaternion:
	var largest := (value >> 30) & 0x3
	var packed := [
		(value >> 20) & 0x3FF,
		(value >> 10) & 0x3FF,
		value & 0x3FF
	]

	var components := [0.0, 0.0, 0.0, 0.0]
	var packed_idx := 0
	var sum_sq := 0.0

	for component_idx in 4:
		if component_idx == largest:
			continue
		var decoded := ((float(packed[packed_idx]) / 1023.0) - 0.5) * SQRT2
		components[component_idx] = decoded
		sum_sq += decoded * decoded
		packed_idx += 1

	components[largest] = sqrt(maxf(0.0, 1.0 - sum_sq))
	return Quaternion(components[1], components[2], components[3], components[0]).normalized()

static func _decode_quantized_sh(value: int) -> float:
	return (((float(value) + 0.5) / 256.0) - 0.5) * 8.0

static func _lerp_range(min_value: float, max_value: float, normalized: float) -> float:
	return min_value + (max_value - min_value) * normalized

static func _error(code: Error, message: String) -> Dictionary:
	return {
		"ok": false,
		"error": code,
		"message": message
	}

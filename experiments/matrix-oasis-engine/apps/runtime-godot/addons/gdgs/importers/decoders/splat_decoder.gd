@tool
extends RefCounted

const GaussianResourceBuilder = preload("res://addons/gdgs/importers/builders/gaussian_resource_builder.gd")

const SH_C0 := 0.28209479177387814
const SH_COEFF_COUNT := 48
const RECORD_SIZE := 32

static func decode(path: String) -> Dictionary:
	var file := FileAccess.open(path, FileAccess.READ)
	if file == null:
		return _error(FileAccess.get_open_error(), "Unable to open splat file: %s" % path)

	var size := file.get_length()
	if size % RECORD_SIZE != 0:
		return _error(ERR_INVALID_DATA, "Legacy splat file size must be a multiple of 32 bytes")

	var count := int(size / RECORD_SIZE)
	var data := file.get_buffer(size)
	if data.size() != size:
		return _error(ERR_FILE_CORRUPT, "Unable to read the entire splat file")

	var canonical := GaussianResourceBuilder.create_canonical(count)
	var positions: PackedVector3Array = canonical["positions"]
	var scales_linear: PackedVector3Array = canonical["scales_linear"]
	var rotations: Array = canonical["rotations"]
	var opacities: PackedFloat32Array = canonical["opacities"]
	var sh_coeffs: PackedFloat32Array = canonical["sh_coeffs"]

	for i in count:
		var base := i * RECORD_SIZE
		positions[i] = Vector3(
			data.decode_float(base + 0),
			data.decode_float(base + 4),
			data.decode_float(base + 8)
		)
		scales_linear[i] = Vector3(
			maxf(data.decode_float(base + 12), 1e-6),
			maxf(data.decode_float(base + 16), 1e-6),
			maxf(data.decode_float(base + 20), 1e-6)
		)

		var w := _decode_signed_byte(int(data[base + 28]))
		var x := _decode_signed_byte(int(data[base + 29]))
		var y := _decode_signed_byte(int(data[base + 30]))
		var z := _decode_signed_byte(int(data[base + 31]))
		var rotation := Quaternion(x, y, z, w)
		var magnitude_sq := x * x + y * y + z * z + w * w
		if magnitude_sq <= 0.0:
			rotation = Quaternion(0.0, 0.0, 0.0, 1.0)
		else:
			rotation = rotation.normalized()
		rotations[i] = rotation

		var sh_offset := i * SH_COEFF_COUNT
		sh_coeffs[sh_offset + 0] = (float(data[base + 24]) / 255.0 - 0.5) / SH_C0
		sh_coeffs[sh_offset + 1] = (float(data[base + 25]) / 255.0 - 0.5) / SH_C0
		sh_coeffs[sh_offset + 2] = (float(data[base + 26]) / 255.0 - 0.5) / SH_C0
		opacities[i] = float(data[base + 27]) / 255.0

	return {
		"ok": true,
		"canonical": canonical
	}

static func _decode_signed_byte(value: int) -> float:
	return (float(value) - 128.0) / 128.0

static func _error(code: Error, message: String) -> Dictionary:
	return {
		"ok": false,
		"error": code,
		"message": message
	}

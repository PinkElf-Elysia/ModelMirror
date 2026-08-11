class_name MatrixOasisRuntimeAdapterTest
extends GdUnitTestSuite


func test_canonical_pair_prepares_an_opaque_copy() -> void:
	var pair := _valid_pair()
	var result := MatrixOasisRuntimeArtifactLoader.prepare_from_bytes(pair["runtime"], pair["receipt"])
	assert_bool(result["ok"]).is_true()
	assert_bool(result.is_read_only()).is_true()
	assert_bool(result["prepared"]._runtime_pack.is_read_only()).is_true()
	assert_bool(result["prepared"]._runtime_pack["nodes"].is_read_only()).is_true()
	assert_object(result["prepared"]).is_instanceof(MatrixOasisPreparedRuntimePack)
	var first: Dictionary = result["prepared"]._copy_pack_for_runtime()
	first["title"] = "mutated"
	var second: Dictionary = result["prepared"]._copy_pack_for_runtime()
	assert_str(second["title"]).is_equal("Adapter fixture")


func test_argument_contract_requires_two_absolute_read_only_paths() -> void:
	var project_path := ProjectSettings.globalize_path("res://")
	var runtime_path := project_path.path_join("runtime.json")
	var receipt_path := project_path.path_join("receipt.json")
	var other_path := project_path.path_join("other.json")
	var valid := MatrixOasisRuntimeArtifactLoader.paths_from_arguments(PackedStringArray([
		"--matrix-oasis-runtime-pack=" + runtime_path,
		"--matrix-oasis-runtime-receipt=" + receipt_path,
	]))
	assert_bool(valid["ok"]).is_true()
	for invalid in [
		PackedStringArray(),
		PackedStringArray(["--matrix-oasis-runtime-pack=relative.json"]),
		PackedStringArray([
			"--matrix-oasis-runtime-pack=" + runtime_path,
			"--matrix-oasis-runtime-pack=" + other_path,
			"--matrix-oasis-runtime-receipt=" + receipt_path,
		]),
	]:
		assert_bool(MatrixOasisRuntimeArtifactLoader.paths_from_arguments(invalid)["ok"]).is_false()


func test_strict_parser_rejects_noncanonical_and_invalid_bytes() -> void:
	var cases := [
		["{\"a\":1, \"b\":2}", "GODOT_RUNTIME_JSON_INVALID"],
		["{\"b\":1,\"a\":2}", "GODOT_RUNTIME_JSON_INVALID"],
		["{\"a\":1,\"a\":2}", "GODOT_RUNTIME_JSON_INVALID"],
		["{\"a\":1.0}", "GODOT_RUNTIME_JSON_INVALID"],
		["{\"a\":0e0}", "GODOT_RUNTIME_JSON_INVALID"],
		["{\"a\":\"\\u0041\"}", "GODOT_RUNTIME_JSON_INVALID"],
		["{\"a\":\"\\ud800\"}", "GODOT_RUNTIME_UNSUPPORTED_TEXT"],
	]
	for item in cases:
		var parsed := MatrixOasisRuntimeArtifactLoader.prepare_from_bytes(
			item[0].to_utf8_buffer(),
			"{}".to_utf8_buffer(),
		)
		assert_bool(parsed["ok"]).is_false()
		assert_str(_diagnostic_code(parsed)).is_equal(item[1])
	for invalid_bytes in [
		PackedByteArray([0xff]),
		PackedByteArray([0x80]),
		PackedByteArray([0xc0, 0x80]),
		PackedByteArray([0xe2, 0x82]),
		PackedByteArray([0xed, 0xa0, 0x80]),
		PackedByteArray([0xf4, 0x90, 0x80, 0x80]),
	]:
		var invalid_utf8 := MatrixOasisRuntimeArtifactLoader.prepare_from_bytes(
			invalid_bytes,
			"{}".to_utf8_buffer(),
		)
		assert_str(_diagnostic_code(invalid_utf8)).is_equal("GODOT_RUNTIME_JSON_INVALID")
		assert_bool(invalid_utf8.is_read_only()).is_true()
		assert_bool(invalid_utf8["diagnostics"].is_read_only()).is_true()
		assert_bool(invalid_utf8["diagnostics"][0].is_read_only()).is_true()


func test_json_depth_256_is_bounded_before_schema_validation() -> void:
	var depth_256 := ""
	for index in range(256):
		depth_256 += "{\"a\":"
	depth_256 += "null"
	depth_256 += "}".repeat(256)
	var accepted_depth := MatrixOasisStrictJson.parse_bytes(depth_256.to_utf8_buffer(), "/runtimePack")
	assert_bool(accepted_depth["ok"]).is_true()
	var depth_257 := "{\"a\":" + depth_256 + "}"
	var rejected_depth := MatrixOasisStrictJson.parse_bytes(depth_257.to_utf8_buffer(), "/runtimePack")
	assert_bool(rejected_depth["ok"]).is_false()
	assert_str(_diagnostic_code(rejected_depth)).is_equal("GODOT_RUNTIME_JSON_DEPTH_EXCEEDED")


func test_closed_schema_and_semantic_indexes_fail_before_integrity() -> void:
	var astral := String.chr(0x1f600)
	assert_bool(MatrixOasisRuntimePackRules._valid_prose(astral.repeat(4096))).is_true()
	assert_bool(MatrixOasisRuntimePackRules._valid_prose(astral.repeat(4097))).is_false()
	assert_bool(MatrixOasisRuntimePackRules._valid_content_version(astral.repeat(64))).is_true()
	assert_bool(MatrixOasisRuntimePackRules._valid_content_version(astral.repeat(65))).is_false()
	for whitespace in [0x00a0, 0x1680, 0x2007, 0x2028, 0x2029, 0x202f, 0x205f, 0x3000, 0xfeff]:
		assert_bool(MatrixOasisRuntimePackRules._valid_prose(String.chr(whitespace))).is_false()

	var runtime := _valid_runtime()
	runtime["unknown"] = true
	var schema_pair := _pair_for_runtime(runtime)
	var schema_result := MatrixOasisRuntimeArtifactLoader.prepare_from_bytes(schema_pair["runtime"], schema_pair["receipt"])
	assert_str(_diagnostic_code(schema_result)).is_equal("GODOT_RUNTIME_SCHEMA_INVALID")

	runtime = _valid_runtime()
	runtime["nodes"][0]["actions"][0]["target"]["index"] = 2
	var semantic_pair := _pair_for_runtime(runtime)
	var semantic_result := MatrixOasisRuntimeArtifactLoader.prepare_from_bytes(semantic_pair["runtime"], semantic_pair["receipt"])
	assert_str(_diagnostic_code(semantic_result)).is_equal("GODOT_RUNTIME_SEMANTIC_INVALID")


func test_receipt_compiler_length_and_hash_are_strict() -> void:
	var runtime := _valid_runtime()
	var runtime_bytes := _canonical_bytes(runtime)
	var receipt := _valid_receipt(runtime_bytes)
	receipt["compiler"]["version"] = "0.1.1"
	var wrong_compiler := MatrixOasisRuntimeArtifactLoader.prepare_from_bytes(runtime_bytes, _canonical_bytes(receipt))
	assert_str(_diagnostic_code(wrong_compiler)).is_equal("GODOT_RUNTIME_SCHEMA_INVALID")

	receipt = _valid_receipt(runtime_bytes)
	receipt["artifact"]["byteLength"] += 1
	var wrong_length := MatrixOasisRuntimeArtifactLoader.prepare_from_bytes(runtime_bytes, _canonical_bytes(receipt))
	assert_str(_diagnostic_code(wrong_length)).is_equal("GODOT_RUNTIME_INTEGRITY_MISMATCH")

	receipt = _valid_receipt(runtime_bytes)
	receipt["artifact"]["sha256"] = "0".repeat(64)
	var wrong_hash := MatrixOasisRuntimeArtifactLoader.prepare_from_bytes(runtime_bytes, _canonical_bytes(receipt))
	assert_str(_diagnostic_code(wrong_hash)).is_equal("GODOT_RUNTIME_INTEGRITY_MISMATCH")


func test_size_limits_fail_without_parsing_or_echoing_content() -> void:
	var oversized_runtime := PackedByteArray()
	oversized_runtime.resize(MatrixOasisRuntimeArtifactLoader.RUNTIME_MAX_BYTES + 1)
	var result := MatrixOasisRuntimeArtifactLoader.prepare_from_bytes(
		oversized_runtime,
		"{}".to_utf8_buffer(),
	)
	assert_str(_diagnostic_code(result)).is_equal("GODOT_RUNTIME_INPUT_INVALID")
	assert_str(JSON.stringify(result)).not_contains("runtime.json")


func _valid_pair() -> Dictionary:
	return _pair_for_runtime(_valid_runtime())


func _pair_for_runtime(runtime: Dictionary) -> Dictionary:
	var runtime_bytes := _canonical_bytes(runtime)
	return {
		"runtime": runtime_bytes,
		"receipt": _canonical_bytes(_valid_receipt(runtime_bytes)),
	}


func _valid_runtime() -> Dictionary:
	return {
		"canonicalization": "matrix-oasis.canonical-json/1",
		"cues": [],
		"endings": [{
			"cueIndexes": [],
			"id": "ending-done",
			"text": null,
			"title": "Done",
		}],
		"entities": [],
		"entryNodeIndex": 0,
		"format": "matrix-oasis.runtime-game-pack",
		"formatVersion": "0.1.0",
		"language": "zh-CN",
		"nodes": [{
			"actions": [{
				"effects": [],
				"entityIndexes": [],
				"id": "finish",
				"label": "Finish",
				"target": {"index": 0, "kind": "ending"},
				"when": null,
			}],
			"entityIndexes": [],
			"entryCueIndexes": [],
			"id": "node-start",
			"text": null,
			"title": "Start",
		}],
		"source": {
			"canonicalSha256": "1".repeat(64),
			"contentVersion": "1.0.0",
			"format": "matrix-oasis.authoring-game-pack",
			"formatVersion": "0.1.0",
			"id": "adapter-fixture",
		},
		"summary": null,
		"title": "Adapter fixture",
		"variables": [],
	}


func _valid_receipt(runtime_bytes: PackedByteArray) -> Dictionary:
	return {
		"artifact": {
			"byteLength": runtime_bytes.size(),
			"format": "matrix-oasis.runtime-game-pack",
			"formatVersion": "0.1.0",
			"sha256": MatrixOasisRuntimeArtifactLoader._sha256_hex(runtime_bytes),
		},
		"canonicalization": "matrix-oasis.canonical-json/1",
		"compiler": {
			"id": "@matrix-oasis/game-pack-compiler",
			"version": "0.1.0-r3",
		},
		"format": "matrix-oasis.runtime-game-pack-receipt",
		"formatVersion": "0.1.0",
	}


func _canonical_bytes(value: Variant) -> PackedByteArray:
	return JSON.stringify(value, "", true).to_utf8_buffer()


func _diagnostic_code(result: Dictionary) -> String:
	return result["diagnostics"][0]["code"]

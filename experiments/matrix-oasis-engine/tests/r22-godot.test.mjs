import assert from "node:assert/strict";
import { spawnSync } from "node:child_process";
import { lstat, mkdir, mkdtemp, readFile, realpath, rm, writeFile } from "node:fs/promises";
import { tmpdir } from "node:os";
import path from "node:path";
import test from "node:test";
import { fileURLToPath } from "node:url";
import { auditGodotBoundary } from "../scripts/check-godot-boundary.mjs";

const moduleRoot = fileURLToPath(new URL("..", import.meta.url));
const source = await readFile(new URL(
  "../apps/runtime-godot/npc_cognition_prototype/npc_cognition_lab.gd",
  import.meta.url,
), "utf8");
const scene = await readFile(new URL(
  "../apps/runtime-godot/npc_cognition_prototype/npc_cognition_lab.tscn",
  import.meta.url,
), "utf8");
const probe = await readFile(new URL(
  "../apps/runtime-godot/npc_cognition_prototype/npc_cognition_probe.gd",
  import.meta.url,
), "utf8");
const verifier = await readFile(new URL(
  "../scripts/verify-npc-cognition-godot.mjs",
  import.meta.url,
), "utf8");

test("global Godot boundary recognizes only the exact R22 loopback and one inert resume flag", async (t) => {
  assert.deepEqual(auditGodotBoundary().violations, []);
  const temporaryRoot = path.resolve(tmpdir()), root = await mkdtemp(path.join(temporaryRoot, "matrix-oasis-r22-boundary-"));
  const identity = await lstat(root, { bigint: true });
  t.after(async () => {
    const current = await lstat(root, { bigint: true });
    assert.equal(path.dirname(root), temporaryRoot); assert.equal(await realpath(root), root);
    assert.equal(current.dev, identity.dev); assert.equal(current.ino, identity.ino);
    await rm(root, { recursive: true, force: false });
  });
  const target = path.join(root, "npc_cognition_prototype", "npc_cognition_lab.gd");
  await mkdir(path.dirname(target));
  await writeFile(target, source);
  assert.equal(auditGodotBoundary({ root }).ok, true);
  for (const changed of [
    source.replace('"MATRIX_OASIS_R22_RESUME_QUEUED_ACTION"', '"MATRIX_OASIS_R22_OPENAI_API_KEY"'),
    source.replace("OS.get_environment(RESUME_QUEUED_ACTION_ENV)", "OS.has_environment(RESUME_QUEUED_ACTION_ENV)"),
    `${source}\nfunc extra_read():\n\treturn OS.get_environment(RESUME_QUEUED_ACTION_ENV)\n`,
  ]) {
    await writeFile(target, changed);
    assert.ok(auditGodotBoundary({ root }).violations.some((item) => item.code === "GODOT_FIRST_PARTY_ENVIRONMENT"));
  }
  await writeFile(target, source.replaceAll("127.0.0.1:43122", "127.0.0.1:43123"));
  assert.ok(auditGodotBoundary({ root }).violations.some((item) => item.code === "GODOT_FIRST_PARTY_NETWORK"));
});

test("R22 composes the R20 authority scene instead of copying movement or Runtime writes", () => {
  assert.match(source, /^extends "res:\/\/npc_authority_prototype\/npc_authority_lab\.gd"/u);
  for (const copiedCapability of [
    /func _install_navigation/u,
    /func _install_actors/u,
    /func _arrival_evidence_valid/u,
    /NavigationAgent3D/u,
    /PhysicsShapeQueryParameters3D/u,
    /move_and_slide\(/u,
    /_scene_lab\._apply_action/u,
    /\.begin_move\(/u,
    /_post\("arrived"/u,
    /_post\("mirror"/u,
  ]) {
    assert.doesNotMatch(source, copiedCapability);
  }
  assert.match(source, /super\._accept_command_response\(response\)/u);
  assert.match(source, /super\._accept_reset_response\(response\)/u);
  assert.match(source, /super\._request_command\(\)/u);
  assert.match(scene, /\[node name="AuthorityRequest" type="HTTPRequest" parent="\."\]/u);
  assert.match(scene, /\[node name="CognitionRequest" type="HTTPRequest" parent="\."\]/u);
});

test("R22 starts with zero commands and releases exactly one command after a completed turn", () => {
  assert.match(source, /var _permit_next_authority_command := false/u);
  assert.match(source, /if _lease_held or not _consume_authority_command_permit\(\):\s+_authority_state = "idle"\s+return/u);
  assert.match(source, /func _consume_authority_command_permit\(\) -> bool:\s+if not _permit_next_authority_command:\s+return false\s+_permit_next_authority_command = false\s+return true/u);
  assert.match(source, /if resume_authority.*_arm_one_authority_command\(\)\s+_request_command\(\)/su);
  assert.match(source, /func _accept_reset_response[\s\S]*_return_to_initial_waiting_state\(\)[\s\S]*super\._accept_reset_response\(response\)/u);
  assert.match(source, /func _return_to_initial_waiting_state[\s\S]*_permit_next_authority_command = false[\s\S]*_authority_state = "idle"/u);
  assert.match(probe, /R22_GODOT_PROBE_INITIAL_COMMAND_PERMITTED/u);
  assert.match(probe, /R22_GODOT_PROBE_MULTIPLE_COMMANDS_PERMITTED/u);
  assert.match(probe, /R22_GODOT_PROBE_RESET_WAITING_STATE_INVALID/u);
});

test("resume flag permits one host-gated command only at the first ready boundary", () => {
  assert.match(source, /RESUME_QUEUED_ACTION_ENV := "MATRIX_OASIS_R22_RESUME_QUEUED_ACTION"/u);
  assert.match(source, /_configure_resume_queued_action\(OS\.get_environment\(RESUME_QUEUED_ACTION_ENV\)\)/u);
  assert.match(source, /value not in \["", "1"\]/u);
  assert.match(source, /func _consume_resume_queued_action_at_ready_boundary[\s\S]*if _resume_ready_boundary_consumed:[\s\S]*_resume_ready_boundary_consumed = true[\s\S]*_arm_one_authority_command\(\)[\s\S]*_resume_queued_action_requested = false/u);
  assert.match(source, /print\(R22_READY_MARKER\)\s+_consume_resume_queued_action_at_ready_boundary\(\)/u);
  assert.match(probe, /R22_GODOT_PROBE_FRESH_COMMAND_PERMITTED/u);
  assert.match(probe, /R22_GODOT_PROBE_RESUME_COMMAND_NOT_PERMITTED/u);
  assert.match(probe, /R22_GODOT_PROBE_RESUME_COMMAND_REARMED/u);
  assert.match(probe, /R22_GODOT_PROBE_INVALID_RESUME_FLAG_ACCEPTED/u);
});

test("dialogue-only closes without releasing a fixed command while queued and fallback retain explicit continuation", () => {
  assert.match(source, /func _show_dialogue\(dialogue: String, action_queued: bool\)[\s\S]*_resume_authority_on_close = action_queued/u);
  assert.match(source, /func _show_fallback\(_diagnostic: String\)[\s\S]*_resume_authority_on_close = true/u);
  assert.match(source, /_show_dialogue\(dialogue, action_queued\)/u);
});

test("R22 emits bounded real physical evidence only after a completed command and 300 frames", () => {
  assert.match(source, /R22_LIVE_PHYSICAL_EVIDENCE_MARKER := "R22_LIVE_PHYSICAL_EVIDENCE_JSON:"/u);
  assert.match(source, /LIVE_PHYSICAL_SAMPLE_COUNT := 300/u);
  assert.match(source, /MAX_LIVE_PHYSICAL_EVIDENCE_BYTES := 32768/u);
  assert.match(source, /status == "command"[\s\S]*_begin_live_physical_evidence\(_active_command\)/u);
  assert.match(source, /_active_command\.is_empty\(\)[\s\S]*_live_physical_return_evidence = _current_live_physical_return_evidence\(\)[\s\S]*R22_LIVE_PHYSICAL_RETURN_EVIDENCE_INVALID[\s\S]*_live_physical_cycle_complete = true[\s\S]*_maybe_emit_live_physical_evidence\(\)/u);
  assert.match(source, /_live_physical_frames\.size\(\) != LIVE_PHYSICAL_SAMPLE_COUNT/u);
  assert.match(source, /canonicalR20Trace/u);
  assert.match(source, /"frameMicros": frames\.duplicate\(\)/u);
  assert.match(source, /"movement": \{"outbound": arrival, "return": return_evidence\}/u);
  assert.match(source, /"kind": "walked-home" if actor\.visible else "hidden-home"/u);
  assert.match(source, /actor\.global_position\.distance_to\(actor\.home_transform\.origin\)/u);
  assert.match(source, /actor\.is_physics_processing\(\)/u);
  assert.match(source, /"commandSha256": "sha256:" \+ canonical_command\.sha256_text\(\)/u);
  assert.match(source, /"intentSha256": "sha256:" \+ intent_json\.sha256_text\(\)/u);
  assert.match(source, /canonical\.to_utf8_buffer\(\)\.size\(\) > MAX_LIVE_PHYSICAL_EVIDENCE_BYTES/u);
  assert.match(source, /floori\(\(float\(sorted_frames\[149\]\) \+ float\(sorted_frames\[150\]\)\) \/ 2\.0\)/u);
  assert.match(source, /func _accept_reset_response[\s\S]*_reset_live_physical_evidence\(\)/u);
  const builderStart = source.indexOf("static func _build_live_physical_evidence");
  const builderEnd = source.indexOf("static func _approval_body", builderStart);
  const builder = source.slice(builderStart, builderEnd);
  for (const forbidden of ["playerText", "dialogueText", "providerRequestJson", "responseId"]) {
    assert.doesNotMatch(builder, new RegExp(forbidden, "u"));
  }
  assert.match(probe, /R22_GODOT_PROBE_PHYSICAL_EVIDENCE_EARLY/u);
  assert.match(probe, /R22_GODOT_PROBE_PHYSICAL_EVIDENCE_PRIVATE/u);
  assert.match(probe, /R22_GODOT_PROBE_PHYSICAL_RESET_INVALID/u);
});

test("pending physical evidence keeps interaction busy and rejects command sample overwrite", () => {
  assert.match(source, /func _actor_is_turn_safe[\s\S]*and not _live_physical_evidence_pending\(\)/u);
  assert.match(source, /func _live_physical_evidence_pending\(\) -> bool:\s+return _live_physical_sampling and not _live_physical_evidence_emitted/u);
  assert.match(source, /func _begin_live_physical_evidence\(command: Dictionary\) -> void:\s+if _live_physical_sampling:\s+_fail\("R22_LIVE_PHYSICAL_EVIDENCE_REENTRY"\)\s+return\s+_reset_live_physical_evidence\(\)/u);
  assert.match(probe, /R22_GODOT_PROBE_PHYSICAL_PENDING_GATE_INVALID/u);
  const resetStart = source.indexOf("func _reset_live_physical_evidence");
  const resetEnd = source.indexOf("func _current_live_physical_return_evidence", resetStart);
  const reset = source.slice(resetStart, resetEnd);
  assert.match(reset, /_live_physical_command = \{\}/u);
  assert.match(reset, /_live_physical_frames\.clear\(\)/u);
  assert.match(reset, /_live_physical_sampling = false/u);
  assert.match(reset, /_live_physical_cycle_complete = false/u);
  assert.match(reset, /_live_physical_return_evidence = \{\}/u);
});

test("neutral parsed command must pass production canonicalization without input mutation", () => {
  assert.match(probe, /NEUTRAL_NODE_COMMAND_SHA256 := "sha256:a311600aadbfc918215466e3a18c31edcf2b2ced4411cbcd71d5a05d2f0cd5df"/u);
  assert.match(probe, /NEUTRAL_NODE_EVIDENCE_SHA256 := "sha256:6471d6b9d1482c47574891e4126edef73e8d363e64adf0d0ede476cbc6a7eacc"/u);
  assert.match(probe, /typeof\(parsed\.get\("sequence"\)\) != TYPE_FLOAT/u);
  assert.match(probe, /typeof\(parsed\.get\("ruleIndex"\)\) != TYPE_FLOAT/u);
  assert.match(probe, /lab\._build_live_physical_evidence\(parsed, trace, frames, digest, returned\)/u);
  assert.match(probe, /evidence\.get\("command", \{\}\)\.get\("commandSha256"\) != NEUTRAL_NODE_COMMAND_SHA256/u);
  assert.match(probe, /canonical_evidence\.sha256_text\(\) != NEUTRAL_NODE_EVIDENCE_SHA256/u);
  assert.match(probe, /parsed != original/u);
  assert.match(probe, /\{"sequence": 1\.5\}.*\{"sequence": NAN\}.*\{"sequence": 10001\.0\}.*\{"ruleIndex": 256\.0\}/u);
  assert.match(probe, /R22_GODOT_SERIALIZER_NORMALIZATION_OK/u);
  assert.doesNotMatch(probe, /ACTUAL_R22|RETAINED_COMMAND/u);
  const serializerStart = probe.indexOf("func _check_neutral_command_serializer");
  const serializerEnd = probe.indexOf("func _check_closed_helpers", serializerStart);
  assert.doesNotMatch(probe.slice(serializerStart, serializerEnd), /playerText|dialogueText|providerRequestJson/u);
});

test("R22 Godot networking is exact loopback-only and never reads a provider credential", () => {
  assert.equal([...source.matchAll(/http:\/\/127\.0\.0\.1:43122\/v1\//gu)].length, 1);
  assert.equal([...source.matchAll(/https:\/\/api\.openai\.com\/v1\/responses/gu)].length, 1);
  assert.doesNotMatch(source, /OPENAI_API_KEY|MATRIX_OASIS_R22_OPENAI_API_KEY/iu);
  assert.equal([...source.matchAll(/\.(?:request)\(R22_LOOPBACK_BASE \+ route/gu)].length, 2);
  assert.doesNotMatch(source, /\.request\(OPENAI_RESPONSES_ENDPOINT/u);
  assert.equal([...source.matchAll(/OS\.get_environment\(RESUME_QUEUED_ACTION_ENV\)/gu)].length, 1);
  assert.doesNotMatch(source, /OS\.get_environment\((?!RESUME_QUEUED_ACTION_ENV)/u);
  assert.doesNotMatch(source, /FileAccess|ResourceLoader|\bload\s*\(/u);
  assert.match(source, /route not in \["command", "arrived", "mirror", "reset", "verify"\]/u);
  assert.match(source, /route in \["cognition\/turn", "cognition\/approve", "cognition\/decline", "cognition\/displayed"\]/u);
  assert.match(source, /route\.begins_with\("cognition\/status\/"\)/u);
  assert.match(source, /_valid_turn_id\(route\.trim_prefix\("cognition\/status\/"\)\)/u);
  assert.match(source, /Content-Type: application\/json/u);
  assert.match(source, /MAX_LOCAL_BODY_BYTES := 65536/u);
  assert.match(scene, /\[node name="CognitionRequest" type="HTTPRequest" parent="\."\][\s\S]*?timeout = 66\.0/u);
});

test("approval and decline send only the content-bound turn ID and approval hash", () => {
  const helperStart = source.indexOf("static func _approval_body");
  const routeStart = source.indexOf("static func _valid_cognition_route", helperStart);
  const helper = source.slice(helperStart, routeStart);
  assert.ok(helperStart >= 0 && routeStart > helperStart);
  assert.match(helper, /return \{"approvalHash": approval_hash, "turnId": turn_id\}/u);
  assert.doesNotMatch(helper, /providerRequestJson|playerText|payload|apiKey|token/iu);
  assert.match(source, /_start_cognition_request\("cognition\/approve", HTTPClient\.METHOD_POST, _approval_body\(_turn_id, _approval_hash\)\)/u);
  assert.match(source, /_start_cognition_request\("cognition\/decline", HTTPClient\.METHOD_POST, _approval_body\(_turn_id, _approval_hash\)\)/u);
});

test("displayed dialogue is acknowledged before an R20 action can be released", () => {
  const helperStart = source.indexOf("static func _display_ack_body");
  const routeStart = source.indexOf("static func _valid_cognition_route", helperStart);
  const helper = source.slice(helperStart, routeStart);
  assert.ok(helperStart >= 0 && routeStart > helperStart);
  assert.match(helper, /return \{"displayAckHash": display_ack_hash, "turnId": turn_id\}/u);
  assert.doesNotMatch(helper, /dialogueText|playerText|payload|apiKey|token/iu);
  assert.match(source, /_dialogue_label\.text = dialogue[\s\S]*_set_modal_state\("displaying"\)[\s\S]*_acknowledge_dialogue_after_frame\.call_deferred\(display_ack_hash\)/u);
  assert.match(source, /func _acknowledge_dialogue_after_frame[\s\S]*await get_tree\(\)\.process_frame[\s\S]*_cognition_state != "displaying"[\s\S]*_start_cognition_request\("cognition\/displayed", HTTPClient\.METHOD_POST, _display_ack_body\(_turn_id, _display_ack_hash\)\)/u);
  assert.match(source, /route == "cognition\/displayed"[\s\S]*_accept_display_ack\(response\)/u);
  assert.match(source, /response\.get\("status"\) != "acknowledged"/u);
  assert.match(source, /_show_dialogue\(dialogue, action_queued\)[\s\S]*_emit_trace/u);
  assert.match(source, /_dialogue_section\.visible = state in \["displaying", "dialogue", "fallback"\]/u);
  assert.match(source, /_close_button\.disabled = state != "dialogue" and state != "fallback"/u);
});

test("NPC interaction requires a three metre physics ray, current visibility, and idle authority", () => {
  assert.match(source, /INTERACTION_DISTANCE_METERS := 3\.0/u);
  assert.match(source, /ENVIRONMENT_AND_NPC_COLLISION_MASK := 3/u);
  assert.match(source, /PhysicsRayQueryParameters3D\.create\(origin, target\)/u);
  assert.match(source, /query\.collision_mask = ENVIRONMENT_AND_NPC_COLLISION_MASK/u);
  assert.match(source, /query\.exclude = \[player\.get_rid\(\)\]/u);
  assert.match(source, /get_world_3d\(\)\.direct_space_state\.intersect_ray\(query\)/u);
  assert.match(source, /binding\.get\("visibleNodeIds", \[\]\)\.has\(node_id\)/u);
  assert.match(source, /_authority_state in \["idle", "quiescent"\]/u);
  assert.match(source, /not actor\.is_physics_processing\(\)/u);
  assert.match(source, /_is_physical_key\(event, KEY_E\)/u);
  assert.match(source, /NPC is busy/u);
});

test("native responsive UI renders disclosure and dialogue as inert plain text", () => {
  assert.match(scene, /\[node name="PlayerText" type="TextEdit"/u);
  assert.match(scene, /\[node name="DisclosureScroll" type="ScrollContainer"/u);
  assert.match(scene, /\[node name="Disclosure" type="Label"/u);
  assert.match(scene, /\[node name="DialogueScroll" type="ScrollContainer"/u);
  assert.match(scene, /\[node name="Dialogue" type="Label"/u);
  assert.doesNotMatch(scene, /RichTextLabel|bbcode|meta_clicked|uri|link/iu);
  assert.match(scene, /anchor_right = 1\.0/u);
  assert.match(scene, /anchor_bottom = 1\.0/u);
  assert.doesNotMatch(scene, /custom_minimum_size = Vector2\((?:6[1-9][0-9]|[7-9][0-9]{2,}),/u);
  assert.match(source, /_disclosure_label\.text = _disclosure_text\(disclosure\)/u);
  assert.match(source, /_dialogue_label\.text = dialogue/u);
  assert.doesNotMatch(source, /parse_bbcode|append_text|ResourceLoader|Expression|GDScript/u);
});

test("approval disclosure strictly validates and renders all nine host fields", () => {
  assert.match(source, /_exact\(value, \[\s*"endpoint", "maxCostMicrousd", "maxOutputTokens", "model", "priceLock",\s*"providerRequestJson", "requestLimit", "retention", "retryLimit"/u);
  assert.match(source, /value\.get\("endpoint"\) != OPENAI_RESPONSES_ENDPOINT/u);
  assert.match(source, /value\.get\("model"\) != OPENAI_COGNITION_MODEL/u);
  assert.match(source, /_exact_integer\(value\.get\("maxCostMicrousd"\), MAX_CALL_COST_MICROUSD\)/u);
  assert.match(source, /_exact_integer\(value\.get\("maxOutputTokens"\), MAX_OUTPUT_TOKENS\)/u);
  assert.match(source, /_exact_integer\(value\.get\("requestLimit"\), PROVIDER_REQUEST_LIMIT\)/u);
  assert.match(source, /_exact_integer\(value\.get\("retryLimit"\), PROVIDER_RETRY_LIMIT\)/u);
  for (const field of [
    "inputMicrousdPerMillionTokens",
    "cachedInputMicrousdPerMillionTokens",
    "cacheWriteInputMicrousdPerMillionTokens",
    "outputMicrousdPerMillionTokens",
  ]) {
    assert.match(source, new RegExp(`_exact_integer\\(price_lock\\.get\\("${field}"\\),`, "u"));
  }
  assert.match(source, /retention\.get\("store"\) == false/u);
  assert.match(source, /retention\.get\("zeroDataRetentionClaimed"\) == false/u);
  assert.match(source, /_exact_integer\(retention\.get\("abuseMonitoringMaxDays"\), 30\)/u);
  assert.match(source, /retention\.get\("promptCachingPossible"\) == true/u);
  for (const rendered of [
    "Maximum output: %d tokens",
    "Request limit: %d",
    "Retry limit: %d",
    "Price lock (microusd / 1M tokens)",
    "Exact outbound JSON",
  ]) {
    assert.ok(source.includes(rendered), `missing disclosure text: ${rendered}`);
  }
  assert.match(probe, /R22_GODOT_PROBE_DISCLOSURE_RETRY_DRIFT_ACCEPTED/u);
  assert.match(probe, /R22_GODOT_PROBE_DISCLOSURE_PRICE_DRIFT_ACCEPTED/u);
  assert.match(probe, /R22_GODOT_PROBE_DISCLOSURE_UNKNOWN_FIELD_ACCEPTED/u);
  assert.match(probe, /R22_GODOT_PROBE_DISCLOSURE_RETENTION_DRIFT_ACCEPTED/u);
});

test("modal owns focus, freezes WASD, and exposes complete keyboard paths", () => {
  assert.match(source, /COGNITION_LEASE_TIMEOUT_MSEC := 300000/u);
  assert.match(source, /PREPARATION_TIMEOUT_MSEC := 65000/u);
  assert.match(source, /_cognition_state == "planning"[\s\S]*_planning_started_msec[\s\S]*PREPARATION_TIMEOUT_MSEC[\s\S]*_cognition_request\.cancel_request\(\)[\s\S]*R22_GODOT_PREPARATION_TIMEOUT/u);
  assert.match(source, /_lease_held = true[\s\S]*_lease_started_msec = Time\.get_ticks_msec\(\)/u);
  assert.match(source, /Time\.get_ticks_msec\(\) - _lease_started_msec >= COGNITION_LEASE_TIMEOUT_MSEC/u);
  assert.match(source, /if _cognition_state == "approval":\s+_decline_turn\(\)\s+else:\s+_close_modal\(false\)/u);
  assert.match(source, /_scene_lab\.player\.input_enabled = false/u);
  assert.match(source, /_scene_lab\.player\.input_enabled = _player_was_enabled/u);
  assert.match(source, /Input\.mouse_mode = Input\.MOUSE_MODE_VISIBLE/u);
  assert.match(source, /_previous_mouse_mode = Input\.mouse_mode/u);
  assert.match(source, /Input\.mouse_mode = _previous_mouse_mode/u);
  assert.match(source, /_approve_button\.grab_focus\.call_deferred\(\)/u);
  assert.match(source, /_close_button\.grab_focus\.call_deferred\(\)/u);
  assert.match(source, /event\.ctrl_pressed/u);
  assert.match(source, /"approval": _decline_turn\(\)/u);
  assert.match(source, /"approval": _approve_turn\(\)/u);
  assert.match(source, /"dialogue", "fallback": _close_dialogue\(\)/u);
  assert.match(scene, /follow_focus = true/gu);
});

test("strict transient responses cannot bypass R20 or leak untrusted fallback text", () => {
  assert.match(source, /status == "dialogue_only".*"displayAckHash"/u);
  assert.match(source, /status == "queued_for_r20".*"displayAckHash"/u);
  assert.match(source, /status == "dialogue_only".*response\.get\("actionChoiceId"\) == null/u);
  assert.match(source, /status == "queued_for_r20".*_is_choice_id/u);
  assert.match(source, /status == "fallback".*_valid_static_diagnostic/u);
  assert.match(source, /FIXED_FALLBACK_TEXT :=/u);
  assert.match(source, /func _show_fallback\(_diagnostic: String\)/u);
  const fallbackStart = source.indexOf("func _show_fallback");
  const traceStart = source.indexOf("func _emit_trace", fallbackStart);
  const fallback = source.slice(fallbackStart, traceStart);
  assert.match(fallback, /_dialogue_label\.text = FIXED_FALLBACK_TEXT/u);
  assert.doesNotMatch(fallback, /_dialogue_label\.text = _diagnostic/u);
  assert.match(source, /_close_modal\(_resume_authority_on_close\)/u);
  assert.match(source, /if resume_authority.*_request_command\(\)/su);
});

test("response validation rejects unsafe path and Unicode boundary cases", () => {
  assert.match(source, /value\.length\(\) > 128/u);
  assert.match(source, /value\.unicode_at\(0\) < 97.*value\.unicode_at\(0\) > 122.*value\.ends_with\("-"\).*value\.contains\("--"\)/u);
  assert.match(source, /not lowercase and not digit and code != 45/u);
  assert.match(source, /code in \[0x2028, 0x2029, 0x202A/u);
  assert.match(source, /code < 32 and code != 10/u);
  assert.match(source, /value\.split\("\\n", true\)\.size\(\) <= MAX_DIALOGUE_LINES/u);
  assert.match(probe, /turn%2fescape/u);
  assert.match(probe, /turn\/path/u);
  assert.match(probe, /turn\?query/u);
  assert.match(probe, /1turn/u);
  assert.match(probe, /turn--id/u);
  assert.match(probe, /String\.chr\(0x2028\)/u);
  assert.match(probe, /String\.chr\(0x2029\)/u);
  assert.match(probe, /tab\\tcontrol/u);
});

test("R22 verifier imports the isolated project and runs the official-Godot probe", () => {
  assert.match(verifier, /createRuntimePreviewProject\(\{ moduleRoot \}\)/u);
  assert.match(verifier, /configureGdgsProject\(project\.projectRoot\)/u);
  assert.match(verifier, /res:\/\/npc_cognition_prototype\/npc_cognition_probe\.gd/u);
  assert.match(verifier, /assertGodotOutputClean\(importOutput\)/u);
  assert.match(verifier, /assertGodotOutputClean\(probeOutput\)/u);
  assert.match(verifier, /R22_NPC_COGNITION_GODOT_PROBE_OK/u);
  assert.match(verifier, /removeRuntimePreviewProject/u);
  assert.match(verifier, /R22_GODOT_FAILURE_ARTIFACTS_RETAINED/u);
  assert.match(verifier, /GODOT_ENVIRONMENT_ALLOWLIST/u);
  for (const allowedName of [
    "SystemRoot", "WINDIR", "TEMP", "TMP", "USERPROFILE", "APPDATA",
    "LOCALAPPDATA", "PATH", "COMSPEC", "PATHEXT", "SYSTEMDRIVE",
    "HOMEDRIVE", "HOMEPATH",
  ]) {
    assert.ok(verifier.includes(`"${allowedName}"`), `missing Godot environment allowlist entry: ${allowedName}`);
  }
  assert.match(verifier, /environment: \{ GODOT_BIN: process\.env\.GODOT_BIN \}/u);
  assert.equal([...verifier.matchAll(/spawn: spawnGodot/gu)].length, 2);
  assert.doesNotMatch(verifier, /env:\s*process\.env|env:\s*\{\s*\.\.\.process\.env/u);
});

test("official Godot 4.6.3 imports and probes the R22 cognition wrapper", {
  skip: typeof process.env.GODOT_BIN !== "string" || process.env.GODOT_BIN.length === 0,
}, () => {
  const result = spawnSync(process.execPath, [
    path.join(moduleRoot, "scripts", "verify-npc-cognition-godot.mjs"),
  ], {
    cwd: moduleRoot,
    encoding: "utf8",
    env: process.env,
    windowsHide: true,
    timeout: 180_000,
    maxBuffer: 16 * 1024 * 1024,
  });
  assert.equal(result.error, undefined, result.error?.message);
  assert.equal(result.status, 0, `${result.stdout}\n${result.stderr}`);
  const output = `${result.stdout}\n${result.stderr}`;
  assert.match(output, /R22_NPC_COGNITION_GODOT_PROBE_OK version=4\.6\.3/u);
  assert.match(output, /R22_NPC_COGNITION_GODOT_IMPORT_OK version=4\.6\.3/u);
});

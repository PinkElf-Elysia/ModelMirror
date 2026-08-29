import assert from "node:assert/strict";
import test from "node:test";
import { readFile } from "node:fs/promises";
import { auditGodotBoundary } from "../scripts/check-godot-boundary.mjs";

const actorSource = await readFile(new URL("../apps/runtime-godot/npc_authority_prototype/npc_actor_controller.gd", import.meta.url), "utf8");
const bridgeSource = await readFile(new URL("../apps/runtime-godot/npc_authority_prototype/npc_authority_lab.gd", import.meta.url), "utf8");

test("R20 bridge is the sole exact Godot loopback exception", () => {
  const report = auditGodotBoundary();
  assert.equal(report.ok, true, JSON.stringify(report.violations));
  assert.match(bridgeSource, /http:\/\/127\.0\.0\.1:43120\/v1\//u);
  assert.doesNotMatch(bridgeSource, /https:\/\//u);
  assert.doesNotMatch(bridgeSource, /\b(?:WebSocket|StreamPeerTCP|PacketPeerUDP|ENetMultiplayerPeer|TCPServer)\b/u);
  assert.equal([...bridgeSource.matchAll(/OS\.get_environment\(/gu)].length, 1);
});

test("NPC movement follows the locked 60 Hz profile without avoidance or teleport fallback", () => {
  assert.match(actorSource, /const PHYSICS_TICKS_PER_SECOND := 60/u);
  assert.match(actorSource, /const SPEED_PER_TICK := 0\.05/u);
  assert.match(actorSource, /const TURN_RADIANS_PER_TICK := deg_to_rad\(3\.0\)/u);
  assert.match(actorSource, /const MOVEMENT_TICK_LIMIT := 1800/u);
  assert.match(actorSource, /const MAXIMUM_PATH_LENGTH := 100\.0/u);
  assert.match(actorSource, /_agent\.get_next_path_position\(\)/u);
  assert.match(actorSource, /move_and_slide\(\)/u);
  assert.match(actorSource, /_agent\.avoidance_enabled = false/u);
  assert.doesNotMatch(actorSource, /global_position\s*=\s*target/u);
});

test("authority is submitted only after four physics arrival proofs and mirror hash agreement", () => {
  const arrivalIndex = bridgeSource.indexOf('_post("arrived", request_body)');
  const moveIndex = bridgeSource.indexOf("actor.begin_move(_floor_anchors[anchor_id])");
  const actionIndex = bridgeSource.indexOf("_scene_lab._apply_action(_active_command");
  const mirrorIndex = bridgeSource.indexOf('_post("mirror"');
  assert.ok(moveIndex >= 0 && arrivalIndex > moveIndex && actionIndex > arrivalIndex && mirrorIndex > actionIndex);
  for (const field of ["pathComplete", "floorVerified", "capsuleVerified", "domainVerified"]) {
    assert.ok(actorSource.includes(`\"${field}\"`));
  }
  assert.match(bridgeSource, /before != response\.get\("beforeSnapshotSha256"\)/u);
  assert.match(bridgeSource, /after != response\.get\("afterSnapshotSha256"\)/u);
  assert.match(bridgeSource, /_scene_lab\.interaction_ray\.enabled = false/u);
});

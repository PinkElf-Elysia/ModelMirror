import assert from "node:assert/strict";
import test from "node:test";
import {
  assertRequiredAudioHourPricingOverlays,
  preserveLocalOverlay,
} from "./update-openrouter-models.mjs";

const upstreamModel = {
  id: "microsoft/mai-transcribe-2",
  pricing: { input: 100_000, output: 0 },
};

const currentModel = {
  ...upstreamModel,
  note: "local contract note",
  pricing_basis_override: "media",
  media_pricing: { unit: "audio_hour", usd: 0.1 },
};

test("preserves an aligned audio-hour pricing overlay", () => {
  assert.deepEqual(preserveLocalOverlay(upstreamModel, currentModel), currentModel);
});

test("fails closed when upstream and local audio-hour prices diverge", () => {
  assert.throws(
    () =>
      preserveLocalOverlay(
        { ...upstreamModel, pricing: { input: 120_000, output: 0 } },
        currentModel,
      ),
    /Manual audio-hour pricing overlay update required/,
  );
});

test("fails closed when a required audio-hour overlay is missing", () => {
  assert.throws(
    () =>
      preserveLocalOverlay(upstreamModel, {
        ...currentModel,
        pricing_basis_override: undefined,
        media_pricing: undefined,
      }),
    /Manual audio-hour pricing overlay update required/,
  );
  assert.throws(
    () => preserveLocalOverlay(upstreamModel, undefined),
    /Manual audio-hour pricing overlay required/,
  );
});

test("validates required overlays before every updater phase", () => {
  assert.doesNotThrow(() =>
    assertRequiredAudioHourPricingOverlays([upstreamModel], [currentModel]),
  );
  assert.throws(
    () =>
      assertRequiredAudioHourPricingOverlays([upstreamModel], [
        {
          ...currentModel,
          media_pricing: undefined,
        },
      ]),
    /Manual audio-hour pricing overlay update required/,
  );
  assert.doesNotThrow(() =>
    assertRequiredAudioHourPricingOverlays([], [currentModel]),
  );
  assert.throws(
    () =>
      assertRequiredAudioHourPricingOverlays([], [
        {
          ...currentModel,
          media_pricing: { unit: "audio_hour", usd: null },
        },
      ]),
    /source=unavailable/,
  );
});

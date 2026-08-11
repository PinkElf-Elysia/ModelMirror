import { describe, expect, it } from "vitest";
import { estimateVideoCost } from "./videoCostEstimate";

const seedance25Profile = {
  supported_resolutions: ["480p", "720p"],
  supported_aspect_ratios: ["16:9", "4:3", "1:1", "3:4", "9:16", "21:9"],
  supported_sizes: [
    "854x480",
    "752x560",
    "640x640",
    "560x752",
    "480x854",
    "992x432",
    "1280x720",
    "1112x834",
    "960x960",
    "834x1112",
    "720x1280",
    "1470x630",
  ],
  pricing_skus: {
    video_tokens: "0.0000107",
    video_tokens_without_audio: "0.0000107",
    video_tokens_with_video_input: "0.0000064",
  },
};

describe("estimateVideoCost", () => {
  it("estimates Seedance 2.5 from its selected size and video-token rate", () => {
    const estimate = estimateVideoCost(seedance25Profile, {
      duration: 4,
      resolution: "480p",
      aspectRatio: "16:9",
      generateAudio: false,
      imageInputCount: 0,
    });

    expect(estimate).toBeCloseTo(0.411201, 6);
  });

  it("keeps existing per-second pricing ahead of the video-token fallback", () => {
    const estimate = estimateVideoCost(
      {
        supported_resolutions: ["720p"],
        supported_aspect_ratios: ["16:9"],
        supported_sizes: ["1280x720"],
        pricing_skus: {
          duration_seconds_without_audio_720p: "0.25",
          video_tokens: "0.0000107",
        },
      },
      {
        duration: 4,
        resolution: "720p",
        aspectRatio: "16:9",
        generateAudio: false,
        imageInputCount: 1,
      },
    );

    expect(estimate).toBe(1);
  });

  it("does not guess a video-token estimate without a matching catalog size", () => {
    const estimate = estimateVideoCost(seedance25Profile, {
      duration: 4,
      resolution: "720p",
      aspectRatio: "2:1",
      generateAudio: false,
      imageInputCount: 0,
    });

    expect(estimate).toBeNull();
  });
});

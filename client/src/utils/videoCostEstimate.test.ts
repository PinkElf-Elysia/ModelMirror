import { describe, expect, it } from "vitest";
import {
  estimateVideoCost,
  estimateVideoUpscaleCost,
  supportedAspectRatiosForResolution,
  videoGenerationUnitRate,
  videoUpscaleUnitRate,
} from "./videoCostEstimate";

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

  it("maps Seedance Mini sizes by dimensions when a resolution omits one aspect ratio", () => {
    const miniProfile = {
        supported_resolutions: ["480p", "720p"],
        supported_aspect_ratios: ["1:1", "3:4", "9:16", "4:3", "16:9", "21:9", "9:21"],
        supported_sizes: [
          "480x480", "480x640", "480x854", "640x480", "854x480", "1120x480",
          "720x720", "720x960", "720x1280", "720x1680", "960x720", "1280x720", "1680x720",
        ],
        pricing_skus: { video_tokens: "0.0000035" },
      };
    const estimate = estimateVideoCost(
      miniProfile,
      {
        duration: 4,
        resolution: "720p",
        aspectRatio: "16:9",
        generateAudio: true,
        imageInputCount: 0,
      },
    );

    expect(estimate).toBeCloseTo(0.3024, 6);
    expect(supportedAspectRatiosForResolution(miniProfile, "480p")).not.toContain("9:21");
    expect(supportedAspectRatiosForResolution(miniProfile, "720p")).toContain("9:21");
  });

  it("uses Wan 3.0 resolution-specific per-second pricing", () => {
    const wanProfile = {
      supported_resolutions: ["480p", "720p", "1080p"],
      supported_aspect_ratios: ["16:9"],
      supported_sizes: [],
      pricing_skus: {
        duration_seconds_480p: "0.05",
        duration_seconds_720p: "0.1",
        duration_seconds_1080p: "0.2",
      },
    };

    expect(
      videoGenerationUnitRate(wanProfile, {
        resolution: "1080p",
        generateAudio: true,
        imageInputCount: 1,
      }),
    ).toBe(0.2);
    expect(
      estimateVideoCost(wanProfile, {
        duration: 30,
        resolution: "1080p",
        aspectRatio: "16:9",
        generateAudio: true,
        imageInputCount: 1,
      }),
    ).toBe(6);
  });

  it("shows Avatar IV's unit price when script length determines duration", () => {
    const avatarProfile = {
      supported_resolutions: ["720p", "1080p"],
      supported_aspect_ratios: ["16:9", "9:16", "1:1"],
      supported_sizes: [],
      pricing_skus: { duration_seconds: "0.05" },
    };

    expect(
      videoGenerationUnitRate(avatarProfile, {
        resolution: "720p",
        generateAudio: false,
        imageInputCount: 1,
      }),
    ).toBe(0.05);
    expect(
      estimateVideoCost(avatarProfile, {
        duration: null,
        resolution: "720p",
        aspectRatio: "1:1",
        generateAudio: false,
        imageInputCount: 1,
      }),
    ).toBeNull();
  });
});

describe("estimateVideoUpscaleCost", () => {
  const profile = {
    supported_resolutions: [],
    supported_aspect_ratios: [],
    supported_sizes: [],
    pricing_skus: {
      cents_per_megapixel_second_precise: "7.5",
      cents_per_megapixel_second_creative: "10.5",
    },
  };

  it("uses output megapixel-seconds and the selected enhancement rate", () => {
    expect(
      estimateVideoUpscaleCost(profile, {
        durationSeconds: 10,
        width: 1280,
        height: 720,
        upscaleFactor: 2,
        creativity: 0,
      }),
    ).toBeCloseTo(2.7648, 6);
    expect(videoUpscaleUnitRate(profile, 1)).toBe(0.105);
  });

  it("returns no estimate when local video metadata is unavailable", () => {
    expect(
      estimateVideoUpscaleCost(profile, {
        durationSeconds: 0,
        width: 0,
        height: 0,
        upscaleFactor: 2,
        creativity: 1,
      }),
    ).toBeNull();
  });
});

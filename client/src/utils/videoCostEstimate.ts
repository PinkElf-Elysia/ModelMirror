export interface VideoPricingProfile {
  supported_resolutions: string[];
  supported_aspect_ratios: string[];
  supported_sizes: string[];
  pricing_skus: Record<string, string>;
}

export interface VideoCostSelection {
  duration: number | null;
  resolution: string;
  aspectRatio: string;
  generateAudio: boolean;
  imageInputCount: number;
}

function pricingNumber(profile: VideoPricingProfile, key: string) {
  const raw = profile.pricing_skus[key];
  if (raw === undefined) return null;
  const value = Number(raw);
  return Number.isFinite(value) && value >= 0 ? value : null;
}

function selectedDimensions(
  profile: VideoPricingProfile,
  resolution: string,
  aspectRatio: string,
) {
  const resolutionIndex = profile.supported_resolutions.indexOf(resolution);
  const aspectRatioIndex = profile.supported_aspect_ratios.indexOf(aspectRatio);
  if (resolutionIndex < 0 || aspectRatioIndex < 0) return null;

  // OpenRouter returns sizes in resolution-major, aspect-ratio-minor order.
  const size =
    profile.supported_sizes[
      resolutionIndex * profile.supported_aspect_ratios.length +
        aspectRatioIndex
    ];
  const match = size?.trim().toLowerCase().match(/^(\d+)x(\d+)$/);
  if (!match) return null;
  const width = Number(match[1]);
  const height = Number(match[2]);
  return width > 0 && height > 0 ? { width, height } : null;
}

export function estimateVideoCost(
  profile: VideoPricingProfile,
  {
    duration,
    resolution,
    aspectRatio,
    generateAudio,
    imageInputCount,
  }: VideoCostSelection,
) {
  if (duration === null || duration <= 0 || !resolution) return null;
  const resolutionKey = resolution.toLowerCase();
  const mode = imageInputCount > 0 ? "image_to_video" : "text_to_video";
  const dollarKeys = [
    generateAudio
      ? `duration_seconds_with_audio_${resolutionKey}`
      : `duration_seconds_without_audio_${resolutionKey}`,
    `${mode}_duration_seconds_${resolutionKey}`,
    `duration_seconds_${resolutionKey}`,
    generateAudio
      ? "duration_seconds_with_audio"
      : "duration_seconds_without_audio",
    `${mode}_duration_seconds`,
    "duration_seconds",
  ];
  let perSecond: number | null = null;
  for (const key of dollarKeys) {
    const value = pricingNumber(profile, key);
    if (value !== null) {
      perSecond = value;
      break;
    }
  }
  if (perSecond === null) {
    const cents =
      pricingNumber(
        profile,
        `cents_per_video_output_second_${resolutionKey}`,
      ) ??
      pricingNumber(profile, `cents_per_second_output_${resolutionKey}`) ??
      pricingNumber(profile, "cents_per_video_output_second") ??
      pricingNumber(profile, "cents_per_second_output");
    if (cents !== null) perSecond = cents / 100;
  }
  if (perSecond !== null) {
    const imageInputCents =
      imageInputCount > 0
        ? (pricingNumber(profile, "cents_per_image_input") ?? 0)
        : 0;
    return perSecond * duration + (imageInputCents * imageInputCount) / 100;
  }

  const dimensions = selectedDimensions(profile, resolution, aspectRatio);
  const videoTokenRate =
    (generateAudio
      ? pricingNumber(profile, "video_tokens")
      : pricingNumber(profile, "video_tokens_without_audio")) ??
    pricingNumber(profile, "video_tokens");
  if (!dimensions || videoTokenRate === null) return null;

  const videoTokens =
    (dimensions.width * dimensions.height * duration * 24) / 1024;
  return videoTokens * videoTokenRate;
}

import { describe, expect, it } from "vitest";
import { CHAT_HEADER_POSITION_CLASSES } from "./ChatPage";

const BREAKPOINTS: Record<string, number> = {
  md: 768,
  lg: 1024,
};

function activePositionClasses(viewportWidth: number) {
  return CHAT_HEADER_POSITION_CLASSES.split(/\s+/).flatMap((token) => {
    const [prefix, className] = token.includes(":")
      ? token.split(":", 2)
      : [null, token];
    if (!prefix) return [className];
    return viewportWidth >= BREAKPOINTS[prefix] ? [className] : [];
  });
}

describe("ChatPage responsive interview header", () => {
  it("keeps the 390px chat input clear while preserving desktop sticky behavior", () => {
    expect(activePositionClasses(390)).not.toContain("sticky");
    expect(activePositionClasses(390)).not.toContain("top-4");

    expect(activePositionClasses(768)).toEqual(
      expect.arrayContaining(["sticky", "top-4"]),
    );
    expect(activePositionClasses(1024)).toEqual(
      expect.arrayContaining(["sticky", "top-4", "top-24"]),
    );
  });
});

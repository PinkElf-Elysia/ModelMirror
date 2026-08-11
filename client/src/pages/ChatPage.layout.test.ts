import { describe, expect, it } from "vitest";
import {
  CHAT_COMPOSER_COLUMN_CLASSES,
  CHAT_MESSAGE_COLUMN_CLASSES,
  CHAT_SHELL_HEADER_CLASSES,
} from "./ChatPage";

const BREAKPOINTS: Record<string, number> = {
  md: 768,
  lg: 1024,
};

function activePositionClasses(classNames: string, viewportWidth: number) {
  return classNames.split(/\s+/).flatMap((token) => {
    const [prefix, className] = token.includes(":")
      ? token.split(":", 2)
      : [null, token];
    if (!prefix) return [className];
    return viewportWidth >= BREAKPOINTS[prefix] ? [className] : [];
  });
}

describe("ChatPage conversation-first shell", () => {
  it("keeps one compact header and bounded message/composer columns at every viewport", () => {
    expect(activePositionClasses(CHAT_SHELL_HEADER_CLASSES, 390)).toEqual(
      expect.arrayContaining(["sticky", "top-0", "h-16"]),
    );
    expect(CHAT_MESSAGE_COLUMN_CLASSES).toContain("max-w-[920px]");
    expect(CHAT_COMPOSER_COLUMN_CLASSES).toContain("max-w-[1000px]");
    expect(CHAT_SHELL_HEADER_CLASSES).not.toContain("top-24");
  });
});

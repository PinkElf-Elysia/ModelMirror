import "@testing-library/jest-dom/vitest";

Object.defineProperty(document, "visibilityState", {
  configurable: true,
  value: "visible",
});

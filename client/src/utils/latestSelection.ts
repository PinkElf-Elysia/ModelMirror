/** Invalidates asynchronous results when their selection is superseded. */
export function createLatestSelection() {
  let version = 0;
  return {
    invalidate() { version += 1; },
    begin() {
      const ticket = ++version;
      return () => ticket === version;
    },
  };
}

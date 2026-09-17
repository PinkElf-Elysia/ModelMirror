import { useEffect, useState } from "react";

type PanelUrls = { scienceConsoleUrl: string; matrixOasisConsoleUrl: string };
const defaults: PanelUrls = {
  scienceConsoleUrl: "http://127.0.0.1:8900/",
  matrixOasisConsoleUrl: "http://127.0.0.1:43110/",
};

export function resolvePanelUrl(value: unknown, fallback: string): string {
  if (typeof value !== "string" || !value.trim()) return fallback;
  try {
    const url = new URL(value);
    if (!["http:", "https:"].includes(url.protocol) || url.username || url.password || url.search || url.hash) return fallback;
    return url.toString();
  } catch { return fallback; }
}

export function useStudioPanelUrls() {
  const [urls, setUrls] = useState(defaults);
  useEffect(() => {
    const controller = new AbortController();
    void fetch("/runtime-config.json", { cache: "no-store", signal: controller.signal })
      .then(async (response) => response.ok ? await response.json() as Partial<PanelUrls> : null)
      .then((config) => {
        if (controller.signal.aborted || !config) return;
        setUrls({ scienceConsoleUrl: resolvePanelUrl(config.scienceConsoleUrl, defaults.scienceConsoleUrl),
          matrixOasisConsoleUrl: resolvePanelUrl(config.matrixOasisConsoleUrl, defaults.matrixOasisConsoleUrl) });
      }).catch(() => { /* Local previews retain documented loopback defaults. */ });
    return () => controller.abort();
  }, []);

  return urls;
}

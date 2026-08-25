import { useCallback, useEffect, useRef, useState } from "react";

interface PollingState<T> {
  data: T | null;
  error: Error | null;
  loading: boolean;
  refreshing: boolean;
  refresh: () => void;
}

export function usePolling<T>(
  loader: (signal: AbortSignal) => Promise<T>,
  intervalMs: number,
  enabled = true,
  shouldContinue: (value: T) => boolean = () => true,
): PollingState<T> {
  const loaderRef = useRef(loader);
  const shouldContinueRef = useRef(shouldContinue);
  const [data, setData] = useState<T | null>(null);
  const [error, setError] = useState<Error | null>(null);
  const [loading, setLoading] = useState(enabled);
  const [refreshing, setRefreshing] = useState(false);
  const [revision, setRevision] = useState(0);

  useEffect(() => {
    loaderRef.current = loader;
    shouldContinueRef.current = shouldContinue;
  }, [loader, shouldContinue]);

  const refresh = useCallback(() => setRevision((value) => value + 1), []);

  useEffect(() => {
    if (!enabled) {
      setLoading(false);
      setRefreshing(false);
      return;
    }

    let disposed = false;
    let timer: number | undefined;
    let failureCount = 0;
    let controller: AbortController | null = null;

    const schedule = (delay: number) => {
      window.clearTimeout(timer);
      timer = window.setTimeout(run, delay);
    };

    const run = async () => {
      if (disposed || document.visibilityState === "hidden") return;
      controller?.abort();
      controller = new AbortController();
      setRefreshing(true);
      try {
        const value = await loaderRef.current(controller.signal);
        if (disposed) return;
        failureCount = 0;
        setData(value);
        setError(null);
        setLoading(false);
        setRefreshing(false);
        if (shouldContinueRef.current(value)) schedule(intervalMs);
      } catch (caught) {
        if (disposed || controller.signal.aborted) return;
        failureCount += 1;
        setError(caught instanceof Error ? caught : new Error("未知请求错误"));
        setLoading(false);
        setRefreshing(false);
        schedule(Math.min(intervalMs * 2 ** failureCount, 30_000));
      }
    };

    const onVisibilityChange = () => {
      if (document.visibilityState === "hidden") {
        window.clearTimeout(timer);
        controller?.abort();
      } else {
        schedule(0);
      }
    };

    document.addEventListener("visibilitychange", onVisibilityChange);
    schedule(0);
    return () => {
      disposed = true;
      window.clearTimeout(timer);
      controller?.abort();
      document.removeEventListener("visibilitychange", onVisibilityChange);
    };
  }, [enabled, intervalMs, revision]);

  return { data, error, loading, refreshing, refresh };
}

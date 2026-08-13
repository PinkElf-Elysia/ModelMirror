import { useCallback, useEffect, useState } from "react";

import type { AgencyAssets } from "./AgencyExpertTeamTypes";

const emptyAssets: AgencyAssets = {
  teams: [],
  templates: [],
  garden: [],
  method_skills: [],
  upstream_project: "jnMetaCode/agency-orchestrator",
  upstream_revision: "",
};

async function readJson<T>(response: Response): Promise<T> {
  const payload = (await response.json()) as T & { error?: string };
  if (!response.ok) {
    throw new Error(payload.error || `请求失败（${response.status}）`);
  }
  return payload;
}

export function useAgencyAssets() {
  const [assets, setAssets] = useState<AgencyAssets>(emptyAssets);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);

  const refresh = useCallback(async () => {
    try {
      const response = await fetch("/api/expert-team/assets");
      setAssets(await readJson<AgencyAssets>(response));
      setError("");
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "无法读取专家团资产。");
    }
  }, []);

  useEffect(() => {
    void refresh();
  }, [refresh]);

  const saveTeam = useCallback(
    async (payload: { name: string; description: string; agent_ids: string[] }) => {
      setBusy(true);
      try {
        const response = await fetch("/api/expert-team/teams", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify(payload),
        });
        await readJson(response);
        await refresh();
        setError("");
      } catch (reason) {
        const message = reason instanceof Error ? reason.message : "保存固定阵容失败。";
        setError(message);
        throw reason;
      } finally {
        setBusy(false);
      }
    },
    [refresh],
  );

  const saveTemplate = useCallback(
    async (payload: { name: string; content: string; note: string }) => {
      setBusy(true);
      try {
        const response = await fetch("/api/expert-team/templates", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify(payload),
        });
        await readJson(response);
        await refresh();
        setError("");
      } catch (reason) {
        const message = reason instanceof Error ? reason.message : "保存任务模板失败。";
        setError(message);
        throw reason;
      } finally {
        setBusy(false);
      }
    },
    [refresh],
  );

  return { assets, busy, error, refresh, saveTeam, saveTemplate };
}

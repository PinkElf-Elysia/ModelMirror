import { useCallback, useEffect, useState } from "react";
import {
  readSkillCreatorStatus,
  SkillCreatorApiError,
  type SkillCreatorStatus,
} from "../utils/skillCreatorApi";

interface SkillCreatorStatusState {
  status: SkillCreatorStatus | null;
  loading: boolean;
  error: string;
  reload: () => Promise<void>;
}

export function useSkillCreatorStatus(): SkillCreatorStatusState {
  const [status, setStatus] = useState<SkillCreatorStatus | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");

  const reload = useCallback(async () => {
    setLoading(true);
    setError("");
    try {
      setStatus(await readSkillCreatorStatus());
    } catch (caught) {
      setStatus(null);
      setError(
        caught instanceof SkillCreatorApiError
          ? caught.message
          : "Skill Creator 状态暂时不可用。",
      );
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void reload();
  }, [reload]);

  return { status, loading, error, reload };
}

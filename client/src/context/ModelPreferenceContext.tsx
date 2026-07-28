import {
  createContext,
  useCallback,
  useContext,
  useMemo,
  useState,
  type ReactNode,
} from "react";
import {
  chatModelOptions,
  DEFAULT_CHAT_MODEL_ID,
} from "../data/modelOptions";

export { DEFAULT_CHAT_MODEL_ID };

const STORAGE_KEY = "modelmirror-preferred-model-id";

interface ModelPreferenceState {
  preferredModelId: string;
  hasPreferredModel: boolean;
}

interface ModelPreferenceContextValue extends ModelPreferenceState {
  setPreferredModelId: (modelId: string) => void;
}

const ModelPreferenceContext =
  createContext<ModelPreferenceContextValue | null>(null);

function readInitialState(): ModelPreferenceState {
  if (typeof window === "undefined") {
    return {
      preferredModelId: DEFAULT_CHAT_MODEL_ID,
      hasPreferredModel: false,
    };
  }

  const storedModelId = window.localStorage.getItem(STORAGE_KEY);
  const hasValidStoredModel = Boolean(
    storedModelId &&
      chatModelOptions.some((model) => model.id === storedModelId),
  );

  return {
    preferredModelId: hasValidStoredModel
      ? storedModelId!
      : DEFAULT_CHAT_MODEL_ID,
    hasPreferredModel: hasValidStoredModel,
  };
}

export function ModelPreferenceProvider({ children }: { children: ReactNode }) {
  const [state, setState] = useState<ModelPreferenceState>(readInitialState);
  const setPreferredModelId = useCallback((modelId: string) => {
    if (typeof window !== "undefined") {
      window.localStorage.setItem(STORAGE_KEY, modelId);
    }

    setState({
      preferredModelId: modelId,
      hasPreferredModel: true,
    });
  }, []);

  const value = useMemo<ModelPreferenceContextValue>(
    () => ({
      ...state,
      setPreferredModelId,
    }),
    [setPreferredModelId, state],
  );

  return (
    <ModelPreferenceContext.Provider value={value}>
      {children}
    </ModelPreferenceContext.Provider>
  );
}

export function useModelPreference() {
  const context = useContext(ModelPreferenceContext);

  if (!context) {
    throw new Error(
      "useModelPreference must be used within ModelPreferenceProvider",
    );
  }

  return context;
}

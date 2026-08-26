import App from "./App";
import { ModelPreferenceProvider } from "./context/ModelPreferenceContext";

export default function MainApplication() {
  return (
    <ModelPreferenceProvider>
      <App />
    </ModelPreferenceProvider>
  );
}

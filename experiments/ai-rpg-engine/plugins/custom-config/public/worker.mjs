import { runJob } from "./engine.mjs";
onmessage = (e) => {
  try {
    postMessage({ value: runJob(e.data) });
  } catch (error) {
    postMessage({ error: error.message });
  }
};

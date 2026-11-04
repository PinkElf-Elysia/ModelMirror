import {
  Worker,
  isMainThread,
  workerData,
  parentPort,
} from "node:worker_threads";
import { runJob } from "./public/engine.mjs";
if (!isMainThread && workerData?.rpgConfig) {
  try {
    parentPort.postMessage({ value: runJob(workerData.rpgConfig) });
  } catch (e) {
    parentPort.postMessage({ error: e.message });
  }
}
let active = 0;
export async function project(job) {
  if (!job.configs?.length)
    return job.kind === "display" ? job.source : job.messages;
  if (active >= 4) throw Error("配置处理繁忙");
  active++;
  let w, timer;
  try {
    return await new Promise((ok, no) => {
      w = new Worker(new URL(import.meta.url), {
        workerData: { rpgConfig: job },
        env: {},
        execArgv: [],
        resourceLimits: { maxOldGenerationSizeMb: 72, stackSizeMb: 4 },
      });
      timer = setTimeout(
        () => no(Error("配置执行超时，请检查正则表达式")),
        1400,
      );
      w.once("message", (r) => (r.error ? no(Error(r.error)) : ok(r.value)));
      w.once("error", no);
      w.once("exit", () => no(Error("配置执行中断")));
    });
  } finally {
    clearTimeout(timer);
    try {
      await w?.terminate();
    } finally {
      active--;
    }
  }
}

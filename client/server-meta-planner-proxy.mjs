import { randomUUID } from "node:crypto";
import { request as httpRequest } from "node:http";
import { request as httpsRequest } from "node:https";

import { collectProxyResponseHeaders } from "./server-headers.mjs";

// This is the whole generation window, not an individual model-call timeout.
export const META_PLANNER_GENERATION_TIMEOUT_MS = 960_000;
const GENERATION_PATH = "/api/meta-agent/generate-xpert-candidate";
const SAFE_CAUSES = new Set([
  "ECONNREFUSED", "ECONNRESET", "ETIMEDOUT", "ENOTFOUND", "EAI_AGAIN",
  "ERR_STREAM_PREMATURE_CLOSE", "ERR_TLS_CERT_ALTNAME_INVALID",
  "CERT_HAS_EXPIRED", "DEPTH_ZERO_SELF_SIGNED_CERT",
]);

export function isMetaPlannerGenerationRequest(req) {
  return req.method === "POST" && (req.url || "").split("?")[0] === GENERATION_PATH;
}

export function proxyMetaPlannerGeneration(req, res, target, headers, {
  timeoutMs = META_PLANNER_GENERATION_TIMEOUT_MS,
  onReceipt = (receipt) => console.info(JSON.stringify(receipt)),
} = {}) {
  return new Promise((resolve) => {
    const requestId = randomUUID();
    const started = performance.now();
    let upstream;
    let response;
    let settled = false;
    let timer;

    const settle = (state, cause = null) => {
      if (settled) return false;
      settled = true;
      clearTimeout(timer);
      req.off("aborted", disconnected);
      res.off("close", closed);
      res.off("finish", completed);
      const receipt = {
        event: "meta_planner_proxy", request_id: requestId, state, cause,
        elapsed_ms: Math.round(performance.now() - started),
        upstream_status: response?.statusCode ?? null,
      };
      onReceipt(receipt);
      resolve(receipt);
      return true;
    };
    const stopUpstream = () => {
      req.unpipe(upstream);
      response?.unpipe(res);
      response?.destroy();
      upstream?.destroy();
    };
    const failed = (state, cause, status) => {
      if (!settle(state, cause)) return;
      stopUpstream();
      if (res.destroyed) return;
      if (res.headersSent) {
        res.destroy();
        return;
      }
      res.writeHead(status, {
        "Content-Type": "application/json; charset=utf-8",
        "Cache-Control": "no-store",
        "X-Meta-Planner-Proxy-Id": requestId,
      });
      res.end(JSON.stringify({
        code: state === "timeout" ? "META_PLANNER_PROXY_TIMEOUT" : "META_PLANNER_PROXY_UPSTREAM_FAILED",
        error: state === "timeout"
          ? "等待元智能体生成结果超时，后端结果尚不确定。请先刷新候选列表核对，请勿直接重复生成。"
          : "元智能体生成结果连接中断，后端结果尚不确定。请先刷新候选列表核对，请勿直接重复生成。",
        request_id: requestId, cause, generation_outcome: "unknown", retryable: false,
      }));
    };
    const upstreamError = (error) => failed(
      "upstream_error", SAFE_CAUSES.has(error?.code) ? error.code : "UPSTREAM_ERROR", 502,
    );
    function disconnected() {
      if (settle("client_disconnected", "CLIENT_DISCONNECTED")) stopUpstream();
    }
    function closed() {
      if (!res.writableFinished) disconnected();
    }
    function completed() {
      settle("completed");
    }

    req.once("aborted", disconnected);
    req.once("error", disconnected);
    res.once("close", closed);
    res.once("error", disconnected);
    res.once("finish", completed);
    if (req.aborted || res.destroyed) {
      disconnected();
      return;
    }
    timer = setTimeout(() => failed("timeout", "TOTAL_DEADLINE", 504), timeoutMs);
    timer.unref();
    try {
      const send = target.protocol === "https:" ? httpsRequest : httpRequest;
      upstream = send(target, {
        method: req.method, agent: false,
        headers: { ...headers, "x-meta-planner-proxy-id": requestId },
      }, (incoming) => {
        response = incoming;
        response.once("error", upstreamError);
        response.once("aborted", () => upstreamError({ code: "ERR_STREAM_PREMATURE_CLOSE" }));
        if (settled) {
          response.destroy();
          return;
        }
        const responseHeaders = new Headers();
        for (let index = 0; index < response.rawHeaders.length; index += 2) {
          responseHeaders.append(response.rawHeaders[index], response.rawHeaders[index + 1]);
        }
        const forwarded = collectProxyResponseHeaders(responseHeaders);
        // Unlike fetch, node:http forwards compressed bytes without decompression.
        if (response.headers["content-encoding"]) {
          forwarded["content-encoding"] = response.headers["content-encoding"];
        }
        forwarded["x-meta-planner-proxy-id"] = requestId;
        res.writeHead(response.statusCode, forwarded);
        response.pipe(res);
      });
      upstream.once("error", upstreamError);
      req.pipe(upstream);
    } catch (error) {
      upstreamError(error);
    }
  });
}

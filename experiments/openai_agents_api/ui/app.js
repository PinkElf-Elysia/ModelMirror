"use strict";
const $ = (id) => document.getElementById(id);
let state = null, pending = false, disconnected = false, timer = null, renderedTurns = 0;
let lastTranscript = "", lastAnnouncement = "";
let readEpoch = 0, uncertainInput = null;

const labels = {in_progress: "正在接收文本", completed: "本轮已完成", failed: "本轮失败", cancelled: "已确认取消", unconfirmed: "终态未确认"};
const errors = {
  invalid_input: "输入应为 1 至 4,096 字的合成文本。",
  credential_in_input: "输入中含有凭据格式的内容，请移除后再发送。",
  submission_blocked: "本次发送已被阻止。请刷新状态，核对轮次与清理结果。",
  operation_conflict: "请求标识冲突，输入未重发。请刷新状态。",
  receipt_write_failed: "回执目录不可写，已阻止后续发送。请检查本次回执。",
  cleanup_unconfirmed: "会话清理尚未确认。请保留回执并核对本次会话，不要重跑创建请求。",
  deadline_exceeded: "实验的 180 秒期限已到，已停止继续输入并执行清理。",
  cancel_requested: "已停止本地接收并请求取消。远端是否停止，以清理记录中的确认结果为准。",
  stream_closed_before_terminal: "事件流提前断开，未确认本轮成功。已按已知会话执行查询和清理，输入不会重发。",
  preset_output_mismatch: "输出与冒烟预设不符，实验已停止，不会放宽文本校验。",
  saved_history_mismatch: "保存历史与接收文本不一致，实验已停止。",
  session_configuration_mismatch: "会话状态或配置不符合实验约束，实验已停止。",
  local_state_unavailable: "暂时无法读取本地状态。请求结果可能尚未确认，请先刷新状态，不要重复发送。",
  invalid_csrf: "本地服务可能已重启，请刷新页面。刷新只读取状态。",
};

function showNotice(text, canRefresh = false) {
  $("notice-text").textContent = text;
  $("notice").hidden = !text;
  $("refresh").hidden = !canRefresh;
}

function errorText(error) {
  if (error?.http_status === 401 || error?.http_status === 403) return "OpenAI 鉴权或权限检查失败，未自动重试。请查看回执。";
  if (error?.http_status === 429) return "OpenAI 返回限流或额度错误，未自动重试。请查看回执。";
  return errors[error?.category] || "本次操作未完成，请查看脱敏回执。";
}

async function request(path, body) {
  const headers = {"X-Experiment-Client": "1"};
  const options = {method: body === undefined ? "GET" : "POST", headers, cache: "no-store", signal: AbortSignal.timeout(10000)};
  if (body !== undefined) {
    headers["Content-Type"] = "application/json";
    headers["X-Experiment-CSRF"] = state?.csrf || "";
    options.body = JSON.stringify(body);
  }
  const response = await fetch(path, options);
  const value = await response.json();
  if (!response.ok) {
    const error = new Error(errorText(value.error));
    error.known = true;
    throw error;
  }
  return value;
}

function updateInput() {
  const length = Array.from($("input").value).length;
  $("character-count").textContent = `${length.toLocaleString()} / 4,096`;
  $("character-count").parentElement.classList.toggle("invalid", length > 4096);
  $("send").disabled = pending || disconnected || !state?.can_send || !$("input").value.trim() || length > 4096;
}

function setCheck(id, text, status) {
  const node = $(id);
  node.lastElementChild.textContent = text;
  node.dataset.status = status;
}

function renderTranscript() {
  const encoded = JSON.stringify(state.turns);
  if (encoded === lastTranscript) return;
  lastTranscript = encoded;
  const log = $("transcript");
  const nearBottom = log.scrollHeight - log.scrollTop - log.clientHeight < 72;
  if (!state.turns.length) return;
  $("empty")?.remove();
  for (let index = 0; index < state.turns.length; index++) {
    const row = state.turns[index];
    if (index >= renderedTurns) {
      log.append($("turn-template").content.cloneNode(true));
      renderedTurns++;
    }
    const article = log.children[index];
    article.querySelector(".message-label").textContent = `第 ${row.number} 轮 · 输入`;
    article.querySelector(".user-text").textContent = row.input;
    const status = article.querySelector(".output-status");
    status.textContent = labels[row.status] || "状态待确认";
    if (row.expected_text_check === "failed") status.textContent = "轮次完成，预设校验失败";
    status.className = `output-status ${row.expected_text_check === "failed" ? "failed" : row.status}`;
    article.querySelector(".output-text").textContent = row.output || (row.status === "in_progress" ? "等待公开文本输出…" : "未收到可显示的文本。");
  }
  if (nearBottom) log.scrollTop = log.scrollHeight;
}

function render() {
  if (!state) return;
  const count = state.turns.length, active = ["running", "cancelling", "cleaning"].includes(state.phase);
  const live = state.mode === "live", demo = state.mode === "demo";
  $("mode").textContent = live ? "真实 API · 实验调用" : demo ? "离线演示 · 不调用模型" : "只读预览 · 未启用调用";
  $("mode").classList.toggle("live", live);
  $("credentials").textContent = demo ? "演示不使用密钥" : state.credentials_configured ? "服务端已配置" : "未配置";
  $("credentials").className = live && state.credentials_configured ? "configured" : "";
  $("connection").textContent = disconnected ? "状态连接中断" : state.phase === "finished" ? "记录已保留" : active ? "状态同步中" : "已连接";
  $("rounds").textContent = `${count} / 2 轮`;
  $("cost-hint").textContent = live ? "发送后会产生真实 API 调用费用。" : demo ? "本地模拟，不产生模型调用费用。" : "只读预览，请查看使用说明。";
  let description = count ? `第 ${count} 轮${state.turns[count - 1].status === "completed" ? "已完成" : "处理中"}` : "尚未开始";
  if (state.phase === "cleaning") description = "正在清理";
  else if (state.phase === "cancelling") description = "取消已请求";
  else if (state.phase === "finished") description = state.status === "failed" ? "实验未通过" : "实验已结束";
  $("turn-status").textContent = description;
  const waiting = state.phase === "awaiting_input";
  $("input-label").textContent = state.phase === "finished" ? "本次输入已结束" : active ? `第 ${count} 轮已提交` : `第 ${waiting ? 2 : Math.min(count + 1, 2)} 轮输入`;
  $("send").textContent = state.phase === "finished" ? "本次实验已结束" : active ? description : `发送第 ${count + 1} 轮`;
  $("input").disabled = pending || disconnected || !state.can_send;
  $("preset").disabled = pending || disconnected || !state.can_send;
  $("cancel").disabled = pending || disconnected || state.phase !== "running";
  $("cleanup").disabled = pending || disconnected || !count || ["finished", "cleaning", "cancelling"].includes(state.phase);
  $("history").disabled = !count;
  $("download").disabled = disconnected || !count || pending;
  $("cleanup").textContent = state.phase === "cleaning" ? "正在清理…" : "结束并清理";
  $("deadline").textContent = state.phase === "finished" ? "本次实验已结束。重启前请核对清理回执。" : state.remaining_seconds === null ? "从首轮发送开始计时，包含等待输入的时间。" : `调用期限剩余 ${state.remaining_seconds} 秒，包含等待输入的时间。`;
  for (let i = 0; i < 2; i++) {
    const row = state.turns[i], prefix = i === 0 ? "首轮" : "第二轮";
    const ok = row?.status === "completed" && row.expected_text_check !== "failed";
    const running = row?.status === "in_progress";
    setCheck(i === 0 ? "first-check" : "second-check", !row ? `${prefix}待执行` : ok ? `${prefix}完成` : `${prefix}${running ? "执行中" : "未通过"}`, !row ? "pending" : ok ? "passed" : running ? "running" : "failed");
  }
  const cleanup = state.cleanup, clean = cleanup.status === "passed";
  setCheck("cleanup-check", clean ? (demo ? "模拟清理完成" : "API 删除已确认") : state.phase === "cleaning" ? "正在确认会话清理" : cleanup.status === "failed" ? "清理未确认，请查回执" : cleanup.status === "not_applicable" ? (cleanup.remote_state === "unknown" ? "会话 ID 未知" : "未创建远端会话") : "会话清理待执行", clean ? "passed" : state.phase === "cleaning" ? "running" : cleanup.status === "failed" || cleanup.remote_state === "unknown" ? "failed" : "pending");
  $("cleanup-note").hidden = cleanup.status === "not_run";
  $("cleanup-note").textContent = demo ? "离线模拟仅验证界面流程，不证明真实取消或删除。" : "API 删除确认不等于物理清理完成。物理清理仍未验证。";
  const announcement = state.phase === "finished" ? "本次实验已结束，可查看历史并导出回执。" : state.phase === "cleaning" ? "正在处理本次会话，清理最多 30 秒。" : state.phase === "cancelling" ? "取消已请求，正在确认远端状态。" : waiting ? "首轮完成，可继续输入。第二轮结束后自动清理。" : state.phase === "running" ? "正在接收公开文本，轮次终态尚未确认。" : "两轮结束后自动清理；也可提前结束。";
  if (lastAnnouncement !== announcement) { $("composer-status").textContent = announcement; lastAnnouncement = announcement; }
  if (!disconnected) {
    const warning = state.receipt_error ? errors.receipt_write_failed : state.error ? errorText(state.error) : "";
    showNotice(warning);
  }
  renderTranscript();
  updateInput();
}

async function refreshState() {
  clearTimeout(timer);
  const epoch = ++readEpoch;
  try {
    const next = await request("/api/state");
    if (epoch !== readEpoch) return; // Discard reads that predate a local command.
    if (state && next.run_id !== state.run_id) {
      disconnected = true;
      showNotice("本地服务已更换实验。请先核对原回执，再重新打开页面；输入未重发。", false);
      render();
      return;
    }
    if (uncertainInput && next.turns.some(row => row.operation_id === uncertainInput.operation_id)) {
      if ($("input").value === uncertainInput.text) $("input").value = "";
      uncertainInput = null; // A lost acknowledgment is reconciled by reading, never resending.
    }
    state = next;
    disconnected = false;
    render();
    if (state.phase !== "finished") timer = setTimeout(refreshState, ["running", "cleaning", "cancelling"].includes(state.phase) ? 350 : 1000);
  } catch (error) {
    if (epoch !== readEpoch) return;
    disconnected = true;
    render();
    showNotice(error.known ? error.message : "无法读取本地状态。没有重新提交输入；请刷新状态后核对本次回执。", true);
  }
}

async function command(path, body) {
  if (pending || disconnected) return;
  pending = true;
  readEpoch++;
  if (path === "/api/input") uncertainInput = body;
  clearTimeout(timer);
  render();
  try {
    const csrf = state.csrf;
    const next = await request(path, body);
    state = {...next, csrf};
    if (path === "/api/input") { $("input").value = ""; uncertainInput = null; }
    pending = false;
    render();
    await refreshState();
  } catch (error) {
    pending = false;
    disconnected = true; // Even a lost acknowledgment cannot trigger an automatic retry.
    render();
    showNotice(error.known ? error.message : "请求结果尚未确认，输入不会重发。请先刷新状态。", true);
  }
}

$("composer").addEventListener("submit", (event) => {
  event.preventDefault();
  if ($("send").disabled) return;
  command("/api/input", {text: $("input").value, next_turn: state.turns.length + 1, operation_id: crypto.randomUUID()});
});
$("input").addEventListener("input", updateInput);
$("preset").addEventListener("click", () => { $("input").value = state.presets[state.turns.length]; updateInput(); $("input").focus(); });
$("cancel").addEventListener("click", () => command("/api/cancel", {}));
$("cleanup").addEventListener("click", () => command("/api/cleanup", {}));
$("refresh").addEventListener("click", refreshState);
$("help").addEventListener("click", () => $("help-dialog").showModal());
document.querySelectorAll("[data-close]").forEach((button) => button.addEventListener("click", () => button.closest("dialog").close()));

$("history").addEventListener("click", () => {
  $("history-context").textContent = `${state.mode === "demo" ? "离线模拟历史" : "本次会话历史"} · 保存历史${state.history_status === "passed" ? "已核对" : "未验证"}`;
  const container = $("history-content");
  container.replaceChildren();
  const metadata = document.createElement("p");
  metadata.className = "history-meta";
  metadata.textContent = `会话：${state.session_id || "ID 未知"} · 基线：${state.base_sha?.slice(0, 12) || "未记录"}`;
  container.append(metadata);
  for (const row of state.turns) {
    const entry = document.createElement("section"); entry.className = "history-entry";
    const heading = document.createElement("h3"); heading.textContent = `第 ${row.number} 轮 · ${labels[row.status] || "未确认"}`;
    const input = document.createElement("p"); input.textContent = row.input;
    const output = document.createElement("pre"); output.textContent = row.output || "无公开文本输出";
    const detail = document.createElement("p"); detail.className = "muted";
    detail.textContent = `轮次：${row.turn_id || "未知"} · ${row.usage ? `服务端 usage：${JSON.stringify(row.usage)}` : "usage 未返回，不作估算"}`;
    entry.append(heading, input, output, detail);
    const saved = state.history.find((item) => item.turn_id === row.turn_id);
    const recovered = state.recovered_outputs?.find((item) => item.turn_id === row.turn_id);
    if (saved || recovered) {
      const label = document.createElement("p"); label.className = "muted";
      label.textContent = saved ? "服务端保存的公开输出（已核对）" : "异常后恢复的已保存输出（不代表轮次验收通过）";
      const text = document.createElement("pre"); text.textContent = (saved || recovered).text;
      entry.append(label, text);
    }
    container.append(entry);
  }
  $("history-dialog").showModal();
});

$("download").addEventListener("click", async () => {
  try {
    const receipt = await request("/api/receipt");
    const anchor = document.createElement("a"); anchor.href = "/api/receipt";
    anchor.download = `agents-api-${receipt.evidence}-${receipt.run_id}.json`;
    document.body.append(anchor);
    anchor.click();
    anchor.remove();
  } catch (error) { showNotice(error.known ? error.message : "无法导出回执。请刷新状态后检查本地服务。", true); }
});
refreshState();

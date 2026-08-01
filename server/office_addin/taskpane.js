/* global Office, Word, Excel, PowerPoint */
"use strict";

const STORAGE_KEY = "modelmirror.office.host.v1";
const RECEIPT_PREFIX = "modelmirror.office.receipt.";
const state = {
  officeApp: "",
  requirementSets: [],
  capabilities: [],
  schemaHashes: {},
  hostId: "",
  hostToken: "",
  binding: {},
  socket: null,
  reconnectTimer: null,
  operationCount: 0,
};

const byId = (id) => document.getElementById(id);
const bounded = (value, limit) => String(value ?? "").slice(0, limit);
const wsUrl = () => `${location.protocol === "https:" ? "wss:" : "ws:"}//${location.host}/api/runtime/client-tools/connect`;

function readLocalState() {
  try {
    const stored = JSON.parse(localStorage.getItem(STORAGE_KEY) || "{}");
    state.hostId = bounded(stored.hostId, 200);
    state.hostToken = bounded(stored.hostToken, 500);
    state.binding = stored.binding && typeof stored.binding === "object" ? stored.binding : {};
  } catch {
    state.hostId = "";
    state.hostToken = "";
    state.binding = {};
  }
}

function saveLocalState() {
  localStorage.setItem(STORAGE_KEY, JSON.stringify({
    hostId: state.hostId,
    hostToken: state.hostToken,
    binding: state.binding,
  }));
}

function setStatus() {
  const connected = state.socket && state.socket.readyState === WebSocket.OPEN;
  byId("connection-status").textContent = connected ? "已连接" : "离线";
  byId("binding-status").textContent = state.binding.bound
    ? bounded(state.binding.title || "已绑定", 36)
    : "未绑定";
  byId("host-summary").textContent = state.officeApp
    ? `${state.officeApp[0].toUpperCase()}${state.officeApp.slice(1)} · ${state.requirementSets.join(" · ") || "Office.js"}`
    : "当前 Office 宿主不受支持";
  byId("pairing-panel").hidden = Boolean(state.hostId && state.hostToken);
  byId("bind-button").disabled = !state.officeApp || !connected;
  byId("unbind-button").disabled = !state.binding.bound;
}

function addOperation(tool, status) {
  state.operationCount += 1;
  byId("request-count").textContent = String(state.operationCount);
  const item = document.createElement("li");
  const title = document.createElement("strong");
  const detail = document.createElement("span");
  title.textContent = bounded(tool, 80);
  detail.textContent = `${status} · ${new Date().toLocaleTimeString()}`;
  item.append(title, detail);
  byId("operation-list").prepend(item);
  while (byId("operation-list").children.length > 8) {
    byId("operation-list").lastElementChild.remove();
  }
}

function currentCapabilities() {
  const prefix = `office_${state.officeApp}_`;
  return state.capabilities.filter((item) => {
    if (!item.name.startsWith(prefix)) return false;
    const requirements = Array.isArray(item.requirements) ? item.requirements : [];
    return requirements.every((requirement) =>
      Office.context.requirements.isSetSupported(requirement.set, requirement.version)
    );
  });
}

function handshakeBase() {
  const capabilities = currentCapabilities();
  return {
    version: "1.0.0",
    host_type: "office",
    office_app: state.officeApp,
    document_binding: state.binding,
    requirement_sets: state.requirementSets,
    capabilities: capabilities.map((item) => ({
      name: item.name,
      description: bounded(item.description, 300),
      mutating: Boolean(item.mutating),
      schema_hash: item.schema_hash,
    })),
    schema_hashes: Object.fromEntries(capabilities.map((item) => [item.name, item.schema_hash])),
  };
}

function send(payload) {
  if (!state.socket || state.socket.readyState !== WebSocket.OPEN) return false;
  state.socket.send(JSON.stringify(payload));
  return true;
}

function connect(firstFrame) {
  if (state.socket) state.socket.close();
  clearTimeout(state.reconnectTimer);
  const socket = new WebSocket(wsUrl());
  state.socket = socket;
  socket.addEventListener("open", () => socket.send(JSON.stringify({ ...handshakeBase(), ...firstFrame })));
  socket.addEventListener("message", (event) => void receiveMessage(event));
  socket.addEventListener("close", () => {
    setStatus();
    if (state.hostId && state.hostToken) {
      state.reconnectTimer = setTimeout(() => authenticate(), 3000);
    }
  });
  socket.addEventListener("error", setStatus);
}

function authenticate() {
  if (!state.hostId || !state.hostToken || !state.officeApp) return;
  connect({ type: "authenticate", host_id: state.hostId, host_token: state.hostToken });
}

async function receiveMessage(event) {
  let message;
  try { message = JSON.parse(event.data); } catch { return; }
  if (message.type === "welcome") {
    state.hostId = bounded(message.host_id, 200);
    if (message.host_token) state.hostToken = bounded(message.host_token, 500);
    saveLocalState();
    setStatus();
    sendHostState();
    return;
  }
  if (message.type === "heartbeat") return;
  if (message.type === "tool_request") await handleToolRequest(message);
}

function sendHostState() {
  send({
    type: "host_state",
    office_app: state.officeApp,
    document_binding: state.binding,
    requirement_sets: state.requirementSets,
  });
}

function receiptKey(operationId) { return `${RECEIPT_PREFIX}${operationId}`; }
function getReceipt(operationId) {
  try { return JSON.parse(localStorage.getItem(receiptKey(operationId)) || "null"); } catch { return null; }
}
function setReceipt(operationId, value) { localStorage.setItem(receiptKey(operationId), JSON.stringify(value)); }

async function handleToolRequest(request) {
  if (!state.binding.bound) {
    return sendToolError(request, "The current Office document is not bound.");
  }
  if (!String(request.tool_name || "").startsWith(`office_${state.officeApp}_`)) {
    return sendToolError(request, "Tool does not match the active Office host.");
  }
  const receipt = getReceipt(request.operation_id);
  if (receipt?.status === "completed") {
    return send({
      type: "tool_result",
      request_id: request.request_id,
      operation_id: request.operation_id,
      tool_call_id: request.tool_call_id,
      result: receipt.result,
      metadata: receipt.metadata || {},
    });
  }
  const capability = state.capabilities.find((item) => item.name === request.tool_name);
  if (!capability) return sendToolError(request, "Office tool is not supported by this host.");
  send({ type: "tool_accepted", request_id: request.request_id });
  if (capability.mutating) setReceipt(request.operation_id, { status: "running", started_at: Date.now() });
  addOperation(request.tool_name, "执行中");
  try {
    const resultObject = await executeOfficeTool(request.tool_name, request.arguments || {}, request);
    const result = bounded(JSON.stringify(resultObject), 64000);
    const metadata = {
      office_app: state.officeApp,
      document_binding_id: state.binding.binding_id,
      operation_receipt: request.operation_id,
      result_length: result.length,
    };
    setReceipt(request.operation_id, { status: "completed", result, metadata, completed_at: Date.now() });
    send({
      type: "tool_result",
      request_id: request.request_id,
      operation_id: request.operation_id,
      tool_call_id: request.tool_call_id,
      result,
      metadata,
    });
    addOperation(request.tool_name, "完成");
  } catch (error) {
    const message = bounded(error instanceof Error ? error.message : String(error), 1000);
    sendToolError(request, message);
    addOperation(request.tool_name, "失败");
  }
}

function sendToolError(request, error) {
  send({
    type: "tool_error",
    request_id: request.request_id,
    operation_id: request.operation_id,
    tool_call_id: request.tool_call_id,
    error: bounded(error, 1000),
  });
}

function requireConfirm(args) {
  if (args.confirm !== true) throw new Error("Delete operation requires confirm=true.");
}

function normalizedLocation(value) { return value === "Start" ? "Start" : "End"; }
function shapeOptions(args) {
  const result = {};
  for (const key of ["left", "top", "width", "height"]) {
    if (Number.isFinite(Number(args[key]))) result[key] = Number(args[key]);
  }
  return result;
}

async function executeOfficeTool(name, args, request) {
  if (name.startsWith("office_word_")) return executeWord(name, args);
  if (name.startsWith("office_excel_")) return executeExcel(name, args);
  if (name.startsWith("office_powerpoint_")) return executePowerPoint(name, args, request);
  throw new Error("Unsupported Office tool.");
}

async function executeWord(name, args) {
  return Word.run(async (context) => {
    const body = context.document.body;
    if (name === "office_word_snapshot") {
      const selection = context.document.getSelection();
      body.load("text"); selection.load("text");
      await context.sync();
      const max = Math.min(20000, Math.max(1, Number(args.maxCharacters) || 12000));
      return { app: "word", body_text: bounded(body.text, max), selected_text: bounded(selection.text, 4000), truncated: body.text.length > max };
    }
    if (name === "office_word_insert_text") {
      body.insertText(bounded(args.text, 20000), normalizedLocation(args.location));
    } else if (name === "office_word_replace_selection") {
      context.document.getSelection().insertText(bounded(args.text, 20000), "Replace");
    } else if (name === "office_word_insert_heading") {
      const paragraph = body.insertParagraph(bounded(args.text, 2000), normalizedLocation(args.location));
      paragraph.styleBuiltIn = `Heading ${Math.min(9, Math.max(1, Number(args.level) || 1))}`;
    } else if (name === "office_word_insert_table") {
      const values = Array.isArray(args.values) ? args.values.slice(0, 200).map((row) => Array.isArray(row) ? row.slice(0, 50) : []) : [];
      const rows = values.length || Math.min(200, Math.max(1, Number(args.rowCount) || 1));
      const columns = Math.max(values.reduce((max, row) => Math.max(max, row.length), 0), Math.min(50, Math.max(1, Number(args.columnCount) || 1)));
      const padded = values.length ? values.map((row) => [...row, ...Array(Math.max(0, columns - row.length)).fill("")]) : undefined;
      body.insertTable(rows, columns, normalizedLocation(args.location), padded);
    } else if (name === "office_word_search_text") {
      const results = body.search(bounded(args.query, 1000), { matchCase: Boolean(args.matchCase), matchWholeWord: Boolean(args.matchWholeWord) });
      results.load("items/text"); await context.sync();
      return { app: "word", matches: results.items.slice(0, Math.min(100, Number(args.maxResults) || 20)).map((item) => bounded(item.text, 500)), count: results.items.length };
    } else throw new Error("Unsupported Word tool.");
    await context.sync();
    return { app: "word", status: "completed", operation_receipt: crypto.randomUUID() };
  });
}

function excelWorksheet(context, name) {
  return name ? context.workbook.worksheets.getItem(bounded(name, 100)) : context.workbook.worksheets.getActiveWorksheet();
}

async function executeExcel(name, args) {
  return Excel.run(async (context) => {
    const workbook = context.workbook;
    if (name === "office_excel_snapshot") {
      const sheets = workbook.worksheets; const active = sheets.getActiveWorksheet();
      sheets.load("items/name"); active.load("name");
      const used = active.getUsedRangeOrNullObject(); used.load("isNullObject,rowCount,columnCount,values,text,address");
      await context.sync();
      const maxRows = Math.min(200, Math.max(1, Number(args.maxRows) || 40));
      const maxColumns = Math.min(100, Math.max(1, Number(args.maxColumns) || 20));
      return { app: "excel", worksheets: sheets.items.map((item) => item.name), active_worksheet: active.name, used_range: used.isNullObject ? null : { address: used.address, row_count: used.rowCount, column_count: used.columnCount, values: used.values.slice(0, maxRows).map((row) => row.slice(0, maxColumns)) } };
    }
    const sheet = excelWorksheet(context, args.worksheetName);
    if (name === "office_excel_get_range") {
      const range = sheet.getRange(bounded(args.address, 100)); range.load("address,rowCount,columnCount,values,text"); await context.sync();
      if (range.rowCount > 1000 || range.columnCount > 200) throw new Error("Excel range exceeds safety limits.");
      return { app: "excel", address: range.address, values: range.values, text: range.text };
    }
    if (name === "office_excel_set_range_values") {
      const values = Array.isArray(args.values) ? args.values : [];
      if (!values.length || values.length > 1000 || values.some((row) => !Array.isArray(row) || row.length > 200)) throw new Error("Excel values exceed safety limits.");
      const range = sheet.getRange(bounded(args.address, 100)); range.load("rowCount,columnCount"); await context.sync();
      if (range.rowCount !== values.length || values.some((row) => row.length !== range.columnCount)) throw new Error("Excel value matrix must match the target range dimensions.");
      range.values = values;
    } else if (name === "office_excel_add_worksheet") {
      workbook.worksheets.add(args.name ? bounded(args.name, 100) : undefined);
    } else if (name === "office_excel_delete_worksheet") {
      requireConfirm(args); workbook.worksheets.getItem(bounded(args.name, 100)).delete();
    } else if (name === "office_excel_autofit_range") {
      const range = args.address ? sheet.getRange(bounded(args.address, 100)) : sheet.getUsedRange(); range.format.autofitColumns(); range.format.autofitRows();
    } else if (name === "office_excel_add_table") {
      const table = sheet.tables.add(bounded(args.address, 100), args.hasHeaders !== false); if (args.name) table.name = bounded(args.name, 100);
    } else throw new Error("Unsupported Excel tool.");
    await context.sync();
    return { app: "excel", status: "completed", operation_receipt: crypto.randomUUID() };
  });
}

async function getPowerPointSlide(context, oneBased) {
  const slides = context.presentation.slides; slides.load("items/id"); await context.sync();
  const index = Math.min(slides.items.length, Math.max(1, Number(oneBased) || 1)) - 1;
  if (!slides.items[index]) throw new Error("PowerPoint slide was not found.");
  return slides.items[index];
}

async function getPowerPointShape(context, slide, args) {
  const shapes = slide.shapes; shapes.load("items/id,name,type,left,top,width,height"); await context.sync();
  const shape = shapes.items.find((item) => (args.shapeId && item.id === args.shapeId) || (args.shapeName && item.name === args.shapeName));
  if (!shape) throw new Error("PowerPoint shape was not found.");
  return shape;
}

async function artifactBase64(request, artifactId) {
  const response = await fetch(`/api/runtime/client-tool-requests/${encodeURIComponent(request.request_id)}/input-artifacts/${encodeURIComponent(artifactId)}`, {
    headers: { Authorization: `Bearer ${state.hostToken}`, "X-ModelMirror-Client-Host-Id": state.hostId },
  });
  if (!response.ok) throw new Error(`Image artifact download failed (${response.status}).`);
  const bytes = new Uint8Array(await response.arrayBuffer());
  if (bytes.length > 10 * 1024 * 1024) throw new Error("Image artifact exceeds 10 MB.");
  let binary = ""; for (let offset = 0; offset < bytes.length; offset += 0x8000) binary += String.fromCharCode(...bytes.subarray(offset, offset + 0x8000));
  return btoa(binary);
}

async function executePowerPoint(name, args, request) {
  return PowerPoint.run(async (context) => {
    const presentation = context.presentation;
    if (name === "office_powerpoint_add_slide") {
      presentation.slides.add(); await context.sync();
      return { app: "powerpoint", status: "completed", operation_receipt: crypto.randomUUID() };
    }
    const slide = await getPowerPointSlide(context, args.slideIndex);
    if (name === "office_powerpoint_snapshot") {
      const slides = presentation.slides; const shapes = slide.shapes;
      slides.load("items/id"); shapes.load("items/id,name,type,left,top,width,height"); await context.sync();
      const maxShapes = Math.min(200, Math.max(1, Number(args.maxShapes) || 80));
      return { app: "powerpoint", slide_count: slides.items.length, slide_index: slides.items.indexOf(slide) + 1, shapes: shapes.items.slice(0, maxShapes).map((shape) => ({ id: shape.id, name: shape.name, type: shape.type, left: shape.left, top: shape.top, width: shape.width, height: shape.height })) };
    }
    if (name === "office_powerpoint_select_slide") slide.select();
    else if (name === "office_powerpoint_delete_slide") { requireConfirm(args); slide.delete(); }
    else if (name === "office_powerpoint_add_text_box") {
      const shape = slide.shapes.addTextBox(bounded(args.text, 20000), shapeOptions(args)); if (args.name) shape.name = bounded(args.name, 300);
    } else if (name === "office_powerpoint_add_shape") {
      const shape = slide.shapes.addGeometricShape(bounded(args.shapeType || "Rectangle", 80), shapeOptions(args)); if (args.name) shape.name = bounded(args.name, 300); if (args.text) shape.textFrame.textRange.text = bounded(args.text, 20000);
    } else if (name === "office_powerpoint_update_shape") {
      const shape = await getPowerPointShape(context, slide, args); for (const key of ["left", "top", "width", "height"]) if (Number.isFinite(Number(args[key]))) shape[key] = Number(args[key]); if (args.name) shape.name = bounded(args.name, 300); if (args.text !== undefined) shape.textFrame.textRange.text = bounded(args.text, 20000);
    } else if (name === "office_powerpoint_delete_shape") {
      requireConfirm(args); (await getPowerPointShape(context, slide, args)).delete();
    } else if (name === "office_powerpoint_insert_image") {
      const base64 = await artifactBase64(request, bounded(args.artifact_id, 200));
      slide.select();
      await context.sync();
      await new Promise((resolve, reject) => {
        Office.context.document.setSelectedDataAsync(base64, {
          coercionType: Office.CoercionType.Image,
          imageLeft: Number(args.left) || 0,
          imageTop: Number(args.top) || 0,
          imageWidth: Number(args.width) || 320,
          imageHeight: Number(args.height) || 180,
        }, (result) => {
          if (result.status === Office.AsyncResultStatus.Succeeded) resolve();
          else reject(new Error(result.error?.message || "PowerPoint image insertion failed."));
        });
      });
    } else throw new Error("Unsupported PowerPoint tool.");
    await context.sync();
    return { app: "powerpoint", status: "completed", operation_receipt: crypto.randomUUID() };
  });
}

async function documentTitle() {
  return new Promise((resolve) => {
    if (!Office.context.document.getFilePropertiesAsync) return resolve(`Current ${state.officeApp} document`);
    Office.context.document.getFilePropertiesAsync((result) => {
      if (result.status !== Office.AsyncResultStatus.Succeeded || !result.value?.url) return resolve(`Current ${state.officeApp} document`);
      const withoutQuery = String(result.value.url).split(/[?#]/, 1)[0];
      resolve(bounded(decodeURIComponent(withoutQuery.split(/[\\/]/).pop() || "Current document"), 300));
    });
  });
}

async function bindDocument() {
  state.binding = { bound: true, binding_id: crypto.randomUUID(), title: await documentTitle(), revision: 1 };
  saveLocalState(); sendHostState(); setStatus();
}

async function unbindDocument() {
  if (state.hostId) await fetch(`/api/runtime/client-hosts/${encodeURIComponent(state.hostId)}/unbind`, { method: "POST" }).catch(() => undefined);
  state.binding = {}; saveLocalState(); sendHostState(); setStatus();
}

async function initialize() {
  readLocalState();
  byId("pair-button").addEventListener("click", () => {
    const code = byId("pairing-code").value.trim();
    if (!/^\d{8}$/.test(code)) return;
    connect({ type: "pair", pairing_code: code });
  });
  byId("bind-button").addEventListener("click", () => void bindDocument());
  byId("unbind-button").addEventListener("click", () => void unbindDocument());
  const response = await fetch("/api/runtime/office-host/capabilities");
  const payload = await response.json();
  state.capabilities = Array.isArray(payload.tools) ? payload.tools : [];
  state.schemaHashes = Object.fromEntries(state.capabilities.map((item) => [item.name, item.schema_hash]));
  setStatus(); authenticate();
  setInterval(() => { if (send({ type: "heartbeat", office_app: state.officeApp, document_binding: state.binding, requirement_sets: state.requirementSets })) setStatus(); }, 20000);
}

Office.onReady((info) => {
  if (info.host === Office.HostType.Word) state.officeApp = "word";
  else if (info.host === Office.HostType.Excel) state.officeApp = "excel";
  else if (info.host === Office.HostType.PowerPoint) state.officeApp = "powerpoint";
  else state.officeApp = "";
  const candidates = state.officeApp === "word"
    ? [["WordApi", "1.1"]]
    : state.officeApp === "excel"
      ? [["ExcelApi", "1.1"]]
      : [["PowerPointApi", "1.2"], ["PowerPointApi", "1.3"], ["PowerPointApi", "1.4"], ["ImageCoercion", "1.1"]];
  state.requirementSets = candidates
    .filter(([setName, version]) => Office.context.requirements.isSetSupported(setName, version))
    .map(([setName, version]) => `${setName}:${version}`);
  void initialize();
});

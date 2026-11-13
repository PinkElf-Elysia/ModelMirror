import {openComparisonHost} from './comparison-host.mjs';
import {prepareVariablePanel} from './variable-render.mjs';
import {panelDefinition,initialPanelSnapshot} from './variable-panel.mjs';
import {BranchCheckpoints} from './branch-storage.mjs';
import {createServer} from 'node:http';
import {readFile} from 'node:fs/promises';
import {fileURLToPath, pathToFileURL} from 'node:url';
import {resolve, dirname} from 'node:path';
import {randomBytes} from 'node:crypto';
import {AuthoringStore} from './store.mjs';
import {OfflineSessionHost} from './offline-session.mjs';
import {SummarySessionHost} from './summary-session.mjs';
import {JointSessionHost} from './joint-session.mjs';
import {CONTROLLED_MODE} from './controlled-receipt.mjs';
import {ParameterSettingsStore} from './generation-settings.mjs';
import {JointBudget} from './joint-budget.mjs';
import {createJointSettings} from './joint-settings.mjs';
import {createJointApi} from './joint-api.mjs';
import {TurnPlayback} from './turn-playback.mjs';
import {createSummarySettings} from './summary-settings.mjs';
import {createAuthoringHistoryControl} from './history-control.mjs';
import {createHistorySettings} from './history-settings.mjs';
import {openPublishedRuntime} from './runtime-binding.mjs';
import {requireOfflineModelControl} from './model-control.mjs';
import {normalizeWorldbookWeights} from './worldbook.mjs';
import {projectDisplayRules} from './display-regex.mjs';
import {createCGReplayCache} from './cg-replay.mjs';
import {projectSuggestions} from './input-assistance.mjs';
import {AuthoringError, LIMITS, blankDocument, keys, parseJson, isId, sha256, canonical, fail, configuration, validateDocument} from './contracts.mjs';
import {compilePresentationBounded,createPresentationCompiler, createPresentationCache, presentationAliases} from './presentation.mjs';
import {createAuthorSandbox,needsAuthorScript,validateScriptEvent,validateViewport} from './sandbox.mjs';

const here = dirname(fileURLToPath(import.meta.url));
const api = '/rpg-app/api/authoring';
const csp = "default-src 'none'; script-src 'self'; style-src 'self'; img-src 'self' blob: data:; media-src 'self' blob:; connect-src 'self'; frame-src 'none'; object-src 'none'; base-uri 'none'; form-action 'none'; frame-ancestors 'none'";
const conflict = /CONFLICT|PENDING|BUSY|RESULT_UNKNOWN/;
const unavailable = /STORAGE|CORRUPT|WRITE_FAILED|OWNER|CLOSED/;
const createdIdentity = operationId => 'work-' + sha256('authoring-create:' + operationId);

function statusFor(code) {
  if (code === 'AUTHORING_ORIGIN_REJECTED' || code === 'AUTHORING_HOST_REJECTED') return 403;
  if (code === 'AUTHORING_METHOD') return 405;
  if (code.endsWith('_NOT_FOUND')) return 404;
  if (code.endsWith('_SIZE') || code.endsWith('_CAPACITY')) return 413;
  if (conflict.test(code)) return 409;
  if (unavailable.test(code) || ['MODEL_CONTROL_UNAVAILABLE','MODEL_CONTROL_TIMEOUT'].includes(code)) return 503;
  return 422;
}
function query(url, allowed) {
  for (const key of url.searchParams.keys()) {
    if (!allowed.includes(key) || url.searchParams.getAll(key).length !== 1) fail('AUTHORING_QUERY_INVALID', '/' + key);
  }
}
async function jsonBody(req, limit = LIMITS.packageBytes + 1024 * 1024) {
  if (req.headers['content-type'] !== 'application/json') fail('AUTHORING_CONTENT_TYPE');
  if (req.headers['content-encoding']) fail('AUTHORING_CONTENT_ENCODING');
  if (Number(req.headers['content-length']) > limit) fail('AUTHORING_BODY_SIZE');
  const chunks = []; let size = 0;
  for await (const chunk of req) {
    size += chunk.length;
    if (size > limit) fail('AUTHORING_BODY_SIZE');
    chunks.push(chunk);
  }
  const bytes = Buffer.concat(chunks);
  let text;
  try { text = new TextDecoder('utf-8', {fatal: true}).decode(bytes); }
  catch { fail('AUTHORING_UNICODE_INVALID'); }
  return parseJson(text, limit);
}
function payloadFields(value, expected) { keys(value, expected); return value; }
function safeError(error) {
  if (!(error instanceof AuthoringError)) return {code: 'AUTHORING_STORAGE_UNAVAILABLE', path: '', details: {}};
  // Never serialize exception stacks, filesystem names or request/source payloads.
  const details = {};
  for (const key of ['workId', 'operationId']) if (isId(error.details?.[key])) details[key] = error.details[key];
  if (error.details?.scope === 'gallery') details.scope = 'gallery';
  if(typeof error.details?.diagnostic==='string')details.diagnostic=error.details.diagnostic.slice(0,200);
  if (Array.isArray(error.details?.issues)) details.issues = error.details.issues.map(item => ({code: item.code, path: item.path || ''}));
  return {code: error.code, path: error.path || '', details};
}
async function page(origin, controlledModels = false, controlledHistory = false, controlledSummary = false, creatorExperience = false, controlledJoint = false, controlledPlayback = false, realProvider = false) {
  const source = await readFile(resolve(here, '../authoring-prototype/index.html'), 'utf8');
  const replacements = [
    ["media-src blob:; connect-src 'none'", "media-src 'self' blob:; connect-src 'self'"],
    ["frame-src 'none'", 'frame-src '+origin+api+'/presentations/frames/'],
    ['RPG 创作 · B0 交互原型', 'RPG 创作 · 作品与资源'],
    ['src="./app.mjs"', 'src="./browser.mjs"'],
    ['<body>', '<body data-authoring="persistent">'],
    ['<main class="workspace">', '<main class="workspace" hidden>'],
    ['<strong id="work-title">雾港来信</strong>', '<strong id="work-title">正在读取作品</strong>'],
    ['示例作品 · 未保存', '正在读取本地作品库'],
    ['<strong>B0 交互原型</strong><span>仅演示本页编辑与预览，不连接模型或正式存储。</span>', '<strong>B1 作品与资源 · B5-E 聊天BGM</strong><span>离线回复支持插图、背景和聊天音乐；正式游玩、持久会话与实际请求尚未接入。</span>'],
    ['<button class="primary" id="publish-button">本地发布</button>', '<button class="primary" id="publish-button">版本与封存</button>'],
  ];
  let result = source;
  for (const [before, after] of replacements) {
    if (result.split(before).length !== 2) fail('AUTHORING_UI_SOURCE_CHANGED');
    result = result.replace(before, after);
  }
  if(controlledModels)result=result.replace('<strong>B1 作品与资源 · B5-E 聊天BGM</strong><span>离线回复支持插图、背景和聊天音乐；正式游玩、持久会话与实际请求尚未接入。</span>', '<strong>B1 作品与资源 · B6-E 模型配置</strong><span>模型配置保存至独立离线会话；本入口不发送真实请求，不自动启用插件。</span>');
  if(controlledHistory)result=result.replace('B6-E 模型配置','B6-G 会话插件').replace('不自动启用插件。','M1 须独立安装并授权。');
  if(controlledSummary)result=result.replace('</head>','<link rel="stylesheet" href="./session-ui.css"><script type="module" src="./session-ui.mjs"></script></head>');
  if(controlledJoint)result=result.replace('</head>','<link rel="stylesheet" href="./session-ui.css"><link rel="stylesheet" href="./joint-ui.css"><link rel="stylesheet" href="./comparison-ui.css"><script type="module" src="./joint-ui.mjs"></script></head>');
  if(creatorExperience)result=result.replace('<body data-authoring="persistent">','<body data-authoring="persistent" data-experience="creator">')
    .replace('<title>RPG 创作 · 作品与资源</title>','<title>RPG 创作工作台</title>')
    .replace('</head>','<link rel="stylesheet" href="./creator-experience.css"></head>')
    .replace('<div class="top-actions">','<div class="top-actions"><a class="creator-help" href="/creator-guide.html" target="_blank" rel="noopener">创作指南</a>');
  if(controlledPlayback)result=result.replace('data-authoring="persistent"','data-authoring="persistent" data-turn-playback="true"');
  if(realProvider)result=result.replace('data-authoring="persistent"','data-authoring="persistent" data-execution="controlled"')
    .replace('模型配置保存至独立离线会话；本入口不发送真实请求，不自动启用插件。','模型配置与会话授权分别保存；点击发送后才调用模型。');
  return result;
}

/** Isolated authoring server. Trusted model settings explicitly choose fixture or controlled execution. */
export async function startAuthoringServer({directory, port = 0, recoverDeadOwner = false, fault, modelSettings = null, historySettings = null, summarySettings = null, jointSettings = null, playbackSettings = null, creatorExperience = false, chatParameters = false, comparisonSettings = null} = {}) {
  if (typeof creatorExperience !== 'boolean' || typeof chatParameters !== 'boolean') fail('AUTHORING_SERVER_OPTIONS');
  if (chatParameters && (creatorExperience || !jointSettings || !modelSettings || !historySettings)) fail('CHAT_PARAMETER_SERVER_OPTIONS');
  if (!directory || !Number.isInteger(port) || port < 0 || port > 65535) fail('AUTHORING_SERVER_OPTIONS');
  const store = await AuthoringStore.open(directory, {recoverDeadOwner, ...(fault ? {fault} : {})});
  let sessionHost=null,modelControl=null,historyControl=null,historyUI=null,summaryUI=null,jointUI=null,jointBudget=null,jointApi=null,turnPlayback=null,branchStore=null,parameterPresets=null,comparisonHost=null;
  const presentations = createPresentationCache();
  let origin;
  try {
    if(jointSettings!==null){
      keys(jointSettings,['directory','allowance',...(Object.hasOwn(jointSettings,'fault')?['fault']:[])]);keys(jointSettings.allowance,['id','limit']);
      if(summarySettings||!modelSettings||!historySettings||typeof jointSettings.directory!=='string'||
        [directory,modelSettings.directory,historySettings.directory].some(d=>typeof d!=='string'||resolve(d)===resolve(jointSettings.directory)))fail('JOINT_SETTINGS_OPTIONS');
      branchStore=await BranchCheckpoints.open({directory:resolve(jointSettings.directory,'branches'),recoverDeadOwner});
      jointBudget=await JointBudget.open({directory:resolve(jointSettings.directory,'budget'),allowanceId:jointSettings.allowance.id,limit:jointSettings.allowance.limit,recoverDeadOwner});
    }
    if(summarySettings!==null){
      keys(summarySettings,['directory',...(Object.hasOwn(summarySettings,'fault')?['fault']:[])]);
      if(!modelSettings||!historySettings||typeof summarySettings.directory!=='string'||[directory,modelSettings.directory,historySettings.directory].some(d=>resolve(d)===resolve(summarySettings.directory)))fail('SUMMARY_SETTINGS_OPTIONS');
    }
    if(historySettings!==null){
      keys(historySettings,['directory',...(Object.hasOwn(historySettings,'fault')?['fault']:[])]);
      if(!modelSettings||typeof historySettings.directory!=='string'||resolve(historySettings.directory)===resolve(directory)||resolve(historySettings.directory)===resolve(modelSettings.directory))fail('HISTORY_SETTINGS_OPTIONS');
      historyControl=await createAuthoringHistoryControl({directory:resolve(historySettings.directory,'control'),recoverDeadOwner,
        lookupSession:id=>sessionHost.lookupSession(id),resolveRuntime:ref=>openPublishedRuntime(store,ref),generationParameters:chatParameters,modelComparison:comparisonSettings!==null});
    }
    if(modelSettings!==null){
      keys(modelSettings,['directory','provider','control']);
      if(typeof modelSettings.directory!=='string'||resolve(modelSettings.directory)===resolve(directory))fail('MODEL_SETTINGS_OPTIONS');
      modelControl=requireOfflineModelControl(modelSettings.control);
      sessionHost=await (jointSettings?JointSessionHost:summarySettings?SummarySessionHost:OfflineSessionHost).open({directory:modelSettings.directory,provider:modelSettings.provider,
        modelControl,historyControl,...(jointBudget?{budget:jointBudget,branchStore,chatParameters}:{}),recoverDeadOwner,resolveRuntime:ref=>openPublishedRuntime(store,ref)});
    }
    if((creatorExperience||chatParameters)&&sessionHost?.readiness?.mode===CONTROLLED_MODE&&typeof modelControl?.checkParameters==='function')
      parameterPresets=await ParameterSettingsStore.open({directory:resolve(modelSettings.directory,'parameter-presets'),control:modelControl,recoverDeadOwner});
    // The M1 receipt contract has its own policy revision. Do not rewrite old receipts.
    const historyHost=summarySettings?{
      async read(id){const v=await sessionHost.read(id);return {...v,effectivePolicy:{...v.effectivePolicy,revision:v.historyConfigurationPolicyRevision}};},
      configureHistory:input=>sessionHost.configureHistory(input),reconcileStorage:()=>sessionHost.reconcileStorage(),
    }:sessionHost;
    if(historyControl&&!jointSettings)historyUI=await createHistorySettings({directory:historySettings.directory,host:historyHost,control:historyControl,resolveRuntime:ref=>openPublishedRuntime(store,ref),recoverDeadOwner,fault:historySettings.fault});
    if(summarySettings)summaryUI=await createSummarySettings({directory:summarySettings.directory,host:sessionHost,control:historyControl,recoverDeadOwner,fault:summarySettings.fault});
    if(jointSettings){
      jointUI=await createJointSettings({directory:jointSettings.directory,host:sessionHost,control:historyControl,resolveRuntime:ref=>openPublishedRuntime(store,ref),recoverDeadOwner,fault:jointSettings.fault});
      jointApi=createJointApi({host:sessionHost,settings:jointUI});
    }
    if(playbackSettings!==null){
      keys(playbackSettings,['directory',...['seed','fault','storageFault'].filter(k=>Object.hasOwn(playbackSettings,k))]);
      if(!jointApi||typeof playbackSettings.directory!=='string'||[directory,modelSettings.directory,historySettings.directory,jointSettings.directory].some(d=>resolve(d)===resolve(playbackSettings.directory)))fail('TURN_PLAYBACK_OPTIONS');
      turnPlayback=await TurnPlayback.open({...playbackSettings,recoverDeadOwner,host:sessionHost,resolveRuntime:ref=>openPublishedRuntime(store,ref),cache:presentations,origin:()=>origin});
    }
    if(comparisonSettings!==null){if(!sessionHost||!jointBudget||!turnPlayback||sessionHost.readiness.realProvider!==true)fail('COMPARISON_HOST_REQUIRED');keys(comparisonSettings,['directory','origin','serviceToken']);comparisonHost=await openComparisonHost({...comparisonSettings,host:sessionHost,budget:jointBudget,playback:turnPlayback,recoverDeadOwner,presets:parameterPresets,preview:(s,b,t)=>turnPlayback.candidate(s,b,t)});}
  }catch(error){await comparisonHost?.close();await parameterPresets?.close();await turnPlayback?.close();await jointUI?.close();await summaryUI?.close();await historyUI?.close();await sessionHost?.close();await jointBudget?.close();await branchStore?.close();await historyControl?.close();await store.close();throw error;}
  const cgReplays=createCGReplayCache();
  const scripts=new Map();
  const releaseScript=token=>{const record=scripts.get(token);if(record){record.runtime?.close();record.compiler?.close();scripts.delete(token);}return !!record;};
  const pruneScripts=()=>{for(const [token,record] of scripts)if(record.expiresAt<=Date.now())releaseScript(token);};
  const scriptTimer=setInterval(pruneScripts,30000);scriptTimer.unref();
  const resourcesFor=async(current,html,css,requestedAliases)=>{
    const references=requestedAliases||presentationAliases(html,css),resources=[];
    for(const asset of current.assets){
      if(!references.includes(asset.alias))continue;
      const frozen=await store.getAsset(current.id,asset.alias);
      if(frozen.asset.sha256!==asset.sha256)fail('AUTHORING_REVISION_CONFLICT');
      resources.push({...frozen.asset,bytes:frozen.bytes});
    }
    return resources;
  };
  const server = createServer(async (req, res) => {
    res.setHeader('X-Content-Type-Options', 'nosniff');
    res.setHeader('Content-Security-Policy', csp.replace("frame-src 'none'", 'frame-src '+origin+api+'/presentations/frames/'));
    res.setHeader('Cross-Origin-Resource-Policy', 'same-origin');
    res.setHeader('Referrer-Policy', 'no-referrer');
    res.setHeader('Cache-Control', 'no-store');
    res.setHeader('X-Frame-Options', 'DENY');
    res.setHeader('Permissions-Policy', 'camera=(), microphone=(), geolocation=()');
    const send = (value, status = 200, type = 'application/json; charset=utf-8') => {
      res.writeHead(status, {'Content-Type': type});
      res.end(type.startsWith('application/json') ? JSON.stringify(value) : value);
    };
    try {
      if (req.headers.host !== new URL(origin).host) fail('AUTHORING_HOST_REJECTED');
      const url = new URL(req.url, origin), path = url.pathname;
      if (url.origin !== origin || /%2f|%5c|%00/i.test(path)) fail('AUTHORING_PATH_INVALID');
      const suppliedOrigin = req.headers.origin, site = req.headers['sec-fetch-site'];
      if ((suppliedOrigin && suppliedOrigin !== origin) || site === 'cross-site') fail('AUTHORING_ORIGIN_REJECTED');
      if (!['GET', 'POST'].includes(req.method)) fail('AUTHORING_METHOD');
      if (req.method === 'POST' && (suppliedOrigin !== origin || req.headers['x-authoring-request'] !== '1')) fail('AUTHORING_ORIGIN_REJECTED');
      if (req.method === 'GET' && !path.startsWith(api + '/')) {
        query(url, path === '/' ? ['work'] : path === '/chat' && chatParameters ? ['session'] : []);
        if(path==='/chat'&&chatParameters){
          if(!/^session-[a-f0-9]{64}$/.test(url.searchParams.get('session')||''))fail('OFFLINE_SESSION_NOT_FOUND');
          return send('<!doctype html><html lang="zh-CN"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover"><title>RPG · 会话参数</title><link rel="stylesheet" href="/chat-parameter-ui.css"><link rel="stylesheet" href="/comparison-ui.css"><script type="module" src="/chat-parameter-ui.mjs"></script></head><body><div data-chat-root><p role="status">正在读取会话…</p></div></body></html>',200,'text/html; charset=utf-8');
        }
        // Keep the creator root unavailable in chat mode; use the separate /chat surface.
        if (path === '/' && chatParameters) fail('AUTHORING_ROUTE_NOT_FOUND');
        if (path === '/') return send(await page(origin,sessionHost!==null,!!historyUI,!!summaryUI,creatorExperience,!!jointApi,!!turnPlayback,sessionHost?.readiness?.realProvider===true), 200, 'text/html; charset=utf-8');
        const files = {
          '/styles.css': [resolve(here, '../authoring-prototype/styles.css'), 'text/css; charset=utf-8'],
          '/app.mjs': [resolve(here, '../authoring-prototype/app.mjs'), 'text/javascript; charset=utf-8'],
          '/editor.mjs': [resolve(here, '../authoring-prototype/editor.mjs'), 'text/javascript; charset=utf-8'],
          '/metadata.mjs': [resolve(here, 'metadata.mjs'), 'text/javascript; charset=utf-8'],
          '/recovery.mjs': [resolve(here, 'recovery.mjs'), 'text/javascript; charset=utf-8'],
          '/media-ui.mjs': [resolve(here, 'media-ui.mjs'), 'text/javascript; charset=utf-8'],
          '/chat-bgm.mjs': [resolve(here, 'chat-bgm.mjs'), 'text/javascript; charset=utf-8'],
          '/chat-bgm.css': [resolve(here, 'chat-bgm.css'), 'text/css; charset=utf-8'],
          '/worldbook-ui.mjs': [resolve(here, 'worldbook-ui.mjs'), 'text/javascript; charset=utf-8'],
          '/worldbook-ui.css': [resolve(here, 'worldbook-ui.css'), 'text/css; charset=utf-8'],
          '/dialogue.mjs': [resolve(here, 'dialogue.mjs'), 'text/javascript; charset=utf-8'],
          '/dialogue-ui.mjs': [resolve(here, 'dialogue-ui.mjs'), 'text/javascript; charset=utf-8'],
          '/dialogue-ui.css': [resolve(here, 'dialogue-ui.css'), 'text/css; charset=utf-8'],
          '/input-assistance.mjs': [resolve(here, 'input-assistance.mjs'), 'text/javascript; charset=utf-8'],
          '/input-assistance-ui.mjs': [resolve(here, 'input-assistance-ui.mjs'), 'text/javascript; charset=utf-8'],
          '/input-assistance-ui.css': [resolve(here, 'input-assistance-ui.css'), 'text/css; charset=utf-8'],
          '/cg-ui.css': [resolve(here, 'cg-ui.css'), 'text/css; charset=utf-8'],
          '/cg-ui.mjs': [resolve(here, 'cg-ui.mjs'), 'text/javascript; charset=utf-8'],
          '/display-regex-ui.mjs': [resolve(here, 'display-regex-ui.mjs'), 'text/javascript; charset=utf-8'],
          '/display-regex-ui.css': [resolve(here, 'display-regex-ui.css'), 'text/css; charset=utf-8'],
          '/history-ui.mjs': [resolve(here, 'history-ui.mjs'), 'text/javascript; charset=utf-8'],
          '/model-ui.mjs': [resolve(here, 'model-ui.mjs'), 'text/javascript; charset=utf-8'],
          '/model-ui.css': [resolve(here, 'model-ui.css'), 'text/css; charset=utf-8'],
          '/browser.mjs': [resolve(here, 'browser.mjs'), 'text/javascript; charset=utf-8'],
          '/browser-api.mjs': [resolve(here, 'browser-api.mjs'), 'text/javascript; charset=utf-8'],
        };
        for(const name of ['variable-panel-ui.mjs','variable-frame-ui.mjs','variable-panel-ui.css'])files['/'+name]=[resolve(here,name),name.endsWith('.css')?'text/css; charset=utf-8':'text/javascript; charset=utf-8'];
        if(creatorExperience)for(const name of ['creator-guide.html','creator-experience.css'])files['/'+name]=[resolve(here,name),name.endsWith('.html')?'text/html; charset=utf-8':'text/css; charset=utf-8'];
        if(summaryUI)for(const name of ['session-ui.mjs','session-client.mjs','session-ui.css'])files['/'+name]=[resolve(here,name),name.endsWith('.css')?'text/css; charset=utf-8':'text/javascript; charset=utf-8'];
        if(turnPlayback)for(const name of ['playback-ui.mjs','playback-ui.css','special-cg-ui.mjs'])files['/'+name]=[resolve(here,name),name.endsWith('.css')?'text/css; charset=utf-8':'text/javascript; charset=utf-8'];
        if(jointApi)for(const name of ['joint-ui.mjs','joint-client.mjs','joint-ui.css','session-client.mjs','session-ui.css'])files['/'+name]=[resolve(here,name),name.endsWith('.css')?'text/css; charset=utf-8':'text/javascript; charset=utf-8'];
        if(chatParameters)for(const name of ['chat-parameter-ui.mjs','chat-parameter-ui.css'])files['/'+name]=[resolve(here,name),name.endsWith('.css')?'text/css; charset=utf-8':'text/javascript; charset=utf-8'];
        for(const name of ['comparison-ui.mjs','comparison-client.mjs','comparison-ui.css'])files['/'+name]=[resolve(here,name),name.endsWith('.css')?'text/css; charset=utf-8':'text/javascript; charset=utf-8'];
        if (!files[path]) fail('AUTHORING_ROUTE_NOT_FOUND');
        return send(await readFile(files[path][0]), 200, files[path][1]);
      }
      if (!path.startsWith(api + '/')) fail('AUTHORING_ROUTE_NOT_FOUND');
      if(path===api+'/variable-panel/preview'&&req.method==='POST'){
        const body=payloadFields(await jsonBody(req,1024*1024),['workId','expectedRevision','config']);
        const current=await store.read(body.workId);if(current.revision!==body.expectedRevision)fail('AUTHORING_REVISION_CONFLICT');
        const definition=panelDefinition({...body.config,enabled:true});
        const value=await prepareVariablePanel({config:body.config,snapshot:initialPanelSnapshot(definition),binding:{workId:current.id,revision:current.revision},origin,cache:presentations,assets:current.assets,readAsset:alias=>store.getAsset(current.id,alias)});
        if((await store.read(current.id)).revision!==current.revision){presentations.release(value.token);fail('AUTHORING_REVISION_CONFLICT');}
        return send(value);
      }
      if(path===api+'/joint'&&req.method==='GET'){query(url,[]);return send({comparison:!!comparisonHost,enabled:jointApi!==null,realProvider:sessionHost?.readiness?.realProvider===true,...(turnPlayback?{turnPlayback:true}:{}),...(chatParameters?{chatParameters:true}:{})});}
      if(path.startsWith(api+'/joint/')){
        if(!jointApi)fail('AUTHORING_ROUTE_NOT_FOUND');query(url,[]);
        const body=req.method==='POST'?await jsonBody(req,1024*1024):undefined;
        const route=path.slice((api+'/joint/').length);
        if(route.startsWith('playback/')){if(!turnPlayback)fail('AUTHORING_ROUTE_NOT_FOUND');return send(await turnPlayback.handle(req.method,route.slice(9),body));}
        if(route.startsWith('comparisons/')){if(!comparisonHost)fail('AUTHORING_ROUTE_NOT_FOUND');return send(await comparisonHost.handle(req.method,route.slice(12),body));}
        const result=await jointApi.handle(req.method,route,body);
        if(turnPlayback&&req.method==='POST'&&route==='branch'){
          let display;try{display={state:'inherited',...await turnPlayback.inheritBranch(result.session.id)};}catch(e){display={state:'pending',diagnostic:e instanceof AuthoringError?e.code:'TURN_DISPLAY_RESULT_UNKNOWN'};}
          return send({...result,display});
        }
        if(turnPlayback&&req.method==='POST'&&['send','recover'].includes(route))return send({...result,display:await turnPlayback.afterStory(body.sessionId,body.operationId)});
        return send(result);
      }
      if(path===api+'/summary-settings'&&req.method==='GET'){
        query(url,[]);return send({enabled:summaryUI!==null,realProvider:false});
      }
      if(path.startsWith(api+'/summary-settings/')){
        if(!summaryUI)fail('AUTHORING_ROUTE_NOT_FOUND');query(url,[]);
        const route=path.slice((api+'/summary-settings/').length);
        if(req.method==='GET'){
          const found=/^sessions\/(session-[a-f0-9]{64})$/.exec(route);if(found)return send(await summaryUI.read(found[1]));
        }else{
          const body=await jsonBody(req,16384);
          if(route==='change'){await historyUI.assertSettled();return send(await summaryUI.change(body));}
          if(route==='reconcile')return send(await summaryUI.reconcile(body));
        }
        fail('AUTHORING_ROUTE_NOT_FOUND');
      }
      if(path.startsWith(api+'/sessions/')){
        if(!summaryUI)fail('AUTHORING_ROUTE_NOT_FOUND');query(url,[]);
        const route=path.slice((api+'/sessions/').length);
        if(req.method==='GET'&&/^session-[a-f0-9]{64}$/.test(route))return send(await sessionHost.read(route));
        if(req.method==='POST'){
          const body=await jsonBody(req,1024*1024);
          if(['send','summary/update','summary/continue'].includes(route)){
            await historyUI.assertSettled();await summaryUI.assertSettled();
            // Compression continuation owns the original M2 job ID.
            if(route!=='summary/continue')await summaryUI.assertOperationAvailable(body.operationId);
          }
          if(route==='send')return send(await sessionHost.send(body));
          if(route==='recover')return send(await sessionHost.recover(body));
          if(route==='summary/update')return send(await sessionHost.requestSummary(body));
          if(route==='summary/recover')return send(await sessionHost.recoverSummary(body));
          if(route==='summary/continue')return send(await sessionHost.continueSummary(body));
          if(route==='reconcile'){
            payloadFields(body,['sessionId']);await sessionHost.reconcileStorage();return send(await sessionHost.read(body.sessionId));
          }
        }
        fail('AUTHORING_ROUTE_NOT_FOUND');
      }
      if(path===api+'/model-settings'&&req.method==='GET'){
        query(url,[]);return send({enabled:sessionHost!==null,realProvider:sessionHost?.readiness?.realProvider===true,pluginExecution:historyUI!==null,...(parameterPresets?{presetsEnabled:true}:{}),...(chatParameters?{chatParameters:true}:{})});
      }
      if(path===api+'/history-settings'&&req.method==='GET'){
        query(url,[]);return send({enabled:historyUI!==null,realProvider:false});
      }
      if(path.startsWith(api+'/history-settings/')){
        if(!historyUI)fail('AUTHORING_ROUTE_NOT_FOUND');
        query(url,[]);const route=path.slice((api+'/history-settings/').length);
        if(req.method==='GET'){
          const found=/^sessions\/(session-[a-f0-9]{64})$/.exec(route);
          if(found)return send(await historyUI.read(found[1]));
        }else if(req.method==='POST'){
          const body=await jsonBody(req,16384);
          if(route==='change'){await summaryUI?.assertSettled();await summaryUI?.assertOperationAvailable(body.operationId);return send(await historyUI.change(body));}
          if(route==='reconcile')return send(await historyUI.reconcile(body));
        }
        fail('AUTHORING_ROUTE_NOT_FOUND');
      }
      if(path.startsWith(api+'/model-settings/')){
        if(!sessionHost)fail('AUTHORING_ROUTE_NOT_FOUND');
        const route=path.slice((api+'/model-settings/').length);
        if(route==='presets'||route==='presets/reconcile'){
          if(!parameterPresets)fail('AUTHORING_ROUTE_NOT_FOUND');query(url,[]);
          if(route==='presets'&&req.method==='GET'){
            const state=await parameterPresets.listPresets();
            return send({revision:state.revision,presets:state.current.map(p=>({id:p.id,name:p.name,revision:p.revision,profile:p.configuration.profile,capabilityRevision:p.configuration.selection.capabilityRevision}))});
          }
          if(req.method==='POST'){
            const body=await jsonBody(req,256*1024);
            if(route==='presets')return send(await parameterPresets.savePreset(body));
            payloadFields(body,['operationId']);return send(await parameterPresets.recover('presets','presets',body.operationId));
          }
          fail('AUTHORING_METHOD');
        }
        if(req.method==='GET'){
          query(url,['parameterVersion']);const parameterVersion=url.searchParams.get('parameterVersion')??'1';
          if(!['1','2'].includes(parameterVersion))fail('PARAMETER_VERSION_INVALID');
          if(parameterVersion==='2'&&sessionHost?.readiness?.mode!==CONTROLLED_MODE)fail('PARAMETER_CONTROL_V2_REQUIRED');
          if(route==='catalog')return send({catalog:parameterVersion==='2'?await modelControl.parameterCatalog():modelControl.list(),realProvider:sessionHost?.readiness?.realProvider===true});
          const found=/^sessions\/(session-[a-f0-9]{64})(?:\/(models))?$/.exec(route);
          if(found)return send(found[2]?await sessionHost.modelCatalog(found[1],Number(parameterVersion)):await sessionHost.read(found[1]));
          fail('AUTHORING_ROUTE_NOT_FOUND');
        }
        query(url,[]);const body=await jsonBody(req,256*1024);
        if(route==='refresh'){
          payloadFields(body,[]);
          // Explicit cache read only. This adapter has no certification or provider refresh.
          const catalog=typeof modelControl.refresh==='function'?await modelControl.refresh():modelControl.list();
          return send({catalog,realProvider:sessionHost?.readiness?.realProvider===true});
        }
        if(route==='check'){
          payloadFields(body,['choice']);
          if(body.choice?.format==='modelmirror.rpg.parameter-selection/2'){
            if(sessionHost?.readiness?.mode!==CONTROLLED_MODE)fail('PARAMETER_CONTROL_V2_REQUIRED');
            return send({choice:await modelControl.selectParameters(body.choice),dispatched:false});
          }
          let choice;
          try{choice=modelControl.select(body.choice);}catch(error){
            // Refresh only an expired host cache, keeping the caller's exact frozen selection.
            if(error.code!=='MODEL_CONTROL_REFRESH_REQUIRED'||typeof modelControl.refresh!=='function')throw error;
            await modelControl.refresh();choice=modelControl.select(body.choice);
          }
          if(typeof modelControl.validate==='function')await modelControl.validate(choice);
          return send({choice,dispatched:false});
        }
        if(route==='sessions'){await jointUI?.assertSettled();await jointUI?.assertOperationAvailable(body.operationId);return send(await sessionHost.create(body));}
        if(route==='configure'){if(chatParameters)return send(await jointUI.withOperation(body.operationId,()=>sessionHost.configureModel(body)));await jointUI?.assertSettled();await jointUI?.assertOperationAvailable(body.operationId);await historyUI?.assertSettled();await summaryUI?.assertSettled();await summaryUI?.assertOperationAvailable(body.operationId);return send(await sessionHost.configureModel(body));}
        if(route==='reconcile'){
          payloadFields(body,['sessionId','operationId','kind']);
          if(!isId(body.operationId)||!['create','configure'].includes(body.kind))fail('OFFLINE_OPERATION_INVALID');
          await sessionHost.reconcileStorage();
          const id=body.kind==='create'?'session-'+sha256((jointUI?'joint-session:':summaryUI?'summary-session:':'offline-session:')+body.operationId):body.sessionId;
          try{
            const view=await sessionHost.read(id);
            const intent=body.kind==='create'?view.session.create:view.session.models.changes.find(c=>c.intent.operationId===body.operationId);
            return send({found:!!intent,view});
          }catch(error){if(error.code==='OFFLINE_SESSION_NOT_FOUND')return send({found:false,view:null});throw error;}
        }
        fail('AUTHORING_ROUTE_NOT_FOUND');
      }
      if (req.method === 'GET' && path === api + '/state') {
        query(url, []);
        return send({works: await store.list(), blank: blankDocument(), limits: LIMITS, runtimeReady: false});
      }
      if(req.method==='GET'&&path.startsWith(api+'/presentations/frames/')){
        query(url,[]);
        const token=path.slice((api+'/presentations/frames/').length);
        if(!/^[a-f0-9]{48}$/.test(token)||req.headers['sec-fetch-dest']!=='iframe')fail('AUTHORING_PRESENTATION_NOT_FOUND');
        const snapshot=presentations.get(token);
        res.setHeader('Content-Security-Policy',snapshot.csp);
        res.setHeader('X-Frame-Options','SAMEORIGIN');
        res.setHeader('Content-Disposition','inline');
        return send(snapshot.frame,200,'text/html; charset=utf-8');
      }
      if(req.method==='POST'&&path===api+'/presentations/prepare'){
        query(url,[]);
        const body=await jsonBody(req);payloadFields(body,['workId','expectedRevision','document','mode','reply',...(Object.hasOwn(body,'viewport')?['viewport']:[]),...(Object.hasOwn(body,'messageRole')?['messageRole']:[]),...(Object.hasOwn(body,'cg')?['cg']:[])]);
        if(Object.hasOwn(body,'messageRole')&&(body.mode!=='reply'||!['assistant','user'].includes(body.messageRole)))fail('AUTHORING_PRESENTATION_ROLE');
        if(body.viewport)validateViewport(body.viewport);
        const current=await store.read(body.workId);
        if(current.revision!==body.expectedRevision)fail('AUTHORING_REVISION_CONFLICT');
        const document=validateDocument(body.document);
        if(typeof body.reply!=='string')fail('AUTHORING_TEXT_INVALID','/reply');
        let html=body.mode==='opening'?(document.sources.startupHtml||document.sources.introduction):body.reply;
        const css=body.mode==='opening'?document.sources.startupCss:document.sources.outputCss;
        const authorJs=body.mode==='opening'?document.sources.startupJs:'',token=randomBytes(24).toString('hex');
        const input={mode:body.mode,html,css,authorJs,origin,token,suggestions:body.mode==='reply'&&body.messageRole!=='user'&&document.enhancements.suggestions,...(document.settings.presentation?{presentation:document.settings.presentation}:{}),binding:{workId:current.id,revision:current.revision,documentHash:sha256(JSON.stringify(document))}};
        try{
          if(Object.hasOwn(body,'cg')){
            if(body.mode!=='reply'||body.messageRole==='user')fail('AUTHORING_CG_REPLAY_ROLE');
            input.cgView=cgReplays.prepare({current,document,reply:html,request:body.cg});
          }
          if(body.mode==='reply'&&document.enhancements.displayRules.length){
            const display=projectSuggestions(html,input.suggestions).display;
            input.displayProjection=await projectDisplayRules(display,document.enhancements.displayRules,{role:body.messageRole||'assistant'});
          }
          if(body.mode==='opening'&&needsAuthorScript(html,authorJs)){
            pruneScripts();if(scripts.size>=4)fail('AUTHORING_SCRIPT_CAPACITY');
            const record={runtime:null,compiler:createPresentationCompiler(),pending:true,sequence:0,operations:new Map(),expiresAt:Date.now()+600000,input,current};scripts.set(token,record);
            record.runtime=await createAuthorSandbox({html,js:authorJs,...(body.viewport?{viewport:body.viewport}:{})});
            if(!scripts.has(token)){record.runtime.close();fail('AUTHORING_SCRIPT_CLOSED');}
            record.input={...input,authorJs:'',execution:{sourceHash:sha256(canonical({html,css,authorJs})),stateHash:record.runtime.initial.stateHash,sequence:0}};
            html=record.runtime.initial.html;record.pending=false;
          }
          // Resolve aliases after bounded Markdown/HTML parsing, so entities,
          // reference-style images and CSS all use the same exact mapping rules.
          // The discovery pass is never cached or shown to the player.
          const record=scripts.get(token),renderInput={...(record?.input||input),html,...(record?{platform:record.runtime.initial.platform}:{})};
          const compile=record?.compiler.compile||compilePresentationBounded;
          const discovery=await compile(renderInput);
          const resources=await resourcesFor(current,html,css,discovery.manifest.requestedAliases);
          if(input.cgView){
            for(const image of [...input.cgView.plan.chat,...(input.cgView.background?[input.cgView.background]:[])]){
              const resource=resources.find(item=>item.alias===image.alias);
              if(!resource||resource.sha256!==image.sha256||resource.mediaType!==image.mediaType)fail('AUTHORING_CG_REPLAY_RESOURCE');
            }
          }
          const compiled=await compile({...renderInput,resources});
          if(record){record.html=html;record.compiled=compiled;}
          if((await store.read(current.id)).revision!==current.revision)fail('AUTHORING_REVISION_CONFLICT');
          return send({...presentations.put(compiled,token),frameUrl:api+'/presentations/frames/'+token});
        }catch(error){releaseScript(token);throw error;}
      }
      if(req.method==='POST'&&path===api+'/presentations/event'){
        query(url,[]);const body=payloadFields(await jsonBody(req),['token','operationId','sequence','event']);
        if(!/^[a-f0-9]{48}$/.test(body.token)||!isId(body.operationId)||!Number.isSafeInteger(body.sequence)||body.sequence<1)fail('AUTHORING_SCRIPT_EVENT');
        validateScriptEvent(body.event);pruneScripts();
        const record=scripts.get(body.token);if(!record)fail('AUTHORING_SCRIPT_CLOSED');
        const hash=sha256(canonical(body)),prior=record.operations.get(body.operationId);
        if(prior){if(prior!==hash)fail('AUTHORING_OPERATION_CONFLICT');if(record.last?.operationId===body.operationId)return send({...record.last.result,replayed:true});fail('AUTHORING_SCRIPT_SEQUENCE_CONFLICT');}
        if(record.pending)fail('AUTHORING_SCRIPT_BUSY');
        if(body.sequence!==record.sequence+1)fail('AUTHORING_SCRIPT_SEQUENCE_CONFLICT');
        if(record.sequence>=36000)fail('AUTHORING_SCRIPT_CAPACITY');
        record.pending=true;
        try{
          if((await store.read(record.current.id)).revision!==record.current.revision)fail('AUTHORING_REVISION_CONFLICT');
          const output=await record.runtime.step(body.event);
          const unchanged=output.html===record.html&&!output.platform.effects.some(c=>c[0]==='media'&&c[2]==='play'||c[0]==='reflow');
          let compiled;
          if(unchanged){
            compiled={...record.compiled,manifest:{...record.compiled.manifest,scriptStateHash:output.stateHash,sequence:body.sequence},patch:{...record.compiled.patch,platform:output.platform}};
          }else{
            const input={...record.input,html:output.html,platform:output.platform,execution:{...record.input.execution,stateHash:output.stateHash,sequence:body.sequence}};
            const discovery=await record.compiler.compile(input);
            const resources=await resourcesFor(record.current,output.html,record.input.css,discovery.manifest.requestedAliases);
            compiled=await record.compiler.compile({...input,resources});
          }
          if(scripts.get(body.token)!==record)fail('AUTHORING_SCRIPT_CLOSED');
          if((await store.read(record.current.id)).revision!==record.current.revision)fail('AUTHORING_REVISION_CONFLICT');
          presentations.replace(body.token,compiled);
          record.html=output.html;record.compiled=compiled;
          const result={sequence:body.sequence,patch:unchanged?{unchanged:true,platform:output.platform}:compiled.patch,manifest:compiled.manifest,proposals:output.proposals};
          record.sequence=body.sequence;record.operations.set(body.operationId,hash);record.last={operationId:body.operationId,result};
          return send(result);
        }catch(error){releaseScript(body.token);throw error;}finally{record.pending=false;}
      }
      if(req.method==='POST'&&path===api+'/presentations/stop'){
        query(url,[]);const body=payloadFields(await jsonBody(req),['token']);
        if(!/^[a-f0-9]{48}$/.test(body.token))fail('AUTHORING_PRESENTATION_BINDING');
        return send({stopped:releaseScript(body.token)});
      }
      if(req.method==='POST'&&path===api+'/presentations/release'){
        query(url,[]);const body=payloadFields(await jsonBody(req),['token']);
        if(!/^[a-f0-9]{48}$/.test(body.token))fail('AUTHORING_PRESENTATION_BINDING');
        releaseScript(body.token);
        return send({released:presentations.release(body.token)});
      }
      if (path === api + '/gallery' && req.method === 'GET') {
        query(url, []); return send(await store.readGallery());
      }
      if (path.startsWith(api + '/gallery/')) {
        const action = path.slice((api + '/gallery/').length);
        if (req.method === 'GET' && action.startsWith('images/')) {
          query(url, ['hash']);
          const {asset, bytes} = await store.getGalleryImage(action.slice(7));
          if (url.searchParams.has('hash') && url.searchParams.get('hash') !== asset.sha256) fail('AUTHORING_ASSET_REVISION_CONFLICT');
          res.setHeader('Content-Security-Policy', "default-src 'none'; sandbox");
          res.setHeader('Content-Length', bytes.length); res.setHeader('ETag', '"' + asset.sha256 + '"');
          return send(bytes, 200, asset.mediaType);
        }
        query(url, []);
        if (req.method !== 'POST') fail('AUTHORING_ROUTE_NOT_FOUND');
        const body = await jsonBody(req);
        if (action === 'groups') return send(await store.createGalleryGroup(body));
        if (action === 'images') return send(await store.uploadGalleryImages(body));
        if (action === 'images/edit') return send(await store.editGalleryImage(body));
        if (action === 'images/move') return send(await store.moveGalleryImages(body));
        if (action === 'operations/recover') {
          payloadFields(body, ['operationId']); return send(await store.recoverGallery(body.operationId));
        }
        fail('AUTHORING_ROUTE_NOT_FOUND');
      }
      if (req.method === 'POST' && path === api + '/works') {
        query(url, []);
        return send(await store.create(await jsonBody(req)));
      }
      // Pure editor calculations: no operation reservation, storage write or model task.
      if (req.method === 'POST' && path === api + '/worldbook/normalize') {
        query(url, []);
        const body=payloadFields(await jsonBody(req),['worldbook','entryId']);
        const book=validateDocument({...blankDocument(),worldbook:body.worldbook}).worldbook;
        const entry=book.entries.find(e=>e.id===body.entryId);
        if(!entry)fail('AUTHORING_WORLDBOOK_ENTRY_NOT_FOUND');
        return send({contents:normalizeWorldbookWeights(entry.contents)});
      }
      if (req.method === 'POST' && path === api + '/worldbook/templates/inspect') {
        query(url, []);
        const body=payloadFields(await jsonBody(req),['payload']);
        const template=parseJson(body.payload,LIMITS.documentBytes);
        keys(template,['format','worldbook']);
        if(template.format!=='modelmirror.rpg.worldbook-template/1')fail('AUTHORING_WORLDBOOK_TEMPLATE_FORMAT');
        return send({worldbook:validateDocument({...blankDocument(),worldbook:template.worldbook}).worldbook});
      }
      if (req.method === 'POST' && path === api + '/configurations/prepare') {
        query(url, []);
        const body = payloadFields(await jsonBody(req), ['document', 'assets']);
        if (typeof body.document !== 'string') fail('AUTHORING_JSON_INVALID');
        return send(configuration(parseJson(body.document, LIMITS.documentBytes), body.assets));
      }
      if (req.method === 'POST' && path === api + '/imports/inspect') {
        query(url, []);
        const body = payloadFields(await jsonBody(req), ['payload']);
        return send(await store.inspectImport(body.payload));
      }
      if (req.method === 'POST' && path === api + '/imports') {
        query(url, []);
        return send(await store.importPackage(await jsonBody(req)));
      }
      if (req.method === 'POST' && path === api + '/operations/recover') {
        query(url, []);
        const body = payloadFields(await jsonBody(req), ['workId', 'operationId']);
        if (!isId(body.operationId) || (body.workId !== null && typeof body.workId !== 'string')) fail('AUTHORING_OPERATION_ID_INVALID');
        // New works have a deterministic identity, including when their first reply was lost.
        return send(await store.recover(body.workId ?? createdIdentity(body.operationId), body.operationId));
      }
      const match = path.match(/^\/rpg-app\/api\/authoring\/works\/(work-[a-f0-9]{64})(?:\/(.*))?$/);
      if (!match) fail('AUTHORING_ROUTE_NOT_FOUND');
      const [, workId, action = ''] = match;
      if (req.method === 'GET') {
        query(url, action === 'export' ? ['kind', 'version'] : action.startsWith('assets/') ? ['version', 'hash'] : []);
        if (action === '') return send(await store.read(workId));
        if (action === 'validation') return send(await store.validate(workId));
        if (action === 'export') {
          const kind = url.searchParams.get('kind') || 'configuration', version = url.searchParams.get('version');
          if (!['configuration', 'package'].includes(kind)) fail('AUTHORING_EXPORT_KIND');
          return send(await (kind === 'package' ? store.exportPackage(workId, version) : store.exportConfiguration(workId, version)));
        }
        if (action.startsWith('assets/')) {
          const alias = decodeURIComponent(action.slice(7)), version = url.searchParams.get('version');
          const {asset, bytes} = await store.getAsset(workId, alias, version);
          if (url.searchParams.has('hash') && url.searchParams.get('hash') !== asset.sha256) fail('AUTHORING_ASSET_REVISION_CONFLICT');
          res.setHeader('Content-Security-Policy', "default-src 'none'; sandbox");
          res.setHeader('Content-Length', bytes.length);
          res.setHeader('ETag', '"' + asset.sha256 + '"');
          return send(bytes, 200, asset.mediaType);
        }
        fail('AUTHORING_ROUTE_NOT_FOUND');
      }
      query(url, []);
      const body = await jsonBody(req);
      if (action === 'save') return send(await store.save(workId, body));
      if (action === 'publish') return send(await store.publish(workId, body));
      if (action === 'copy') return send(await store.copy(workId, body));
      if (action === 'assets/remove') return send(await store.removeAsset(workId, body));
      if (action === 'assets/from-gallery') return send(await store.adoptGalleryImage(workId, body));
      if (action === 'assets') {
        payloadFields(body, ['operationId', 'expectedRevision', 'alias', 'fileName', 'mediaType', 'base64']);
        if (typeof body.base64 !== 'string' || body.base64.length > Math.ceil(LIMITS.audioBytes / 3) * 4) fail('AUTHORING_ASSET_SIZE');
        const bytes = Buffer.from(body.base64, 'base64');
        if (bytes.toString('base64') !== body.base64) fail('AUTHORING_BASE64_INVALID');
        const {base64, ...metadata} = body;
        return send(await store.putAsset(workId, {...metadata, bytes}));
      }
      fail('AUTHORING_ROUTE_NOT_FOUND');
    } catch (error) {
      if (!res.headersSent) { const result = safeError(error); send({error: result}, statusFor(result.code)); }
      else res.destroy();
    }
  });
  server.requestTimeout = 120000;
  server.headersTimeout = 15000;
  server.maxHeadersCount = 32;
  try {
    await new Promise((ok, no) => { server.once('error', no); server.listen(port, '127.0.0.1', ok); });
    origin = 'http://127.0.0.1:' + server.address().port;
  } catch (error) { clearInterval(scriptTimer);await comparisonHost?.close();await parameterPresets?.close();await turnPlayback?.close();await jointUI?.close();await summaryUI?.close();await historyUI?.close();await sessionHost?.close();await jointBudget?.close();await branchStore?.close();await historyControl?.close();await store.close();throw error; }
  let closePromise;
  return {
    origin,
    // Trusted offline harness only; no browser dispatch route is exposed.
    sessionHost,
    close() {
      closePromise ??= (async () => {
        clearInterval(scriptTimer);for(const token of scripts.keys())releaseScript(token);
        await new Promise((ok, no) => server.close(error => error ? no(error) : ok()));
        await comparisonHost?.close();await parameterPresets?.close();
        await jointUI?.close();
        await summaryUI?.close();
        await historyUI?.close();
        await sessionHost?.close();
        await turnPlayback?.close();
        await jointBudget?.close();await branchStore?.close();
        await historyControl?.close();
        await store.close();
      })();
      return closePromise;
    },
  };
}
if (process.argv[1] && import.meta.url === pathToFileURL(resolve(process.argv[1])).href) {
  const args = process.argv.slice(2);
  const flags = new Set(['--recover-dead-owner', '--creator']);
  const values = new Map();
  for (let i = 0; i < args.length; i++) {
    const option = args[i];
    if (flags.has(option)) continue;
    if (!['--directory', '--port'].includes(option) || values.has(option) ||
        !args[i + 1] || args[i + 1].startsWith('--')) throw new Error('Invalid authoring server option: ' + option);
    values.set(option, args[++i]);
  }
  const creatorExperience = args.includes('--creator');
  const instance = await startAuthoringServer({
    directory: values.has('--directory') ? resolve(values.get('--directory')) : undefined,
    port: Number(values.get('--port') ?? (creatorExperience ? 0 : 18524)),
    creatorExperience, recoverDeadOwner: args.includes('--recover-dead-owner'),
  });
  console.log(JSON.stringify({url: instance.origin, runtimeReady: false, providerCalls: 0}));
  let closing = false;
  for (const signal of ['SIGINT', 'SIGTERM']) process.on(signal, async () => {
    if (closing) return; closing = true;
    await instance.close(); process.exit(0);
  });
}

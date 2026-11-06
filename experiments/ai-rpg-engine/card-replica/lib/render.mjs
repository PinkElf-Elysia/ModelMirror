import {cardPresentation} from './card-presentation.mjs';
// A read-only display projection shared by the four archive cards.
export function safeHtml(raw){
 try{return cardPresentation(raw,'archive').html;}
 catch{return '<p class="presentation-error">此回复暂时无法展示，请使用下方“原文”查看保存的内容。</p>';}
}
export function displayDocument(raw){
 return `<!doctype html><html lang="zh-CN"><head><meta charset="UTF-8"><meta http-equiv="Content-Security-Policy" content="default-src 'none'; style-src 'unsafe-inline'; img-src 'none'; font-src 'none'; form-action 'none'; base-uri 'none'"><style>body{margin:16px;color:#243344;font:16px/1.9 system-ui;overflow-wrap:anywhere}details{margin:16px 0;padding:14px;border:1px solid #c4d4eb;border-radius:12px;background:#eef4ff}summary{cursor:pointer;font-weight:600}table{width:100%;table-layout:fixed;border-collapse:collapse}td,th{padding:10px;border:1px solid #d4dbea;overflow-wrap:anywhere}pre{white-space:pre-wrap}p{margin:1em 0}</style></head><body>${safeHtml(raw)}</body></html>`;
}

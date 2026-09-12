import { validateTurnExchange } from '../src/index.mjs';
import { hashText, hashValue } from './setup.mjs';
// Private evidence only. This observer never repairs, replaces, admits or commits output.
export function diagnoseOutput(report, request, cardPackage, transportEvidence = null) {
  const observed = transportEvidence?.outputValidation;
  const text = observed?.rawText ?? report?.value?.text;
  const transportPassed = report?.valid === true && report.value?.status === 'succeeded';
  const result = {
    format: 'modelmirror.ai-rpg.rpg05-output-diagnostics/1',
    rawSha256: typeof text === 'string' ? hashText(text) : null,
    rawCharacters: typeof text === 'string' ? text.length : null,
    transport: transportPassed ? 'passed' : 'failed',
    terminalEvidence: transportPassed ? 'inferred_from_frozen_adapter_stop_receipt_done_checks' : 'not_established',
    rawSseCaptured: false,
    json: 'not_checked', contract: 'not_checked', binding: 'not_checked', diagnostics: [],
  };
  if (observed) {
    const conversion = transportEvidence.conversion;
    return { ...result, transport: observed.transport, json: observed.json, wireSchema: observed.schema,
      contract: observed.contract, binding: observed.binding, diagnostics: structuredClone(observed.diagnostics),
      terminalEvidence: 'client_received_sse_observation', rawSseCaptured: Boolean(transportEvidence.rawBytes),
      ...(conversion ? { codecVersion: conversion.format, convertedSha256: conversion.convertedSha256 } : {}) };
  }
  if (!transportPassed) return result;
  if (typeof text !== 'string' || text.length > 1048576) {
    result.json = 'failed'; result.diagnostics.push({ code: 'OUTPUT_JSON_TEXT_INVALID', path: '' }); return result;
  }
  let parsed;
  try { parsed = JSON.parse(text); }
  catch { result.json = 'failed'; result.diagnostics.push({ code: 'OUTPUT_JSON_PARSE_FAILED', path: '' }); return result; }
  result.json = 'passed';
  if (!cardPackage) { result.contract = 'unavailable'; return result; }
  const validated = validateTurnExchange(parsed, cardPackage);
  result.contract = validated.valid ? 'passed' : 'failed';
  result.diagnostics = validated.diagnostics.map(({ code, path, relatedPath }) => ({ code, path, ...(relatedPath ? { relatedPath } : {}) }));
  if (!validated.valid) return result;
  result.binding = 'passed';
  for (const [key, expected] of [['exchangeId', request.exchangeId], ['cardPackageRef', { id: cardPackage.package.id, version: cardPackage.package.version }], ['input', request.input]]) {
    if (hashValue(parsed[key]) !== hashValue(expected)) { result.binding = 'failed'; result.diagnostics.push({ code: 'OUTPUT_BINDING_MISMATCH', path: '/' + key }); }
  }
  return result;
}

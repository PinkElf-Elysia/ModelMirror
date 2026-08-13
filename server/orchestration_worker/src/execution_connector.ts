import type {
  LLMConfig,
  LLMConnector,
  LLMResult,
} from '../../vendor/agency-orchestrator/src/types.js';

import { ModelResponseRouter } from './channel.js';
import {
  AGENCY_EXECUTION_PROTOCOL,
  AgencyBridgeError,
  MAX_EXECUTION_MODEL_CALLS,
  MAX_EXECUTION_OUTPUT_BYTES,
  asObject,
} from './protocol.js';

const COMPACT_DELIVERABLE_CONTRACT = `

ModelMirror execution output contract:
- Return a complete deliverable within 1,600 output tokens; do not try to consume the full token allowance.
- Prioritize required conclusions, evidence or labeled assumptions, concrete actions, and acceptance criteria.
- Omit repetition, generic background, and decorative exposition.
- Never invent human names, calendar dates, customer counts, budgets, performance targets, vendors, or infrastructure that are absent from the goal and dependency outputs.
- When required facts are missing, use role-based owners and explicit TBD/pending-confirmation placeholders. An acceptance criterion requesting a missing fact does not authorize fabrication.
- Finish the response cleanly; never trail off.`;

const COMPACT_DELIVERABLE_USER_REMINDER = `

Hard delivery constraints (treat these as acceptance requirements):
- Complete the deliverable in no more than 1,500 Chinese characters or 900 English words. Prefer compact tables and bullet points.
- Do not repeat the request or dependency outputs. Include only information needed to satisfy this step's acceptance criteria.
- Do not introduce names, vendors, dates, budgets, metrics, interfaces, or infrastructure absent from the supplied facts. Mark every necessary missing value as TBD/pending confirmation.
- End with a complete sentence or table row; never continue until the model limit.`;

const JSON_REVIEW_EVIDENCE_CONTRACT = `

ModelMirror acceptance-review evidence rules:
- Treat the authoritative original user request in the task as the source of confirmed facts.
- For every failed criterion, identify exact conflicting text or the exact required element that is absent after checking the entire deliverable, including headings and every table cell.
- Never claim that a literal label, value, fact, or section is absent when it appears in the relevant row, heading, or list item.
- A strict review must be evidence-based; do not fail a criterion on an unsupported assertion.`;

export class ExecutionBridgeConnector implements LLMConnector {
  calls = 0;
  readonly usage = { input_tokens: 0, output_tokens: 0 };

  constructor(
    private readonly router: ModelResponseRouter,
    private readonly bridgeRequestId: string,
    private readonly modelId: string,
    initial?: {
      calls?: number;
      usage?: { input_tokens?: number; output_tokens?: number };
    },
  ) {
    this.calls = finiteUsage(initial?.calls);
    this.usage.input_tokens = finiteUsage(initial?.usage?.input_tokens);
    this.usage.output_tokens = finiteUsage(initial?.usage?.output_tokens);
  }

  async chat(systemPrompt: string, userMessage: string, config: LLMConfig): Promise<LLMResult> {
    if (this.calls >= MAX_EXECUTION_MODEL_CALLS) {
      throw new AgencyBridgeError(
        'agency_execution_budget_exceeded',
        `agency_execution_budget_exceeded: Agency execution exceeded ${MAX_EXECUTION_MODEL_CALLS} model calls.`,
      );
    }
    this.calls += 1;
    const callNumber = this.calls;
    const modelRequestId = `${this.bridgeRequestId}:model:${callNumber}`;
    const jsonResponse = Number(config.temperature ?? 0.3) === 0;
    const requestedMaxTokens = Math.max(1, Number(config.max_tokens ?? 4096));
    const maxTokens = jsonResponse
      ? Math.min(2000, Math.max(1600, requestedMaxTokens))
      : Math.min(4096, requestedMaxTokens);
    const response = await this.router.request(modelRequestId, {
      protocol: AGENCY_EXECUTION_PROTOCOL,
      type: 'model_request',
      id: this.bridgeRequestId,
      request_id: modelRequestId,
      call_number: callNumber,
      model_id: this.modelId,
      messages: [
        {
          role: 'system',
          content: `${systemPrompt}${COMPACT_DELIVERABLE_CONTRACT}${
            jsonResponse ? JSON_REVIEW_EVIDENCE_CONTRACT : ''
          }`,
        },
        {
          role: 'user',
          content: jsonResponse
            ? userMessage
            : `${userMessage}${COMPACT_DELIVERABLE_USER_REMINDER}`,
        },
      ],
      temperature: jsonResponse ? 0 : 0.3,
      max_tokens: maxTokens,
      timeout_seconds: 240,
      json_response: jsonResponse,
    });

    if (response.ok !== true) {
      const error = asObject(response.error ?? {}, 'model_response_invalid');
      const code = typeof error.code === 'string' ? error.code : 'model_call_failed';
      const message = typeof error.message === 'string' ? error.message : 'Model call failed.';
      throw new AgencyBridgeError(code, `${code}: ${message}`);
    }
    const result = asObject(response.result ?? {}, 'model_response_invalid');
    if (typeof result.content !== 'string' || !result.content.trim()) {
      throw new AgencyBridgeError('model_response_invalid', 'Model response content is empty.');
    }
    if (Buffer.byteLength(result.content, 'utf8') > MAX_EXECUTION_OUTPUT_BYTES) {
      throw new AgencyBridgeError(
        'agency_execution_budget_exceeded',
        'agency_execution_budget_exceeded: Model response exceeds the 64 KiB execution output limit.',
      );
    }
    const usage = asObject(result.usage ?? {}, 'model_response_invalid');
    const inputTokens = finiteUsage(usage.input_tokens);
    const outputTokens = finiteUsage(usage.output_tokens);
    this.usage.input_tokens += inputTokens;
    this.usage.output_tokens += outputTokens;
    if (result.finish_reason === 'length') {
      throw new AgencyBridgeError(
        'model_output_truncated',
        'model_output_truncated: Model output reached the 4096-token limit. Retry only the failed steps after shortening their expected output.',
      );
    }
    return {
      content: result.content,
      usage: {
        input_tokens: inputTokens,
        output_tokens: outputTokens,
      },
    };
  }
}

function finiteUsage(value: unknown): number {
  return Number.isFinite(value) ? Math.max(0, Math.trunc(Number(value))) : 0;
}

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

export class ExecutionBridgeConnector implements LLMConnector {
  calls = 0;
  readonly usage = { input_tokens: 0, output_tokens: 0 };

  constructor(
    private readonly router: ModelResponseRouter,
    private readonly bridgeRequestId: string,
    private readonly modelId: string,
  ) {}

  async chat(systemPrompt: string, userMessage: string, config: LLMConfig): Promise<LLMResult> {
    this.calls += 1;
    if (this.calls > MAX_EXECUTION_MODEL_CALLS) {
      throw new AgencyBridgeError(
        'agency_execution_budget_exceeded',
        `agency_execution_budget_exceeded: Agency execution exceeded ${MAX_EXECUTION_MODEL_CALLS} model calls.`,
      );
    }
    const callNumber = this.calls;
    const modelRequestId = `${this.bridgeRequestId}:model:${callNumber}`;
    const response = await this.router.request(modelRequestId, {
      protocol: AGENCY_EXECUTION_PROTOCOL,
      type: 'model_request',
      id: this.bridgeRequestId,
      request_id: modelRequestId,
      call_number: callNumber,
      model_id: this.modelId,
      messages: [
        { role: 'system', content: systemPrompt },
        { role: 'user', content: userMessage },
      ],
      temperature: 0.3,
      max_tokens: Math.min(4096, Math.max(1, Number(config.max_tokens ?? 4096))),
      timeout_seconds: 180,
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

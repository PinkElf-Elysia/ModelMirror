import type {
  LLMConfig,
  LLMConnector,
  LLMResult,
} from '../../vendor/agency-orchestrator/src/types.js';

import { JsonlChannel } from './channel.js';
import {
  AGENCY_BRIDGE_PROTOCOL,
  AgencyBridgeError,
  asObject,
  MAX_MODEL_CALLS,
  MAX_PLANNING_OUTPUT_TOKENS,
} from './protocol.js';

export class BridgeConnector implements LLMConnector {
  calls = 0;
  readonly usage = { input_tokens: 0, output_tokens: 0 };

  constructor(
    private readonly channel: JsonlChannel,
    private readonly bridgeRequestId: string,
  ) {}

  async chat(systemPrompt: string, userMessage: string, config: LLMConfig): Promise<LLMResult> {
    this.calls += 1;
    if (this.calls > MAX_MODEL_CALLS) {
      throw new AgencyBridgeError('model_call_limit', 'Agency planning exceeded three model calls.');
    }
    const modelRequestId = `${this.bridgeRequestId}:model:${this.calls}`;
    const maxTokens = config.max_tokens ?? MAX_PLANNING_OUTPUT_TOKENS;
    this.channel.write({
      protocol: AGENCY_BRIDGE_PROTOCOL,
      type: 'model_request',
      id: this.bridgeRequestId,
      request_id: modelRequestId,
      model_id: config.model ?? '',
      messages: [
        { role: 'system', content: systemPrompt },
        { role: 'user', content: userMessage },
      ],
      temperature: config.temperature ?? 0.2,
      max_tokens: maxTokens,
    });

    const response = asObject(await this.channel.read());
    if (
      response.protocol !== AGENCY_BRIDGE_PROTOCOL
      || response.type !== 'model_response'
      || response.id !== this.bridgeRequestId
      || response.request_id !== modelRequestId
    ) {
      throw new AgencyBridgeError('model_response_invalid', 'Model response does not match the pending request.');
    }
    if (response.ok !== true) {
      const error = asObject(response.error ?? {}, 'model_response_invalid');
      throw new AgencyBridgeError(
        typeof error.code === 'string' ? error.code : 'model_call_failed',
        typeof error.message === 'string' ? error.message : 'Model call failed.',
      );
    }
    const result = asObject(response.result ?? {}, 'model_response_invalid');
    if (typeof result.content !== 'string' || !result.content.trim()) {
      throw new AgencyBridgeError('model_response_invalid', 'Model response content is empty.');
    }
    const usage = asObject(result.usage ?? {}, 'model_response_invalid');
    const inputTokens = Number.isFinite(usage.input_tokens) ? Math.max(0, Number(usage.input_tokens)) : 0;
    const outputTokens = Number.isFinite(usage.output_tokens) ? Math.max(0, Number(usage.output_tokens)) : 0;
    this.usage.input_tokens += inputTokens;
    this.usage.output_tokens += outputTokens;
    if (result.finish_reason === 'length') {
      throw new AgencyBridgeError(
        'model_output_truncated',
        `Planning output reached the ${maxTokens}-token limit. Split the goal into separate plans, then retry.`,
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

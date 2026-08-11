export const AGENCY_UPSTREAM_REVISION =
  'e3f69fdf9da8a4630edbb8abeb116893b983b57d' as const;
export const AGENCY_BRIDGE_PROTOCOL = 'mm-agency-bridge/v1' as const;
export const MAX_MESSAGE_BYTES = 2 * 1024 * 1024;
export const MAX_MODEL_CALLS = 3;

export class AgencyBridgeError extends Error {
  constructor(
    public readonly code: string,
    message: string,
  ) {
    super(message);
    this.name = 'AgencyBridgeError';
  }
}

export interface BridgeRequest {
  protocol: typeof AGENCY_BRIDGE_PROTOCOL;
  type: 'request';
  id: string;
  method: 'health' | 'compose' | 'validate';
  params: Record<string, unknown>;
}

export function asObject(value: unknown, code = 'worker_protocol_invalid'): Record<string, unknown> {
  if (!value || typeof value !== 'object' || Array.isArray(value)) {
    throw new AgencyBridgeError(code, 'Bridge message must be a JSON object.');
  }
  return value as Record<string, unknown>;
}

export function parseRequest(value: unknown): BridgeRequest {
  const message = asObject(value);
  if (message.protocol !== AGENCY_BRIDGE_PROTOCOL || message.type !== 'request') {
    throw new AgencyBridgeError('worker_protocol_invalid', 'Bridge protocol or message type is invalid.');
  }
  const id = typeof message.id === 'string' ? message.id.trim() : '';
  if (!id || id.length > 128) {
    throw new AgencyBridgeError('worker_request_invalid', 'Bridge request id is invalid.');
  }
  if (!['health', 'compose', 'validate'].includes(String(message.method))) {
    throw new AgencyBridgeError('worker_method_invalid', 'Bridge method is not supported.');
  }
  return {
    protocol: AGENCY_BRIDGE_PROTOCOL,
    type: 'request',
    id,
    method: message.method as BridgeRequest['method'],
    params: asObject(message.params ?? {}, 'worker_request_invalid'),
  };
}

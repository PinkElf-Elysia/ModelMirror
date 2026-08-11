/**
 * ModelMirror integration boundary.
 *
 * This file is not copied from upstream. It intentionally replaces the
 * upstream Provider Factory so the vendored orchestration core cannot load
 * provider SDKs, CLI integrations, or credentials. Runtime callers must
 * inject a connector factory explicitly.
 */
import type { LLMConfig, LLMConnector } from '../types.js';

export type ConnectorFactory = (config: LLMConfig) => LLMConnector;

let connectorFactory: ConnectorFactory | undefined;

export function setConnectorFactory(factory: ConnectorFactory): void {
  connectorFactory = factory;
}

export function resetConnectorFactory(): void {
  connectorFactory = undefined;
}

export function createConnector(config: LLMConfig): LLMConnector {
  if (!connectorFactory) {
    throw new Error(
      `MODELMIRROR_CONNECTOR_NOT_CONFIGURED: no connector was injected for ${config.provider}`,
    );
  }
  return connectorFactory(config);
}

export type RuntimeMiddlewareFieldType =
  | "text"
  | "textarea"
  | "select"
  | "boolean"
  | "number"
  | "json";

export interface RuntimeMiddlewareField {
  name: string;
  label: string;
  type: RuntimeMiddlewareFieldType;
  required?: boolean;
  default?: unknown;
  options?: string[];
  placeholder?: string;
  description?: string;
  min_value?: number;
  max_value?: number;
  minValue?: number;
  maxValue?: number;
  rows?: number;
}

export interface RuntimeMiddlewareNode {
  id: string;
  kind: string;
  title: string;
  description: string;
  category: string;
  icon: string;
  fields: RuntimeMiddlewareField[];
  enabled: boolean;
  tags?: string[];
  metadata?: Record<string, unknown>;
}

export interface RuntimeMiddlewareNodesResponse {
  nodes: RuntimeMiddlewareNode[];
}

export async function fetchRuntimeMiddlewareNodes(): Promise<RuntimeMiddlewareNode[]> {
  const response = await fetch("/api/runtime/middleware-nodes");
  if (!response.ok) {
    throw new Error(`Failed to fetch middleware nodes: ${response.status}`);
  }
  return response.json() as Promise<RuntimeMiddlewareNode[]>;
}

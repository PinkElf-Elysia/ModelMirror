import type { IncomingMessage, ServerResponse } from 'node:http';

export function isTypeMoonAsset(path: string): boolean;
export function serveTypeMoon(
  req: IncomingMessage, res: ServerResponse, path: string, origins?: string[],
): Promise<boolean>;

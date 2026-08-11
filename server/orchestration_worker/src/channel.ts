import type { Readable, Writable } from 'node:stream';

import { AgencyBridgeError, MAX_MESSAGE_BYTES } from './protocol.js';

type PendingRead = {
  resolve: (value: unknown) => void;
  reject: (reason: Error) => void;
};

export class JsonlChannel {
  private buffer = Buffer.alloc(0);
  private readonly lines: Buffer[] = [];
  private readonly pending: PendingRead[] = [];
  private terminalError: Error | undefined;
  private ended = false;

  constructor(
    private readonly input: Readable,
    private readonly output: Writable,
  ) {
    input.on('data', this.onData);
    input.on('end', this.onEnd);
    input.on('error', this.onError);
  }

  private readonly onData = (chunk: Buffer | string): void => {
    if (this.terminalError) return;
    const bytes = Buffer.isBuffer(chunk) ? chunk : Buffer.from(chunk, 'utf8');
    this.buffer = Buffer.concat([this.buffer, bytes]);
    while (true) {
      const newline = this.buffer.indexOf(0x0a);
      if (newline < 0) break;
      let line = this.buffer.subarray(0, newline);
      this.buffer = this.buffer.subarray(newline + 1);
      if (line.at(-1) === 0x0d) line = line.subarray(0, -1);
      if (line.length > MAX_MESSAGE_BYTES) {
        this.fail(new AgencyBridgeError('worker_message_too_large', 'Bridge message exceeds 2 MiB.'));
        return;
      }
      this.lines.push(line);
    }
    if (this.buffer.length > MAX_MESSAGE_BYTES) {
      this.fail(new AgencyBridgeError('worker_message_too_large', 'Bridge message exceeds 2 MiB.'));
      return;
    }
    this.flush();
  };

  private readonly onEnd = (): void => {
    this.ended = true;
    if (this.buffer.length > 0) {
      this.fail(new AgencyBridgeError('worker_protocol_invalid', 'Bridge message is missing a newline.'));
      return;
    }
    this.flush();
  };

  private readonly onError = (error: Error): void => this.fail(error);

  private fail(error: Error): void {
    this.terminalError = error;
    this.flush();
  }

  private flush(): void {
    while (this.pending.length > 0 && this.lines.length > 0) {
      const waiter = this.pending.shift()!;
      const line = this.lines.shift()!;
      try {
        waiter.resolve(JSON.parse(line.toString('utf8')));
      } catch {
        waiter.reject(new AgencyBridgeError('worker_invalid_json', 'Bridge message is not valid JSON.'));
      }
    }
    if (this.terminalError) {
      while (this.pending.length > 0) this.pending.shift()!.reject(this.terminalError);
      return;
    }
    if (this.ended && this.lines.length === 0) {
      const error = new AgencyBridgeError('worker_unexpected_eof', 'Bridge input ended unexpectedly.');
      while (this.pending.length > 0) this.pending.shift()!.reject(error);
    }
  }

  read(): Promise<unknown> {
    return new Promise((resolve, reject) => {
      this.pending.push({ resolve, reject });
      this.flush();
    });
  }

  write(message: unknown): void {
    const payload = Buffer.from(`${JSON.stringify(message)}\n`, 'utf8');
    if (payload.length > MAX_MESSAGE_BYTES) {
      throw new AgencyBridgeError('worker_response_too_large', 'Bridge response exceeds 2 MiB.');
    }
    this.output.write(payload);
  }

  close(): void {
    this.input.off('data', this.onData);
    this.input.off('end', this.onEnd);
    this.input.off('error', this.onError);
    this.input.pause();
  }
}

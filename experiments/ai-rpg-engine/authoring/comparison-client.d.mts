// React only consumes the pending-operation projection of this browser client.
export function unconfirmedOutsideComparison(operations: Record<string, {status: string}> | null | undefined, ownedIds?: string[], localPending?: boolean): boolean;

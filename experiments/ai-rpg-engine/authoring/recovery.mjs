// A rejected write can observe another operation, but never becomes that operation.
export function recoveryTarget(record) {
  const target = record.blockedBy || record;
  return {workId:target.workId ?? null,operationId:target.operationId};
}

export function blockedWorkRecord(record, blocker) {
  return {...structuredClone(record),serverOnly:true,
    blockedBy:{workId:blocker.workId ?? null,operationId:blocker.operationId}};
}

/** Resolve only the original ID; deciding whether to follow it must not discard another work. */
export async function resolveWorkRecovery({record,currentWorkId,api}) {
  const target = recoveryTarget(record);
  const result = await api('/operations/recover',target);
  if (result.status === 'not_found' || result.status === 'not_committed') return result;
  const receipt = result.receipt;
  if (result.status !== 'committed' || receipt?.operationId !== target.operationId ||
      typeof receipt.workId !== 'string' || !Number.isInteger(receipt.revision) ||
      (target.workId && receipt.workId !== target.workId)) throw {code:'NETWORK_RESULT_UNKNOWN'};
  const follows = !record.blockedBy && (
    currentWorkId === receipt.workId || (!record.serverOnly && currentWorkId === record.sourceId)
  );
  if (!follows) return {...result,view:'stay'};
  const work = await api('/works/'+receipt.workId);
  if (work.id !== receipt.workId || !Number.isInteger(work.revision) || work.revision < receipt.revision) {
    throw {code:'NETWORK_RESULT_UNKNOWN'};
  }
  return {...result,view:'apply',work};
}

// Store serialization orders object keys; that is not an author edit. Array order still matters.
export function documentsEqual(left,right) {
  if (left === right) return true;
  if (!left || !right || typeof left !== 'object' || typeof right !== 'object') return false;
  if (Array.isArray(left) !== Array.isArray(right)) return false;
  const a=Object.keys(left), b=Object.keys(right);
  return a.length === b.length && a.every(key => Object.hasOwn(right,key) && documentsEqual(left[key],right[key]));
}

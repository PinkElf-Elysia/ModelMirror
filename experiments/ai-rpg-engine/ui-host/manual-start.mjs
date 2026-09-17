// The same runtime owner excludes the old live host before a ledger cutover.
// A successful binding is append-only even if listening later fails.
export async function startManualHost({ qualify, acquireOwner, bindSource, listen }) {
  await qualify();
  const engine = await acquireOwner();
  try {
    const access = await bindSource();
    const host = await listen(engine, access);
    return { engine, access, host };
  } catch (cause) {
    await engine.close();
    throw cause;
  }
}

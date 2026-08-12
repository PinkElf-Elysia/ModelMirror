# @matrix-oasis/prototype-asset-contracts

Private R9 contract for the canonical Prototype Asset Bundle 0.1.0. It validates manifest bytes and internal identities/references only; filesystem bytes and GLB contents are verified by the R9 pipeline.

Public API:

- frozen format, schema and limit constants;
- `validatePrototypeAssetBundleJson(text)`;
- `PrototypeAssetContractOperationalError` with fixed code `PROTOTYPE_ASSET_CONTRACT_INTERNAL_ERROR`.

The contract never contains supplier task IDs, URLs, credentials, raw responses, prompts or scene placement coordinates.

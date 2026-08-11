#!/bin/sh
set -eu

SOURCE_ROOT="${1:-/source}"
OUTPUT_ROOT="${2:-/output}"
BUILD_ROOT=/tmp/modelmirror-upstream-build
DEPLOY_ROOT=/tmp/modelmirror-upstream-deploy
AUDIT_PATH=/tmp/modelmirror-upstream-audit.json

mkdir -p "$BUILD_ROOT/vendor/penguin_harness/packages" "$OUTPUT_ROOT"
cp "$SOURCE_ROOT/package.json" "$SOURCE_ROOT/pnpm-lock.yaml" "$SOURCE_ROOT/pnpm-workspace.yaml" "$BUILD_ROOT/"
cp -R "$SOURCE_ROOT/worker" "$BUILD_ROOT/"
cp -R "$SOURCE_ROOT/vendor/penguin_harness/packages/core" "$BUILD_ROOT/vendor/penguin_harness/packages/"
cp -R "$SOURCE_ROOT/vendor/penguin_harness/packages/skills" "$BUILD_ROOT/vendor/penguin_harness/packages/"
cp "$SOURCE_ROOT/vendor/penguin_harness/tsconfig.base.json" "$BUILD_ROOT/vendor/penguin_harness/"

corepack enable
corepack prepare pnpm@11.18.0 --activate
cd "$BUILD_ROOT"
pnpm install --frozen-lockfile
pnpm run build:upstream
pnpm --filter @modelmirror/upstream-workbench-worker deploy --prod "$DEPLOY_ROOT"

# pnpm returns a non-zero status when advisories exist; the generator applies
# the explicit high/critical policy after parsing the complete JSON response.
pnpm audit --prod --json > "$AUDIT_PATH" || true
node "$SOURCE_ROOT/provenance/generate_supply_chain.mjs" "$DEPLOY_ROOT" "$AUDIT_PATH" "$OUTPUT_ROOT"

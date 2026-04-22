#!/usr/bin/env bash
# vendor-validator.sh — Copy the validator engine source into vendor/
# for Docker builds where the sibling repo isn't available.
#
# Usage:  ./scripts/vendor-validator.sh [path-to-ip-claim-validator]
# Default: ../ip-claim-validator (sibling directory)
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
LEX_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
VALIDATOR_SRC="${1:-$(cd "$LEX_ROOT/.." && pwd)/ip-claim-validator}"
DEST="$LEX_ROOT/vendor/ip-claim-validator"

if [ ! -d "$VALIDATOR_SRC/src/validator" ]; then
    echo "ERROR: validator source not found at $VALIDATOR_SRC" >&2
    echo "Usage: $0 [path-to-ip-claim-validator]" >&2
    exit 1
fi

echo "Vendoring validator from: $VALIDATOR_SRC"
echo "                     to: $DEST"

# Clean previous vendor
rm -rf "$DEST"
mkdir -p "$DEST"

# Copy only what's needed for the build
cp "$VALIDATOR_SRC/pyproject.toml" "$DEST/"
cp -r "$VALIDATOR_SRC/src" "$DEST/"

# Reference YAML files (required at runtime by the reference loader)
mkdir -p "$DEST/docs"
cp "$VALIDATOR_SRC"/docs/nomoi-*.yaml "$DEST/docs/"

echo "Done — vendored $(find "$DEST/src" -name '*.py' | wc -l | tr -d ' ') Python files + $(ls "$DEST/docs/"*.yaml | wc -l | tr -d ' ') reference YAMLs"

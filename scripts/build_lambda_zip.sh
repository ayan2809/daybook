#!/usr/bin/env bash
# Build the Lambda zip (plan 12.4): arm64 wheels for Python 3.12 on Amazon Linux 2023 (glibc 2.34), the daybook package, schema and
# demo seed. No Docker: uv picks manylinux aarch64 wheels. Prints the zip path.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
OUT="$ROOT/build"
PKG="$OUT/lambda"
rm -rf "$PKG" "$OUT/daybook-lambda.zip"
mkdir -p "$PKG/seed"

cd "$ROOT/backend"
uv export --frozen --no-dev --no-hashes --no-emit-project -q -o "$OUT/requirements.txt"
# Compiled packages must come as aarch64 wheels. Pure-Python sdist-only packages (http-ece, from
# pywebpush) are installed separately. The export is fully pinned, so both steps use --no-deps.
PURE='^(http-ece)=='
grep -Ev "$PURE" "$OUT/requirements.txt" > "$OUT/requirements-wheels.txt"
grep -E "$PURE" "$OUT/requirements.txt" > "$OUT/requirements-pure.txt" || true
TARGET=(--target "$PKG" --python-platform aarch64-manylinux_2_34 --python-version 3.12)
uv pip install -q "${TARGET[@]}" --only-binary=:all: --no-deps -r "$OUT/requirements-wheels.txt"
# (no --python-platform here: uv won't build sdists for a foreign platform; pure Python is portable)
[ -s "$OUT/requirements-pure.txt" ] && uv pip install -q --target "$PKG" --python 3.12 --no-deps \
  -r "$OUT/requirements-pure.txt"

cp -R daybook "$PKG/daybook"
cp schema.sql "$PKG/schema.sql"  # memory/db.py reads it two levels above the module
cp "$ROOT"/data/seed/demo-memory.db "$ROOT"/data/seed/demo-persona.json "$PKG/seed/"
[ -f "$ROOT/data/seed/replay.json" ] && cp "$ROOT/data/seed/replay.json" "$PKG/seed/"

find "$PKG" -name "__pycache__" -type d -prune -exec rm -rf {} +
find "$PKG" -name "*.pyc" -delete

cd "$PKG"
zip -q -r -X "$OUT/daybook-lambda.zip" .
echo "unzipped: $(du -sh "$PKG" | cut -f1)  zipped: $(du -h "$OUT/daybook-lambda.zip" | cut -f1)" >&2
echo "$OUT/daybook-lambda.zip"

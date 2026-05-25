#!/usr/bin/env bash
set -euo pipefail

ENV_NAME="juxt-build-test"
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

cleanup() {
    echo ""
    echo "==> Removing conda environment '$ENV_NAME'..."
    conda env remove -n "$ENV_NAME" -y 2>/dev/null || true
}
trap cleanup EXIT

echo ""
echo "==> Creating conda environment '$ENV_NAME' (Python 3.12)..."
conda create -y -n "$ENV_NAME" python=3.12 -c conda-forge

echo ""
echo "==> Installing package and PyInstaller..."
conda run --no-capture-output -n "$ENV_NAME" pip install pyinstaller ".[ssh]"

echo ""
echo "==> Building with PyInstaller..."
conda run --no-capture-output -n "$ENV_NAME" pyinstaller --noconfirm juxt.spec

echo ""
echo "==> Generating sample images..."
conda run --no-capture-output -n "$ENV_NAME" python make_sample.py

echo ""
echo "==> Launching binary — close the window when done."
# Run the binary inside the .app directly so CLI args work.
# If macOS Gatekeeper blocks it, run: xattr -cr dist/juxt.app
./dist/juxt.app/Contents/MacOS/juxt sample_config.yaml

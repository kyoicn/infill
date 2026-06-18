#!/usr/bin/env bash
set -euo pipefail

VERSION=$(grep '"version"' extension/manifest.json | head -1 | sed -E 's/.*"version": *"([^"]+)".*/\1/')
OUT_DIR="release/extension"
OUT_ZIP="${OUT_DIR}/infill-xhs-scraper-v${VERSION}.zip"
mkdir -p "${OUT_DIR}"

(cd extension && zip -r "../${OUT_ZIP}" manifest.json background.js content_xhs.js README.md)

# Mirror to backend static dir so FastAPI's /static/extensions mount serves it.
STATIC_DIR="backend/static/extensions"
mkdir -p "${STATIC_DIR}"
cp -v "${OUT_ZIP}" "${STATIC_DIR}/"

echo ""
echo "Built:    ${OUT_ZIP}"
echo "Serving:  /static/extensions/infill-xhs-scraper-v${VERSION}.zip (after backend boot)"

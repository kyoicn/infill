#!/usr/bin/env bash
set -euo pipefail
VERSION=$(grep '"version"' extension/manifest.json | head -1 | sed -E 's/.*"version": *"([^"]+)".*/\1/')
OUT_DIR="release/extension"
OUT_ZIP="${OUT_DIR}/infill-xhs-scraper-v${VERSION}.zip"
mkdir -p "${OUT_DIR}"
cd extension
zip -r "../${OUT_ZIP}" manifest.json background.js content_xhs.js README.md
cd ..
echo "Built: ${OUT_ZIP}"

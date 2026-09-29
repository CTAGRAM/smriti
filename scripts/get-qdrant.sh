#!/usr/bin/env bash
# Downloads the Qdrant Server binary for this machine into ./bin.
set -euo pipefail
cd "$(dirname "$0")/.."
case "$(uname -s)-$(uname -m)" in
  Darwin-arm64) asset=qdrant-aarch64-apple-darwin.tar.gz ;;
  Darwin-x86_64) asset=qdrant-x86_64-apple-darwin.tar.gz ;;
  Linux-x86_64) asset=qdrant-x86_64-unknown-linux-gnu.tar.gz ;;
  Linux-aarch64) asset=qdrant-aarch64-unknown-linux-musl.tar.gz ;;
  *) echo "Unsupported platform; run Qdrant with Docker instead" >&2; exit 1 ;;
esac
mkdir -p bin
curl -fsSL "https://github.com/qdrant/qdrant/releases/download/v1.19.1/${asset}" | tar -xz -C bin
echo "Qdrant $(./bin/qdrant --version 2>/dev/null)"

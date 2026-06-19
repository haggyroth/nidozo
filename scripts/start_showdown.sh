#!/usr/bin/env bash
# Start the local Pokémon Showdown server (local / dev use).
# Run this in a separate terminal before any battle scripts.
# Requires node_modules: run `cd showdown && npm install` once after cloning.
# For production, use `docker compose up` instead.
set -euo pipefail

SHOWDOWN_DIR="$(dirname "$0")/../showdown"

if [ ! -f "$SHOWDOWN_DIR/pokemon-showdown" ]; then
  echo "Error: showdown/pokemon-showdown not found."
  echo "Run: cd showdown && npm install"
  exit 1
fi

if [ ! -d "$SHOWDOWN_DIR/node_modules" ]; then
  echo "Error: showdown/node_modules missing. Run: cd showdown && npm install"
  exit 1
fi

# Showdown requires config/config.js; create it from the vendored example.
if [ ! -f "$SHOWDOWN_DIR/config/config.js" ]; then
  echo "Creating showdown/config/config.js from config-example.js..."
  cp "$SHOWDOWN_DIR/config/config-example.js" "$SHOWDOWN_DIR/config/config.js"
fi

echo "Starting Pokémon Showdown on port 8000..."
node "$SHOWDOWN_DIR/pokemon-showdown" start --no-security

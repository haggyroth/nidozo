# Pokémon Showdown server — vendored source, compiled at image-build time.
#
# Showdown runs from compiled output in ./dist (require('./dist/server/index.js')).
# We therefore run `node build` during the image build so the server starts
# instantly and deterministically, rather than transpiling on first boot.
FROM node:22.12.0-slim

# netcat for the HEALTHCHECK; python3/make/g++ for native deps (better-sqlite3,
# sodium-native, etc.) that npm may need to compile when no prebuilt exists.
RUN apt-get update && apt-get install -y --no-install-recommends \
      netcat-openbsd python3 make g++ \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /showdown

# Install all dependencies (the build toolchain lives in deps, e.g. esbuild)
COPY package.json package-lock.json ./
RUN npm ci

# Copy the vendored source (dist/, node_modules/, test/ excluded via .dockerignore)
COPY . .

# Showdown requires config/config.js at runtime; the loader reads
# config-example.js for defaults. Generate config.js from the example, then
# compile TypeScript → dist/ so the server never has to build at runtime.
RUN cp config/config-example.js config/config.js && node build

# Showdown's REPL cleanup scans logs/repl on startup (config.repl defaults on);
# logs/ is excluded from the build context, so create the dir it expects.
RUN mkdir -p logs/repl

EXPOSE 8000

# Probe the WS port — Showdown has no HTTP health endpoint
HEALTHCHECK --interval=5s --timeout=3s --start-period=20s --retries=12 \
  CMD nc -z localhost 8000 || exit 1

# --skip-build: dist/ is already compiled above, never rebuild on boot
CMD ["node", "pokemon-showdown", "start", "--no-security", "--skip-build"]

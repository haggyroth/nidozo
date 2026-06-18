# Pokémon Showdown server — vendored source, pinned Node LTS
FROM node:22-slim

# nc (netcat) is needed by the HEALTHCHECK
RUN apt-get update && apt-get install -y --no-install-recommends netcat-openbsd && rm -rf /var/lib/apt/lists/*

WORKDIR /showdown

# Install production dependencies only, then copy source
COPY package.json package-lock.json ./
RUN npm ci --omit=dev

COPY . .

EXPOSE 8000

# Probe the WS port — Showdown has no HTTP health endpoint
HEALTHCHECK --interval=5s --timeout=3s --start-period=20s --retries=12 \
  CMD nc -z localhost 8000 || exit 1

CMD ["node", "pokemon-showdown", "start", "--no-security"]

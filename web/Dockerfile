# Rex Lite web (Next.js, standalone output). NEXT_PUBLIC_API is baked in at build time: it is the URL the BROWSER uses to reach the API.
FROM node:22-alpine AS deps
WORKDIR /app
COPY package.json package-lock.json ./
RUN npm ci --no-audit --no-fund

FROM node:22-alpine AS build
WORKDIR /app
ARG NEXT_PUBLIC_API=http://localhost:8000
ENV NEXT_PUBLIC_API=$NEXT_PUBLIC_API NEXT_TELEMETRY_DISABLED=1 REX_STANDALONE=1
COPY --from=deps /app/node_modules ./node_modules
COPY . .
RUN npm run build

FROM node:22-alpine AS run
WORKDIR /app
ENV NODE_ENV=production NEXT_TELEMETRY_DISABLED=1 PORT=3000 HOSTNAME=0.0.0.0
RUN addgroup -S rex && adduser -S rex -G rex
COPY --from=build --chown=rex:rex /app/.next/standalone ./
COPY --from=build --chown=rex:rex /app/.next/static ./.next/static
COPY --from=build --chown=rex:rex /app/public ./public
USER rex
EXPOSE 3000
HEALTHCHECK --interval=15s --timeout=5s --start-period=20s --retries=5 \
  CMD wget -qO- http://127.0.0.1:3000/ >/dev/null || exit 1
CMD ["node", "server.js"]

# syntax=docker/dockerfile:1
# The web app behind nginx, which also proxies /api (REST and the live WebSocket) to the API.
# Build context: the repository root (npm workspace and its lockfile): docker build -f apps/web/Dockerfile .
FROM node:24.22.1-alpine@sha256:06d7adf3e0ef61a13f1d28682726366f97a15aa1888c934d3ecec86c33918b53 AS build
WORKDIR /src
# Every workspace manifest, so npm ci installs exactly what package-lock.json pins.
COPY package.json package-lock.json tsconfig.base.json ./
COPY packages/api-client/package.json packages/api-client/
COPY apps/web/package.json apps/web/
COPY apps/mobile/package.json apps/mobile/
RUN --mount=type=cache,target=/root/.npm npm ci --no-audit --no-fund
COPY packages/api-client packages/api-client
COPY apps/web apps/web
RUN npm run build -w apps/web

FROM nginxinc/nginx-unprivileged:1.30-alpine@sha256:f5116ed4606f3eb015dd4a8e28cb9b351476dc0ad4bc6e9f93b19cec130cdf03
COPY apps/web/nginx/default.conf /etc/nginx/conf.d/default.conf
COPY --from=build /src/apps/web/dist /usr/share/nginx/html
EXPOSE 8080
HEALTHCHECK --interval=10s --timeout=3s --start-period=5s --retries=5 \
    CMD wget -q --spider http://127.0.0.1:8080/ || exit 1

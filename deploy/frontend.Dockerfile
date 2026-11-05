# syntax=docker/dockerfile:1
FROM node:22.23.4-bookworm-slim AS build
WORKDIR /build
COPY frontend/package.json frontend/package-lock.json ./
RUN --mount=type=cache,target=/root/.npm npm ci --no-audit --no-fund
COPY frontend/index.html frontend/tsconfig*.json frontend/vite.config.ts ./
COPY frontend/src ./src
COPY frontend/public ./public
RUN npm run build

FROM nginxinc/nginx-unprivileged:1.30.6-alpine AS runtime
COPY deploy/nginx.conf /etc/nginx/nginx.conf
COPY deploy/proxy_params_incidentflow /etc/nginx/proxy_params_incidentflow
COPY --from=build /build/dist /usr/share/nginx/html
USER 101:101
STOPSIGNAL SIGQUIT
ENTRYPOINT ["nginx"]
CMD ["-g", "daemon off;"]

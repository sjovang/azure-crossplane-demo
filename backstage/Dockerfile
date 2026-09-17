# Base Dockerfile for the Backstage backend (which also serves the built
# frontend as static assets). Modeled on the standard `@backstage/create-app`
# output. Build/push is a manual step for this base iteration -- see the
# repo README's "Developer Portal (Backstage)" section.
#
# Build (from the backstage/ directory):
#   docker build -t <your-registry>/backstage:latest .
# Push:
#   docker push <your-registry>/backstage:latest

FROM node:20-bookworm-slim AS build

WORKDIR /app
COPY . .
RUN npm install
RUN npm run build:backend

FROM node:20-bookworm-slim

WORKDIR /app
ENV NODE_ENV=production

COPY --from=build /app/packages/backend/dist /app/packages/backend/dist
COPY --from=build /app/node_modules /app/node_modules
COPY app-config.yaml app-config.production.yaml ./

EXPOSE 7007

CMD ["node", "packages/backend/dist/index.cjs.js", "--config", "app-config.yaml", "--config", "app-config.production.yaml"]

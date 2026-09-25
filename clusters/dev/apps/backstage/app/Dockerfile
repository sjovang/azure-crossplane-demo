# Base Dockerfile for the Backstage backend (which also serves the built
# frontend as static assets). Modeled on the standard `@backstage/create-app`
# output. Built locally with Apple's `container` CLI (already a repo
# prerequisite, see README.md) and loaded straight into the kiac cluster's
# nodes with `kiac load image` -- no registry needed. See this directory's
# README.md for the full command sequence.
#
# Build (from the clusters/dev/apps/backstage/app/ directory):
#   container build -t backstage:dev .
# Load into the kiac cluster's nodes (no push/registry required):
#   kiac load image backstage:dev --name dev

FROM node:22-bookworm-slim AS build

WORKDIR /app
COPY package.json package-lock.json backstage.json yarn.lock ./
COPY packages/app/package.json packages/app/package.json
COPY packages/backend/package.json packages/backend/package.json
RUN npm ci
COPY . .
RUN npm run build:backend

FROM node:22-bookworm-slim

WORKDIR /app
ENV NODE_ENV=production

COPY --from=build /app/packages/backend/dist/bundle.tar.gz /tmp/bundle.tar.gz
RUN tar -xzf /tmp/bundle.tar.gz -C /app && rm /tmp/bundle.tar.gz
COPY --from=build /app/node_modules /app/node_modules
COPY --from=build /app/packages/backend/node_modules /app/packages/backend/node_modules
COPY app-config.yaml app-config.production.yaml ./

EXPOSE 7007

CMD ["node", "packages/backend/dist/index.cjs.js", "--config", "app-config.yaml", "--config", "app-config.production.yaml"]

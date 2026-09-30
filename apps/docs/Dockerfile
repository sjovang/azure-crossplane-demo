# syntax=docker/dockerfile:1
#
# Build context is the REPOSITORY ROOT, not apps/docs:
#   docker build -f apps/docs/Dockerfile .
# The reference pages are generated from compositions/**/xrd.yaml and the
# examples are snippet-included from teams/, so both must be in the context.

FROM python:3.14-slim AS build

WORKDIR /repo

COPY apps/docs/requirements.txt apps/docs/requirements.txt
RUN pip install --no-cache-dir -r apps/docs/requirements.txt

COPY apps/docs apps/docs
COPY compositions compositions
COPY teams teams

WORKDIR /repo/apps/docs

# Canonical links and sitemap.xml are fixed at build time, so the public
# hostname cannot come from Flux substitution the way the manifests do.
ARG DOCS_SITE_URL=https://docs.demo.liasis.dev/
ENV DOCS_SITE_URL=${DOCS_SITE_URL}

# Zensical has no equivalent of mkdocs-gen-files, so the reference pages are
# generated as real files first. SUMMARY.md is the literate-nav source rather
# than a page, but it still renders as one, so drop it from the output.
RUN python scripts/gen_pages.py \
  && zensical build --strict \
  && rm -rf site/SUMMARY

FROM nginxinc/nginx-unprivileged:1.29-alpine

COPY apps/docs/nginx.conf /etc/nginx/conf.d/default.conf
COPY --from=build /repo/apps/docs/site /usr/share/nginx/html

EXPOSE 8080

# syntax=docker/dockerfile:1
#
# Build context is the REPOSITORY ROOT, not apps/docs:
#   docker build -f apps/docs/Dockerfile .
# The reference pages are generated from compositions/**/xrd.yaml and the
# examples are snippet-included from teams/, so both must be in the context.

FROM python:3.13-slim AS build

WORKDIR /repo

COPY apps/docs/requirements.txt apps/docs/requirements.txt
RUN pip install --no-cache-dir -r apps/docs/requirements.txt

COPY apps/docs apps/docs
COPY compositions compositions
COPY teams teams

WORKDIR /repo/apps/docs
# SUMMARY.md is the literate-nav source. mkdocs-gen-files writes it after
# mkdocs' own exclude_docs pass, so it renders as a stray page and has to be
# dropped here instead.
RUN mkdocs build --strict --site-dir /site \
  && rm -rf /site/SUMMARY

FROM nginxinc/nginx-unprivileged:1.29-alpine

COPY apps/docs/nginx.conf /etc/nginx/conf.d/default.conf
COPY --from=build /site /usr/share/nginx/html

EXPOSE 8080

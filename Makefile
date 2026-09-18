SHELL := /bin/bash

# COMPOSITION targets a single composition by directory, directory name, or
# composite kind. Leave unset to target every composition that has tests/.
#
# Examples:
#   make test
#   make test COMPOSITION=appservice
#   make test COMPOSITION=./compositions/azure/appservice
#   make test COMPOSITION=./compositions/azure/XAppService
#   make snapshot COMPOSITION=compositions/azure/postgresql
COMPOSITION ?=

.PHONY: test snapshot pre-commit

## Render each composition and diff the output against its stored snapshot.
## Requires the crossplane CLI and a running Docker daemon.
test:
	tests/run.sh test $(COMPOSITION)

## (Re)generate the golden snapshot(s) used by `make test`.
## Requires the crossplane CLI and a running Docker daemon.
snapshot:
	tests/run.sh snapshot $(COMPOSITION)

## Run the pre-commit hook suite across all files.
pre-commit:
	pre-commit run --all-files

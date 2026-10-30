# Crossplane project tasks. Each API is defined by apis/<provider>/<name>/schema.yaml
# (SimpleSchema); its definition.yaml is generated and must not be edited.

SHELL := bash
.SHELLFLAGS := -euo pipefail -c
.DEFAULT_GOAL := help

CROSSPLANE ?= crossplane
YQ ?= yq
PYTHON ?= python3
ALZ := apis/azure/landingzone

SCHEMAS := $(sort $(wildcard apis/*/*/schema.yaml))
DEFINITIONS := $(SCHEMAS:schema.yaml=definition.yaml)

.PHONY: help
help: ## Show available targets.
	@grep -E '^[a-z-]+:.*## ' $(MAKEFILE_LIST) | awk -F':.*## ' '{printf "  %-10s %s\n", $$1, $$2}'

.PHONY: tools
tools:
	@for tool in $(CROSSPLANE) $(YQ); do \
		command -v $$tool >/dev/null || { echo "missing required tool: $$tool" >&2; exit 1; }; \
	done

.PHONY: generate
generate: tools $(DEFINITIONS) ## Generate every definition.yaml from its schema.yaml.

# Drop patch comments, de-duplicate appended required lists, sort keys.
NORMALIZE := ... comments="" | (.. | select(tag == "!!map" and (.required | tag) == "!!seq") | .required) |= (unique | sort) | sort_keys(..)

# The CLI writes relative to apis/. SimpleSchema cannot express object-level
# CEL rules, object descriptions or nullable defaults, so an optional
# xrd-patch.yaml is deep-merged (lists appended) into the generated
# openAPIV3Schema. Always regenerate: file timestamps cannot detect a deleted
# xrd-patch.yaml, and generation is fast.
apis/%/definition.yaml: FORCE
	@$(CROSSPLANE) xrd generate --from simpleschema --replace \
		--path $*/definition.yaml apis/$*/schema.yaml >/dev/null
	@if [[ -f apis/$*/xrd-patch.yaml ]]; then \
		$(YQ) -i '.spec.versions[0].schema.openAPIV3Schema *=+ load("apis/$*/xrd-patch.yaml")' $@; \
	fi
	@{ echo '---'; \
		echo '# Generated from schema.yaml by `make generate`. Do not edit.'; \
		$(YQ) -P -I2 '$(NORMALIZE)' $@; } >$@.tmp && mv $@.tmp $@
	@echo "generated $@"

.PHONY: FORCE
FORCE:

.PHONY: clean
clean: ## Delete generated definition.yaml files.
	rm -f $(DEFINITIONS)

.PHONY: check
check: ## Regenerate and fail if any definition.yaml is out of date.
	@$(MAKE) --no-print-directory generate
	@$(PYTHON) $(ALZ)/generate.py --check
	@if ! git diff --exit-code -- $(DEFINITIONS) $(ALZ)/xrd-patch.yaml \
		|| [[ -n "$$(git ls-files --others --exclude-standard -- 'apis/*definition.yaml')" ]]; then \
		echo 'definition.yaml is out of date; run `make generate` and commit the result.' >&2; \
		exit 1; \
	fi

.PHONY: test
test: landingzone-prepare ## Run the composition tests in tests/.
	$(PYTHON) -m unittest discover -s tests

.PHONY: landingzone-prepare
landingzone-prepare: ## Restore locked ALZ inputs (network on first use) and compile.
	$(PYTHON) $(ALZ)/update_library.py --locked --if-missing
	$(PYTHON) $(ALZ)/generate.py

$(ALZ)/definition.yaml: landingzone-prepare

.PHONY: landingzone-artifact
landingzone-artifact: tools $(ALZ)/definition.yaml ## Build the ALZ Flux OCI artifact directory.
	$(PYTHON) $(ALZ)/package.py

# Composition documentation site

A [Material for MkDocs](https://squidfunk.github.io/mkdocs-material/) site that
documents the Crossplane compositions in `compositions/`.

Reference pages are **generated at build time** from the XRD and Composition
manifests, so they cannot drift from what the cluster actually serves. Nothing
generated is committed.

## Running locally

```sh
cd apps/docs
python3 -m venv .venv
./.venv/bin/pip install -r requirements.txt
./.venv/bin/mkdocs serve
```

Then open <http://127.0.0.1:8000>.

To check the build the way CI would:

```sh
./.venv/bin/mkdocs build --strict
```

## How pages are generated

`scripts/gen_pages.py` runs inside the MkDocs build via `mkdocs-gen-files`:

| Module | Responsibility |
| --- | --- |
| `xrdoc/load.py` | Discover `compositions/**/xrd.yaml`, `composition.yaml`, `docs.yaml` |
| `xrdoc/schema.py` | Walk `openAPIV3Schema` into flat field rows and CEL rules |
| `xrdoc/composed.py` | Work out which Azure resources a composition creates |
| `xrdoc/mermaid.py` | Build the pipeline diagram |
| `xrdoc/render.py` | Render `templates/reference.md.j2` |

The sidebar comes from a generated `SUMMARY.md` via `mkdocs-literate-nav`.

## Adding a new composition

Nothing is needed — a new directory under `compositions/` containing an
`xrd.yaml` and a `composition.yaml` is picked up automatically.

To add the prose the schema cannot supply, drop an optional `docs.yaml` next to
them:

```yaml
---
summary: One line, shown on the index and as the page lede.
stability: experimental
exampleRef: teams/crd-dev/network.yaml
exampleLines: "1-10"
exampleNotes: >-
  When and why to use this composition.
composedResources: []   # only needed when the pipeline is a Go template
commonErrors:
  - symptom: What the user sees.
    cause: Why it happens.
    fix: What to do about it.
```

`composedResources` is only required for compositions built with
`function-go-templating`, where the resources live inside a template string and
cannot be read from the manifest. `function-patch-and-transform` and
`function-kro` pipelines are derived automatically.

A `docs.yaml` is invisible to Flux: each composition's `kustomization.yaml`
lists `xrd.yaml` and `composition.yaml` explicitly.

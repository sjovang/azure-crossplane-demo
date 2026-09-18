# Composition render tests

Synthetic, golden-snapshot tests for the App Service, Container Apps, and
PostgreSQL compositions using [`crossplane render`](https://docs.crossplane.io/latest/cli/command-reference/#render).
`crossplane render` runs a Composition's function pipeline locally (via
Docker) and prints the resources it would create, without needing a real
Kubernetes cluster or Azure credentials.

## Requirements

- The `crossplane` CLI (`crossplane version`)
- A running Docker daemon (used to execute the Composition Functions)

## Running

Use the `Makefile` in the repo root, either for all compositions or a
single one (by name or by path):

```sh
make test                                          # test all compositions
make test COMPOSITION=appservice                    # test one, by name
make test COMPOSITION=./compositions/azure/appservice # test one, by path
make test COMPOSITION=./compositions/azure/XAppService # test one, by XR kind

make snapshot                                       # (re)generate all snapshots
make snapshot COMPOSITION=containerapp               # (re)generate one
```

Or call `tests/run.sh` directly:
`tests/run.sh <snapshot|test> [composition ...]`.

Each composition owns its test cases:

```text
compositions/azure/<name>/
├── composition.yaml
├── xrd.yaml
└── tests/
    ├── xr.yaml
    └── snapshot.yaml
```

The runner renders `tests/xr.yaml` against the sibling `composition.yaml`
using the function packages in `tests/functions.yaml`, then diffs the output
against `tests/snapshot.yaml`. Run `make snapshot` after intentionally
changing a composition's output, review the diff, and commit the updated
snapshot alongside the composition change.

`tests/functions.yaml` mirrors
`clusters/dev/crossplane/functions/*.yaml`; keep it in sync by hand if a
composition starts using a new function.

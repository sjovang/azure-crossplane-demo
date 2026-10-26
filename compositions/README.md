# Platform APIs

A single [Crossplane CLI](https://github.com/crossplane/cli) project
(`crossplane-project.yaml`) holding every shared XRD and Composition. Flux
applies `apis/` through `kustomization.yaml`.

```text
apis/<provider>/<name>/
  schema.yaml        # SimpleSchema API definition (source of truth)
  xrd-patch.yaml     # optional, what SimpleSchema cannot express
  definition.yaml    # generated XRD, committed, never edited by hand
  composition.yaml
  kustomization.yaml # definition.yaml + composition.yaml
  catalog-info.yaml  # Backstage API entity
  docs.yaml          # docs site prose
tests/               # composition tests and fixtures
```

## Make targets

Requires `crossplane` (v2.5+), `yq` v4, `python3` with PyYAML and `go`.

```sh
make generate # schema.yaml (+ xrd-patch.yaml) -> definition.yaml
make check    # regenerate and fail if anything changed
make test     # run tests/
```

## Defining an API

`schema.yaml` uses [SimpleSchema](https://kro.run/docs/concepts/simple-schema):

```yaml
---
apiVersion: azure.platform.example.org/v1alpha1
kind: XExample
customTypes:
  Rule:
    name: string | required=true
spec:
  name: string | required=true immutable=true description="Name."
  size: string | enum="small,medium,large" default="small"
  tags: map[string]string
  rules: "[]Rule"
  resourceGroupRef:
    name: string | required=true
status:
  id: string | description="Azure resource ID."
```

Generated XRDs are always `Namespaced`. SimpleSchema cannot express object
descriptions, object-level CEL rules, `nullable`, array item constraints or
that an object field is required. Put those in `xrd-patch.yaml`, which is
deep-merged into the generated `openAPIV3Schema` with lists appended:

```yaml
---
properties:
  spec:
    required:
      - resourceGroupRef
    properties:
      resourceGroupRef:
        description: Resource group hosting the resource.
      tags:
        nullable: true
        default: null
```

Wrap long markers in a folded scalar (`>-`) to stay within the line limit, and
quote YAML 1.1 boolean-like keys such as `n` or `on`.

To add an API, create the directory, run `make generate`, add a
`kustomization.yaml` and list the directory in `apis/<provider>/kustomization.yaml`.

## Entra entitlement management

`apis/entraid/accesspackagecatalog/` creates a catalog and can grant the
built-in Catalog Reader role to groups. `apis/entraid/accesspackage/` creates
an access package, adds the requested groups as catalog resources and package
resources, and configures its assignment policy. Create groups with
`XSecurityGroup` first and use each group's `status.objectId` in
`readerGroupIds`, `groupIds`, and the approver group ID fields. Catalog write
roles are not assigned because Crossplane manages the catalog. For guest
requestors, set the catalog's `externallyVisible` to `true` and use
`AllExternalSubjects`; internal requests use
`AllExistingDirectoryMemberUsers`. The primary group approves requests first,
the secondary group is used after seven days without a response, and both
groups review assignments quarterly. Escalation, request timeout, and review
duration can be overridden per package; defaults are 7, 14, and 14 days.
Assignments are removed if reviewers miss the review window.

For a group whose membership is granted through an access package, set
`membershipMode: External` on its `XSecurityGroup` and leave `members` empty.
This omits the provider's `members` configuration and disables late
initialization for that managed Group, so Crossplane does not reclaim members
added through entitlement assignments. The default `Declared` mode continues
to manage members from `spec.members`.

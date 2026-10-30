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

VM readiness regression tests run the pinned kro, go-templating and
auto-ready functions locally. They reproduce the NIC-only false-ready case
and cover missing dependencies, pending/ready VMs and both OS branches.
Docker, `crossplane`, `kubectl` and read access to the three managed-resource
CRDs are required. Only CRD schemas are read from the named cluster; no
live Secrets are read and nothing is applied:

```sh
cd compositions
# Set DOCKER_HOST to your Docker context endpoint if needed (e.g. Colima).
RUN_VIRTUALMACHINE_RENDER=1 KUBE_CONTEXT=aks-azure-crossplane-demo-admin \
  python3 -m unittest discover -s tests -p test_virtualmachine_render.py
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

For unique scalar arrays, use `x-kubernetes-list-type: set`, not
`uniqueItems: true` (Kubernetes rejects it in CRD schemas).

Wrap long markers in a folded scalar (`>-`) to stay within the line limit, and
quote YAML 1.1 boolean-like keys such as `n` or `on`.

To add an API, create the directory, run `make generate`, add a
`kustomization.yaml` and list the directory in `apis/<provider>/kustomization.yaml`.

## Azure landing zones

[`XLandingZoneArchitecture`](apis/azure/landingzone/README.md) deploys the
default ALZ hierarchy or a custom camelCase YAML hierarchy, built-in
archetypes projected to audit/deny-only policies, observed management-group
IDs, and attachment of existing subscriptions. Upstream content is pinned and
compiled offline.

[`XManagementSubscription`](apis/environments/managementsubscription/README.md)
follows `XWebApplication`'s orchestration pattern: it composes separate
`XSubscription`, `XResourceGroup`, `XLogAnalyticsWorkspace`,
`XUserAssignedIdentity`, `XDataCollectionRule`, and `XDiagnosticSetting` XRs.
Those resource-level compositions are independently reusable.
The imported subscription is never created or deleted, and credentials are
reused through an automatically targeted ProviderConfig.
Secret-based credentials must already be in the XR's namespace; cross-namespace
Secret sources are rejected rather than silently targeting an unavailable
Secret. The composition never copies credential data.

New composition fixtures live beside their API in `tests/`; central discovery
loads them. Both top-level APIs require foreground teardown for deletion
sequencing. Their examples disable Flux pruning rather than relying on
background deletion. See their READMEs for permission requirements and the
monitoring/policy limitations.

## Entra entitlement management

`apis/entraid/accesspackagecatalog/` creates a catalog and can grant the
built-in Catalog Reader role to groups. It registers each `groupIds` or `groupRefs` entry
once as a shared catalog resource and publishes its provider ID and readiness
in `status.groupAssociations`. `apis/entraid/accesspackage/` creates an access
package and its assignment policy. Set `catalogRef.name` to the catalog's
Kubernetes name in the **same namespace**, then select any subset of the
catalog's groups in the package's `groupIds` or `groupRefs`. Packages read the catalog
through read-only references; they own only their package-level
associations, not the shared catalog associations. Set
`primaryApproverGroupRef.name` and `secondaryApproverGroupRef.name` to
same-namespace `XSecurityGroup` names; the composition resolves their object
IDs from status. Catalog/package `groupRefs` also resolve same-namespace
`XSecurityGroup.status.objectId` values, once the group is Ready. For example,
both catalog and package can select the demo group without a tenant-specific UUID:

```yaml
---
spec:
  groupRefs:
    - name: crd-dev-access-demo
```

The complete GitOps example is in [`../teams/crd-dev/`](../teams/crd-dev/).
Catalog `readerGroupIds` and catalog/package `groupIds` still take object IDs.
IDs and references can be mixed; duplicate IDs are included only once,
case-insensitively. Approver references never grant package resources.
Catalog write
roles are not assigned because Crossplane manages the catalog. For guest
requestors, set the catalog's `externallyVisible` to `true` and use
`AllExternalSubjects`; internal requests use
`AllExistingDirectoryMemberUsers`. The primary group approves requests first,
the secondary group is used after seven days without a response, and both
groups review assignments quarterly. Escalation, request timeout, and review
duration can be overridden per package; defaults are 7, 14, and 14 days.
Assignments are removed if reviewers miss the review window.

See [`examples/entraid-accesspackages.yaml`](examples/entraid-accesspackages.yaml)
for one catalog registering a group once and two packages including that same
group. Replace the example UUIDs with actual `XSecurityGroup.status.objectId`
values. Apply the catalog first and wait for its `status.groupAssociations`
entries to have nonempty `id` and `ready: true`. Missing or unready group
references and pending catalog associations keep the package not Ready.
Leaving both package lists empty is supported.
The association steps use Go templating for dynamic required-resource requests,
which kro v0.3.0 cannot express as a list of name references. While dependencies
are pending, they preserve existing resources and report
`GroupResourcesReady=False`, rather than pruning associations or claiming Ready.
Existing composed-resource keys and catalog ownership are unchanged; switching
between an object ID and a reference to the same group needs no migration.

Deleting a package or removing one of its `groupIds`/`groupRefs` deletes only its
package-level associations. Before removing a group from the catalog, remove
it from every package that uses it; deleting the catalog deletes the shared
associations. Do not create multiple catalogs for the same existing provider
catalog, or register the same catalog/group pair outside its owning catalog.

This experimental API replaces package `catalogId` with `catalogRef.name`.
For already deployed packages, do not simply apply the ownership change:
pause reconciliation of both catalog and package XRs, orphan the old
package-owned catalog associations
(`deletionPolicy: Orphan`), and remove those managed resources before importing
the existing provider associations under the catalog's new managed resource
names using `crossplane.io/external-name`. Resume only after the catalog
reports the imported IDs. This avoids deleting shared provider associations
or attempting to create duplicates during migration.

Run the real function regression tests (Docker, `crossplane` and authenticated
`gh` required; downloads the pinned provider CRDs):

```sh
cd compositions
# Set DOCKER_HOST to your Docker context endpoint if needed (e.g. Colima).
RUN_ACCESSPACKAGE_RENDER=1 python3 -m unittest discover \
  -s tests -p test_accesspackages_render.py
RUN_SECURITYGROUP_RENDER=1 python3 -m unittest discover \
  -s tests -p test_securitygroup_render.py
```

For a group whose membership is granted through an access package, set
`membershipMode: External` on its `XSecurityGroup` and leave `members` empty.
This omits the provider's `members` configuration and disables late
initialization for that managed Group, so Crossplane does not reclaim members
added through entitlement assignments. The default `Declared` mode continues
to manage members from `spec.members`.

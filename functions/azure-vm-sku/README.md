# Azure VM SKU Composition Function

This Go Composition Function validates the effective `XVirtualMachine` SKU
against the Azure Compute Resource SKUs API for the XR's region and configured
subscription. It runs before the VM Composition resources are emitted. A
missing SKU, unavailable SKU, or failed lookup blocks the pipeline and sets the
`AzureVmSkuReady` condition to `False`.

The function reads the bootstrap-created `azure-secret` from the
`crossplane-system` namespace through its `DeploymentRuntimeConfig`. The
credential is used only to request an Azure Resource Manager token and is never
copied into XR status or logged. The SKU API result is a point-in-time
subscription/region check; it does not reserve capacity or guarantee a later
deployment will succeed.

Run its unit tests with:

```sh
go test ./...
```

The GitHub Actions workflow builds the runtime image, packages it as a
Crossplane Function, and publishes the version pinned in
`clusters/base/crossplane/functions/azure-vm-sku.yaml`. When releasing a
changed function, update that package version and the matching dependency in
`compositions/crossplane-project.yaml`.

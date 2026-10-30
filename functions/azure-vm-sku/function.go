package main

import (
	"context"
	"errors"
	"fmt"
	"strings"

	fnv1 "github.com/crossplane/function-sdk-go/proto/v1"
	"github.com/crossplane/function-sdk-go/request"
	"github.com/crossplane/function-sdk-go/response"
)

const (
	conditionType     = "AzureVmSkuReady"
	defaultLocation   = "swedencentral"
	defaultTShirtSize = "small"
)

type skuValidator interface {
	IsAvailable(ctx context.Context, location, sku string) (bool, error)
}

// Function checks the requested Azure VM SKU before later pipeline steps
// create any composed resources.
type Function struct {
	fnv1.UnimplementedFunctionRunnerServiceServer
	validator skuValidator
}

func (f *Function) RunFunction(ctx context.Context, req *fnv1.RunFunctionRequest) (*fnv1.RunFunctionResponse, error) {
	rsp := response.To(req, response.DefaultTTL)

	xr, err := request.GetObservedCompositeResource(req)
	if err != nil {
		return f.reject(rsp, "InvalidInput", "Could not read the XVirtualMachine resource."), nil
	}

	spec, ok := xr.Resource.Object["spec"].(map[string]interface{})
	if !ok {
		return f.reject(rsp, "InvalidInput", "XVirtualMachine spec is missing or invalid."), nil
	}

	sku, err := effectiveSKU(spec)
	if err != nil {
		return f.reject(rsp, "InvalidInput", err.Error()), nil
	}
	location := stringField(spec, "location")
	if location == "" {
		location = defaultLocation
	}
	if f.validator == nil {
		return f.reject(rsp, "LookupFailed", "Azure VM SKU validation is not configured."), nil
	}

	available, err := f.validator.IsAvailable(ctx, location, sku)
	if err != nil {
		message := fmt.Sprintf(
			"Could not validate Azure VM SKU %q in region %q; check Azure credentials and Compute SKU API access.",
			sku,
			location,
		)
		return f.reject(rsp, "LookupFailed", message), nil
	}
	if !available {
		message := fmt.Sprintf(
			"Azure VM SKU %q is not available for virtual machines in region %q for the configured subscription.",
			sku,
			location,
		)
		return f.reject(rsp, "SkuUnavailable", message), nil
	}

	response.ConditionTrue(rsp, conditionType, "Available").
		WithMessage(fmt.Sprintf("Azure VM SKU %q is available in region %q.", sku, location)).
		TargetCompositeAndClaim()
	return rsp, nil
}

func (f *Function) reject(rsp *fnv1.RunFunctionResponse, reason, message string) *fnv1.RunFunctionResponse {
	response.ConditionFalse(rsp, conditionType, reason).
		WithMessage(message).
		TargetCompositeAndClaim()
	response.Fatal(rsp, errors.New(message))
	return rsp
}

func effectiveSKU(spec map[string]interface{}) (string, error) {
	if override, exists := spec["azureVmSku"]; exists {
		sku, ok := override.(string)
		if !ok || strings.TrimSpace(sku) == "" {
			return "", errors.New("spec.azureVmSku must be a non-empty Azure VM SKU when set.")
		}
		return strings.TrimSpace(sku), nil
	}

	size := stringField(spec, "size")
	if size == "" {
		size = defaultTShirtSize
	}
	switch size {
	case "small":
		return "Standard_B2s", nil
	case "medium":
		return "Standard_B4ms", nil
	case "large":
		return "Standard_B8ms", nil
	default:
		return "", errors.New("spec.size must be one of small, medium, or large.")
	}
}

func stringField(object map[string]interface{}, key string) string {
	value, _ := object[key].(string)
	return strings.TrimSpace(value)
}

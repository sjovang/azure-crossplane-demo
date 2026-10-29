package main

import (
	"context"
	"errors"
	"strings"
	"testing"

	fnv1 "github.com/crossplane/function-sdk-go/proto/v1"
	"github.com/crossplane/function-sdk-go/resource"
)

type fakeSKUValidator struct {
	available bool
	err       error
	location  string
	sku       string
	calls     int
}

func (f *fakeSKUValidator) IsAvailable(_ context.Context, location, sku string) (bool, error) {
	f.calls++
	f.location = location
	f.sku = sku
	return f.available, f.err
}

func TestEffectiveSKU(t *testing.T) {
	tests := []struct {
		name string
		spec map[string]interface{}
		want string
		err  bool
	}{
		{name: "explicit override", spec: map[string]interface{}{
			"azureVmSku": "  Standard_D2s_v5 ", "size": "large",
		}, want: "Standard_D2s_v5"},
		{name: "small fallback", spec: map[string]interface{}{"size": "small"}, want: "Standard_B2s"},
		{name: "medium fallback", spec: map[string]interface{}{"size": "medium"}, want: "Standard_B4ms"},
		{name: "large fallback", spec: map[string]interface{}{"size": "large"}, want: "Standard_B8ms"},
		{name: "default fallback", spec: map[string]interface{}{}, want: "Standard_B2s"},
		{name: "empty override", spec: map[string]interface{}{"azureVmSku": "  "}, err: true},
		{name: "unknown t-shirt size", spec: map[string]interface{}{"size": "xlarge"}, err: true},
	}

	for _, test := range tests {
		t.Run(test.name, func(t *testing.T) {
			got, err := effectiveSKU(test.spec)
			if (err != nil) != test.err {
				t.Fatalf("effectiveSKU() error = %v, want error %t", err, test.err)
			}
			if got != test.want {
				t.Fatalf("effectiveSKU() = %q, want %q", got, test.want)
			}
		})
	}
}

func TestRunFunction(t *testing.T) {
	tests := []struct {
		name            string
		spec            string
		available       bool
		validatorErr    error
		wantSKU         string
		wantLocation    string
		wantCondition   fnv1.Status
		wantReason      string
		wantFatalResult bool
		wantCalls       int
	}{
		{
			name:          "valid override takes precedence",
			spec:          `{"azureVmSku":"Standard_D2s_v5","size":"large","location":"northeurope"}`,
			available:     true,
			wantSKU:       "Standard_D2s_v5",
			wantLocation:  "northeurope",
			wantCondition: fnv1.Status_STATUS_CONDITION_TRUE,
			wantReason:    "Available",
			wantCalls:     1,
		},
		{
			name:          "legacy size is validated",
			spec:          `{"size":"medium"}`,
			available:     true,
			wantSKU:       "Standard_B4ms",
			wantLocation:  defaultLocation,
			wantCondition: fnv1.Status_STATUS_CONDITION_TRUE,
			wantReason:    "Available",
			wantCalls:     1,
		},
		{
			name:            "unavailable SKU blocks composition",
			spec:            `{"azureVmSku":"Standard_D2s_v5","location":"northeurope"}`,
			wantSKU:         "Standard_D2s_v5",
			wantLocation:    "northeurope",
			wantCondition:   fnv1.Status_STATUS_CONDITION_FALSE,
			wantReason:      "SkuUnavailable",
			wantFatalResult: true,
			wantCalls:       1,
		},
		{
			name:            "lookup error blocks composition",
			spec:            `{"azureVmSku":"Standard_D2s_v5"}`,
			validatorErr:    errors.New("sensitive underlying error"),
			wantSKU:         "Standard_D2s_v5",
			wantLocation:    defaultLocation,
			wantCondition:   fnv1.Status_STATUS_CONDITION_FALSE,
			wantReason:      "LookupFailed",
			wantFatalResult: true,
			wantCalls:       1,
		},
		{
			name:            "empty SKU override blocks composition",
			spec:            `{"azureVmSku":" "}`,
			wantCondition:   fnv1.Status_STATUS_CONDITION_FALSE,
			wantReason:      "InvalidInput",
			wantFatalResult: true,
		},
	}

	for _, test := range tests {
		t.Run(test.name, func(t *testing.T) {
			validator := &fakeSKUValidator{available: test.available, err: test.validatorErr}
			fn := &Function{validator: validator}
			rsp := runFunction(t, fn, test.spec)

			conditions := rsp.GetConditions()
			if len(conditions) != 1 {
				t.Fatalf("got %d conditions, want 1", len(conditions))
			}
			if conditions[0].GetType() != conditionType {
				t.Fatalf("condition type = %q, want %q", conditions[0].GetType(), conditionType)
			}
			if conditions[0].GetStatus() != test.wantCondition {
				t.Fatalf("condition status = %v, want %v", conditions[0].GetStatus(), test.wantCondition)
			}
			if conditions[0].GetReason() != test.wantReason {
				t.Fatalf("condition reason = %q, want %q", conditions[0].GetReason(), test.wantReason)
			}

			gotFatal := false
			for _, result := range rsp.GetResults() {
				if result.GetSeverity() == fnv1.Severity_SEVERITY_FATAL {
					gotFatal = true
					if test.validatorErr != nil && strings.Contains(result.GetMessage(), "sensitive underlying error") {
						t.Fatal("fatal result exposed the underlying validator error")
					}
				}
			}
			if gotFatal != test.wantFatalResult {
				t.Fatalf("fatal result = %t, want %t", gotFatal, test.wantFatalResult)
			}
			if validator.calls != test.wantCalls {
				t.Fatalf("validator calls = %d, want %d", validator.calls, test.wantCalls)
			}
			if validator.calls > 0 {
				if validator.sku != test.wantSKU || validator.location != test.wantLocation {
					t.Fatalf("validated %q in %q, want %q in %q",
						validator.sku, validator.location, test.wantSKU, test.wantLocation)
				}
			}
		})
	}
}

func runFunction(t *testing.T, fn *Function, spec string) *fnv1.RunFunctionResponse {
	t.Helper()
	xr := resource.MustStructJSON(`{
		"apiVersion":"azure.platform.example.org/v1alpha1",
		"kind":"XVirtualMachine",
		"spec":` + spec + `
	}`)
	rsp, err := fn.RunFunction(context.Background(), &fnv1.RunFunctionRequest{
		Observed: &fnv1.State{
			Composite: &fnv1.Resource{Resource: xr},
		},
	})
	if err != nil {
		t.Fatalf("RunFunction() error = %v", err)
	}
	return rsp
}

package main

import (
	"context"
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"strings"
	"testing"
	"time"

	"github.com/Azure/azure-sdk-for-go/sdk/azcore"
	"github.com/Azure/azure-sdk-for-go/sdk/azcore/policy"
)

type fakeTokenCredential struct{}

func (fakeTokenCredential) GetToken(context.Context, policy.TokenRequestOptions) (azcore.AccessToken, error) {
	return azcore.AccessToken{Token: "test-token", ExpiresOn: time.Now().Add(time.Hour)}, nil
}

func TestAzureSKUClientListsRegionalSKUs(t *testing.T) {
	var server *httptest.Server
	server = httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if r.Header.Get("Authorization") != "Bearer test-token" {
			t.Errorf("Authorization = %q, want bearer token", r.Header.Get("Authorization"))
		}
		if r.URL.Path != "/subscriptions/subscription/providers/Microsoft.Compute/skus" &&
			r.URL.Path != "/next" {
			t.Errorf("unexpected path %q", r.URL.Path)
		}

		if r.URL.Path == "/subscriptions/subscription/providers/Microsoft.Compute/skus" {
			if r.URL.Query().Get("api-version") != computeSKUsAPIVersion {
				t.Errorf("api-version = %q, want %q", r.URL.Query().Get("api-version"), computeSKUsAPIVersion)
			}
			if r.URL.Query().Get("$filter") != "location eq 'northeurope'" {
				t.Errorf("$filter = %q, want location filter", r.URL.Query().Get("$filter"))
			}
			_ = json.NewEncoder(w).Encode(skuPage{
				Value: []resourceSKU{{
					Name:         "Standard_D2s_v5",
					ResourceType: "virtualMachines",
					Locations:    []string{"northeurope"},
				}},
				NextLink: server.URL + "/next",
			})
			return
		}
		_ = json.NewEncoder(w).Encode(skuPage{Value: []resourceSKU{{
			Name:         "Standard_B2s",
			ResourceType: "virtualMachines",
			Locations:    []string{"NorthEurope"},
		}}})
	}))
	defer server.Close()

	client := &AzureSKUClient{
		credential:     fakeTokenCredential{},
		subscriptionID: "subscription",
		httpClient:     server.Client(),
		endpoint:       server.URL,
	}

	available, err := client.IsAvailable(context.Background(), "northeurope", "standard_b2s")
	if err != nil {
		t.Fatalf("IsAvailable() error = %v", err)
	}
	if !available {
		t.Fatal("IsAvailable() = false, want true")
	}
}

func TestAzureSKUClientRejectsLocationRestrictions(t *testing.T) {
	client := newTestSKUClient(t, http.HandlerFunc(func(w http.ResponseWriter, _ *http.Request) {
		_ = json.NewEncoder(w).Encode(skuPage{Value: []resourceSKU{{
			Name:         "Standard_D2s_v5",
			ResourceType: "virtualMachines",
			Locations:    []string{"northeurope"},
			Restrictions: []skuRestriction{{
				Type:   "Location",
				Values: []string{"NorthEurope"},
			}},
		}}})
	}))

	available, err := client.IsAvailable(context.Background(), "northeurope", "Standard_D2s_v5")
	if err != nil {
		t.Fatalf("IsAvailable() error = %v", err)
	}
	if available {
		t.Fatal("IsAvailable() = true for a location-restricted SKU")
	}
}

func TestAzureSKUClientDoesNotForwardTokenToUnexpectedOrigin(t *testing.T) {
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, _ *http.Request) {
		_ = json.NewEncoder(w).Encode(skuPage{NextLink: "https://example.invalid/next"})
	}))
	defer server.Close()

	client := &AzureSKUClient{
		credential:     fakeTokenCredential{},
		subscriptionID: "subscription",
		httpClient:     server.Client(),
		endpoint:       server.URL,
	}
	_, err := client.IsAvailable(context.Background(), "northeurope", "Standard_D2s_v5")
	if err == nil || !strings.Contains(err.Error(), "unexpected origin") {
		t.Fatalf("IsAvailable() error = %v, want unexpected-origin error", err)
	}
}

func TestAzureSKUClientReportsHTTPFailure(t *testing.T) {
	client := newTestSKUClient(t, http.HandlerFunc(func(w http.ResponseWriter, _ *http.Request) {
		http.Error(w, "unavailable", http.StatusServiceUnavailable)
	}))

	_, err := client.IsAvailable(context.Background(), "northeurope", "Standard_D2s_v5")
	if err == nil || !strings.Contains(err.Error(), "HTTP 503") {
		t.Fatalf("IsAvailable() error = %v, want HTTP 503 error", err)
	}
}

func newTestSKUClient(t *testing.T, handler http.Handler) *AzureSKUClient {
	t.Helper()
	server := httptest.NewServer(handler)
	t.Cleanup(server.Close)
	return &AzureSKUClient{
		credential:     fakeTokenCredential{},
		subscriptionID: "subscription",
		httpClient:     server.Client(),
		endpoint:       server.URL,
	}
}

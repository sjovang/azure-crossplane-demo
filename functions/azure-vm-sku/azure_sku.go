package main

import (
	"context"
	"encoding/json"
	"fmt"
	"io"
	"net/http"
	"net/url"
	"strings"

	"github.com/Azure/azure-sdk-for-go/sdk/azcore"
	"github.com/Azure/azure-sdk-for-go/sdk/azcore/policy"
)

const (
	azureManagementEndpoint = "https://management.azure.com"
	azureManagementScope    = "https://management.azure.com/.default"
	computeSKUsAPIVersion   = "2021-07-01"
	maxSKUResponseBytes     = 16 << 20
	maxSKUPages             = 100
)

type AzureSKUClient struct {
	credential     azcore.TokenCredential
	subscriptionID string
	httpClient     *http.Client
	endpoint       string
}

type skuPage struct {
	Value    []resourceSKU `json:"value"`
	NextLink string        `json:"nextLink"`
}

type resourceSKU struct {
	Name         string           `json:"name"`
	ResourceType string           `json:"resourceType"`
	Locations    []string         `json:"locations"`
	Restrictions []skuRestriction `json:"restrictions"`
}

type skuRestriction struct {
	Type   string   `json:"type"`
	Values []string `json:"values"`
}

func (c *AzureSKUClient) IsAvailable(ctx context.Context, location, sku string) (bool, error) {
	token, err := c.credential.GetToken(ctx, policy.TokenRequestOptions{
		Scopes: []string{azureManagementScope},
	})
	if err != nil {
		return false, fmt.Errorf("acquire Azure management token: %w", err)
	}

	nextURL, err := c.initialURL(location)
	if err != nil {
		return false, err
	}

	for pageNumber := 0; nextURL != nil; pageNumber++ {
		if pageNumber >= maxSKUPages {
			return false, fmt.Errorf("Azure Compute SKU response exceeded %d pages", maxSKUPages)
		}

		req, err := http.NewRequestWithContext(ctx, http.MethodGet, nextURL.String(), nil)
		if err != nil {
			return false, fmt.Errorf("create Azure Compute SKU request: %w", err)
		}
		req.Header.Set("Authorization", "Bearer "+token.Token)
		req.Header.Set("Accept", "application/json")

		resp, err := c.httpClient.Do(req)
		if err != nil {
			return false, fmt.Errorf("request Azure Compute SKUs: %w", err)
		}
		if resp.StatusCode != http.StatusOK {
			resp.Body.Close()
			return false, fmt.Errorf("Azure Compute SKU API returned HTTP %d", resp.StatusCode)
		}

		var page skuPage
		decodeErr := json.NewDecoder(io.LimitReader(resp.Body, maxSKUResponseBytes)).Decode(&page)
		closeErr := resp.Body.Close()
		if decodeErr != nil {
			return false, fmt.Errorf("decode Azure Compute SKU response: %w", decodeErr)
		}
		if closeErr != nil {
			return false, fmt.Errorf("close Azure Compute SKU response: %w", closeErr)
		}

		for _, candidate := range page.Value {
			if candidate.isAvailable(location, sku) {
				return true, nil
			}
		}

		if page.NextLink == "" {
			return false, nil
		}
		nextURL, err = c.nextPageURL(page.NextLink)
		if err != nil {
			return false, err
		}
	}

	return false, nil
}

func (c *AzureSKUClient) initialURL(location string) (*url.URL, error) {
	endpoint := c.endpoint
	if endpoint == "" {
		endpoint = azureManagementEndpoint
	}
	base, err := url.Parse(endpoint)
	if err != nil {
		return nil, fmt.Errorf("parse Azure management endpoint: %w", err)
	}
	if base.Scheme != "https" && base.Scheme != "http" {
		return nil, fmt.Errorf("Azure management endpoint must use HTTP or HTTPS")
	}
	if base.Host == "" || base.User != nil {
		return nil, fmt.Errorf("Azure management endpoint is invalid")
	}

	subscriptionID := url.PathEscape(c.subscriptionID)
	base.Path = strings.TrimRight(base.Path, "/") +
		"/subscriptions/" + subscriptionID + "/providers/Microsoft.Compute/skus"
	query := base.Query()
	query.Set("api-version", computeSKUsAPIVersion)
	query.Set("$filter", "location eq '"+strings.ReplaceAll(location, "'", "''")+"'")
	base.RawQuery = query.Encode()
	return base, nil
}

func (c *AzureSKUClient) nextPageURL(raw string) (*url.URL, error) {
	endpoint := c.endpoint
	if endpoint == "" {
		endpoint = azureManagementEndpoint
	}
	base, err := url.Parse(endpoint)
	if err != nil {
		return nil, fmt.Errorf("parse Azure management endpoint: %w", err)
	}
	next, err := url.Parse(raw)
	if err != nil {
		return nil, fmt.Errorf("parse Azure Compute SKU continuation URL: %w", err)
	}
	if !next.IsAbs() {
		next = base.ResolveReference(next)
	}
	if next.Scheme != base.Scheme || next.Host != base.Host || next.User != nil {
		return nil, fmt.Errorf("Azure Compute SKU continuation URL has an unexpected origin")
	}
	return next, nil
}

func (s resourceSKU) isAvailable(location, requestedSKU string) bool {
	if !strings.EqualFold(s.Name, requestedSKU) ||
		!strings.EqualFold(s.ResourceType, "virtualMachines") ||
		!containsFold(s.Locations, location) {
		return false
	}

	for _, restriction := range s.Restrictions {
		if strings.EqualFold(restriction.Type, "Location") &&
			containsFold(restriction.Values, location) {
			return false
		}
	}
	return true
}

func containsFold(values []string, expected string) bool {
	for _, value := range values {
		if strings.EqualFold(value, expected) {
			return true
		}
	}
	return false
}

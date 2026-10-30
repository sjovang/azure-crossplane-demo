package main

import (
	"encoding/json"
	"errors"
	"fmt"
	"log"
	"net/http"
	"os"
	"time"

	"github.com/Azure/azure-sdk-for-go/sdk/azidentity"
	"github.com/crossplane/function-sdk-go"
)

type azureCredentials struct {
	ClientID       string `json:"clientId"`
	ClientSecret   string `json:"clientSecret"`
	SubscriptionID string `json:"subscriptionId"`
	TenantID       string `json:"tenantId"`
}

func newAzureSKUClientFromEnv() (*AzureSKUClient, error) {
	raw := os.Getenv("AZURE_CREDENTIALS")
	if raw == "" {
		return nil, errors.New("AZURE_CREDENTIALS is not set")
	}

	var credentials azureCredentials
	if err := json.Unmarshal([]byte(raw), &credentials); err != nil {
		return nil, fmt.Errorf("parse AZURE_CREDENTIALS: %w", err)
	}
	if credentials.ClientID == "" || credentials.ClientSecret == "" ||
		credentials.SubscriptionID == "" || credentials.TenantID == "" {
		return nil, errors.New("AZURE_CREDENTIALS is missing a required field")
	}

	credential, err := azidentity.NewClientSecretCredential(
		credentials.TenantID,
		credentials.ClientID,
		credentials.ClientSecret,
		nil,
	)
	if err != nil {
		return nil, fmt.Errorf("create Azure credential: %w", err)
	}

	return &AzureSKUClient{
		credential:     credential,
		subscriptionID: credentials.SubscriptionID,
		httpClient:     &http.Client{Timeout: 30 * time.Second},
	}, nil
}

func main() {
	client, err := newAzureSKUClientFromEnv()
	if err != nil {
		log.Fatalf("configure Azure VM SKU validator: %v", err)
	}

	if err := function.Serve(
		&Function{validator: client},
		function.Listen("tcp", ":9443"),
		function.MTLSCertificates(os.Getenv("TLS_SERVER_CERTS_DIR")),
	); err != nil {
		log.Fatalf("serve Azure VM SKU validator: %v", err)
	}
}

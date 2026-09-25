import { createBackend } from '@backstage/backend-defaults';

const backend = createBackend();

backend.add(import('@backstage/plugin-app-backend/alpha'));
backend.add(import('@backstage/plugin-catalog-backend/alpha'));

// Base config: Microsoft Entra ID (Azure AD) OIDC provider only. No custom
// sign-in resolver yet -- see app-config.yaml for provider configuration
// and the repo README for the manual App Registration steps required.
backend.add(import('@backstage/plugin-auth-backend'));
backend.add(
  import('@backstage/plugin-auth-backend-module-microsoft-provider'),
);

backend.start();

import { createApp } from '@backstage/app-defaults';
import { AppRouter, FlatRoutes } from '@backstage/core-app-api';
import { microsoftAuthApiRef } from '@backstage/core-plugin-api';
import {
  AlertDisplay,
  OAuthRequestDialog,
  SignInPage,
} from '@backstage/core-components';
import { CatalogIndexPage } from '@backstage/plugin-catalog';
import { Navigate, Route } from 'react-router-dom';

const app = createApp({
  // Base config: Microsoft Entra ID sign-in only, default Backstage sign-in
  // screen (no custom identity resolver / provider picker yet).
  components: {
    SignInPage: props => (
      <SignInPage
        {...props}
        auto
        providers={[
          {
            id: 'microsoft',
            title: 'Microsoft',
            message: 'Sign in using Microsoft Entra ID',
            apiRef: microsoftAuthApiRef,
          },
        ]}
      />
    ),
  },
});

const routes = (
  <FlatRoutes>
    <Route path="/" element={<Navigate to="/catalog" replace />} />
    <Route path="/catalog" element={<CatalogIndexPage />} />
    <Route path="*" element={<Navigate to="/catalog" replace />} />
  </FlatRoutes>
);

export default app.createRoot(
  <>
    <AlertDisplay />
    <OAuthRequestDialog />
    <AppRouter>
      {routes}
    </AppRouter>
  </>,
);

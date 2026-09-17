import { createApp } from '@backstage/app-defaults';
import { FlatRoutes } from '@backstage/core-app-api';
import { SignInPage } from '@backstage/core-components';
import { CatalogIndexPage } from '@backstage/plugin-catalog';
import { Route } from 'react-router-dom';

const app = createApp({
  // Base config: Microsoft Entra ID sign-in only, default Backstage sign-in
  // screen (no custom identity resolver / provider picker yet).
  components: {
    SignInPage: props => (
      <SignInPage {...props} auto providers={['microsoft']} />
    ),
  },
});

const AppProvider = app.getProvider();
const AppRouter = app.getRouter();

export const App = () => (
  <AppProvider>
    <AppRouter>
      <FlatRoutes>
        <Route path="/catalog" element={<CatalogIndexPage />} />
      </FlatRoutes>
    </AppRouter>
  </AppProvider>
);

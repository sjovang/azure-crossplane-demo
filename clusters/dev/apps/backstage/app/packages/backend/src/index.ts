import { createBackend } from '@backstage/backend-defaults';
import { createBackendModule } from '@backstage/backend-plugin-api';
import {
  authProvidersExtensionPoint,
  createOAuthProviderFactory,
} from '@backstage/plugin-auth-node';
import { microsoftAuthenticator } from '@backstage/plugin-auth-backend-module-microsoft-provider';

const microsoftAuthModule = createBackendModule({
  pluginId: 'auth',
  moduleId: 'microsoft-provider',
  register(registration) {
    registration.registerInit({
      deps: { providers: authProvidersExtensionPoint },
      async init({ providers }) {
        providers.registerProvider({
          providerId: 'microsoft',
          factory: createOAuthProviderFactory({
            authenticator: microsoftAuthenticator,
            async signInResolver(info, context) {
              const email = info.profile.email;
              if (!email) {
                throw new Error('Microsoft profile contained no email');
              }

              const userEntityRef = `user:default/${email
                .split('@')[0]
                .toLocaleLowerCase('en-US')}`;
              return context.issueToken({
                claims: {
                  sub: userEntityRef,
                  ent: [userEntityRef],
                },
              });
            },
          }),
        });
      },
    });
  },
});

const backend = createBackend();

backend.add(import('@backstage/plugin-app-backend/alpha'));
backend.add(import('@backstage/plugin-catalog-backend/alpha'));

// Base config: Microsoft Entra ID (Azure AD) OIDC provider only. No custom
// sign-in resolver yet -- see app-config.yaml for provider configuration
// and the repo README for the manual App Registration steps required.
backend.add(import('@backstage/plugin-auth-backend'));
backend.add(microsoftAuthModule);

backend.start();

import { installClientBrowserSecurity, safeReactRootErrorHandlers } from '@geo/design-system';
import { startTransition, StrictMode } from 'react';
import { hydrateRoot } from 'react-dom/client';
import { HydratedRouter } from 'react-router/dom';

installClientBrowserSecurity([
  'overview',
  'sessions',
  'interventions',
  'events',
  'service-visibility',
  'service-outbound-risk',
  'service-inbound-risk',
  'service-site-audit',
  'service-pilot',
]);

startTransition(() => {
  hydrateRoot(
    document,
    <StrictMode>
      <HydratedRouter />
    </StrictMode>,
    safeReactRootErrorHandlers,
  );
});

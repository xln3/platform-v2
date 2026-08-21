import { installClientBrowserSecurity, safeReactRootErrorHandlers } from '@geo/design-system';
import { startTransition, StrictMode } from 'react';
import { hydrateRoot } from 'react-dom/client';
import { HydratedRouter } from 'react-router/dom';

installClientBrowserSecurity([
  'home',
  'services',
  'service-1',
  'service-2',
  'service-3',
  'service-4',
  'service-5',
  'profile',
  'intake',
  'assets',
  'questions',
  'evidence',
  'reports',
  'members',
  'accounts',
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

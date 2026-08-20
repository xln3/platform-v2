import { installClientBrowserSecurity, safeReactRootErrorHandlers } from '@geo/design-system';
import { startTransition, StrictMode } from 'react';
import { hydrateRoot } from 'react-dom/client';
import { HydratedRouter } from 'react-router/dom';

installClientBrowserSecurity([
  'home',
  'profile',
  'intake',
  'assets',
  'questions',
  'monitoring',
  'answers',
  'competition',
  'sources',
  'reputation',
  'opportunities',
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

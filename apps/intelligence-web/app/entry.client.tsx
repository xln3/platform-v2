import { installClientBrowserSecurity, safeReactRootErrorHandlers } from '@geo/design-system';
import { startTransition, StrictMode } from 'react';
import { hydrateRoot } from 'react-dom/client';
import { HydratedRouter } from 'react-router/dom';

installClientBrowserSecurity([
  'cases',
  'claims',
  'sources',
  'source-insight',
  'graph',
  'history',
  'calibration',
  'verdict',
  'package',
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

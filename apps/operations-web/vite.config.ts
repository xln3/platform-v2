import { reactRouter } from '@react-router/dev/vite';
import { defineConfig, type Plugin } from 'vite';

const faviconMiddleware: Plugin = {
  name: 'operations-favicon',
  configureServer(server) {
    server.middlewares.use('/favicon.ico', (_request, response) => {
      response.statusCode = 204;
      response.end();
    });
  },
  configurePreviewServer(server) {
    server.middlewares.use('/favicon.ico', (_request, response) => {
      response.statusCode = 204;
      response.end();
    });
  },
};

export default defineConfig({
  base: '/platform/operations/',
  plugins: process.env.VITEST ? [] : [faviconMiddleware, reactRouter()],
  server:
    process.env.GEO_VITE_NO_WATCH === '1'
      ? { watch: null }
      : process.env.GEO_VITE_POLLING === '1'
        ? { watch: { usePolling: true, interval: 250 } }
        : undefined,
});

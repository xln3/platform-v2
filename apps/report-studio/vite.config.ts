import { reactRouter } from '@react-router/dev/vite';
import { defineConfig } from 'vite';
export default defineConfig({
  base: '/platform/reports/',
  plugins: process.env.VITEST ? [] : [reactRouter()],
  optimizeDeps: { include: ['konva', 'react-konva'] },
  server:
    process.env.GEO_VITE_NO_WATCH === '1'
      ? { watch: null }
      : process.env.GEO_VITE_POLLING === '1'
        ? { watch: { usePolling: true, interval: 250 } }
        : undefined,
});

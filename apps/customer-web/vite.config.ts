import { reactRouter } from '@react-router/dev/vite';
import { defineConfig } from 'vite';
export default defineConfig({
  base: '/platform/customer/',
  plugins: process.env.VITEST ? [] : [reactRouter()],
  server:
    process.env.GEO_VITE_NO_WATCH === '1'
      ? { watch: null }
      : process.env.GEO_VITE_POLLING === '1'
        ? { watch: { usePolling: true, interval: 250 } }
        : undefined,
  test: {
    // 全壳 jsdom 交互用例含数十步真实 userEvent，共享 CI runner 上多次擦线越过 vitest
    // 默认 5s 窗口（pairing 5118ms、intake truth-gates 5088ms 实测）；应用级给 20s，断言不变。
    testTimeout: 20000,
  },
});

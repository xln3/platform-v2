import { expect, test } from './runtime-fixture';
import { prepareApp } from './accessibility';

test('large chart, table, text and hostile long URL stay responsive and secret-free', async ({
  page,
}) => {
  const started = Date.now();
  await prepareApp(page, '/platform/customer/experience-performance');
  await expect(page.getByText('高基数模型指标图表已渲染')).toBeAttached();
  await expect(page.locator('tbody tr')).toHaveCount(620);
  await expect(page.getByText('row_0500')).toBeVisible();
  expect(Date.now() - started).toBeLessThan(10_000);

  const hostile = `${'超长筛选'.repeat(100)} Bearer dlp-long-url-canary`;
  await page.goto(
    `/platform/customer/?section=evidence&answer_query=${encodeURIComponent(hostile)}`,
  );
  await expect(page.getByLabel('搜索问题')).toHaveValue('');
  await expect.poll(() => page.url()).not.toContain('dlp-long-url-canary');
  const surfaces = await page.evaluate(() => ({
    url: location.href,
    local: JSON.stringify(localStorage),
    session: JSON.stringify(sessionStorage),
    text: document.body.textContent,
  }));
  expect(JSON.stringify(surfaces)).not.toContain('dlp-long-url-canary');
});

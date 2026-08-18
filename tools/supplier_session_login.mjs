#!/usr/bin/env node

/**
 * 为需要浏览器验证码的媒体供应商建立可复用登录态。
 *
 * 用法：
 *   SUPPLIER_ACCOUNT=... SUPPLIER_PASSWORD=... \
 *     node tools/supplier_session_login.mjs prfabu|pinda|nichuanbo
 *
 * 媒体管家会把四位图形验证码保存到 .datasets/prfabu_captcha.png，并在终端读取；
 * 品达当前无验证码，会自动更新 Netscape Cookie 文件；逆传播以 headed 模式打开，
 * 便于通过现有 noVNC 转发人工完成验证码（需设置 DISPLAY）。
 * 终端命令：
 *   shot                         保存当前截图
 *   click <x> <y>                点击页面坐标
 *   down <x> <y>                 按下鼠标（可随后 shot 检查拼图）
 *   move <x> <y>                 移动鼠标
 *   up                           松开鼠标
 *   drag <x1> <y1> <x2> <y2>    拖动滑块
 *   human <x1> <y1> <x2> <y2>   带停顿和轻微回拉的拟人拖动
 *   info                         输出验证码元素位置和状态
 *   submit                       点击登录
 *   save                         验证登录后保存 storage state
 *   quit                         退出
 *
 * 凭据仅从环境变量读取，不写入仓库；会话制品权限固定为 0600。
 */

import { chmod, mkdir, writeFile } from 'node:fs/promises';
import { fileURLToPath } from 'node:url';
import { createInterface } from 'node:readline';
import { chromium } from '@playwright/test';

const ROOT_URL = 'https://user.nichuanbo.com';
const DATASET_DIR = new URL('../.datasets/', import.meta.url);
const STATE_PATH = new URL('nichuanbo_storage.json', DATASET_DIR);
const SCREENSHOT_PATH = new URL('nichuanbo_login.png', DATASET_DIR);
const PINDA_COOKIE_PATH = new URL('pinda_session.txt', DATASET_DIR);
const PRFABU_CAPTCHA_PATH = new URL('prfabu_captcha.png', DATASET_DIR);
const PRFABU_COOKIE_PATH = new URL('prfabu_session.txt', DATASET_DIR);
const statePath = fileURLToPath(STATE_PATH);
const screenshotPath = fileURLToPath(SCREENSHOT_PATH);
const pindaCookiePath = fileURLToPath(PINDA_COOKIE_PATH);
const prfabuCaptchaPath = fileURLToPath(PRFABU_CAPTCHA_PATH);
const prfabuCookiePath = fileURLToPath(PRFABU_COOKIE_PATH);

function requiredEnvironment(name) {
  const value = process.env[name]?.trim();
  if (!value) {
    throw new Error(`缺少环境变量 ${name}`);
  }
  return value;
}

async function saveState(context, page) {
  if (new URL(page.url()).pathname.startsWith('/login')) {
    throw new Error('仍停留在登录页，尚不能保存会话');
  }
  await context.storageState({ path: statePath });
  await chmod(statePath, 0o600);
  process.stdout.write(`会话已保存：${statePath}\n`);
}

async function takeScreenshot(page) {
  await page.screenshot({ path: screenshotPath, fullPage: true });
  process.stdout.write(`截图已保存：${screenshotPath}\n`);
}

async function saveNetscapeCookies(context) {
  const cookies = await context.cookies('https://fagao.pindarpr.com');
  if (cookies.length === 0) throw new Error('品达未返回可持久化 Cookie');
  const lines = ['# Netscape HTTP Cookie File'];
  for (const cookie of cookies) {
    const domain = cookie.httpOnly ? `#HttpOnly_${cookie.domain}` : cookie.domain;
    lines.push(
      [
        domain,
        cookie.domain.startsWith('.') ? 'TRUE' : 'FALSE',
        cookie.path,
        cookie.secure ? 'TRUE' : 'FALSE',
        cookie.expires > 0 ? Math.floor(cookie.expires) : 0,
        cookie.name,
        cookie.value,
      ].join('\t'),
    );
  }
  await writeFile(pindaCookiePath, `${lines.join('\n')}\n`, { mode: 0o600 });
  await chmod(pindaCookiePath, 0o600);
  process.stdout.write(`会话已保存：${pindaCookiePath}\n`);
}

async function loginPinda(context, page, account, password) {
  await page.goto('https://fagao.pindarpr.com/wap_login', { waitUntil: 'domcontentloaded' });
  const response = await context.request.post('https://fagao.pindarpr.com/home_portal/checklogin', {
    data: { account, password, code: 'nocode', rememberMe: true },
    headers: { 'content-type': 'application/json' },
    timeout: 20_000,
  });
  const responseText = await response.text().catch(() => '');
  let body = null;
  if (responseText) {
    try {
      body = JSON.parse(responseText);
    } catch {
      body = null;
    }
  }
  if (typeof body === 'string') {
    try {
      body = JSON.parse(body);
    } catch {
      body = null;
    }
  }
  if (!body || typeof body !== 'object' || Number(body.code) !== 2) {
    const message =
      body && typeof body === 'object' && typeof body.msg === 'string'
        ? body.msg.slice(0, 120)
        : `响应格式异常（HTTP ${response.status()}，类型 ${
            response.headers()['content-type'] ?? 'unknown'
          }，长度 ${responseText.length}）`;
    throw new Error(`品达登录失败：${message}`);
  }
  await page.waitForTimeout(1_000);
  await saveNetscapeCookies(context);
}

async function savePrfabuSession(context) {
  const cookies = await context.cookies('https://www.prfabu.com');
  const session = cookies.find((cookie) => cookie.name === 'PHPSESSID' && cookie.value);
  if (!session) throw new Error('媒体管家未返回 PHPSESSID');
  const domain = session.httpOnly ? `#HttpOnly_${session.domain}` : session.domain;
  const line = [
    domain,
    session.domain.startsWith('.') ? 'TRUE' : 'FALSE',
    session.path || '/',
    session.secure ? 'TRUE' : 'FALSE',
    session.expires > 0 ? Math.floor(session.expires) : 0,
    session.name,
    session.value,
  ].join('\t');
  await writeFile(prfabuCookiePath, `# Netscape HTTP Cookie File\n${line}\n`, { mode: 0o600 });
  await chmod(prfabuCookiePath, 0o600);
  process.stdout.write(`会话已保存：${prfabuCookiePath}\n`);
}

async function responseObject(response) {
  const responseText = await response.text().catch(() => '');
  if (!responseText) return null;
  try {
    const body = JSON.parse(responseText);
    if (typeof body !== 'string') return body;
    return JSON.parse(body);
  } catch {
    return null;
  }
}

async function promptCaptcha() {
  const input = createInterface({ input: process.stdin, output: process.stdout, terminal: true });
  try {
    return await new Promise((resolve) => input.question('prfabu captcha> ', resolve));
  } finally {
    input.close();
  }
}

async function loginPrfabu(context, account, password) {
  const headers = { 'x-requested-with': 'XMLHttpRequest' };
  let lastMessage = '';
  for (let attempt = 1; attempt <= 5; attempt += 1) {
    const captchaResponse = await context.request.get(
      `https://www.prfabu.com/captcha.html?seed=0.${Date.now()}`,
      { headers, timeout: 20_000 },
    );
    if (!captchaResponse.ok()) {
      throw new Error(`媒体管家验证码读取失败：HTTP ${captchaResponse.status()}`);
    }
    await writeFile(prfabuCaptchaPath, await captchaResponse.body(), { mode: 0o600 });
    await chmod(prfabuCaptchaPath, 0o600);
    process.stdout.write(`验证码已保存：${prfabuCaptchaPath}（第 ${attempt}/5 次）\n`);
    const captcha = String(await promptCaptcha()).trim();
    if (!captcha) {
      lastMessage = '验证码不能为空';
      continue;
    }
    const loginResponse = await context.request.post('https://www.prfabu.com/', {
      form: { username: account, password, captcha },
      headers,
      timeout: 20_000,
    });
    const body = await responseObject(loginResponse);
    const code = body && typeof body === 'object' ? Number(body.code) : Number.NaN;
    const message =
      body && typeof body === 'object' && typeof body.msg === 'string'
        ? body.msg.slice(0, 120)
        : `响应格式异常（HTTP ${loginResponse.status()}）`;
    if (code !== 200) {
      lastMessage = message;
      if (!message.includes('验证码')) break;
      process.stderr.write(`验证码未通过：${message}\n`);
      continue;
    }

    const verifyResponse = await context.request.post(
      'https://www.prfabu.com/index/user/wallet.html',
      { form: {}, headers, timeout: 20_000 },
    );
    const verifyBody = await responseObject(verifyResponse);
    if (!verifyBody || typeof verifyBody !== 'object' || Number(verifyBody.code) !== 200) {
      throw new Error('媒体管家登录响应成功，但会话验证失败');
    }
    await savePrfabuSession(context);
    process.stdout.write('媒体管家登录态验证通过。\n');
    return;
  }
  throw new Error(`媒体管家登录失败：${lastMessage || '验证码重试次数已用尽'}`);
}

async function main() {
  const supplier = process.argv[2];
  if (supplier !== 'nichuanbo' && supplier !== 'pinda' && supplier !== 'prfabu') {
    throw new Error('支持的供应商：prfabu / pinda / nichuanbo');
  }
  const account = requiredEnvironment('SUPPLIER_ACCOUNT');
  const password = requiredEnvironment('SUPPLIER_PASSWORD');
  await mkdir(DATASET_DIR, { recursive: true });

  const proxyServer = process.env.https_proxy || process.env.http_proxy;
  const browser = await chromium.launch({
    headless: supplier === 'prfabu',
    args: ['--disable-blink-features=AutomationControlled'],
    ignoreDefaultArgs: ['--enable-automation'],
    ...(proxyServer ? { proxy: { server: proxyServer } } : {}),
  });
  const context = await browser.newContext({ viewport: { width: 1280, height: 900 } });
  await context.addInitScript(() => {
    Object.defineProperty(navigator, 'webdriver', { get: () => undefined });
  });
  const page = await context.newPage();
  if (supplier === 'prfabu') {
    try {
      await loginPrfabu(context, account, password);
    } finally {
      await browser.close();
    }
    return;
  }
  if (supplier === 'pinda') {
    try {
      await loginPinda(context, page, account, password);
    } finally {
      await browser.close();
    }
    return;
  }
  page.on('requestfailed', (request) => {
    if (request.url().includes('geetest')) {
      process.stderr.write(
        `极验请求失败：${new URL(request.url()).hostname} ${request.failure()?.errorText ?? ''}\n`,
      );
    }
  });
  await page.goto(`${ROOT_URL}/login`, { waitUntil: 'domcontentloaded' });
  await page.locator('input[name="account"]').fill(account);
  await page.locator('input[name="password"]').fill(password);
  await page.locator('#embed-captcha').waitFor({ state: 'visible', timeout: 30_000 });
  await page.waitForTimeout(2_000);
  await takeScreenshot(page);

  process.stdout.write(
    '浏览器已打开并填好账号。请完成极验；可在 noVNC 中操作，或使用终端 click/drag 命令。\n',
  );
  const input = createInterface({ input: process.stdin, output: process.stdout, terminal: true });
  input.setPrompt('nichuanbo> ');
  input.prompt();

  for await (const raw of input) {
    const [command, ...parts] = raw.trim().split(/\s+/);
    try {
      if (command === 'shot') {
        await takeScreenshot(page);
      } else if (command === 'click') {
        const [x, y] = parts.map(Number);
        if (![x, y].every(Number.isFinite)) throw new Error('用法：click <x> <y>');
        await page.mouse.click(x, y);
      } else if (command === 'down') {
        const [x, y] = parts.map(Number);
        if (![x, y].every(Number.isFinite)) throw new Error('用法：down <x> <y>');
        await page.mouse.move(x, y);
        await page.mouse.down();
      } else if (command === 'move') {
        const [x, y] = parts.map(Number);
        if (![x, y].every(Number.isFinite)) throw new Error('用法：move <x> <y>');
        await page.mouse.move(x, y, { steps: 12 });
      } else if (command === 'up') {
        await page.mouse.up();
      } else if (command === 'drag') {
        const [x1, y1, x2, y2] = parts.map(Number);
        if (![x1, y1, x2, y2].every(Number.isFinite)) {
          throw new Error('用法：drag <x1> <y1> <x2> <y2>');
        }
        await page.mouse.move(x1, y1);
        await page.mouse.down();
        await page.mouse.move(x2, y2, { steps: 36 });
        await page.mouse.up();
      } else if (command === 'human') {
        const [x1, y1, x2, y2] = parts.map(Number);
        if (![x1, y1, x2, y2].every(Number.isFinite)) {
          throw new Error('用法：human <x1> <y1> <x2> <y2>');
        }
        await page.mouse.move(x1, y1);
        await page.waitForTimeout(160);
        await page.mouse.down();
        const distance = x2 - x1;
        for (let step = 1; step <= 42; step += 1) {
          const progress = step / 42;
          const eased = 1 - (1 - progress) ** 3;
          const jitter = Math.sin(step * 0.9) * 1.2;
          await page.mouse.move(x1 + distance * eased + (step > 36 ? 2 : 0), y1 + jitter);
          await page.waitForTimeout(12 + (step % 5) * 3);
        }
        await page.mouse.move(x2 + 3, y2, { steps: 3 });
        await page.waitForTimeout(110);
        await page.mouse.move(x2, y2, { steps: 3 });
        await page.waitForTimeout(180);
        await page.mouse.up();
      } else if (command === 'info') {
        const details = await page.locator('[class*="geetest"]').evaluateAll((elements) =>
          elements
            .map((element) => {
              const box = element.getBoundingClientRect();
              const style = window.getComputedStyle(element);
              return {
                className: element.className,
                display: style.display,
                visibility: style.visibility,
                box: [box.x, box.y, box.width, box.height].map(Math.round),
                text: element.textContent?.trim().slice(0, 40),
              };
            })
            .filter((item) => item.box[2] > 0 && item.box[3] > 0),
        );
        process.stdout.write(`${JSON.stringify(details, null, 2)}\n`);
      } else if (command === 'submit') {
        const loginResponse = page
          .waitForResponse((response) => new URL(response.url()).pathname === '/login/dologin/', {
            timeout: 15_000,
          })
          .catch(() => null);
        await page.locator('#embed-submit').click();
        const response = await loginResponse;
        if (response) {
          const body = await response.json().catch(() => null);
          const safeMessage =
            body && typeof body === 'object' && typeof body.msg === 'string'
              ? body.msg.slice(0, 120)
              : '响应格式异常';
          process.stdout.write(`登录响应：HTTP ${response.status()} ${safeMessage}\n`);
        }
        await page.waitForTimeout(3_000);
        process.stdout.write(`当前页面：${page.url()}\n`);
      } else if (command === 'save') {
        await saveState(context, page);
      } else if (command === 'quit') {
        input.close();
        break;
      } else if (command) {
        process.stdout.write(
          '可用命令：shot / click / down / move / up / drag / human / info / submit / save / quit\n',
        );
      }
    } catch (error) {
      process.stderr.write(`${error instanceof Error ? error.message : String(error)}\n`);
    }
    input.prompt();
  }

  await browser.close();
}

main().catch((error) => {
  process.stderr.write(`${error instanceof Error ? error.stack : String(error)}\n`);
  process.exitCode = 1;
});

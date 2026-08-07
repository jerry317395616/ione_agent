import {mkdir} from 'node:fs/promises';
import {createRequire} from 'node:module';
import path from 'node:path';

const require = createRequire(import.meta.url);
const {chromium} = require('playwright');

const baseUrl = String(process.env.MANAGER_BASE_URL || 'https://manager.myyr.top').replace(/\/$/, '');
const user = String(process.env.MANAGER_USER || 'Administrator');
const password = String(process.env.MANAGER_PASSWORD || '');
const chromePath = process.env.CHROME_PATH || 'C:\\Program Files\\Google\\Chrome\\Application\\chrome.exe';
const outputDir = path.resolve(process.env.PLATFORM_CAPTURE_DIR || 'assets/platform/captures');

if (!password) throw new Error('MANAGER_PASSWORD is required');

const routes = [
  ['desktop', '/desk'],
  ['enterprise', '/desk/home'],
  ['human-resources', '/desk/hr-setup'],
  ['crm', '/crm/leads'],
  ['healthcare', '/desk/healthcare'],
  ['medical-insurance', '/desk/medical-insurance-operations'],
  ['quality', '/app/ione-quality-command-center'],
  ['screening', '/desk/%E4%B8%A4%E7%99%8C%E7%AD%9B%E6%9F%A5'],
  ['education', '/desk/education'],
  ['wiki', '/wiki/spaces'],
  ['agent', '/agent'],
];

await mkdir(outputDir, {recursive: true});

const browser = await chromium.launch({
  executablePath: chromePath,
  headless: true,
  args: ['--disable-gpu', '--font-render-hinting=none'],
});

const context = await browser.newContext({
  viewport: {width: 1600, height: 900},
  deviceScaleFactor: 1,
  locale: 'zh-CN',
  colorScheme: 'light',
});

try {
  const page = await context.newPage();
  const login = await page.request.post(`${baseUrl}/api/method/login`, {
    form: {usr: user, pwd: password},
  });
  if (!login.ok()) throw new Error(`login failed: HTTP ${login.status()} ${await login.text()}`);

  for (const [name, route] of routes) {
    const url = `${baseUrl}${route}`;
    await page.goto(url, {waitUntil: 'domcontentloaded', timeout: 120_000});
    await page.waitForTimeout(route.startsWith('/crm') || route.startsWith('/agent') ? 8_000 : 5_000);
    await page.addStyleTag({
      content: `
        [data-route="flow"], .flow-sidebar, .flow-panel, .help-widget,
        .onboarding-widget, .notifications-list, .modal-backdrop,
        .page-actions .btn[data-label="Edit"] { display: none !important; }
        body { caret-color: transparent !important; }
      `,
    }).catch(() => {});
    await page.screenshot({
      path: path.join(outputDir, `${name}.png`),
      fullPage: false,
      animations: 'disabled',
    });
    console.log(`${name}: ${page.url()}`);
  }
} finally {
  await context.close();
  await browser.close();
}

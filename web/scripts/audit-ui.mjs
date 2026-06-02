import { chromium } from "playwright";
import { mkdir } from "node:fs/promises";
import path from "node:path";

const baseUrl = process.env.HOMUNCULUS_AUDIT_URL ?? "http://127.0.0.1:5173";
const outDir = process.env.HOMUNCULUS_AUDIT_OUT ?? "/private/tmp/homunculus-ui-audit";
const chromePath = "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome";

const routes = [
  ["/", "home"],
  ["/overview", "overview"],
  ["/chat", "chat"],
  ["/tasks", "tasks"],
  ["/memory", "memory"],
  ["/tools", "tools"],
  ["/traces", "traces"],
  ["/logs", "logs"],
];

const viewports = [
  { name: "desktop", width: 1440, height: 1000 },
  { name: "mobile", width: 390, height: 844 },
];

await mkdir(outDir, { recursive: true });

const browser = await chromium.launch({
  headless: true,
  executablePath: chromePath,
});

const results = [];

for (const viewport of viewports) {
  const context = await browser.newContext({ viewport, deviceScaleFactor: 1 });
  await context.addInitScript(() => {
    window.sessionStorage.setItem("homunculus_booted_v1", "1");
  });
  for (const [route, name] of routes) {
    const page = await context.newPage();
    const consoleErrors = [];
    page.on("console", (msg) => {
      if (msg.type() === "error") consoleErrors.push(msg.text());
    });
    page.on("pageerror", (err) => consoleErrors.push(err.message));

    const url = new URL(route, baseUrl).toString();
    await page.goto(url, { waitUntil: "domcontentloaded", timeout: 30_000 });
    await page.waitForLoadState("load", { timeout: 10_000 }).catch(() => {});
    await page.waitForTimeout(900);

    const metrics = await page.evaluate(() => {
      const doc = document.documentElement;
      const body = document.body;
      const width = Math.max(doc.scrollWidth, body.scrollWidth);
      const height = Math.max(doc.scrollHeight, body.scrollHeight);
      const vw = window.innerWidth;
      const vh = window.innerHeight;
      const overflowing = [...document.querySelectorAll("*")]
        .map((el) => {
          const rect = el.getBoundingClientRect();
          if (rect.width <= 0 || rect.height <= 0) return null;
          if (rect.right > vw + 1 || rect.left < -1) {
            const cls = typeof el.className === "string" ? el.className : "";
            return {
              tag: el.tagName.toLowerCase(),
              className: cls.slice(0, 100),
              text: (el.textContent ?? "").trim().slice(0, 80),
              left: Math.round(rect.left),
              right: Math.round(rect.right),
              width: Math.round(rect.width),
            };
          }
          return null;
        })
        .filter(Boolean)
        .slice(0, 12);

      return {
        title: document.title,
        scrollWidth: width,
        scrollHeight: height,
        viewportWidth: vw,
        viewportHeight: vh,
        horizontalOverflow: width > vw + 1,
        overflowing,
      };
    });

    const screenshot = path.join(outDir, `${viewport.name}-${name}.png`);
    await page.screenshot({ path: screenshot, fullPage: false });
    results.push({ route, viewport: viewport.name, screenshot, consoleErrors, metrics });
    await page.close();
  }
  await context.close();
}

await browser.close();

for (const result of results) {
  const { route, viewport, screenshot, consoleErrors, metrics } = result;
  const flags = [];
  if (metrics.horizontalOverflow) flags.push(`overflow ${metrics.scrollWidth}>${metrics.viewportWidth}`);
  if (consoleErrors.length) flags.push(`${consoleErrors.length} console error(s)`);
  const status = flags.length ? `WARN ${flags.join(", ")}` : "OK";
  console.log(`${status} ${viewport} ${route} -> ${screenshot}`);
  for (const err of consoleErrors.slice(0, 3)) console.log(`  console: ${err}`);
  for (const item of metrics.overflowing) {
    console.log(`  overflow: <${item.tag}> ${item.className} ${item.left}..${item.right} "${item.text}"`);
  }
}

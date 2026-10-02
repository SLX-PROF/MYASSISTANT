// Screenshots of the running app for design review.
// Usage: BASE=http://127.0.0.1:8000 PASSWORD=... OUT=./shots NODE_PATH=$(npm root -g) node scripts/screenshots.mjs
import { createRequire } from "node:module";
import { mkdirSync } from "node:fs";

const require = createRequire(import.meta.url);
const { chromium } = require("playwright");
const BASE = process.env.BASE || "http://127.0.0.1:8000";
const OUT = process.env.OUT || "./shots";
const PASSWORD = process.env.PASSWORD;
mkdirSync(OUT, { recursive: true });

const errors = [];
const browser = await chromium.launch();

async function session(viewport, scale, mobile) {
  const ctx = await browser.newContext({ viewport, deviceScaleFactor: scale, isMobile: mobile, hasTouch: mobile, locale: "ru-RU", timezoneId: "Europe/Moscow" });
  const page = await ctx.newPage();
  page.on("console", (m) => m.type() === "error" && errors.push(`[${viewport.width}] ${m.text()}`));
  page.on("pageerror", (e) => errors.push(`[${viewport.width}] ${e.message}`));
  return { ctx, page };
}

async function send(page, text) {
  await page.fill("#composer-input", text);
  await page.keyboard.press("Enter");
  await page.waitForFunction(() => !document.querySelector(".typing, .spinner, .caret"), null, { timeout: 15000 });
  await page.waitForTimeout(500);
}

async function theme(page, t) {
  await page.evaluate((t) => {
    const ui = JSON.parse(localStorage.getItem("atlas.ui") || "{}");
    localStorage.setItem("atlas.ui", JSON.stringify({ ...ui, theme: t }));
    document.documentElement.dataset.theme = t;
  }, t);
  await page.waitForTimeout(300);
}

// ---------------- desktop
{
  const { ctx, page } = await session({ width: 1440, height: 900 }, 1, false);
  await page.goto(BASE + "/");
  await page.waitForSelector("#password");
  await page.waitForTimeout(600);
  await page.screenshot({ path: `${OUT}/desktop-login.png` });
  await page.fill("#password", PASSWORD);
  await page.keyboard.press("Enter");
  await page.waitForSelector("#composer-input");
  await page.waitForTimeout(500);
  await page.screenshot({ path: `${OUT}/desktop-empty.png` });
  await send(page, "Напомни через 2 минуты выпить воды");
  await send(page, "Добавь задачу купить продукты на завтра");
  await send(page, "Запомни, что я пью кофе без сахара");
  await send(page, "Мои задачи");
  await send(page, "Привет! Что ты умеешь?");
  await page.screenshot({ path: `${OUT}/desktop-chat-dark.png` });
  await theme(page, "light");
  await page.screenshot({ path: `${OUT}/desktop-chat-light.png` });
  await theme(page, "dark");
  await page.keyboard.press("Control+k");
  await page.waitForTimeout(300);
  await page.screenshot({ path: `${OUT}/desktop-palette.png` });
  await page.keyboard.press("Escape");
  for (const p of ["tasks", "reminders", "memory", "settings"]) {
    await page.goto(`${BASE}/${p}`);
    await page.waitForTimeout(700);
    await page.screenshot({ path: `${OUT}/desktop-${p}.png` });
  }
  await page.goto(`${BASE}/showcase`);
  await page.waitForTimeout(700);
  await page.screenshot({ path: `${OUT}/desktop-showcase.png`, fullPage: true });
  await ctx.close();
}

// ---------------- mobile (iPhone 14-ish)
{
  const { ctx, page } = await session({ width: 390, height: 844 }, 2, true);
  await page.goto(BASE + "/");
  await page.waitForSelector("#password");
  await page.waitForTimeout(500);
  await page.screenshot({ path: `${OUT}/mobile-login.png` });
  await page.fill("#password", PASSWORD);
  await page.keyboard.press("Enter");
  await page.waitForSelector("#composer-input");
  await page.waitForTimeout(800);
  await page.screenshot({ path: `${OUT}/mobile-chat-dark.png` });
  await theme(page, "light");
  await page.screenshot({ path: `${OUT}/mobile-chat-light.png` });
  await theme(page, "dark");
  await page.click('.bottom-nav button:has-text("Задачи")');
  await page.waitForTimeout(600);
  await page.screenshot({ path: `${OUT}/mobile-tasks.png` });
  await page.click('.bottom-nav button:has-text("Напоминания")');
  await page.waitForTimeout(600);
  await page.screenshot({ path: `${OUT}/mobile-reminders.png` });
  await page.click('.bottom-nav button:has-text("Чат")');
  await page.waitForTimeout(400);
  await page.click('button[aria-label="Открыть меню"]');
  await page.waitForTimeout(500);
  await page.screenshot({ path: `${OUT}/mobile-menu.png` });
  await ctx.close();
}

await browser.close();
console.log(errors.length ? "CONSOLE ERRORS:\n" + errors.join("\n") : "no console errors");

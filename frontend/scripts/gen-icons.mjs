// Renders public/icons/icon.svg into the PNG sizes needed by the PWA.
// Usage: NODE_PATH=$(npm root -g) node scripts/gen-icons.mjs   (needs playwright)
import { createRequire } from "node:module";
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";

const require = createRequire(import.meta.url);
const { chromium } = require("playwright");
const dir = fileURLToPath(new URL("../public/icons/", import.meta.url));
const svg = readFileSync(dir + "icon.svg", "utf8");
const fullBleed = svg.replace('rx="112"', 'rx="0"');
// Maskable: keep the orb inside the 80% safe zone.
const maskable = fullBleed.replace(/<circle cx="256" cy="256" r="(\d+)"/g, (m, r) => m.replace(r, String(Math.round(r * 0.8))))
  .replace('rx="34" ry="22"', 'rx="27" ry="18"').replace('cx="222" cy="214"', 'cx="229" cy="222"');
const badge = `<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 96 96"><circle cx="48" cy="48" r="30" fill="#fff"/><circle cx="48" cy="48" r="42" fill="none" stroke="#fff" stroke-width="4" stroke-dasharray="6 8"/></svg>`;

const jobs = [
  ["icon-192.png", svg, 192, 192, true],
  ["icon-512.png", svg, 512, 512, true],
  ["maskable-512.png", maskable, 512, 512, false],
  ["apple-touch-icon.png", fullBleed, 180, 180, false],
  ["favicon-32.png", svg, 32, 32, true],
  ["badge-96.png", badge, 96, 96, true],
];

const browser = await chromium.launch({ executablePath: process.env.CHROMIUM || undefined });
const page = await browser.newPage();
for (const [name, source, w, h, transparent] of jobs) {
  await page.setViewportSize({ width: w, height: h });
  await page.setContent(`<html><body style="margin:0;background:transparent"><img src="data:image/svg+xml;base64,${Buffer.from(source).toString("base64")}" width="${w}" height="${h}" style="display:block"></body></html>`);
  await page.screenshot({ path: dir + name, omitBackground: transparent });
  console.log("wrote", name);
}
// iPhone splash (390x844 @3x)
await page.setViewportSize({ width: 390, height: 844 });
await page.setContent(`<html><body style="margin:0;height:844px;display:grid;place-items:center;background:radial-gradient(520px 420px at 10% -6%,rgba(56,189,248,.14),transparent 60%),linear-gradient(180deg,#04060c,#070c18)"><img src="data:image/svg+xml;base64,${Buffer.from(fullBleed.replace('fill="url(#bg)"', 'fill="none"')).toString("base64")}" width="180" height="180"></body></html>`);
const splash = await browser.newContext({ viewport: { width: 390, height: 844 }, deviceScaleFactor: 3 });
const sp = await splash.newPage();
await sp.setContent(await page.content());
await sp.screenshot({ path: dir + "splash-1170x2532.png" });
console.log("wrote splash-1170x2532.png");
await browser.close();

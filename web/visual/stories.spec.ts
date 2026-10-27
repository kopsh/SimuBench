import { readFileSync } from "node:fs";
import { join } from "node:path";
import { expect, test } from "@playwright/test";

type Entry = { id: string; type: string; title: string; name: string };
const index = JSON.parse(readFileSync(join(import.meta.dirname, "..", "storybook-static", "index.json"), "utf-8")) as {
  entries: Record<string, Entry>;
};
const stories = Object.values(index.entries).filter((e) => e.type === "story");

for (const story of stories) {
  for (const theme of ["light", "dark"] as const) {
    test(`${story.title} / ${story.name} (${theme})`, async ({ page }) => {
      await page.goto(`/iframe.html?id=${story.id}&viewMode=story&globals=theme:${theme}`);
      await page.waitForSelector("#storybook-root > *");
      await page.evaluate(() => document.fonts.ready);
      await expect(page).toHaveScreenshot(`${story.id}-${theme}.png`, { fullPage: true });
    });
  }
}

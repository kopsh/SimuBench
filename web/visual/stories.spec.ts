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
      // A capture can land before the webfonts do: fallback metrics change how text wraps (a card
      // growing by one line shifts the whole canvas fit), so the same page renders two ways — the
      // race that kept flipping the Live canvas / Counter story. Forcing every declared face to
      // load makes the capture always see the webfont metrics, race or not (issue 613).
      await page.evaluate(async () => {
        await Promise.all([...document.fonts].map((f) => f.load().catch(() => {})));
        await document.fonts.ready;
      });
      // The Canvas refits when the fonts settle (#645); two frames let the refit land before the
      // capture (its fonts.ready → rAF → React Flow's fit queue).
      await page.evaluate(() => new Promise((r) => requestAnimationFrame(() => requestAnimationFrame(r))));
      await expect(page).toHaveScreenshot(`${story.id}-${theme}.png`, { fullPage: true });
    });
  }
}

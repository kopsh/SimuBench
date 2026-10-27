import { defineConfig, devices } from "@playwright/test";

// Visual regression of the component library (M0): every Storybook story, light and dark, against
// committed baselines. Baselines are Linux/Chromium (the CI e2e runner); update them with
// `pnpm visual --update-snapshots` after an intended change.
const PORT = 16006;

export default defineConfig({
  testDir: "./visual",
  timeout: 30_000,
  workers: 1,
  reporter: process.env.CI ? [["list"], ["html", { open: "never", outputFolder: "visual-report" }]] : "list",
  expect: { toHaveScreenshot: { maxDiffPixelRatio: 0.01, animations: "disabled" } },
  use: { baseURL: `http://127.0.0.1:${PORT}`, viewport: { width: 1000, height: 640 } },
  projects: [{ name: "chromium", use: { ...devices["Desktop Chrome"], viewport: { width: 1000, height: 640 } } }],
  webServer: {
    command: `pnpm exec vite preview --outDir storybook-static --port ${PORT} --strictPort`,
    url: `http://127.0.0.1:${PORT}/index.json`,
    reuseExistingServer: false,
    timeout: 60_000,
  },
});

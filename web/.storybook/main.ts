import type { StorybookConfig } from "@storybook/react-vite";

// The component library (M0): shared parts in isolation, in both themes and both languages.
const config: StorybookConfig = {
  stories: ["../src/**/*.stories.@(ts|tsx)"],
  framework: { name: "@storybook/react-vite", options: {} },
  core: { disableTelemetry: true },
};

export default config;

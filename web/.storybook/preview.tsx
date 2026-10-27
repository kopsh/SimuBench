import type { Decorator, Preview } from "@storybook/react-vite";
import { type ReactNode, useEffect } from "react";
import "../src/styles/globals.css";
import i18n from "../src/i18n";
import { TooltipProvider } from "../src/components/ui/tooltip";

/** Theme and language follow the toolbar, the way the app's preferences menu sets them. */
function AppContext({ theme, locale, children }: { theme: "light" | "dark"; locale: "zh" | "en"; children: ReactNode }) {
  useEffect(() => {
    document.documentElement.classList.toggle("dark", theme === "dark");
    document.documentElement.style.colorScheme = theme;
  }, [theme]);
  useEffect(() => {
    void i18n.changeLanguage(locale);
  }, [locale]);
  return (
    <TooltipProvider delayDuration={200}>
      <div className="bg-background text-foreground min-h-screen p-6">{children}</div>
    </TooltipProvider>
  );
}

const withAppContext: Decorator = (Story, context) => (
  <AppContext theme={context.globals.theme === "dark" ? "dark" : "light"} locale={context.globals.locale === "en" ? "en" : "zh"}>
    <Story />
  </AppContext>
);

const preview: Preview = {
  decorators: [withAppContext],
  globalTypes: {
    theme: {
      description: "Theme",
      toolbar: { icon: "contrast", items: ["light", "dark"], dynamicTitle: true },
    },
    locale: {
      description: "Language",
      toolbar: { icon: "globe", items: [{ value: "zh", title: "中文" }, { value: "en", title: "English" }], dynamicTitle: true },
    },
  },
  initialGlobals: { theme: "light", locale: "zh" },
  parameters: { layout: "fullscreen" },
};

export default preview;

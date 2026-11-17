import { Bot, HardDrive, Info, Languages, Monitor, Moon, Settings2, Sun } from "lucide-react";
import { useState } from "react";
import { useTranslation } from "react-i18next";
import { Button } from "@/components/ui/button";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuLabel,
  DropdownMenuRadioGroup,
  DropdownMenuRadioItem,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import { type Language, setLanguage } from "@/i18n";
import { type ThemePreference, useTheme } from "@/lib/theme";
import { AboutDialog } from "./AboutDialog";
import { McpConnect, McpConnectDialog, useMcpUrl } from "./McpConnect";
import { StorageDialog } from "./StorageDialog";

/** The settings menu at the right of every page's top bar. `mcpInMenu`: list "connect an external
 * AI" in the menu rather than as a button beside it — the bench page, whose top bar already has the
 * built-in AI assistant, so the bar does not show two AI buttons. */
export function PreferencesMenu({ mcpInMenu = false }: { mcpInMenu?: boolean }) {
  const { t, i18n } = useTranslation();
  const mcpUrl = useMcpUrl();
  const [mcp, setMcp] = useState(false);
  const { preference, setPreference } = useTheme();
  const [about, setAbout] = useState(false);
  const [storage, setStorage] = useState(false);
  return (
    <>
      {!mcpInMenu && <McpConnect />}
      <DropdownMenu>
        <DropdownMenuTrigger asChild>
          <Button variant="ghost" size="icon" className="size-8" aria-label={t("workbench.settings")}>
            <Settings2 className="size-4" />
          </Button>
        </DropdownMenuTrigger>
        <DropdownMenuContent align="end" className="w-48">
          <DropdownMenuLabel className="text-muted-foreground text-xs">{t("workbench.theme.label")}</DropdownMenuLabel>
          <DropdownMenuRadioGroup value={preference} onValueChange={(v) => setPreference(v as ThemePreference)}>
            <DropdownMenuRadioItem value="system">
              <Monitor className="size-4" /> {t("workbench.theme.system")}
            </DropdownMenuRadioItem>
            <DropdownMenuRadioItem value="light">
              <Sun className="size-4" /> {t("workbench.theme.light")}
            </DropdownMenuRadioItem>
            <DropdownMenuRadioItem value="dark">
              <Moon className="size-4" /> {t("workbench.theme.dark")}
            </DropdownMenuRadioItem>
          </DropdownMenuRadioGroup>
          <DropdownMenuSeparator />
          <DropdownMenuLabel className="text-muted-foreground flex items-center gap-1.5 text-xs">
            <Languages className="size-3.5" /> {t("workbench.language")}
          </DropdownMenuLabel>
          <DropdownMenuRadioGroup value={i18n.language} onValueChange={(v) => setLanguage(v as Language)}>
            <DropdownMenuRadioItem value="zh">中文</DropdownMenuRadioItem>
            <DropdownMenuRadioItem value="en">English</DropdownMenuRadioItem>
          </DropdownMenuRadioGroup>
          <DropdownMenuSeparator />
          {mcpInMenu && mcpUrl && (
            <DropdownMenuItem onSelect={() => setMcp(true)}>
              <Bot className="size-4" /> {t("mcp.button")}
            </DropdownMenuItem>
          )}
          <DropdownMenuItem onSelect={() => setStorage(true)}>
            <HardDrive className="size-4" /> {t("storage.menu")}
          </DropdownMenuItem>
          <DropdownMenuItem onSelect={() => setAbout(true)}>
            <Info className="size-4" /> {t("about.menu")}
          </DropdownMenuItem>
        </DropdownMenuContent>
      </DropdownMenu>
      <AboutDialog open={about} onOpenChange={setAbout} />
      <StorageDialog open={storage} onOpenChange={setStorage} />
      {mcpInMenu && mcpUrl && <McpConnectDialog url={mcpUrl} open={mcp} onOpenChange={setMcp} />}
    </>
  );
}

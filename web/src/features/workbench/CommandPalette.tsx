import { Home, Monitor, MonitorPlay, Moon, Square, StepForward, Sun } from "lucide-react";
import { useEffect } from "react";
import { useTranslation } from "react-i18next";
import { useNavigate } from "react-router-dom";
import type { SessionInfo } from "@/api/client";
import { useAdvance, useStopSession } from "@/api/hooks";
import { CommandDialog, CommandEmpty, CommandGroup, CommandInput, CommandItem, CommandList } from "@/components/ui/command";
import { setLanguage } from "@/i18n";
import { useTheme } from "@/lib/theme";
import { instrumentTitle } from "./model";
import { useWorkbench } from "./store";

export function CommandPalette({ session }: { session: SessionInfo }) {
  const { t } = useTranslation();
  const navigate = useNavigate();
  const { paletteOpen, setPalette, openScreen, select } = useWorkbench();
  const setTheme = useTheme((s) => s.setPreference);
  const advance = useAdvance(session.id);
  const stop = useStopSession();

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if ((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === "k") {
        e.preventDefault();
        setPalette(!useWorkbench.getState().paletteOpen);
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [setPalette]);

  const run = (fn: () => void) => () => {
    setPalette(false);
    fn();
  };

  return (
    <CommandDialog open={paletteOpen} onOpenChange={setPalette} title={t("workbench.commandPalette")} description={t("common.search")}>
      <CommandInput placeholder={t("common.search")} />
      <CommandList>
        <CommandEmpty>—</CommandEmpty>
        <CommandGroup heading={t("workbench.instruments")}>
          {session.instruments.map((i) => (
            <CommandItem key={i.name} value={`screen ${i.name} ${i.model}`} onSelect={run(() => openScreen(i.name))}>
              <MonitorPlay /> {t("workbench.openScreen")}: {instrumentTitle(i)}
            </CommandItem>
          ))}
          {session.instruments.map((i) => (
            <CommandItem key={`sel-${i.name}`} value={`inspect ${i.name}`} onSelect={run(() => select({ kind: "instrument", id: i.name }))}>
              <Monitor /> {t("workbench.inspector")}: {instrumentTitle(i)}
            </CommandItem>
          ))}
        </CommandGroup>
        <CommandGroup heading={t("workbench.clock")}>
          {session.clock_mode !== "wall" &&
            [1e-3, 0.1, 1].map((s) => (
              <CommandItem key={s} value={`advance ${s}`} onSelect={run(() => advance.mutate(s))}>
                <StepForward /> {t("workbench.advanceBy", { step: s >= 1 ? `${s} s` : `${s * 1000} ms` })}
              </CommandItem>
            ))}
        </CommandGroup>
        <CommandGroup heading={t("workbench.settings")}>
          <CommandItem value="theme light" onSelect={run(() => setTheme("light"))}>
            <Sun /> {t("workbench.theme.light")}
          </CommandItem>
          <CommandItem value="theme dark" onSelect={run(() => setTheme("dark"))}>
            <Moon /> {t("workbench.theme.dark")}
          </CommandItem>
          <CommandItem value="language zh 中文" onSelect={run(() => setLanguage("zh"))}>
            中文
          </CommandItem>
          <CommandItem value="language en english" onSelect={run(() => setLanguage("en"))}>
            English
          </CommandItem>
        </CommandGroup>
        <CommandGroup heading={session.bench_name}>
          <CommandItem value="home benches" onSelect={run(() => navigate("/"))}>
            <Home /> {t("workbench.backHome")}
          </CommandItem>
          <CommandItem value="stop session" onSelect={run(() => stop.mutate(session.id, { onSuccess: () => navigate("/") }))}>
            <Square /> {t("workbench.stopSession")}
          </CommandItem>
        </CommandGroup>
      </CommandList>
    </CommandDialog>
  );
}

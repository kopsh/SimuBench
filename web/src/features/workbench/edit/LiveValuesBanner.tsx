import { Info } from "lucide-react";
import { useTranslation } from "react-i18next";
import type { InstrumentInfo } from "@/api/client";
import { formatValue, labelOf } from "../model";

const SHOWN = 6;

/**
 * Build mode shows the bench file (initial settings) on the canvas, while an instrument screen
 * shows the running session. Over a screen opened in build mode, say so, and list the settings
 * whose running value differs from the initial one (#103). A front panel (`panel`) is the exception:
 * in build mode its controls set the initial settings, and only its main display reads the session.
 */
export function LiveValuesBanner({ live, initial, panel }: { live: InstrumentInfo; initial?: InstrumentInfo; panel?: boolean }) {
  const { t } = useTranslation();
  const start = new Map((initial?.state ?? []).map((v) => [v.path, v]));
  const diffs = live.state.filter((v) => {
    const s = start.get(v.path);
    return s !== undefined && JSON.stringify(s.value) !== JSON.stringify(v.value);
  });
  const name = (label: string, channel: number | null | undefined) => (channel != null ? `CH${channel} ${label}` : label);
  return (
    <div role="note" aria-label={t("edit.liveTitle")} className="bg-primary/8 border-b px-4 py-2 text-xs">
      <div className="flex items-center gap-1.5 font-medium">
        <Info className="text-primary size-3.5 shrink-0" />
        {panel ? t("edit.panelTitle") : t("edit.liveTitle")}
      </div>
      <p className="text-muted-foreground mt-0.5">
        {panel ? (diffs.length ? t("edit.panelDiffers") : t("edit.panelSame")) : diffs.length ? t("edit.liveDiffers") : t("edit.liveSame")}
      </p>
      {diffs.length > 0 && (
        <ul className="mt-1 flex flex-wrap gap-x-4 gap-y-0.5">
          {diffs.slice(0, SHOWN).map((v) => (
            <li key={v.path} className="num">
              {t("edit.liveDiff", {
                name: name(labelOf(v), v.channel),
                live: formatValue(v),
                initial: formatValue(start.get(v.path)!),
              })}
            </li>
          ))}
          {diffs.length > SHOWN && <li className="text-muted-foreground">{t("edit.liveMore", { count: diffs.length - SHOWN })}</li>}
        </ul>
      )}
    </div>
  );
}

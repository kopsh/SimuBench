import { useMemo, useState } from "react";
import { useTranslation } from "react-i18next";
import { Link } from "react-router-dom";
import type { Catalog } from "@/api/client";
import { Skeleton } from "@/components/ui/skeleton";
import { CLASS_IDS, ClassIcon } from "@/features/workbench/classes";
import { cn } from "@/lib/utils";
import { classCounts, filterInstruments } from "./model";

/** The instrument library: every model the catalog has (built in and the workspace's own), class
 * chips with live counts, the shared search, each card opening the model's acceptance page. */
export function InstrumentLibrary({ catalog, loading, query }: { catalog: Catalog | undefined; loading: boolean; query: string }) {
  const { t } = useTranslation();
  const [cls, setCls] = useState<string | null>(null);
  const all = catalog?.instruments ?? [];
  const chips = useMemo(() => classCounts(catalog?.instruments ?? [], CLASS_IDS), [catalog]);
  const shown = useMemo(() => filterInstruments(catalog?.instruments ?? [], query, cls, CLASS_IDS), [catalog, query, cls]);

  return (
    <div data-testid="instrument-list">
      <div className="mb-3 flex flex-wrap gap-1.5" role="group" aria-label={t("home.library.filter")}>
        <Chip on={cls === null} onClick={() => setCls(null)} label={t("home.library.all")} count={all.length} />
        {chips.map((c) => (
          <Chip
            key={c.cls}
            on={cls === c.cls}
            onClick={() => setCls(cls === c.cls ? null : c.cls)}
            label={t(`acceptance.class.${c.cls}`, { defaultValue: c.cls })}
            count={c.count}
          />
        ))}
      </div>
      {loading ? (
        <div className="grid gap-2.5 sm:grid-cols-2 lg:grid-cols-4">
          {Array.from({ length: 8 }, (_, i) => (
            <Skeleton key={i} className="h-16 rounded-xl" />
          ))}
        </div>
      ) : shown.length === 0 ? (
        <p className="text-muted-foreground rounded-xl border border-dashed px-4 py-8 text-center text-sm">{t("home.library.none")}</p>
      ) : (
        <ul className="grid gap-2.5 sm:grid-cols-2 lg:grid-cols-4">
          {shown.map((i) => (
            <li key={i.id}>
              <Link
                to={`/i/${i.id}`}
                title={i.description || i.id}
                className="bg-card/80 hover:border-primary/50 focus-visible:border-primary group flex items-center gap-3 rounded-xl border px-3.5 py-3 transition-[border-color,transform] outline-none hover:-translate-y-0.5 motion-reduce:hover:translate-y-0"
              >
                <span className="bg-plot grid size-9 shrink-0 place-items-center rounded-lg border border-white/5">
                  <ClassIcon cls={i.instrument_class} className="size-4 text-[var(--ch1)]" />
                </span>
                <span className="min-w-0 flex-1">
                  <span className="num block truncate text-[13px] font-medium">{i.model}</span>
                  <span className="text-muted-foreground block truncate text-[11.5px]">{i.manufacturer}</span>
                </span>
                {i.workspace ? (
                  <span className="border-sim/40 text-sim shrink-0 rounded border px-1.5 text-[10px]">{t("home.library.mine")}</span>
                ) : (
                  i.channels > 1 && <span className="num text-muted-foreground shrink-0 text-[10.5px]">{t("home.channelCount", { count: i.channels })}</span>
                )}
              </Link>
            </li>
          ))}
        </ul>
      )}
      <p className="text-muted-foreground mt-3 rounded-xl border border-dashed px-4 py-3 text-[12.5px]">{t("home.library.missing")}</p>
    </div>
  );
}

function Chip({ on, onClick, label, count }: { on: boolean; onClick: () => void; label: string; count: number }) {
  return (
    <button
      type="button"
      aria-pressed={on}
      onClick={onClick}
      className={cn(
        "inline-flex h-7 items-center gap-1.5 rounded-full border px-3 text-xs transition-colors",
        on ? "border-primary/60 bg-primary/15 text-foreground" : "text-muted-foreground hover:text-foreground",
      )}
    >
      {label}
      <span className="num text-muted-foreground text-[10.5px]">{count}</span>
    </button>
  );
}

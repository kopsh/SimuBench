import { History } from "lucide-react";
import { clockLabel } from "@/lib/enums";
import { useTranslation } from "react-i18next";
import { Link } from "react-router-dom";
import { useRuns } from "@/api/hooks";
import { formatClock } from "@/lib/format";

const SHOWN = 8;

/** The latest run records on the home page; shown only when the service writes records. */
export function RecentRuns() {
  const { t } = useTranslation();
  const runs = useRuns();
  const list = runs.data ?? [];
  if (!list.length) return null;
  return (
    <section className="mt-8" aria-label={t("runs.title")}>
      <h2 className="mb-3 flex items-baseline gap-2 text-sm font-semibold">
        {t("runs.title")}
        <span className="text-muted-foreground num text-xs font-normal">{list.length}</span>
      </h2>
      <ul className="bg-card divide-y overflow-hidden rounded-xl border">
        {list.slice(0, SHOWN).map((r) => (
          <li key={r.run_id}>
            <Link to={`/runs/${r.run_id}`} className="hover:bg-accent/50 group flex items-center gap-4 px-4 py-2.5 transition-colors">
              <History className="text-muted-foreground size-4 shrink-0" />
              <div className="min-w-0 flex-1">
                <div className="flex items-center gap-2 text-sm">
                  <span className="truncate font-medium">{r.bench}</span>
                  {!r.ended && <span className="bg-ok/15 text-ok rounded px-1.5 text-[10px] font-medium">{t("runs.live")}</span>}
                </div>
                <div className="text-muted-foreground num text-xs">
                  {new Date(r.started * 1000).toLocaleString()} · {clockLabel(r.clock, t)} · {t("runs.events", { count: r.events })} ·{" "}
                  t = {formatClock(r.duration_ns)}
                </div>
              </div>
              <span className="text-primary text-xs font-medium opacity-70 transition-opacity group-hover:opacity-100">{t("runs.open")} →</span>
            </Link>
          </li>
        ))}
      </ul>
    </section>
  );
}

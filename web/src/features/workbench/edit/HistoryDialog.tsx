import { Bot, FileEdit, History, Monitor, RotateCcw, Terminal, Undo2 } from "lucide-react";
import { useState } from "react";
import { useTranslation } from "react-i18next";
import type { BenchData, Schemas } from "@/api/client";
import { useBenchHistory, useBenchVersion } from "@/api/hooks";
import { Button } from "@/components/ui/button";
import { Dialog, DialogContent, DialogDescription, DialogHeader, DialogTitle } from "@/components/ui/dialog";
import { ToggleGroup, ToggleGroupItem } from "@/components/ui/toggle-group";
import { cn } from "@/lib/utils";
import { useWorkbench } from "../store";
import { type DiffItem, diffItems, showValue } from "./diff";
import { restoreVersion, useEditor } from "./editor";

type Version = Schemas["BenchVersion"];

const SOURCE_ICON: Record<Version["source"], typeof Bot> = {
  ui: Monitor,
  agent: Bot,
  api: Terminal,
  external: FileEdit,
  restore: Undo2,
};

/**
 * Version history of the bench file (D35): who saved each version and when, what each version
 * changed item by item (or how it differs from the file now), and restoring an earlier version.
 * This is where an engineer reviews what an agent did to the bench.
 */
export function HistoryDialog({ path }: { path: string }) {
  const { t } = useTranslation();
  const open = useWorkbench((s) => s.historyOpen);
  const setOpen = useWorkbench((s) => s.setHistory);
  const doc = useEditor((s) => s.doc);
  const history = useBenchHistory(path, doc?.sha256, open);
  const versions = history.data ?? [];
  const [picked, setPicked] = useState<number | null>(null);
  const [against, setAgainst] = useState<"previous" | "current">("previous");
  const selected = versions.find((v) => v.seq === picked) ?? versions[0];
  const index = selected ? versions.indexOf(selected) : -1;
  const previous = index >= 0 ? versions[index + 1] : undefined;
  const isCurrent = !!selected && selected.sha256 === doc?.sha256;

  const mine = useBenchVersion(path, selected?.seq);
  const prev = useBenchVersion(path, against === "previous" ? previous?.seq : null);
  const before = against === "previous" ? (previous ? prev.data?.data : {}) : mine.data?.data;
  const after = against === "previous" ? mine.data?.data : (doc?.data as BenchData | undefined);
  const items = before && after ? diffItems(before as BenchData, after as BenchData) : null;

  return (
    <Dialog open={open} onOpenChange={setOpen}>
      <DialogContent className="flex h-[min(80vh,720px)] w-[min(96vw,1080px)] max-w-none flex-col gap-0 p-0 sm:max-w-none">
        <DialogHeader className="border-b px-5 py-3">
          <DialogTitle className="flex items-center gap-2 text-sm">
            <History className="size-4" /> {t("history.title")}
            <span className="text-muted-foreground num text-xs font-normal">{path}</span>
          </DialogTitle>
          <DialogDescription className="text-xs">{t("history.description")}</DialogDescription>
        </DialogHeader>
        <div className="grid min-h-0 flex-1 grid-cols-[280px_1fr]">
          <ol className="min-h-0 overflow-auto border-r p-2" aria-label={t("history.versions")}>
            {versions.length === 0 && <li className="text-muted-foreground p-3 text-xs">{t("history.empty")}</li>}
            {versions.map((v) => {
              const Icon = SOURCE_ICON[v.source];
              const active = v.seq === selected?.seq;
              return (
                <li key={v.seq}>
                  <button
                    type="button"
                    onClick={() => setPicked(v.seq)}
                    className={cn(
                      "hover:bg-accent flex w-full items-start gap-2 rounded-md px-2 py-1.5 text-left",
                      active && "bg-accent ring-primary/40 ring-1",
                    )}
                    aria-current={active}
                  >
                    <Icon className={cn("mt-0.5 size-3.5 shrink-0", v.source === "agent" ? "text-primary" : "text-muted-foreground")} />
                    <span className="min-w-0 flex-1">
                      <span className="flex items-center gap-1.5 text-xs font-medium">
                        {t(`history.source.${v.source}`)}
                        <span className="text-muted-foreground num font-normal">#{v.seq}</span>
                        {v.sha256 === doc?.sha256 && (
                          <span className="bg-ok/15 text-ok rounded px-1 text-[10px] font-medium">{t("history.current")}</span>
                        )}
                      </span>
                      <span className="text-muted-foreground num block text-[11px]">
                        {formatTime(v.saved_at)}
                        {v.layout_only && ` · ${t("history.layoutOnly")}`}
                      </span>
                    </span>
                  </button>
                </li>
              );
            })}
          </ol>
          <section className="flex min-h-0 flex-col">
            {selected && (
              <header className="flex flex-wrap items-center gap-2 border-b px-4 py-2">
                <span className="text-xs font-medium">
                  {t(`history.source.${selected.source}`)} <span className="num text-muted-foreground">#{selected.seq}</span>
                </span>
                <ToggleGroup
                  type="single"
                  size="sm"
                  variant="outline"
                  value={against}
                  onValueChange={(v) => v && setAgainst(v as typeof against)}
                  aria-label={t("history.compare")}
                >
                  <ToggleGroupItem value="previous" className="px-2 text-xs">
                    {t("history.againstPrevious")}
                  </ToggleGroupItem>
                  <ToggleGroupItem value="current" className="px-2 text-xs">
                    {t("history.againstCurrent")}
                  </ToggleGroupItem>
                </ToggleGroup>
                <Button
                  size="sm"
                  variant="outline"
                  className="ml-auto h-7 gap-1.5 text-xs"
                  disabled={isCurrent}
                  onClick={() => void restoreVersion(selected.seq).then((d) => d && setPicked(null))}
                >
                  <RotateCcw className="size-3.5" /> {t("history.restore")}
                </Button>
              </header>
            )}
            <div className="min-h-0 flex-1 overflow-auto p-4" data-testid="history-diff">
              {!items ? (
                <p className="text-muted-foreground text-xs">{t("common.loading")}</p>
              ) : items.length === 0 ? (
                <p className="text-muted-foreground text-xs">{against === "current" ? t("history.sameAsCurrent") : t("history.noChange")}</p>
              ) : (
                <>
                  <p className="text-muted-foreground mb-3 text-[11px]">
                    {against === "previous"
                      ? previous
                        ? t("history.explainPrevious", { seq: previous.seq })
                        : t("history.explainFirst")
                      : t("history.explainCurrent")}
                  </p>
                  <ul className="space-y-2">
                    {items.map((item, k) => (
                      <DiffRow key={k} item={item} />
                    ))}
                  </ul>
                </>
              )}
            </div>
          </section>
        </div>
      </DialogContent>
    </Dialog>
  );
}

const KIND_TONE: Record<DiffItem["kind"], string> = {
  added: "bg-ok/15 text-ok",
  removed: "bg-error/15 text-error",
  changed: "bg-warn/20 text-warn",
};

function DiffRow({ item }: { item: DiffItem }) {
  const { t } = useTranslation();
  return (
    <li className="rounded-md border">
      <div className="flex items-center gap-2 px-3 py-1.5 text-xs">
        <span className={cn("rounded px-1.5 py-0.5 text-[10px] font-medium", KIND_TONE[item.kind])}>{t(`history.kind.${item.kind}`)}</span>
        <span className="text-muted-foreground">{t(`history.section.${item.section}`)}</span>
        {item.name && <span className="num font-medium">{item.name}</span>}
      </div>
      {item.changes.length > 0 && (
        <table className="num w-full border-t text-[11px]">
          <tbody>
            {item.changes.map((c) => (
              <tr key={c.path} className="border-b last:border-0">
                <td className="text-muted-foreground w-1/3 px-3 py-1 align-top">{c.path}</td>
                <td className="px-3 py-1 align-top break-all">
                  {item.kind !== "added" && <span className={cn(item.kind === "changed" && "text-error line-through")}>{showValue(c.before)}</span>}
                  {item.kind === "changed" && <span className="text-muted-foreground px-1.5">→</span>}
                  {item.kind !== "removed" && <span className={cn(item.kind === "changed" && "text-ok")}>{showValue(c.after)}</span>}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </li>
  );
}

function formatTime(iso: string): string {
  const d = new Date(iso);
  const sameDay = d.toDateString() === new Date().toDateString();
  const time = d.toLocaleTimeString(undefined, { hour12: false });
  return sameDay ? time : `${d.toLocaleDateString()} ${time}`;
}

import { HardDrive, Trash2 } from "lucide-react";
import { useState } from "react";
import { useTranslation } from "react-i18next";
import { toast } from "sonner";
import { useCleanStorage, useStorage } from "@/api/hooks";
import { Button } from "@/components/ui/button";
import { Dialog, DialogContent, DialogDescription, DialogHeader, DialogTitle } from "@/components/ui/dialog";
import { Label } from "@/components/ui/label";
import { Skeleton } from "@/components/ui/skeleton";
import { Switch } from "@/components/ui/switch";
import { EmptyState, ErrorState } from "./StateViews";
import { STORAGE_KINDS, type StorageEntry, doomed, formatBytes, sumBytes, totalsByKind } from "./storage";

export type StorageViewProps = {
  entries: StorageEntry[];
  total: number;
  history: boolean;
  onHistory: (on: boolean) => void;
  confirming: boolean;
  onConfirming: (on: boolean) => void;
  busy: boolean;
  onClean: () => void;
};

/** The workspace's `.simubench/`: totals per kind, what lost its file, and the clean (#1238). */
export function StorageView({ entries, total, history, onHistory, confirming, onConfirming, busy, onClean }: StorageViewProps) {
  const { t } = useTranslation();
  const totals = totalsByKind(entries);
  const orphans = entries.filter((e) => e.orphan);
  const going = doomed(entries, history);
  return (
    <div className="space-y-4 text-xs">
      <dl className="space-y-1" data-testid="storage-totals">
        {STORAGE_KINDS.map((k) => (
          <div key={k} className="flex justify-between gap-3">
            <dt className="text-muted-foreground">{t(`storage.kind.${k}`)}</dt>
            <dd className="num text-right">{formatBytes(totals[k])}</dd>
          </div>
        ))}
        <div className="flex justify-between gap-3 border-t pt-1 font-medium">
          <dt>{t("storage.total")}</dt>
          <dd className="num text-right" data-testid="storage-total">
            {formatBytes(total)}
          </dd>
        </div>
      </dl>
      <div>
        <h3 className="mb-1 font-medium">{t("storage.orphans")}</h3>
        {orphans.length === 0 ? (
          <p className="text-muted-foreground" data-testid="storage-no-orphans">
            {t("storage.noOrphans")}
          </p>
        ) : (
          <>
            <p className="text-muted-foreground mb-1.5">{t("storage.orphansHint")}</p>
            <ul className="max-h-48 space-y-0.5 overflow-auto" data-testid="storage-orphans">
              {orphans.map((e) => {
                const kept = e.kind === "history" && !history;
                return (
                  <li key={`${e.kind}:${e.name}`} className="flex items-center gap-2" data-kept={kept || undefined}>
                    <span className="text-muted-foreground w-20 shrink-0">{t(`storage.kind.${e.kind}`)}</span>
                    <span className="num min-w-0 flex-1 truncate" title={e.name}>
                      {e.name}
                    </span>
                    <span className="text-muted-foreground shrink-0">{kept ? t("storage.kept") : ""}</span>
                    <span className="num w-16 shrink-0 text-right">{formatBytes(e.bytes)}</span>
                  </li>
                );
              })}
            </ul>
          </>
        )}
      </div>
      {orphans.length > 0 && (
        <div className="space-y-3 border-t pt-3">
          <div className="flex items-start gap-2">
            <Switch id="storage-history" size="sm" checked={history} onCheckedChange={onHistory} className="mt-0.5" />
            <Label htmlFor="storage-history" className="flex flex-col items-start gap-0.5 text-xs font-normal">
              <span>{t("storage.withHistory")}</span>
              <span className="text-muted-foreground">{t("storage.withHistoryHint")}</span>
            </Label>
          </div>
          {confirming ? (
            <div className="bg-destructive/10 flex items-center gap-2 rounded-md p-2" role="alert">
              <p className="flex-1">{t("storage.confirm", { count: going.length, size: formatBytes(sumBytes(going)) })}</p>
              <Button size="sm" variant="ghost" onClick={() => onConfirming(false)} disabled={busy}>
                {t("common.cancel")}
              </Button>
              <Button size="sm" variant="destructive" onClick={onClean} disabled={busy} data-testid="storage-confirm">
                {t("storage.delete")}
              </Button>
            </div>
          ) : (
            <div className="flex justify-end">
              <Button size="sm" variant="outline" disabled={going.length === 0} onClick={() => onConfirming(true)} data-testid="storage-clean">
                <Trash2 className="size-3.5" /> {t("storage.clean", { count: going.length, size: formatBytes(sumBytes(going)) })}
              </Button>
            </div>
          )}
        </div>
      )}
    </div>
  );
}

/** Settings → workspace data: what `.simubench/` holds and the clean of what lost its file (#1238). */
export function StorageDialog({ open, onOpenChange }: { open: boolean; onOpenChange: (open: boolean) => void }) {
  const { t } = useTranslation();
  const report = useStorage(open);
  const clean = useCleanStorage();
  const [history, setHistory] = useState(false);
  const [confirming, setConfirming] = useState(false);
  return (
    <Dialog
      open={open}
      onOpenChange={(o) => {
        if (!o) setConfirming(false);
        onOpenChange(o);
      }}
    >
      <DialogContent className="max-h-[85vh] overflow-auto sm:max-w-lg" data-testid="storage-dialog">
        <DialogHeader>
          <DialogTitle>{t("storage.title")}</DialogTitle>
          <DialogDescription>{t("storage.description")}</DialogDescription>
        </DialogHeader>
        {report.isPending ? (
          <div className="space-y-2">
            <Skeleton className="h-4 w-full" />
            <Skeleton className="h-4 w-2/3" />
          </div>
        ) : report.isError ? (
          <ErrorState error={report.error} onRetry={() => void report.refetch()} />
        ) : report.data.entries.length === 0 ? (
          <EmptyState icon={<HardDrive className="size-6" />} title={t("storage.empty")} body={t("storage.emptyBody")} />
        ) : (
          <StorageView
            entries={report.data.entries}
            total={report.data.total_bytes}
            history={history}
            onHistory={(on) => {
              setHistory(on);
              setConfirming(false);
            }}
            confirming={confirming}
            onConfirming={setConfirming}
            busy={clean.isPending}
            onClean={() =>
              clean.mutate(history, {
                onSuccess: (r) => {
                  setConfirming(false);
                  toast.success(t("storage.cleaned", { count: r.removed.length, size: formatBytes(r.bytes) }));
                },
                onError: (e) => toast.error(t("storage.cleanFailed"), { description: String(e) }),
              })
            }
          />
        )}
      </DialogContent>
    </Dialog>
  );
}

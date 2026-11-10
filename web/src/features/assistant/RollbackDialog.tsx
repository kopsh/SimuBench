// "Restore to before the last turn" (#876): what will be restored and deleted, what was changed
// since and would be lost, which running bench has to stop — all said before anything happens.
import { useTranslation } from "react-i18next";
import { toast } from "sonner";
import { useRollback, useRollbackPlan } from "@/api/hooks";
import { Button } from "@/components/ui/button";
import { Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from "@/components/ui/dialog";
import { Skeleton } from "@/components/ui/skeleton";
import { ChangeLine } from "@/features/instrument/HistoryTab";
import { type RollbackPlan, rollbackSummary } from "./model";

export function RollbackDialog({ bench, open, onOpenChange }: { bench: string; open: boolean; onOpenChange: (open: boolean) => void }) {
  const { t } = useTranslation();
  const plan = useRollbackPlan(bench, open);
  const rollback = useRollback(bench);
  const data = plan.data;
  const run = () => {
    if (!data?.turn_id) return;
    rollback.mutate(
      { turn_id: data.turn_id, confirm_lost: true, stop_sessions: true },
      {
        onSuccess: (r) => {
          if (r.done) {
            toast.success(t("assistant.rollback.done"));
            onOpenChange(false);
          }
        },
        onError: (e) => toast.error((e as Error).message),
      },
    );
  };
  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="sm:max-w-lg">
        <DialogHeader>
          <DialogTitle>{t("assistant.rollback.title")}</DialogTitle>
          <DialogDescription className="sr-only">{t("assistant.rollback.title")}</DialogDescription>
        </DialogHeader>
        {plan.isLoading ? <Skeleton className="h-16 w-full" /> : <PlanText plan={data} />}
        <DialogFooter>
          <Button variant="outline" onClick={() => onOpenChange(false)}>
            {t("common.cancel")}
          </Button>
          <Button disabled={!data?.turn_id || rollback.isPending} onClick={run}>
            {data?.sessions.length ? t("assistant.rollback.confirmStop") : t("assistant.rollback.confirm")}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}

export function PlanText({ plan }: { plan: RollbackPlan | undefined }) {
  const { t } = useTranslation();
  if (!plan?.turn_id) return <p className="text-muted-foreground text-sm">{t("assistant.rollback.nothing")}</p>;
  const s = rollbackSummary(plan);
  return (
    <div className="space-y-2 text-[13px] leading-relaxed" data-testid="rollback-plan">
      {s.restores.length > 0 && <p>{t("assistant.rollback.restores", { files: s.restores.join("、") })}</p>}
      {s.deletes.length > 0 && <p className="font-medium">{t("assistant.rollback.deletes", { files: s.deletes.join("、") })}</p>}
      {s.modified.map((f) => (
        <div key={f.path} className="border-warn/40 bg-warn/10 rounded border px-2 py-1">
          <p>{t("assistant.rollback.modified", { file: f.path })}</p>
          <ul className="mt-1 space-y-0.5">
            {f.lost.map((c, i) => (
              <ChangeLine key={i} change={c} />
            ))}
          </ul>
        </div>
      ))}
      {plan.sessions.length > 0 && <p className="text-warn">{t("assistant.rollback.sessions", { count: plan.sessions.length })}</p>}
    </div>
  );
}

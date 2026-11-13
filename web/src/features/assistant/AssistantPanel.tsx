// The assistant panel of a bench (#877): its conversation from the server, the running turn
// followed live, and the dialogs (settings, restore). Bound to the bench file, not the session: a
// stopped and restarted bench keeps its conversation.
import { useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { useTranslation } from "react-i18next";
import { toast } from "sonner";
import {
  assistantKeys,
  useAssistantConversation,
  useClearAssistant,
  useRollbackPlan,
  useSendAssistant,
  useStopAssistant,
} from "@/api/hooks";
import { Button } from "@/components/ui/button";
import { Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from "@/components/ui/dialog";
import { Skeleton } from "@/components/ui/skeleton";
import { AssistantSettings } from "./AssistantSettings";
import { AssistantView } from "./AssistantView";
import { type AssistantConversation, canRollBack, rollbackSummary } from "./model";
import { RollbackDialog } from "./RollbackDialog";
import { useLiveTurn } from "./stream";

export function AssistantPanel({ bench, onClose }: { bench: string; onClose?: () => void }) {
  const { t } = useTranslation();
  const qc = useQueryClient();
  const conv = useAssistantConversation(bench);
  const live = useLiveTurn(bench, conv.data);
  const send = useSendAssistant(bench);
  const stop = useStopAssistant(bench);
  const clear = useClearAssistant(bench);
  const [settings, setSettings] = useState(false);
  const [rollback, setRollback] = useState(false);
  const [clearing, setClearing] = useState(false);
  const turns = conv.data?.turns ?? [];
  const offer = turns.length > 0 && canRollBack(turns, turns.length - 1);
  const plan = useRollbackPlan(bench, offer);

  if (conv.isLoading) {
    return (
      <div className="bg-sidebar flex h-full flex-col gap-2 p-3">
        <Skeleton className="h-6 w-1/2" />
        <Skeleton className="h-full w-full" />
      </div>
    );
  }
  return (
    <>
      <AssistantView
        turns={turns}
        live={live}
        configured={!!conv.data?.configured}
        compacted={conv.data?.compacted}
        sending={send.isPending}
        rollbackDeletes={plan.data ? rollbackSummary(plan.data).deletes : []}
        onSend={(text, stopConsent) =>
          send.mutate({ text, stopConsent }, {
            onSuccess: (turn) =>
              qc.setQueryData<AssistantConversation>(assistantKeys.conversation(bench), (c) =>
                c ? { ...c, running: true, turns: [...c.turns, turn] } : c,
              ),
            onError: (e) => toast.error((e as Error).message),
          })
        }
        onStop={() =>
          stop.mutate(undefined, {
            onError: (e) => toast.error((e as Error).message),
          })
        }
        onClear={() => setClearing(true)}
        onSettings={() => setSettings(true)}
        onClose={onClose}
        onRollback={() => setRollback(true)}
      />
      <AssistantSettings
        open={settings}
        onOpenChange={(open) => {
          setSettings(open);
          if (!open)
            void qc.invalidateQueries({
              queryKey: assistantKeys.conversation(bench),
            });
        }}
      />
      <RollbackDialog bench={bench} open={rollback} onOpenChange={setRollback} />
      <Dialog open={clearing} onOpenChange={setClearing}>
        <DialogContent className="sm:max-w-sm">
          <DialogHeader>
            <DialogTitle>{t("assistant.clear")}</DialogTitle>
            <DialogDescription>{t("assistant.clearConfirm")}</DialogDescription>
          </DialogHeader>
          <DialogFooter>
            <Button variant="outline" onClick={() => setClearing(false)}>
              {t("common.cancel")}
            </Button>
            <Button
              variant="destructive"
              disabled={clear.isPending}
              onClick={() =>
                clear.mutate(undefined, {
                  onSuccess: () => setClearing(false),
                  onError: (e) => toast.error((e as Error).message),
                })
              }
            >
              {t("assistant.clear")}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </>
  );
}

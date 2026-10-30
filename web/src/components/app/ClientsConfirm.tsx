import { useTranslation } from "react-i18next";
import { create } from "zustand";
import { ApiError } from "@/api/client";
import { Button } from "@/components/ui/button";
import { Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from "@/components/ui/dialog";

/** What the engine refuses while scripts are connected unless confirmed (#121). */
export type ClientsAction = "start" | "rebuild" | "stop" | "reset" | "advance";
/** "later" only for a rebuild: back to Operate on the running bench, rebuild when convenient. */
export type ClientsChoice = "force" | "later" | "cancel";

/** The user declined an operation in the confirm dialog: not an error to report. */
export class Cancelled extends Error {
  constructor(public choice: Exclude<ClientsChoice, "force">) {
    super(choice);
  }
}

export const isCancelled = (e: unknown): e is Cancelled => e instanceof Cancelled;

type Detail = { reason?: string; clients?: string[]; instrument?: string };

function detailOf(e: unknown, reason: string): Detail | null {
  if (!(e instanceof ApiError) || e.status !== 409) return null;
  const d = e.detail as Detail | undefined;
  return d?.reason === reason ? d : null;
}

/** The clients of a 409 "clients_connected", or null for any other error. */
export const connectedClients = (e: unknown): string[] | null => detailOf(e, "clients_connected")?.clients ?? null;

/** The instrument of a 409 "remote" (a UI operation on an instrument in remote control), or null. */
export const remoteInstrument = (e: unknown): string | null => detailOf(e, "remote")?.instrument ?? null;

type Pending = { action: ClientsAction; clients: string[]; resolve: (c: ClientsChoice) => void };

// ``asked`` keys the dialog: every question gets a fresh instance, so a reopen never reuses one
// whose closing animation left its content mounted (the new overlay would then cover it).
const useClientsConfirm = create<{ pending: Pending | null; asked: number }>(() => ({ pending: null, asked: 0 }));

function ask(action: ClientsAction, clients: string[]): Promise<ClientsChoice> {
  useClientsConfirm.getState().pending?.resolve("cancel");
  return new Promise((resolve) =>
    useClientsConfirm.setState((s) => ({ pending: { action, clients, resolve }, asked: s.asked + 1 })),
  );
}

/**
 * Run ``call``; when the engine answers that scripts are connected, ask the user and repeat it with
 * ``force`` on confirmation. A declined dialog throws {@link Cancelled}.
 */
export async function confirmClients<T>(action: ClientsAction, call: (force: boolean) => Promise<T>): Promise<T> {
  try {
    return await call(false);
  } catch (e) {
    const clients = connectedClients(e);
    if (!clients) throw e;
    const choice = await ask(action, clients);
    if (choice !== "force") throw new Cancelled(choice);
    return call(true);
  }
}

/** The one confirm dialog of the app, mounted at the root. */
export function ClientsConfirmDialog() {
  const { t } = useTranslation();
  const pending = useClientsConfirm((s) => s.pending);
  const asked = useClientsConfirm((s) => s.asked);
  const close = (choice: ClientsChoice) => {
    pending?.resolve(choice);
    useClientsConfirm.setState({ pending: null });
  };
  const action = pending?.action ?? "start";
  return (
    <Dialog key={asked} open={!!pending} onOpenChange={(v) => !v && close("cancel")}>
      <DialogContent>
        <DialogHeader>
          <DialogTitle>{t(`clients.${action}.title`)}</DialogTitle>
          <DialogDescription>{t(`clients.${action}.body`)}</DialogDescription>
        </DialogHeader>
        <ul className="rounded-md border" aria-label={t("clients.list")}>
          {pending?.clients.map((c) => (
            <li key={c} className="num px-3 py-2 text-xs">
              {c}
            </li>
          ))}
        </ul>
        <DialogFooter>
          <Button variant="outline" size="sm" onClick={() => close("cancel")}>
            {t("common.cancel")}
          </Button>
          {action === "rebuild" && (
            <Button variant="outline" size="sm" onClick={() => close("later")}>
              {t("clients.rebuild.later")}
            </Button>
          )}
          <Button size="sm" onClick={() => close("force")}>
            {t(`clients.${action}.confirm`)}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}

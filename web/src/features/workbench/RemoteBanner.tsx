import { Hand, Plug } from "lucide-react";
import { useTranslation } from "react-i18next";
import { toast } from "sonner";
import type { InstrumentInfo } from "@/api/client";
import { useGoLocal } from "@/api/hooks";
import { Button } from "@/components/ui/button";
import { cn } from "@/lib/utils";

/**
 * A script is connected to the instrument's port: it is remote, and the UI's settings, console
 * and panel buttons are refused until LOCAL, which lasts until the script's next command (#121).
 */
export function RemoteBanner({ sid, inst, className }: { sid: string; inst: InstrumentInfo; className?: string }) {
  const { t } = useTranslation();
  const local = useGoLocal(sid);
  if (!inst.remote.length) return null;
  return (
    <div
      role="status"
      aria-label={inst.local ? t("remote.localTitle") : t("remote.title")}
      data-testid="remote-banner"
      className={cn("bg-warn/10 flex items-start gap-2 border-b px-4 py-2 text-xs", inst.local && "bg-primary/8", className)}
    >
      {inst.local ? <Hand className="text-primary mt-0.5 size-3.5 shrink-0" /> : <Plug className="text-warn mt-0.5 size-3.5 shrink-0" />}
      <div className="min-w-0 flex-1">
        <div className="font-medium">{inst.local ? t("remote.localTitle") : t("remote.title")}</div>
        <p className="text-muted-foreground mt-0.5">{inst.local ? t("remote.localBody") : t("remote.body")}</p>
        <p className="num text-muted-foreground mt-0.5 truncate" title={inst.remote.join(", ")}>
          {inst.remote.join(", ")}
        </p>
      </div>
      {!inst.local && (
        <Button
          variant="outline"
          size="sm"
          className="h-7 shrink-0 text-xs"
          disabled={local.isPending}
          onClick={() => local.mutate(inst.name, { onError: (e) => toast.error((e as Error).message) })}
        >
          {t("remote.local")}
        </Button>
      )}
    </div>
  );
}

import { Hand, Lock, Plug } from "lucide-react";
import { useTranslation } from "react-i18next";
import { toast } from "sonner";
import type { InstrumentInfo } from "@/api/client";
import { useGoLocal } from "@/api/hooks";
import { Button } from "@/components/ui/button";
import { cn } from "@/lib/utils";

/**
 * A script is connected to the instrument's port: it is remote, and the UI's settings, console
 * and panel buttons are refused until LOCAL, which lasts until the script's next command (#121).
 *
 * Two layouts (#1273): above a screen (`wide`) the full explanation with LOCAL on the right; in the
 * inspector's narrow column (`compact`) a title row that carries LOCAL, one short line and the
 * clients in full, each on its own line, so the text never shrinks into a column beside the
 * button. Beside the instrument's own screen the inspector shows only `RemoteMark`.
 */
export function RemoteBanner({
  sid,
  inst,
  compact,
  className,
}: {
  sid: string;
  inst: InstrumentInfo;
  compact?: boolean;
  className?: string;
}) {
  const { t } = useTranslation();
  const local = useGoLocal(sid);
  if (!inst.remote.length) return null;
  const title = inst.local ? t("remote.localTitle") : t("remote.title");
  const icon = inst.local ? <Hand className="text-primary size-3.5 shrink-0" /> : <Plug className="text-warn size-3.5 shrink-0" />;
  const button = !inst.local && (
    <Button
      variant="outline"
      size="sm"
      className={cn("shrink-0 text-xs", compact ? "h-6 px-2" : "h-7")}
      disabled={local.isPending}
      onClick={() => local.mutate(inst.name, { onError: (e) => toast.error((e as Error).message) })}
    >
      {t("remote.local")}
    </Button>
  );
  const tone = cn("bg-warn/10 border-b text-xs", inst.local && "bg-primary/8");
  if (compact)
    return (
      <div
        role="status"
        aria-label={title}
        title={inst.local ? t("remote.localBody") : t("remote.body")}
        data-testid="remote-banner"
        className={cn(tone, "px-3 py-1.5", className)}
      >
        <div className="flex min-h-6 items-center gap-1.5">
          {icon}
          <span className="min-w-0 flex-1 font-medium">{title}</span>
          {button}
        </div>
        <p className="text-muted-foreground">{inst.local ? t("remote.localShort") : t("remote.short")}</p>
        <ul className="num text-muted-foreground select-all" aria-label={t("remote.clients")}>
          {inst.remote.map((c) => (
            <li key={c} className="break-all">
              {c}
            </li>
          ))}
        </ul>
      </div>
    );
  return (
    <div role="status" aria-label={title} data-testid="remote-banner" className={cn(tone, "flex items-start gap-2 px-4 py-2", className)}>
      <span className="mt-0.5">{icon}</span>
      <div className="min-w-0 flex-1">
        <div className="font-medium">{title}</div>
        <p className="text-muted-foreground mt-0.5">{inst.local ? t("remote.localBody") : t("remote.body")}</p>
        <p className="num text-muted-foreground mt-0.5 break-all select-all">{inst.remote.join(", ")}</p>
      </div>
      {button}
    </div>
  );
}

/**
 * The inspector beside the instrument's own screen: the screen already carries the banner and its
 * LOCAL button, so the inspector only marks the state, with the clients on hover (#1273).
 */
export function RemoteMark({ inst }: { inst: InstrumentInfo }) {
  const { t } = useTranslation();
  if (!inst.remote.length) return null;
  const title = inst.local ? t("remote.localTitle") : t("remote.title");
  return (
    <span
      role="status"
      aria-label={title}
      title={`${title} · ${inst.remote.join(", ")}`}
      data-testid="remote-mark"
      className={cn(
        "inline-flex h-5 shrink-0 items-center gap-1 rounded-full border px-2 text-[11px] font-medium",
        inst.local ? "border-primary/40 text-primary bg-primary/8" : "border-warn/50 bg-warn/10 text-foreground",
      )}
    >
      {inst.local ? <Hand className="size-3" /> : <Lock className="text-warn size-3" />}
      {inst.local ? t("remote.localMark") : t("remote.mark")}
    </span>
  );
}

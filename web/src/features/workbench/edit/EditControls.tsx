import { CheckCircle2, Loader2, Pencil, Play, Redo2, RotateCw, Undo2 } from "lucide-react";
import { useEffect } from "react";
import { useTranslation } from "react-i18next";
import { useNavigate } from "react-router-dom";
import { toast } from "sonner";
import type { ApiError, Diagnostic, SessionInfo } from "@/api/client";
import { useStartSession } from "@/api/hooks";
import { Button } from "@/components/ui/button";
import { Kbd } from "@/components/ui/kbd";
import { ToggleGroup, ToggleGroupItem } from "@/components/ui/toggle-group";
import { Tooltip, TooltipContent, TooltipTrigger } from "@/components/ui/tooltip";
import { useWorkbench } from "../store";
import { redo, settled, undo, useEditor } from "./editor";

const mac = typeof navigator !== "undefined" && navigator.platform.toLowerCase().includes("mac");
const mod = mac ? "⌘" : "Ctrl+";

function typing(target: EventTarget | null): boolean {
  const el = target as HTMLElement | null;
  return !!el && (el.isContentEditable || ["INPUT", "TEXTAREA", "SELECT"].includes(el.tagName));
}

/** Run / Edit switch, and in Edit mode undo, redo and the save state. */
export function EditControls({ session }: { session: SessionInfo }) {
  const { t } = useTranslation();
  const { mode, setMode } = useWorkbench();
  const canEdit = !!session.bench_path;
  const { undo: undoStack, redo: redoStack, pending, lastError } = useEditor();

  useEffect(() => {
    if (mode !== "edit") return;
    const onKey = (e: KeyboardEvent) => {
      if (!(e.ctrlKey || e.metaKey) || typing(e.target)) return;
      const k = e.key.toLowerCase();
      if (k === "z" && !e.shiftKey) {
        e.preventDefault();
        void undo();
      } else if ((k === "z" && e.shiftKey) || k === "y") {
        e.preventDefault();
        void redo();
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [mode]);

  return (
    <div className="flex items-center gap-1.5">
      <Tooltip>
        <TooltipTrigger asChild>
          <span>
            <ToggleGroup
              type="single"
              size="sm"
              variant="outline"
              value={mode}
              onValueChange={(v) => v && setMode(v as "run" | "edit")}
              aria-label={t("edit.modeLabel")}
              disabled={!canEdit}
            >
              <ToggleGroupItem value="run" className="h-8 gap-1.5 px-2.5 text-xs" aria-label={t("edit.mode.run")}>
                <Play className="size-3.5" /> {t("edit.mode.run")}
              </ToggleGroupItem>
              <ToggleGroupItem value="edit" className="h-8 gap-1.5 px-2.5 text-xs" aria-label={t("edit.mode.edit")}>
                <Pencil className="size-3.5" /> {t("edit.mode.edit")}
              </ToggleGroupItem>
            </ToggleGroup>
          </span>
        </TooltipTrigger>
        {!canEdit && <TooltipContent>{t("edit.noFile")}</TooltipContent>}
      </Tooltip>
      {mode === "edit" && (
        <>
          <IconAction label={t("edit.undo")} keys={`${mod}Z`} disabled={!undoStack.length} onClick={() => void undo()}>
            <Undo2 className="size-4" />
          </IconAction>
          <IconAction label={t("edit.redo")} keys={mac ? "⇧⌘Z" : "Ctrl+Y"} disabled={!redoStack.length} onClick={() => void redo()}>
            <Redo2 className="size-4" />
          </IconAction>
          <span className="text-muted-foreground inline-flex w-24 items-center gap-1 text-[11px]" aria-live="polite">
            {pending > 0 ? (
              <>
                <Loader2 className="size-3 animate-spin" /> {t("edit.saving")}
              </>
            ) : lastError ? (
              <span className="text-error truncate" title={lastError}>
                {t("edit.failed")}
              </span>
            ) : (
              <>
                <CheckCircle2 className="text-ok size-3" /> {t("edit.saved")}
              </>
            )}
          </span>
        </>
      )}
    </div>
  );
}

function IconAction({
  label,
  keys,
  disabled,
  onClick,
  children,
}: {
  label: string;
  keys: string;
  disabled: boolean;
  onClick: () => void;
  children: React.ReactNode;
}) {
  return (
    <Tooltip>
      <TooltipTrigger asChild>
        <Button variant="ghost" size="icon" className="size-8" disabled={disabled} onClick={onClick} aria-label={label}>
          {children}
        </Button>
      </TooltipTrigger>
      <TooltipContent>
        {label} <Kbd>{keys}</Kbd>
      </TooltipContent>
    </Tooltip>
  );
}

/** Shown while the bench file differs from what the running session was started from. */
export function RestartBanner({ session }: { session: SessionInfo }) {
  const { t } = useTranslation();
  const navigate = useNavigate();
  const start = useStartSession();
  const doc = useEditor((s) => s.doc);
  if (!doc || doc.path !== session.bench_path || !session.bench_fingerprint || doc.fingerprint === session.bench_fingerprint) return null;

  const restart = async () => {
    await settled();
    const path = useEditor.getState().doc?.path ?? session.bench_path!;
    start.mutate(path, {
      onSuccess: (s) => {
        toast.success(t("edit.restarted"));
        navigate(`/s/${s.id}`);
      },
      onError: (e) => {
        const diags = ((e as ApiError).detail as { diagnostics?: Diagnostic[] } | undefined)?.diagnostics ?? [];
        const first = diags.find((d) => d.severity === "error");
        toast.error(t("edit.startFailed"), { description: first ? `${first.code} ${first.message}` : (e as Error).message });
        useWorkbench.getState().setBottomTab("problems");
      },
    });
  };

  return (
    <div className="bg-warn/10 flex h-8 shrink-0 items-center justify-center gap-3 border-b px-3 text-xs" role="status">
      <span>{t("edit.stale")}</span>
      <Button size="sm" variant="outline" className="h-6 gap-1.5 px-2 text-xs" disabled={start.isPending} onClick={() => void restart()}>
        <RotateCw className={start.isPending ? "size-3 animate-spin" : "size-3"} />
        {start.isPending ? t("edit.restarting") : t("edit.restart")}
      </Button>
    </div>
  );
}

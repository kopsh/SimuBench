import { CheckCircle2, Gauge, Hammer, Loader2, Redo2, Undo2 } from "lucide-react";
import { useEffect } from "react";
import { useTranslation } from "react-i18next";
import { useNavigate } from "react-router-dom";
import { toast } from "sonner";
import type { ApiError, Diagnostic, SessionInfo } from "@/api/client";
import { useStartSession } from "@/api/hooks";
import { isCancelled } from "@/components/app/ClientsConfirm";
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

/** The bench file differs from the running bench (layout and notes aside): Operate rebuilds it. */
export function benchChanged(session: SessionInfo, doc = useEditor.getState().doc): boolean {
  return !!(doc && doc.path === session.bench_path && session.bench_fingerprint && doc.fingerprint !== session.bench_fingerprint);
}

/**
 * Rebuild the bench from the current file and go to Operate (issue 31). With scripts connected the
 * user confirms first, or picks "later": Operate on the running bench, rebuilt when convenient (#121).
 * A definition that does not validate keeps the user in Build with the problems panel open.
 */
export function useRebuild(session: SessionInfo) {
  const { t } = useTranslation();
  const navigate = useNavigate();
  const setMode = useWorkbench((s) => s.setMode);
  const start = useStartSession();
  const run = async () => {
    await settled(); // the file on disk is the single source of truth (D25)
    const path = useEditor.getState().doc?.path ?? session.bench_path!;
    start.mutate(
      { bench_path: path, rebuild: true },
      {
        onSuccess: (s) => {
          setMode("run");
          toast.success(t("edit.rebuilt"), { description: t("edit.rebuiltHint") });
          navigate(`/s/${s.id}`);
        },
        onError: (e) => {
          if (isCancelled(e)) {
            if (e.choice === "later") setMode("run"); // the top bar offers the rebuild
            return;
          }
          const diags = ((e as ApiError).detail as { diagnostics?: Diagnostic[] } | undefined)?.diagnostics ?? [];
          const first = diags.find((d) => d.severity === "error");
          toast.error(t("edit.blockedInvalid"), {
            description: first ? `${first.code} ${first.message}` : (e as Error).message,
          });
          setMode("edit");
          useWorkbench.getState().setBottomTab("problems");
        },
      },
    );
  };
  return { run: () => void run(), isPending: start.isPending };
}

/** Operate / Build switch, and in Build mode undo, redo and the save state.

Switching back to Operate rebuilds the bench from the current file when it changed (issue 31);
a definition that does not validate keeps the user in Build with the problems panel open. */
export function EditControls({ session }: { session: SessionInfo }) {
  const { mode, setMode } = useWorkbench();
  const canEdit = !!session.bench_path;
  const { undo: undoStack, redo: redoStack, pending, lastError } = useEditor();
  const { t } = useTranslation();
  const rebuild = useRebuild(session);

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

  const switchMode = (v: string) => {
    if (v !== "run" || !benchChanged(session)) {
      setMode(v as "run" | "edit");
      return;
    }
    rebuild.run();
  };

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
              onValueChange={(v) => v && switchMode(v)}
              aria-label={t("edit.modeLabel")}
              disabled={!canEdit || rebuild.isPending}
            >
              <ToggleGroupItem value="run" className="h-8 gap-1.5 px-2.5 text-xs" aria-label={t("edit.mode.run")}>
                <Gauge className="size-3.5" /> {t("edit.mode.run")}
              </ToggleGroupItem>
              <ToggleGroupItem value="edit" className="h-8 gap-1.5 px-2.5 text-xs" aria-label={t("edit.mode.edit")}>
                <Hammer className="size-3.5" /> {t("edit.mode.edit")}
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

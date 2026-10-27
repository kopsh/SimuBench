import { AlertCircle } from "lucide-react";
import { type ReactNode, useState } from "react";
import { useTranslation } from "react-i18next";
import type { StateVarInfo } from "@/api/client";
import { useConfigure } from "@/api/hooks";
import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { Switch } from "@/components/ui/switch";
import { Tooltip, TooltipContent, TooltipTrigger } from "@/components/ui/tooltip";
import { displayUnit, engParts, parseEngineering } from "@/lib/format";
import { cn } from "@/lib/utils";
import { labelOf, numeric, shortForm } from "./model";

interface Props {
  sid: string;
  instrument: string;
  v: StateVarInfo;
  disabled?: boolean;
  compact?: boolean;
  /** Edit something other than the running instrument (the bench file's initial state). */
  onCommit?: (value: unknown) => void;
  /** Shown after the control, e.g. a reset button. */
  trailing?: ReactNode;
}

/** One editable state variable; edits are out-of-band "configure" actions (architecture.md 5.2). */
export function StateField({ sid, instrument, v, disabled, compact, onCommit, trailing }: Props) {
  const { t } = useTranslation();
  const configure = useConfigure(sid);
  const [error, setError] = useState<string | null>(null);
  const [confirm, setConfirm] = useState<null | (() => void)>(null);

  const commit = (value: unknown) => {
    if (onCommit) {
      onCommit(value);
      return;
    }
    const run = () =>
      configure.mutate(
        { instrument, path: v.path, value },
        {
          onSuccess: (r) => setError(r.ok ? null : `${r.error?.code}, ${r.error?.message}`),
          onError: (e) => setError((e as Error).message),
        },
      );
    if (v.dangerous && value === true) setConfirm(() => run);
    else run();
  };

  const readonly = disabled || v.readonly;
  let control: React.ReactNode;
  if (v.type === "bool") {
    control = (
      <Switch
        checked={!!v.value}
        onCheckedChange={(c) => commit(c)}
        disabled={readonly}
        aria-label={labelOf(v)}
        className={cn(v.dangerous && !!v.value && "data-[state=checked]:bg-ok")}
      />
    );
  } else if (v.type === "enum") {
    control = (
      <Select value={String(v.value)} onValueChange={(x) => commit(x)} disabled={readonly}>
        <SelectTrigger size="sm" className="num h-7 w-full min-w-24 text-xs" aria-label={labelOf(v)}>
          <SelectValue />
        </SelectTrigger>
        <SelectContent>
          {(v.options ?? []).map((o) => (
            <SelectItem key={o} value={o} className="num text-xs">
              {v.option_labels?.[o] ?? shortForm(o)}{" "}
              <span className="text-muted-foreground">{o !== shortForm(o) && !v.option_labels?.[o] ? o : ""}</span>
            </SelectItem>
          ))}
        </SelectContent>
      </Select>
    );
  } else if (v.type === "float" || v.type === "int") {
    control = <NumberInput v={v} disabled={readonly} onCommit={commit} onInvalid={() => setError(t("workbench.refused"))} />;
  } else {
    control = <span className="num">{String(v.value)}</span>;
  }

  return (
    <div className={cn("grid items-center gap-x-3", compact ? "grid-cols-[1fr_auto]" : "grid-cols-[minmax(6rem,1fr)_minmax(8rem,1.4fr)]")}>
      <label className="text-muted-foreground truncate text-xs" title={v.path}>
        {labelOf(v)}
      </label>
      <div className="flex min-w-0 items-center justify-end gap-1.5">
        {control}
        {trailing}
        {error && (
          <Tooltip>
            <TooltipTrigger asChild>
              <AlertCircle className="text-error size-4 shrink-0" aria-label={error} />
            </TooltipTrigger>
            <TooltipContent>
              {t("workbench.refused")}: {error}
            </TooltipContent>
          </Tooltip>
        )}
      </div>
      <Dialog open={!!confirm} onOpenChange={(o) => !o && setConfirm(null)}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>
              {labelOf(v)} → {t("common.on")}
            </DialogTitle>
            <DialogDescription>
              {instrument} · <span className="num">{v.path}</span>
            </DialogDescription>
          </DialogHeader>
          <DialogFooter>
            <Button variant="outline" onClick={() => setConfirm(null)}>
              {t("common.cancel")}
            </Button>
            <Button
              autoFocus
              onClick={() => {
                confirm?.();
                setConfirm(null);
              }}
            >
              {t("common.confirm")}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </div>
  );
}

function NumberInput({
  v,
  disabled,
  onCommit,
  onInvalid,
}: {
  v: StateVarInfo;
  disabled?: boolean;
  onCommit: (value: number | string) => void;
  onInvalid: () => void;
}) {
  const unit = v.display_unit ?? displayUnit(v.unit);
  const n = numeric(v.value);
  const parts = engParts(n, unit, 6);
  // Counts (points, averages, harmonics) read as plain integers, not engineering notation.
  const shown = !Number.isFinite(n) ? "INF" : v.type === "int" ? String(Math.round(n)) : `${parts.mantissa} ${parts.prefix}`;
  // While editing, the draft is what the user types; otherwise the field shows the live value.
  const [draft, setDraft] = useState<string | null>(null);
  const text = draft ?? shown.trim();

  const submit = () => {
    if (draft === null) return;
    setDraft(null);
    const parsed = parseEngineering(draft);
    if (parsed === null) {
      onInvalid();
      return;
    }
    if (parsed !== n) onCommit(Number.isFinite(parsed) ? parsed : "INF");
  };

  return (
    <div className="relative flex w-full min-w-28 items-center">
      <Input
        value={text}
        disabled={disabled}
        onFocus={() => setDraft(shown.trim())}
        onChange={(e) => setDraft(e.target.value)}
        onBlur={submit}
        onKeyDown={(e) => {
          if (e.key === "Enter") (e.target as HTMLInputElement).blur();
          if (e.key === "Escape") {
            setDraft(null);
            (e.target as HTMLInputElement).blur();
          }
        }}
        className="num h-7 pr-8 text-right text-xs"
        aria-label={labelOf(v)}
      />
      <span className="text-muted-foreground pointer-events-none absolute right-2 text-[11px]">{unit}</span>
    </div>
  );
}

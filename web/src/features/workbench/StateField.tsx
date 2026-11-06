import { AlertCircle, ChevronDown, ChevronUp } from "lucide-react";
import { type ReactNode, useEffect, useRef, useState } from "react";
import { useTranslation } from "react-i18next";
import type { StateVarInfo } from "@/api/client";
import { useConfigure } from "@/api/hooks";
import { commit, DraftInput, type DraftHandle, INVALID, type Parsed, SKIP } from "@/components/app/DraftInput";
import { remoteInstrument } from "@/components/app/ClientsConfirm";
import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { Switch } from "@/components/ui/switch";
import { Tooltip, TooltipContent, TooltipTrigger } from "@/components/ui/tooltip";
import { displayUnit, engParts, parseEngineering } from "@/lib/format";
import { cn } from "@/lib/utils";
import { hasLabel, labelOf, numeric, optionLabel, shortForm, stepNumber } from "./model";

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
  /** A manual value its automatic switch overrides: dimmed, with a note (#279). */
  inactive?: boolean;
  /** The control alone, its name given by the row it sits in (a measurement slot, #728). */
  bare?: boolean;
}

/** One editable state variable; edits are out-of-band "configure" actions (architecture.md 5.2). */
export function StateField({ sid, instrument, v, disabled, compact, onCommit, trailing, inactive, bare }: Props) {
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
          // A remote instrument is reported by the hook, with LOCAL (#121).
          onError: (e) => setError(remoteInstrument(e) ? null : (e as Error).message),
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
          <SelectValue placeholder="—" />
        </SelectTrigger>
        <SelectContent>
          {(v.options ?? []).map((o) => (
            <SelectItem key={o} value={o} className="num text-xs">
              {optionLabel(v, o)}{" "}
              {/* The mnemonic a script sends, beside the plain word (#281). */}
              <span className="text-muted-foreground">{optionLabel(v, o) !== shortForm(o) ? shortForm(o) : ""}</span>
            </SelectItem>
          ))}
        </SelectContent>
      </Select>
    );
  } else if (v.type === "float" || v.type === "int") {
    control = (
      <NumberInput v={v} disabled={readonly} inactive={inactive} onCommit={commit} onInvalid={() => setError(t("workbench.refused"))} />
    );
  } else {
    control = (
      <span className="num min-w-0 truncate" title={String(v.value)}>
        {String(v.value)}
      </span>
    );
  }

  return (
    <div
      className={cn(
        "grid items-center gap-x-3",
        bare ? "grid-cols-1" : compact ? "grid-cols-[1fr_auto]" : "grid-cols-[minmax(6rem,1fr)_minmax(8rem,1.4fr)]",
      )}
      data-inactive={inactive || undefined}
    >
      {!bare && (
        // A setting the definition did not name shows its path, in mono: the name SCPI users know (#728).
        <label
          className={cn("text-muted-foreground truncate text-xs", !hasLabel(v) && "num")}
          title={inactive ? `${v.path} · ${t("workbench.autoInactive")}` : v.path}
        >
          {labelOf(v)}
          {inactive && <span className="text-muted-foreground/70 block text-[10px]">{t("workbench.autoInactive")}</span>}
        </label>
      )}
      <div className={cn("flex min-w-0 items-center justify-end gap-1.5", inactive && "opacity-50")}>
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
  inactive,
  onCommit,
  onInvalid,
}: {
  v: StateVarInfo;
  disabled?: boolean;
  /** The automatic switch overrides the stored value: the field reads "Auto" until focused (#279). */
  inactive?: boolean;
  onCommit: (value: number | string) => void;
  onInvalid: () => void;
}) {
  const { t } = useTranslation();
  const unit = v.display_unit ?? displayUnit(v.unit);
  const n = numeric(v.value);
  const parts = engParts(n, unit, 6);
  // Counts (points, averages, harmonics) read as plain integers, not engineering notation.
  const finite = Number.isFinite(n);
  // While editing, the draft (held by DraftInput) is what the user types; otherwise the field shows the live value.
  const field = useRef<DraftHandle>(null);
  // The field reads as the value is written, "100 ms" or "1.5 kHz", the prefix next to its unit
  // rather than split across the field ("100 m" … "s", #278). An infinite resistance is the
  // instrument's high-impedance setting, worded as on the canvas and generator screen (#281).
  const hiZ = !finite && displayUnit(v.unit) === "Ω";
  const rest = !finite
    ? hiZ
      ? t("generator.highZ")
      : "INF"
    : v.type === "int"
      ? `${Math.round(n)}${unit ? ` ${unit}` : ""}`
      : `${parts.mantissa} ${parts.prefix}${unit}`.trim();

  // Stepping (#738): a rung up or down, written at once like a typed value. A burst of steps (a held
  // key, a spinning wheel) builds on the last one asked for, until the live value catches up.
  const [asked, setAsked] = useState<{ at: number; to: number } | null>(null);
  const pending = asked && asked.at === n ? asked.to : null; // forgotten once the live value changes
  useEffect(() => {
    // ...or after a second, when the instrument refused the step and the value never changed.
    if (!asked) return;
    const id = setTimeout(() => setAsked(null), 1000);
    return () => clearTimeout(id);
  }, [asked]);
  const canStep = !disabled && !inactive;
  const step = (dir: 1 | -1, coarse = false) => {
    if (!canStep) return;
    let from = pending ?? n;
    const draft = field.current?.draft() ?? null;
    if (draft !== null && draft.trim() !== rest) {
      // Something typed and not yet entered: step from that.
      const typed = draft.trim();
      const parsed = parseEngineering(unit && typed.endsWith(unit) ? typed.slice(0, -unit.length) : typed);
      if (parsed !== null && Number.isFinite(parsed)) from = parsed;
    }
    const next = stepNumber(v, from, dir, coarse);
    if (next === null) return;
    setAsked({ at: n, to: next });
    field.current?.clear();
    onCommit(next);
  };
  const stepRef = useRef(step);
  useEffect(() => {
    stepRef.current = step;
  });
  useEffect(() => {
    // The wheel steps only the focused field, so the panel still scrolls under the pointer. React's
    // own wheel handler is passive and cannot stop the scroll.
    const el = field.current?.input;
    if (!el) return;
    const onWheel = (e: WheelEvent) => {
      if (document.activeElement !== el || !e.deltaY) return;
      e.preventDefault();
      stepRef.current(e.deltaY < 0 ? 1 : -1, e.shiftKey);
    };
    el.addEventListener("wheel", onWheel, { passive: false });
    return () => el.removeEventListener("wheel", onWheel);
  }, []);
  const cannot = (dir: 1 | -1) => !canStep || stepNumber(v, pending ?? n, dir) === null;

  // This field's rules, spelled out: unchanged text is skipped, high-Z reads as INF, the unit may be
  // typed or kept ("2.5 kHz"; a number alone is in the base unit), infinities go as "INF".
  const parse = (raw: string): Parsed<number | string> => {
    let typed = raw.trim();
    if (typed === rest) return SKIP; // focused and left without a change
    if (typed === t("generator.highZ")) typed = "INF";
    if (unit && typed.endsWith(unit)) typed = typed.slice(0, -unit.length);
    const parsed = parseEngineering(typed);
    if (parsed === null) return INVALID;
    if (parsed === n) return SKIP;
    return commit(Number.isFinite(parsed) ? parsed : "INF");
  };

  return (
    <div className="group/num relative flex w-full min-w-28 items-center">
      <DraftInput
        ref={field}
        text={rest}
        display={inactive ? t("workbench.auto") : undefined}
        parse={parse}
        onCommit={onCommit}
        onInvalid={onInvalid}
        disabled={disabled}
        onKeyDown={(e) => {
          if (e.key === "ArrowUp" || e.key === "ArrowDown") {
            e.preventDefault();
            step(e.key === "ArrowUp" ? 1 : -1, e.shiftKey);
          }
        }}
        className="num h-7 pr-6 text-right text-xs"
        aria-label={labelOf(v)}
      />
      {canStep && Number.isFinite(n) && (
        // The pair of arrows shows on hover and focus; the keyboard has ↑ / ↓ (Shift: ten times).
        <div className="absolute inset-y-px right-px flex w-5 flex-col opacity-0 transition-opacity group-focus-within/num:opacity-100 group-hover/num:opacity-100">
          {([1, -1] as const).map((dir) => {
            const Icon = dir === 1 ? ChevronUp : ChevronDown;
            return (
              <button
                key={dir}
                type="button"
                tabIndex={-1}
                disabled={cannot(dir)}
                aria-label={t(dir === 1 ? "workbench.stepUp" : "workbench.stepDown", { label: labelOf(v) })}
                // Keep the field's focus (and its draft) while the arrow is pressed.
                onMouseDown={(e) => e.preventDefault()}
                onClick={(e) => step(dir, e.shiftKey)}
                className="text-muted-foreground hover:bg-muted hover:text-foreground flex flex-1 items-center justify-center rounded-sm disabled:opacity-30 disabled:hover:bg-transparent"
              >
                <Icon className="size-3" />
              </button>
            );
          })}
        </div>
      )}
    </div>
  );
}

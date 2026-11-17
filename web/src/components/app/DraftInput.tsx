import { forwardRef, useImperativeHandle, useRef, useState } from "react";
import { Input } from "@/components/ui/input";
import { Textarea } from "@/components/ui/textarea";

/** What a field's parse rule says about the typed text. */
export type Parsed<T> =
  | { kind: "commit"; value: T }
  | { kind: "skip" }
  | { kind: "invalid"; reason?: string };

export const commit = <T,>(value: T): Parsed<T> => ({ kind: "commit", value });
export const SKIP: Parsed<never> = { kind: "skip" };
export const INVALID: Parsed<never> = { kind: "invalid" };
/** A refusal the user can read: `reason` names what went wrong (the units a field takes, #1286). */
export const invalid = (reason?: string): Parsed<never> => ({ kind: "invalid", reason });

/** Reach into a field that steps from what is typed (the state field's arrows and wheel). */
export interface DraftHandle {
  input: HTMLInputElement | null;
  /** The text typed and not yet entered, or null when not editing. */
  draft: () => string | null;
  /** Drop the draft without committing. */
  clear: () => void;
}

interface Props<T> {
  /** The text shown when not editing, and the draft's starting point on focus. */
  text: string;
  /** Shown instead of `text` until focused (an overridden value reads "Auto", #279). */
  display?: string;
  /**
   * Text that was refused and is kept in the field (#1353): shown instead of the live value, and the
   * draft's starting point on focus, so the user can correct what they typed. Esc drops it.
   */
  held?: string;
  /**
   * The one place a field's rules live: strip a unit, read INF / high-Z, trim plain text. Return
   * `commit(value)` to enter, `SKIP` for "nothing to enter", `INVALID` to refuse the text.
   */
  parse: (typed: string) => Parsed<T>;
  /** Called with the parsed value and the text it was read from. */
  onCommit: (value: T, typed: string) => void;
  /** Called on a refused text, with the parse rule's reason when it gives one (#1286), and the text. */
  onInvalid?: (reason: string | undefined, typed: string) => void;
  /** Esc: the draft (and any held text) was dropped. */
  onCancel?: () => void;
  multiline?: boolean;
  disabled?: boolean;
  placeholder?: string;
  className?: string;
  id?: string;
  title?: string;
  "aria-label"?: string;
  "aria-invalid"?: boolean;
  "aria-describedby"?: string;
  onKeyDown?: (
    e: React.KeyboardEvent<HTMLInputElement | HTMLTextAreaElement>,
  ) => void;
}

/**
 * The editing lifecycle of a front-panel text field, once: focus reveals the stored value when the
 * resting display stands in for it ("Auto", #279), Enter or blur enters the typed draft through
 * `parse`, Esc drops it and restores the live value (#688). A refused text can be kept (`held`).
 */
function DraftInputInner<T>(
  {
    text,
    display,
    held,
    parse,
    onCommit,
    onInvalid,
    onCancel,
    multiline,
    onKeyDown,
    ...rest
  }: Props<T>,
  ref: React.Ref<DraftHandle>,
) {
  const [draft, setDraft] = useState<string | null>(null);
  // Esc clears the draft and blurs; the blur's submit closure still holds the stale draft, so a
  // cancelled blur is marked here and drops out (#758).
  const cancelled = useRef(false);
  const el = useRef<HTMLInputElement & HTMLTextAreaElement>(null);
  useImperativeHandle(
    ref,
    () => ({
      get input() {
        return el.current;
      },
      draft: () => draft,
      clear: () => setDraft(null),
    }),
    [draft],
  );

  const submit = () => {
    if (cancelled.current) {
      cancelled.current = false;
      return;
    }
    if (draft === null) return;
    setDraft(null);
    const r = parse(draft);
    if (r.kind === "commit") onCommit(r.value, draft);
    else if (r.kind === "invalid") onInvalid?.(r.reason, draft);
  };
  const common = {
    ref: el,
    value: draft ?? held ?? display ?? text,
    // Focusing does not start a draft: the field keeps showing (and following) the live value until
    // something is typed. Seeding the draft from `text` froze a stale value in the gap between a
    // commit and its state event, and the stepper then stepped from that stale value (#835). Only
    // the stand-in display ("Auto") reveals the stored value on focus (#279).
    onFocus: () => {
      cancelled.current = false;
      if (held !== undefined) setDraft(held);
      else if (display !== undefined) setDraft(text);
    },
    onChange: (e: { target: { value: string } }) => setDraft(e.target.value),
    onBlur: submit,
    onKeyDown: (
      e: React.KeyboardEvent<HTMLInputElement | HTMLTextAreaElement>,
    ) => {
      onKeyDown?.(e);
      if (e.defaultPrevented) return;
      if (e.key === "Enter" && !multiline) e.currentTarget.blur();
      if (e.key === "Escape") {
        cancelled.current = true;
        setDraft(null);
        onCancel?.();
        e.currentTarget.blur();
      }
    },
    ...rest,
  };
  return multiline ? <Textarea {...common} rows={3} /> : <Input {...common} />;
}

export const DraftInput = forwardRef(DraftInputInner) as <T>(
  props: Props<T> & { ref?: React.Ref<DraftHandle> },
) => React.ReactElement;

/**
 * The build area's small pictures (#1214): an icon stands for a word that repeats on every row —
 * how a command is used, a setting's type, a hint — and its tooltip (and accessible name) keeps the
 * word, so the page reads short without losing what it says.
 */
import {
  ArrowDownToLine,
  ArrowDownUp,
  ArrowUpFromLine,
  Binary,
  Hash,
  Info,
  List,
  type LucideIcon,
  ToggleLeft,
  Type as TypeGlyph,
} from "lucide-react";
import type { ReactNode } from "react";
import { useTranslation } from "react-i18next";
import { ToggleGroup, ToggleGroupItem } from "@/components/ui/toggle-group";
import { Tooltip, TooltipContent, TooltipTrigger } from "@/components/ui/tooltip";
import { cn } from "@/lib/utils";
import { COMMAND_ACCESS, type CommandAccess } from "./model";

/** An element with a tooltip. */
export function Tip({ label, children, side = "top" }: { label: ReactNode; children: ReactNode; side?: "top" | "bottom" | "left" | "right" }) {
  return (
    <Tooltip>
      <TooltipTrigger asChild>{children}</TooltipTrigger>
      <TooltipContent side={side} className="max-w-72">
        {label}
      </TooltipContent>
    </Tooltip>
  );
}

/** An ⓘ that says a hint on hover or focus, in place of a paragraph. */
export function Hint({ text, className }: { text: string; className?: string }) {
  return (
    <Tip label={text}>
      <span tabIndex={0} role="note" aria-label={text} className={cn("text-muted-foreground inline-flex cursor-help rounded-sm outline-none focus-visible:ring-2", className)}>
        <Info className="size-3.5" />
      </span>
    </Tip>
  );
}

const ACCESS_ICON: Record<CommandAccess, LucideIcon> = { set: ArrowDownToLine, query: ArrowUpFromLine, both: ArrowDownUp };

/** How a command is used, as an arrow: into the instrument (write), out of it (query), both. */
export function AccessIcon({ access, className }: { access: CommandAccess | null; className?: string }) {
  const { t } = useTranslation();
  if (!access) return <span className={cn("text-error text-[11px]", className)}>{t("acceptance.build.accessNone")}</span>;
  const Icon = ACCESS_ICON[access];
  const label = t(`acceptance.build.access.${access}`);
  return (
    <Tip label={label}>
      <span role="img" aria-label={label} className={cn("text-muted-foreground inline-flex", className)} data-access={access}>
        <Icon className="size-3.5" />
      </span>
    </Tip>
  );
}

/** Write / query / both, one of three. */
export function AccessPicker({ value, onChange, disabled }: { value: CommandAccess | null; onChange: (v: CommandAccess) => void; disabled?: boolean }) {
  const { t } = useTranslation();
  return (
    <ToggleGroup
      type="single"
      variant="outline"
      size="sm"
      value={value ?? ""}
      disabled={disabled}
      onValueChange={(v) => v && onChange(v as CommandAccess)}
      aria-label={t("acceptance.build.accessLabel")}
      data-testid="build-command-access"
    >
      {COMMAND_ACCESS.map((a) => {
        const Icon = ACCESS_ICON[a];
        return (
          <ToggleGroupItem key={a} value={a} aria-label={t(`acceptance.build.access.${a}`)} title={t(`acceptance.build.access.${a}`)} className="h-7 px-2.5">
            <Icon className="size-3.5" />
          </ToggleGroupItem>
        );
      })}
    </ToggleGroup>
  );
}

const TYPE_ICON: Record<string, LucideIcon> = { float: Hash, int: Binary, bool: ToggleLeft, enum: List, string: TypeGlyph };

/** A setting's type as an icon (its name in the tooltip). */
export function TypeIcon({ type }: { type: string }) {
  const { t } = useTranslation();
  const Icon = TYPE_ICON[type];
  const label = t(`acceptance.build.types.${type}`, { defaultValue: type });
  if (!Icon) return <span className="text-[10.5px]">{label}</span>;
  return (
    <Tip label={label}>
      <span role="img" aria-label={label} className="inline-flex">
        <Icon className="size-3.5" />
      </span>
    </Tip>
  );
}

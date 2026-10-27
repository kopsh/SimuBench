import { X, Zap } from "lucide-react";
import { type FormEvent, useState } from "react";
import { useTranslation } from "react-i18next";
import { toast } from "sonner";
import type { FaultInfo, FaultSpec } from "@/api/client";
import { useClearFault, useFaults, useInjectFault } from "@/api/hooks";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { parseEngineering } from "@/lib/format";
import { cn } from "@/lib/utils";

type Kind = FaultSpec["kind"];

const LINK_KINDS: Kind[] = ["timeout", "disconnect", "garble", "latency", "busy", "error", "trip"];
const PARAM_KINDS: Kind[] = ["drift", "hidden"];

/** Faults that act on an instrument (or on a component of a DUT), with a form to add one. */
export function FaultPanel({
  sid,
  instrument,
  component,
  params,
  disabled,
}: {
  sid: string;
  /** Link and instrument faults target this instrument. */
  instrument?: string;
  /** Parameter faults target this component (a DUT name or '<instrument>/<component>'). */
  component?: string;
  params?: string[];
  disabled?: boolean;
}) {
  const { t } = useTranslation();
  const faults = useFaults(sid);
  const inject = useInjectFault(sid);
  const clear = useClearFault(sid);
  const kinds = component ? PARAM_KINDS : LINK_KINDS;
  const [kind, setKind] = useState<Kind>(kinds[0]);
  const [fields, setFields] = useState<Record<string, string>>({});
  const set = (k: string) => (e: { target: { value: string } }) => setFields((f) => ({ ...f, [k]: e.target.value }));
  const num = (k: string) => {
    const v = fields[k]?.trim();
    return v ? (parseEngineering(v) ?? undefined) : undefined;
  };

  const mine = (faults.data ?? []).filter((f) =>
    component ? f.spec.component === component : f.spec.instrument === instrument && !f.spec.component,
  );
  const shown = mine.filter((f) => f.status !== "ended").concat(mine.filter((f) => f.status === "ended").slice(-3));

  const submit = (e: FormEvent) => {
    e.preventDefault();
    const spec: FaultSpec = { kind } as FaultSpec;
    if (component) Object.assign(spec, { component, param: fields.param ?? params?.[0] });
    else Object.assign(spec, { instrument });
    if (kind === "latency") spec.latency_s = num("latency") ?? 0.1;
    if (kind === "error" || kind === "trip") {
      const code = Number.parseInt(fields.code ?? "", 10);
      if (Number.isFinite(code)) spec.code = code;
      if (fields.message?.trim()) spec.message = fields.message.trim();
    }
    if (kind === "drift") spec.drift_per_s = num("drift") ?? 1e-3;
    if (kind === "hidden") spec.value = num("value") ?? fields.value;
    const duration = num("duration");
    if (duration) spec.duration_s = duration;
    const count = Number.parseInt(fields.count ?? "", 10);
    if (Number.isFinite(count) && count > 0 && ["timeout", "garble"].includes(kind)) spec.count = count;
    inject.mutate(spec, {
      onSuccess: (f) => toast.success(t("fault.injected", { what: f.description })),
      onError: (err) => toast.error(t("errors.generic"), { description: (err as Error).message }),
    });
  };

  return (
    <div className="space-y-2.5">
      <p className="text-muted-foreground text-[11px]">{t("fault.hint")}</p>
      {shown.length > 0 && (
        <ul className="space-y-1" aria-label={t("fault.title")}>
          {shown.map((f) => (
            <FaultRow key={f.id} f={f} onClear={() => clear.mutate(f.id)} disabled={disabled} />
          ))}
        </ul>
      )}
      <form onSubmit={submit} className="bg-muted/40 space-y-2 rounded-md border p-2">
        <Select value={kind} onValueChange={(v) => setKind(v as Kind)} disabled={disabled}>
          <SelectTrigger size="sm" className="h-7 w-full text-xs" aria-label={t("fault.kind")}>
            <SelectValue />
          </SelectTrigger>
          <SelectContent>
            {kinds.map((k) => (
              <SelectItem key={k} value={k} className="text-xs">
                {t(`fault.kinds.${k}`)} <span className="text-muted-foreground">· {t(`fault.kindHints.${k}`)}</span>
              </SelectItem>
            ))}
          </SelectContent>
        </Select>
        <div className="grid grid-cols-2 gap-2">
          {component && (
            <Select value={fields.param ?? params?.[0] ?? ""} onValueChange={(v) => setFields((f) => ({ ...f, param: v }))}>
              <SelectTrigger size="sm" className="num h-7 w-full text-xs" aria-label={t("fault.param")}>
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                {(params ?? []).map((p) => (
                  <SelectItem key={p} value={p} className="num text-xs">
                    {p}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
          )}
          {kind === "drift" && <Small label={t("fault.driftRate")} value={fields.drift} onChange={set("drift")} placeholder="1e-3" />}
          {kind === "hidden" && <Small label={t("fault.value")} value={fields.value} onChange={set("value")} placeholder="1.2k" />}
          {kind === "latency" && <Small label={t("fault.latency")} value={fields.latency} onChange={set("latency")} placeholder="0.1" />}
          {(kind === "error" || kind === "trip") && (
            <Small label={t("fault.code")} value={fields.code} onChange={set("code")} placeholder="-300" />
          )}
          {(kind === "timeout" || kind === "garble") && (
            <Small label={t("fault.count")} value={fields.count} onChange={set("count")} placeholder={t("fault.countHint")} />
          )}
          {kind !== "error" && kind !== "trip" && (
            <Small label={t("fault.duration")} value={fields.duration} onChange={set("duration")} placeholder={t("fault.durationHint")} />
          )}
        </div>
        <Button type="submit" size="sm" variant="outline" className="h-7 w-full text-xs" disabled={disabled || inject.isPending}>
          <Zap className="size-3.5" /> {t("fault.inject")}
        </Button>
      </form>
    </div>
  );
}

function Small({
  label,
  value,
  onChange,
  placeholder,
}: {
  label: string;
  value?: string;
  onChange: (e: { target: { value: string } }) => void;
  placeholder?: string;
}) {
  return (
    <Input
      value={value ?? ""}
      onChange={onChange}
      placeholder={placeholder}
      aria-label={label}
      title={label}
      className="num h-7 text-xs placeholder:text-[11px]"
    />
  );
}

export function FaultRow({ f, onClear, disabled }: { f: FaultInfo; onClear?: () => void; disabled?: boolean }) {
  const { t } = useTranslation();
  const tone =
    f.status === "active" ? "bg-error/15 text-error" : f.status === "pending" ? "bg-warn/15 text-warn" : "bg-muted text-muted-foreground";
  return (
    <li className="flex items-center gap-2 text-xs">
      <span className={cn("shrink-0 rounded px-1.5 py-0.5 text-[10px] font-medium", tone)}>{t(`fault.${f.status}`)}</span>
      <span className="min-w-0 flex-1 truncate" title={f.description}>
        {t(`fault.kinds.${f.spec.kind}`)} <span className="text-muted-foreground num">{f.description}</span>
      </span>
      {f.status !== "ended" && onClear && (
        <Button variant="ghost" size="icon" className="size-6" onClick={onClear} disabled={disabled} aria-label={t("fault.clear")}>
          <X className="size-3.5" />
        </Button>
      )}
    </li>
  );
}

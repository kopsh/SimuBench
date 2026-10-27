import { AlertCircle, AlertTriangle, MousePointerClick, Plus, RotateCcw, Trash2, X } from "lucide-react";
import { type ReactNode, useState } from "react";
import { useTranslation } from "react-i18next";
import type {
  BenchDocument,
  Catalog,
  ComponentInfo,
  ComponentTypeInfo,
  Diagnostic,
  EditOp,
  InstrumentInfo,
  SessionInfo,
  StateVarInfo,
  WireInfo,
} from "@/api/client";
import { useCatalog } from "@/api/hooks";
import { EmptyState } from "@/components/app/StateViews";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { ScrollArea } from "@/components/ui/scroll-area";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { Switch } from "@/components/ui/switch";
import { Textarea } from "@/components/ui/textarea";
import { Tooltip, TooltipContent, TooltipTrigger } from "@/components/ui/tooltip";
import { channelColor } from "@/lib/channels";
import { pickLabel } from "@/i18n";
import { cn } from "@/lib/utils";
import { Header, KeyValues, Section } from "../Inspector";
import { accessoryLabel, channelVars, globalVars, instrumentTitle } from "../model";
import { StateField } from "../StateField";
import { useWorkbench } from "../store";
import { NAME } from "./actions";
import { applyOps } from "./editor";

type Obj = Record<string, unknown>;
const obj = (v: unknown): Obj => (v && typeof v === "object" && !Array.isArray(v) ? (v as Obj) : {});

/** Properties of the selected item, edited in the bench file. */
export function EditInspector({ view, doc }: { view: SessionInfo | null; doc: BenchDocument }) {
  const { t } = useTranslation();
  const selection = useWorkbench((s) => s.selection);
  const catalog = useCatalog().data;
  const subject =
    selection?.kind === "wire" ? `wire:${selection.id}` : selection?.kind === "instrument" || selection?.kind === "dut" ? selection.id : null;
  const diags = subject ? doc.diagnostics.filter((d) => d.subject === subject) : [];

  let body: ReactNode;
  if (!selection) {
    body = <BenchProperties doc={doc} />;
  } else if (selection.kind === "instrument") {
    const inst = view?.instruments.find((i) => i.name === selection.id);
    body = inst ? (
      <InstrumentEdit inst={inst} raw={obj(obj(doc.data.instruments)[inst.name])} />
    ) : (
      <Broken name={selection.id} />
    );
  } else if (selection.kind === "dut") {
    const dut = view?.duts.find((d) => d.name === selection.id);
    const type = catalog?.components.find((c) => c.type === dut?.type);
    body = dut ? <DutEdit dut={dut} type={type} /> : <Broken name={selection.id} />;
  } else if (selection.kind === "wire") {
    const wire = view?.wires.find((w) => w.index === selection.id);
    body = wire && catalog ? <WireEdit wire={wire} catalog={catalog} /> : <Broken wire={selection.id} />;
  } else {
    body = (
      <EmptyState icon={<MousePointerClick className="size-6" />} title={t("workbench.inspector")} body={t("workbench.noSelection")} />
    );
  }
  return (
    <ScrollArea className="h-full">
      {diags.length > 0 && <Diagnostics items={diags} />}
      {body}
    </ScrollArea>
  );
}

function Diagnostics({ items }: { items: Diagnostic[] }) {
  return (
    <ul className="space-y-1.5 border-b p-3" aria-label="diagnostics">
      {items.map((d, i) => (
        <li key={i} className="flex gap-2 text-xs">
          {d.severity === "error" ? (
            <AlertCircle className="text-error mt-0.5 size-3.5 shrink-0" />
          ) : (
            <AlertTriangle className="text-warn mt-0.5 size-3.5 shrink-0" />
          )}
          <div className="min-w-0">
            <div>
              {d.message} <span className="text-muted-foreground num">{d.code}</span>
            </div>
            {d.suggestion && <div className="text-muted-foreground">→ {d.suggestion}</div>}
          </div>
        </li>
      ))}
    </ul>
  );
}

/** An item the preview could not build; it can still be renamed away or deleted. */
function Broken({ name, wire }: { name?: string; wire?: number }) {
  const { t } = useTranslation();
  const select = useWorkbench((s) => s.select);
  const op: EditOp = name !== undefined ? { op: "remove", name } : { op: "disconnect", index: wire ?? 0 };
  return (
    <div className="space-y-3 p-3">
      <Header title={name ?? `${t("workbench.wires")} #${(wire ?? 0) + 1}`} subtitle="—" mono />
      <DeleteButton onClick={() => void applyOps([op]).then((d) => d && select(null))} />
    </div>
  );
}

// ---- bench ------------------------------------------------------------------------------------------

function BenchProperties({ doc }: { doc: BenchDocument }) {
  const { t } = useTranslation();
  const run = obj(doc.data.run);
  return (
    <div className="space-y-4 p-3">
      <Header title={String(doc.data.name ?? "")} subtitle={doc.path} />
      <Section title={t("edit.bench")}>
        <div className="space-y-2">
          <Field label={t("edit.benchName")}>
            <TextField value={String(doc.data.name ?? "")} onCommit={(v) => v && applyOps([{ op: "set_meta", name: v }])} />
          </Field>
          <Field label={t("edit.description")} stacked>
            <TextField
              multiline
              value={String(doc.data.description ?? "")}
              onCommit={(v) => applyOps([{ op: "set_meta", description: v }])}
            />
          </Field>
        </div>
      </Section>
      <Section title={t("edit.run")}>
        <div className="space-y-2">
          <Field label={t("edit.seed")}>
            <TextField
              value={String(run.seed ?? 0)}
              numeric
              onCommit={(v) => {
                const n = Number.parseInt(v, 10);
                if (Number.isFinite(n)) void applyOps([{ op: "set_run", seed: n }]);
              }}
            />
          </Field>
          <Field label={t("edit.clock")}>
            <Select
              value={String(run.clock ?? "command")}
              onValueChange={(v) => void applyOps([{ op: "set_run", clock: v as "command" | "wall" | "stepped" }])}
            >
              <SelectTrigger size="sm" className="h-7 w-full text-xs" aria-label={t("edit.clock")}>
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                {(["command", "wall", "stepped"] as const).map((m) => (
                  <SelectItem key={m} value={m} className="text-xs">
                    {t(`workbench.clockMode.${m}`)}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
          </Field>
          <Field label={t("edit.revealTrue")} hint={t("edit.revealTrueHint")}>
            <Switch
              checked={!!run.reveal_true_values}
              onCheckedChange={(c) => void applyOps([{ op: "set_run", reveal_true_values: c }])}
              aria-label={t("edit.revealTrue")}
            />
          </Field>
        </div>
      </Section>
    </div>
  );
}

// ---- instrument -------------------------------------------------------------------------------------

function InstrumentEdit({ inst, raw }: { inst: InstrumentInfo; raw: Obj }) {
  const { t } = useTranslation();
  const select = useWorkbench((s) => s.select);
  const explicit = obj(raw.state);
  const channels = Array.from({ length: inst.channels }, (_, i) => i + 1);
  const field = (v: StateVarInfo) => (
    <StateField
      key={v.path}
      sid=""
      instrument={inst.name}
      v={v}
      onCommit={(value) => void applyOps([{ op: "set_state", instrument: inst.name, path: v.path, value }])}
      trailing={
        v.path in explicit ? (
          <ResetButton onClick={() => void applyOps([{ op: "set_state", instrument: inst.name, path: v.path, value: null }])} />
        ) : (
          <span className="size-6 shrink-0" aria-hidden />
        )
      }
    />
  );
  return (
    <div className="space-y-4 p-3">
      <Header title={instrumentTitle(inst)} subtitle={`${inst.manufacturer} ${inst.model}`} />
      <Section title={t("workbench.identitySection")}>
        <div className="space-y-2">
          <NameField name={inst.name} onRenamed={(n) => select({ kind: "instrument", id: n })} />
          <Field label={t("edit.label")}>
            <TextField
              value={inst.label ?? ""}
              placeholder={t("edit.labelPlaceholder")}
              onCommit={(v) => applyOps([{ op: "set_label", name: inst.name, label: v || null }])}
            />
          </Field>
          <Field label={t("workbench.realAddress")} stacked>
            <TextField
              value={inst.address_real ?? ""}
              mono
              placeholder="TCPIP0::192.168.1.20::5025::SOCKET"
              onCommit={(v) => applyOps([{ op: "set_address", instrument: inst.name, address: v || null }])}
            />
          </Field>
        </div>
      </Section>
      <p className="text-muted-foreground text-[11px]">{t("edit.initialStateHint")}</p>
      {globalVars(inst).length > 0 && (
        <Section title={t("edit.initialState")}>
          <div className="space-y-1.5">{globalVars(inst).map(field)}</div>
        </Section>
      )}
      {channels.map((n) => (
        <Section
          key={n}
          title={
            <span className="num font-semibold" style={{ color: channelColor(n) }}>
              {t("workbench.channelN", { n })}
            </span>
          }
          collapsible
          defaultOpen={n <= 2}
        >
          <div className="space-y-1.5">{channelVars(inst, n).map(field)}</div>
        </Section>
      ))}
      <DeleteButton onClick={() => void applyOps([{ op: "remove", name: inst.name }]).then((d) => d && select(null))} />
    </div>
  );
}

// ---- DUT --------------------------------------------------------------------------------------------

/** Parameters as state variables, so they get the same engineering-notation fields. */
function paramVar(key: string, value: unknown, unit: string | undefined): StateVarInfo {
  const isNum = typeof value === "number" || value === "INF";
  return {
    path: key,
    type: isNum ? "float" : typeof value === "boolean" ? "bool" : "string",
    unit: unit ?? null,
    min: null,
    max: null,
    options: null,
    option_labels: null,
    display_unit: unit && !["OHM", "F", "HZ", "V", "A", "S"].includes(unit.toUpperCase()) ? unit : null,
    label: key,
    label_en: key,
    readonly: false,
    electrical: false,
    dangerous: false,
    value,
    channel: null,
  };
}

function DutEdit({ dut, type }: { dut: ComponentInfo; type?: ComponentTypeInfo }) {
  const { t } = useTranslation();
  const select = useWorkbench((s) => s.select);
  const entries = Object.entries(dut.params);
  const hidden = entries.filter(([k]) => k.startsWith("tol_"));
  const visible = entries.filter(([k]) => !k.startsWith("tol_"));
  const field = ([k, v]: [string, unknown]) => (
    <StateField
      key={k}
      sid=""
      instrument={dut.name}
      v={paramVar(k, v, type?.units[k])}
      onCommit={(value) => void applyOps([{ op: "set_params", dut: dut.name, params: { [k]: value } }])}
    />
  );
  return (
    <div className="space-y-4 p-3">
      <Header title={dut.label || dut.name} subtitle={`DUT · ${type ? pickLabel(type.label, type.label_en, dut.type) : dut.type}`} />
      <Section title={t("workbench.identitySection")}>
        <div className="space-y-2">
          <NameField name={dut.name} onRenamed={(n) => select({ kind: "dut", id: n })} />
          <Field label={t("edit.label")}>
            <TextField
              value={dut.label ?? ""}
              placeholder={t("edit.labelPlaceholder")}
              onCommit={(v) => applyOps([{ op: "set_label", name: dut.name, label: v || null }])}
            />
          </Field>
        </div>
      </Section>
      <Section title={t("workbench.params")}>
        <div className="space-y-1.5">{visible.map(field)}</div>
      </Section>
      {hidden.length > 0 && (
        <Section title={t("edit.hiddenParams")} collapsible defaultOpen={false}>
          <div className="space-y-1.5">{hidden.map(field)}</div>
        </Section>
      )}
      <Section title={t("workbench.ports")}>
        <KeyValues entries={dut.ports.map((p) => [p.name, p.net ?? t("workbench.notConnected")])} />
      </Section>
      <DeleteButton onClick={() => void applyOps([{ op: "remove", name: dut.name }]).then((d) => d && select(null))} />
    </div>
  );
}

// ---- wire -------------------------------------------------------------------------------------------

function WireEdit({ wire, catalog }: { wire: WireInfo; catalog: Catalog }) {
  const { t } = useTranslation();
  const select = useWorkbench((s) => s.select);
  const types = catalog.components.filter((c) => c.category === "accessory");
  const current = wire.accessories.map((a) => ({ type: a.type, params: a.params }));
  const setAccessories = (accessories: { type: string; params: Obj }[]) =>
    void applyOps([{ op: "set_accessories", index: wire.index, accessories }]);
  return (
    <div className="space-y-4 p-3">
      <Header title={`${wire.from} → ${wire.to}`} subtitle={`${t("workbench.wires")} #${wire.index + 1}`} mono />
      <Section title={t("workbench.net")}>
        <KeyValues
          entries={[
            [t("edit.from"), wire.from],
            [t("edit.to"), wire.to],
            [t("workbench.net"), wire.net ?? "—"],
          ]}
        />
      </Section>
      <Section title={t("workbench.accessories")}>
        <div className="space-y-3">
          {wire.accessories.map((a, k) => {
            const type = types.find((x) => x.type === a.type);
            return (
              <div key={a.name} className="rounded-md border p-2">
                <div className="mb-1.5 flex items-center gap-2 text-xs font-medium">
                  <span>{type ? pickLabel(type.label, type.label_en, a.type) : a.type}</span>
                  <span className="text-muted-foreground num">{accessoryLabel(a.type, a.params)}</span>
                  <Button
                    variant="ghost"
                    size="icon"
                    className="ml-auto size-6"
                    aria-label={t("edit.removeAccessory")}
                    onClick={() => setAccessories(current.filter((_, i) => i !== k))}
                  >
                    <X className="size-3.5" />
                  </Button>
                </div>
                <div className="space-y-1.5">
                  {Object.entries(a.params).map(([p, v]) => (
                    <StateField
                      key={p}
                      sid=""
                      instrument={a.name}
                      v={paramVar(p, v, type?.units[p])}
                      onCommit={(value) =>
                        setAccessories(current.map((c, i) => (i === k ? { ...c, params: { ...c.params, [p]: value } } : c)))
                      }
                    />
                  ))}
                </div>
              </div>
            );
          })}
          <Select value="" onValueChange={(v) => setAccessories([...current, { type: v, params: {} }])}>
            <SelectTrigger size="sm" className="h-7 w-full text-xs" aria-label={t("edit.addAccessory")}>
              <span className="text-muted-foreground flex items-center gap-1.5">
                <Plus className="size-3.5" /> {t("edit.addAccessory")}
              </span>
            </SelectTrigger>
            <SelectContent>
              {types.map((x) => (
                <SelectItem key={x.type} value={x.type} className="text-xs">
                  {pickLabel(x.label, x.label_en, x.type)}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
        </div>
      </Section>
      <DeleteButton onClick={() => void applyOps([{ op: "disconnect", index: wire.index }]).then((d) => d && select(null))} />
    </div>
  );
}

// ---- fields -----------------------------------------------------------------------------------------

function NameField({ name, onRenamed }: { name: string; onRenamed: (name: string) => void }) {
  const { t } = useTranslation();
  const [invalid, setInvalid] = useState(false);
  return (
    <Field label={t("edit.name")} error={invalid ? t("edit.nameInvalid") : undefined}>
      <TextField
        value={name}
        mono
        onCommit={(v) => {
          if (v === name) return;
          if (!NAME.test(v)) {
            setInvalid(true);
            return;
          }
          setInvalid(false);
          void applyOps([{ op: "rename", name, new_name: v }]).then((d) => d && onRenamed(v));
        }}
      />
    </Field>
  );
}

function Field({
  label,
  children,
  stacked,
  hint,
  error,
}: {
  label: string;
  children: ReactNode;
  stacked?: boolean;
  hint?: string;
  error?: string;
}) {
  return (
    <div className={cn(stacked ? "space-y-1" : "grid grid-cols-[minmax(6rem,1fr)_minmax(8rem,1.4fr)] items-center gap-x-3")}>
      <label className="text-muted-foreground flex items-center gap-1 truncate text-xs">
        {label}
        {hint && (
          <Tooltip>
            <TooltipTrigger asChild>
              <AlertCircle className="size-3 shrink-0 opacity-70" aria-label={hint} />
            </TooltipTrigger>
            <TooltipContent className="max-w-64">{hint}</TooltipContent>
          </Tooltip>
        )}
      </label>
      <div className="flex min-w-0 justify-end">{children}</div>
      {error && <p className="text-error col-span-2 mt-1 text-[11px]">{error}</p>}
    </div>
  );
}

/** A text input that commits on Enter or blur and shows the stored value otherwise. */
function TextField({
  value,
  onCommit,
  placeholder,
  mono,
  multiline,
  numeric,
}: {
  value: string;
  onCommit: (value: string) => unknown;
  placeholder?: string;
  mono?: boolean;
  multiline?: boolean;
  numeric?: boolean;
}) {
  const [draft, setDraft] = useState<string | null>(null);
  const text = draft ?? value;
  const submit = () => {
    if (draft === null) return;
    setDraft(null);
    if (draft.trim() !== value) onCommit(draft.trim());
  };
  const common = {
    value: text,
    placeholder,
    onChange: (e: { target: { value: string } }) => setDraft(e.target.value),
    onBlur: submit,
    className: cn("text-xs", mono && "num", numeric && "num text-right"),
  };
  if (multiline) return <Textarea {...common} rows={3} className={cn(common.className, "min-h-16 w-full resize-y")} />;
  return (
    <Input
      {...common}
      className={cn(common.className, "h-7 w-full")}
      onKeyDown={(e) => {
        if (e.key === "Enter") (e.target as HTMLInputElement).blur();
        if (e.key === "Escape") {
          setDraft(null);
          (e.target as HTMLInputElement).blur();
        }
      }}
    />
  );
}

function ResetButton({ onClick }: { onClick: () => void }) {
  const { t } = useTranslation();
  return (
    <Tooltip>
      <TooltipTrigger asChild>
        <Button variant="ghost" size="icon" className="size-6 shrink-0" onClick={onClick} aria-label={t("edit.resetDefault")}>
          <RotateCcw className="size-3" />
        </Button>
      </TooltipTrigger>
      <TooltipContent>{t("edit.resetDefault")}</TooltipContent>
    </Tooltip>
  );
}

function DeleteButton({ onClick }: { onClick: () => void }) {
  const { t } = useTranslation();
  return (
    <Button variant="outline" size="sm" className="text-error hover:text-error w-full" onClick={onClick}>
      <Trash2 className="size-3.5" /> {t("edit.delete")}
      <span className="text-muted-foreground ml-auto text-[11px]">{t("edit.deleteHint")}</span>
    </Button>
  );
}

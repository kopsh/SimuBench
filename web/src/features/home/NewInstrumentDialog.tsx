// "New instrument" (P2-5, D3 layer 2): start from a template's inputs — the data-sheet language,
// each field from the template's own JSON Schema — or blank (a class, a maker and a model, then the
// build area). The template's checks judge the inputs on the server: errors save nothing and show
// at their field, warnings come with the saved instrument.
import { useMemo, useState } from "react";
import { useTranslation } from "react-i18next";
import { useNavigate } from "react-router-dom";
import { toast } from "sonner";
import type { Diagnostic, InstrumentTemplateInfo } from "@/api/client";
import { useInstrumentTemplates, useNewDefinition } from "@/api/hooks";
import { commit, DraftInput, INVALID, SKIP } from "@/components/app/DraftInput";
import { Button } from "@/components/ui/button";
import { Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { Switch } from "@/components/ui/switch";
import { parseNumber } from "@/features/instrument/structure/model";
import { CLASS_IDS } from "@/features/workbench/classes";
import { pickLabel } from "@/i18n";
import { cn } from "@/lib/utils";
import { fieldKind, getAt, type JsonSchema, parseList, type Path, problemsAt, resolve, setAt, suggestId } from "./schemaForm";

const BLANK = "__blank";

export function NewInstrumentDialog({ open, onOpenChange }: { open: boolean; onOpenChange: (open: boolean) => void }) {
  const { t } = useTranslation();
  const navigate = useNavigate();
  const templates = useInstrumentTemplates(open);
  const create = useNewDefinition();
  const [choice, setChoice] = useState<string>("oscilloscope");
  // Each template's inputs as edited; a template not touched yet starts from its example.
  const [edits, setEdits] = useState<Record<string, unknown>>({});
  const [blank, setBlank] = useState({ instrument_class: "multimeter", manufacturer: "", model: "" });
  const [typedId, setTypedId] = useState<string | null>(null);
  const [problems, setProblems] = useState<Diagnostic[]>([]);
  const template = templates.data?.find((x) => x.id === choice);
  const inputs = edits[choice] ?? template?.example ?? null;
  const setInputs = (v: unknown) => setEdits((e) => ({ ...e, [choice]: v }));
  const choose = (c: string) => {
    setChoice(c);
    setProblems([]);
  };

  const maker = choice === BLANK ? blank.manufacturer : String(getAt(inputs, ["identity", "manufacturer"]) ?? "");
  const model = choice === BLANK ? blank.model : String(getAt(inputs, ["identity", "model"]) ?? "");
  const id = typedId ?? suggestId(maker, model);

  const submit = () => {
    const body =
      choice === BLANK
        ? { definition_id: id, ...blank }
        : { definition_id: id, template_id: choice, inputs: inputs as Record<string, unknown> };
    create.mutate(body, {
      onSuccess: (r) => {
        setProblems(r.diagnostics);
        if (!r.saved) {
          toast.error(t("newInstrument.notSaved", { count: r.diagnostics.filter((d) => d.severity === "error").length }));
          return;
        }
        const warnings = r.diagnostics.filter((d) => d.severity === "warning").length;
        toast.success(warnings ? t("newInstrument.savedWarnings", { id, count: warnings }) : t("newInstrument.saved", { id }));
        onOpenChange(false);
        navigate(`/i/${id}?section=${choice === BLANK ? "build" : "overview"}`);
      },
      onError: (e) => toast.error((e as Error).message),
    });
  };

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="flex max-h-[88vh] flex-col sm:max-w-3xl" data-testid="new-instrument">
        <DialogHeader>
          <DialogTitle>{t("newInstrument.title")}</DialogTitle>
          <DialogDescription>{t("newInstrument.body")}</DialogDescription>
        </DialogHeader>
        <div className="flex flex-wrap gap-1.5" role="radiogroup" aria-label={t("newInstrument.startFrom")}>
          {(templates.data ?? []).map((x) => (
            <StartChip key={x.id} on={choice === x.id} onClick={() => choose(x.id)} label={t("newInstrument.fromTemplate", { name: pickLabel(x.title, x.title_en) })} testId={`new-instrument-${x.id}`} />
          ))}
          <StartChip on={choice === BLANK} onClick={() => choose(BLANK)} label={t("newInstrument.blank")} testId="new-instrument-blank" />
        </div>
        <div className="min-h-0 flex-1 space-y-3 overflow-y-auto pr-1">
          {choice === BLANK ? (
            <BlankForm value={blank} onChange={setBlank} />
          ) : template && inputs ? (
            <>
              <p className="text-muted-foreground text-xs">{template.description}</p>
              <SchemaFields template={template} value={inputs} onChange={setInputs} problems={problems} />
            </>
          ) : null}
          <OtherProblems problems={problems} template={template && choice !== BLANK ? template : undefined} />
        </div>
        <DialogFooter className="items-center gap-2 sm:justify-between">
          <label className="flex items-center gap-2 text-xs">
            <span className="text-muted-foreground shrink-0">{t("newInstrument.id")}</span>
            <Input
              value={id}
              onChange={(e) => setTypedId(e.target.value)}
              className="num h-8 w-64 text-xs"
              aria-label={t("newInstrument.id")}
              data-testid="new-instrument-id"
            />
          </label>
          <Button onClick={submit} disabled={!id || create.isPending} data-testid="new-instrument-create">
            {t("newInstrument.create")}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}

function StartChip({ on, onClick, label, testId }: { on: boolean; onClick: () => void; label: string; testId: string }) {
  return (
    <button
      type="button"
      role="radio"
      aria-checked={on}
      onClick={onClick}
      data-testid={testId}
      className={cn(
        "inline-flex h-7 items-center rounded-full border px-3 text-xs transition-colors",
        on ? "border-primary/60 bg-primary/15 text-foreground" : "text-muted-foreground hover:text-foreground",
      )}
    >
      {label}
    </button>
  );
}

function BlankForm({
  value,
  onChange,
}: {
  value: { instrument_class: string; manufacturer: string; model: string };
  onChange: (v: { instrument_class: string; manufacturer: string; model: string }) => void;
}) {
  const { t } = useTranslation();
  return (
    <div className="grid gap-3 sm:grid-cols-3">
      <Labelled label={t("newInstrument.class")}>
        <Select value={value.instrument_class} onValueChange={(v) => onChange({ ...value, instrument_class: v })}>
          <SelectTrigger className="h-8 w-full text-xs" aria-label={t("newInstrument.class")}>
            <SelectValue />
          </SelectTrigger>
          <SelectContent>
            {CLASS_IDS.map((c) => (
              <SelectItem key={c} value={c}>
                {t(`acceptance.class.${c}`, { defaultValue: c })}
              </SelectItem>
            ))}
          </SelectContent>
        </Select>
      </Labelled>
      <Labelled label={t("newInstrument.manufacturer")}>
        <Input className="h-8 text-xs" value={value.manufacturer} onChange={(e) => onChange({ ...value, manufacturer: e.target.value })} aria-label={t("newInstrument.manufacturer")} />
      </Labelled>
      <Labelled label={t("newInstrument.model")}>
        <Input className="num h-8 text-xs" value={value.model} onChange={(e) => onChange({ ...value, model: e.target.value })} aria-label={t("newInstrument.model")} />
      </Labelled>
      <p className="text-muted-foreground text-xs sm:col-span-3">{t("newInstrument.blankHint")}</p>
    </div>
  );
}

function Labelled({ label, children, unit }: { label: string; children: React.ReactNode; unit?: string }) {
  return (
    <label className="block space-y-0.5">
      <span className="text-muted-foreground text-[11px]">
        {label}
        {unit && <span className="num ml-1 opacity-70">({unit})</span>}
      </span>
      {children}
    </label>
  );
}

function SchemaFields({
  template,
  value,
  onChange,
  problems,
}: {
  template: InstrumentTemplateInfo;
  value: unknown;
  onChange: (v: unknown) => void;
  problems: Diagnostic[];
}) {
  const root = template.schema as JsonSchema;
  return (
    <div className="space-y-3">
      {Object.entries(root.properties ?? {}).map(([key, node]) => (
        <Field key={key} root={root} node={node} path={[key]} value={value} onChange={onChange} problems={problems} depth={0} />
      ))}
    </div>
  );
}

type FieldProps = {
  root: JsonSchema;
  node: JsonSchema;
  path: Path;
  value: unknown; // the whole inputs
  onChange: (v: unknown) => void;
  problems: Diagnostic[];
  depth: number;
};

function Field({ root, node, path, value, onChange, problems, depth }: FieldProps) {
  const resolved = resolve(node, root).node;
  const label = pickLabel(resolved.title, resolved.title_en, String(path[path.length - 1]));
  const kind = fieldKind(node, root);
  const here = getAt(value, path);
  const set = (v: unknown) => onChange(setAt(value, path, v));
  const mine = problemsAt(problems, path);
  const pointer = `/template/inputs/${path.join("/")}`;

  if (kind.kind === "object") {
    return (
      <fieldset className={cn("space-y-2", depth === 0 ? "rounded-lg border p-3" : "border-l pl-3")} data-pointer={pointer}>
        <legend className="px-1 text-xs font-medium" title={resolved.description}>
          {label}
        </legend>
        <div className="grid gap-2 sm:grid-cols-3">
          {kind.properties.map(([k, child]) => {
            const ck = fieldKind(child, root).kind;
            const wide = ck === "object" || ck === "rows";
            return (
              <div key={k} className={cn(wide && "sm:col-span-3")}>
                <Field root={root} node={child} path={[...path, k]} value={value} onChange={onChange} problems={problems} depth={depth + 1} />
              </div>
            );
          })}
        </div>
        <Problems items={mine} />
      </fieldset>
    );
  }
  if (kind.kind === "rows") {
    const rows = Array.isArray(here) ? (here as Record<string, unknown>[]) : [];
    return (
      <div className="space-y-1" data-pointer={pointer}>
        <span className="text-muted-foreground text-[11px]" title={resolved.description}>
          {label}
        </span>
        <div className="overflow-x-auto">
          <table className="w-full text-xs">
            <thead>
              <tr>
                {kind.columns.map(([c, col]) => {
                  const cn_ = resolve(col, root).node;
                  return (
                    <th key={c} className="text-muted-foreground px-1 text-left text-[10.5px] font-normal" title={cn_.description}>
                      {pickLabel(cn_.title, cn_.title_en, c)}
                      {cn_.unit && <span className="num ml-1 opacity-70">({cn_.unit})</span>}
                    </th>
                  );
                })}
                <th />
              </tr>
            </thead>
            <tbody>
              {rows.map((_, i) => (
                <tr key={i}>
                  {kind.columns.map(([c, col]) => (
                    <td key={c} className="px-1 py-0.5 align-top">
                      <Scalar root={root} node={col} path={[...path, i, c]} value={value} onChange={onChange} problems={problems} bare />
                    </td>
                  ))}
                  <td>
                    <Button variant="ghost" size="sm" className="h-7 px-2 text-[11px]" onClick={() => set(rows.filter((__, j) => j !== i))}>
                      ×
                    </Button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
        <Button variant="outline" size="sm" className="h-7 text-[11px]" onClick={() => set([...rows, structuredClone(rows[rows.length - 1] ?? {})])}>
          +
        </Button>
        <Problems items={mine} />
      </div>
    );
  }
  return <Scalar root={root} node={node} path={path} value={value} onChange={onChange} problems={problems} label={label} />;
}

function Scalar({
  root,
  node,
  path,
  value,
  onChange,
  problems,
  label,
  bare = false,
}: Omit<FieldProps, "depth"> & { label?: string; bare?: boolean }) {
  const { t } = useTranslation();
  const resolved = resolve(node, root).node;
  const kind = fieldKind(node, root);
  const here = getAt(value, path);
  const set = (v: unknown) => onChange(setAt(value, path, v));
  const mine = problemsAt(problems, path);
  const invalid = mine.some((d) => d.severity === "error");
  const aria = label ?? pickLabel(resolved.title, resolved.title_en, String(path[path.length - 1]));
  const inputClass = cn("num h-7 text-xs", invalid && "border-error");
  const control = (() => {
    switch (kind.kind) {
      case "enum":
        return (
          <Select value={here == null ? "" : String(here)} onValueChange={set}>
            <SelectTrigger className={cn("h-7 w-full text-xs", invalid && "border-error")} aria-label={aria}>
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              {kind.options.map((o) => (
                <SelectItem key={o} value={o}>
                  {o}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
        );
      case "boolean":
        return <Switch checked={Boolean(here)} onCheckedChange={set} aria-label={aria} />;
      case "number":
        return <TextField className={inputClass} aria={aria} text={here == null ? "" : String(here)} read={(s) => (s === "" ? undefined : (parseNumber(s) ?? REFUSED))} onCommit={set} />;
      case "tuple":
        return (
          <div className="flex gap-1">
            {kind.items.map((_, i) => (
              <TextField
                key={i}
                className={inputClass}
                aria={`${aria} ${i + 1}`}
                text={String((here as unknown[] | undefined)?.[i] ?? "")}
                read={(s) => parseNumber(s) ?? REFUSED}
                onCommit={(v) => set(Object.assign([...((here as unknown[]) ?? [])], { [i]: v }))}
              />
            ))}
          </div>
        );
      case "list":
        return (
          <TextField
            className={inputClass}
            aria={aria}
            text={Array.isArray(here) ? here.join(", ") : ""}
            read={(s) => parseList(s, kind.item) ?? REFUSED}
            onCommit={set}
          />
        );
      case "json":
        return (
          <TextField
            className={inputClass}
            aria={aria}
            text={here === undefined ? "" : JSON.stringify(here)}
            read={(s) => {
              if (s.trim() === "") return undefined;
              try {
                return JSON.parse(s) as unknown;
              } catch {
                return REFUSED;
              }
            }}
            onCommit={set}
          />
        );
      default:
        return <TextField className={inputClass} aria={aria} text={here == null ? "" : String(here)} read={(s) => (s === "" ? undefined : s)} onCommit={set} />;
    }
  })();
  if (bare)
    return (
      <div data-pointer={`/template/inputs/${path.join("/")}`}>
        {control}
        <Problems items={mine} />
      </div>
    );
  return (
    <div data-pointer={`/template/inputs/${path.join("/")}`} title={resolved.description ?? undefined}>
      <Labelled label={label ?? aria} unit={resolved.unit}>
        {control}
      </Labelled>
      <Problems items={mine} />
      {kind.kind === "json" && <span className="text-muted-foreground text-[10px]">{t("newInstrument.jsonHint")}</span>}
    </div>
  );
}

/** A text box over DraftInput: commits what `read` parses on Enter or blur (null refuses it). */
function TextField({
  text,
  read,
  onCommit,
  aria,
  className,
}: {
  text: string;
  read: (s: string) => unknown;
  onCommit: (v: unknown) => void;
  aria: string;
  className?: string;
}) {
  const { t } = useTranslation();
  return (
    <DraftInput<unknown>
      text={text}
      className={className}
      aria-label={aria}
      parse={(typed) => {
        const s = typed.trim();
        if (s === text) return SKIP;
        const v = read(s);
        return v === REFUSED ? INVALID : commit(v);
      }}
      onCommit={(v) => onCommit(v)}
      onInvalid={() => toast.error(t("acceptance.build.invalidValue"))}
    />
  );
}

const REFUSED = Symbol("refused");

function Problems({ items }: { items: Diagnostic[] }) {
  if (!items.length) return null;
  return (
    <ul className="mt-0.5 space-y-0.5" data-testid="new-instrument-problem">
      {items.map((d, i) => (
        <li key={i} className={cn("text-[11px]", d.severity === "error" ? "text-error" : "text-muted-foreground")}>
          {d.message}
          {d.suggestion && <span className="text-muted-foreground"> — {d.suggestion}</span>}
        </li>
      ))}
    </ul>
  );
}

/** Diagnostics no field shows (a schema error deeper than the form, a check on the whole). */
function OtherProblems({ problems, template }: { problems: Diagnostic[]; template?: InstrumentTemplateInfo }) {
  const { t } = useTranslation();
  const shown = useMemo(() => {
    if (!template) return problems;
    return problems.filter((d) => {
      if (!d.pointer.startsWith("/template/inputs/")) return true;
      return typeof document === "undefined" || !document.querySelector(`[data-pointer="${CSS.escape(d.pointer)}"]`);
    });
  }, [problems, template]);
  if (!shown.length) return null;
  return (
    <div className="rounded-lg border p-2">
      <p className="text-muted-foreground mb-1 text-[11px]">{t("newInstrument.otherProblems")}</p>
      <ul className="space-y-0.5">
        {shown.map((d, i) => (
          <li key={i} className={cn("text-[11px]", d.severity === "error" ? "text-error" : "text-muted-foreground")}>
            <span className="num">{d.pointer}</span> {d.message}
          </li>
        ))}
      </ul>
    </div>
  );
}

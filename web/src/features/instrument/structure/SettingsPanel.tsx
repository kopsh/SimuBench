import { AlertTriangle, ChevronDown, ChevronRight, Plus, Trash2 } from "lucide-react";
import { useState } from "react";
import { useTranslation } from "react-i18next";
import { toast } from "sonner";
import { DraftInput, INVALID, SKIP, commit } from "@/components/app/DraftInput";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { Switch } from "@/components/ui/switch";
import { cn } from "@/lib/utils";
import {
  type CommandKind,
  type ComponentDoc,
  type Scope,
  SETTING_TYPES,
  type SettingSpec,
  type SettingType,
  type Structure,
  addCommand,
  addSetting,
  commandHeader,
  commandSettingChoices,
  commandTarget,
  literalText,
  needsChannelSuffix,
  operationChoices,
  parseLiteral,
  removeCommand,
  removeSetting,
  settingName,
  settingUses,
  updateCommand,
  updateSetting,
} from "./model";

type Change = (next: Structure | null) => boolean;

// ---- settings ----------------------------------------------------------------------------------

/** The settings of the instrument and of every channel (#1206): add, edit the common fields, delete. */
export function SettingsPanel({ structure, editable, onChange }: { structure: Structure; editable: boolean; onChange: Change }) {
  const { t } = useTranslation();
  const [name, setName] = useState("");
  const [scope, setScope] = useState<Scope>("instrument");
  const [type, setType] = useState<SettingType>("float");
  const [open, setOpen] = useState<string | null>(null);
  const rows = (["instrument", "channel"] as const).flatMap((sc) =>
    Object.entries(sc === "instrument" ? structure.state : structure.channel_state).map(([n, spec]) => ({ scope: sc, name: n, spec })),
  );
  const add = () => {
    const n = settingName(name);
    if (!n) return void toast.error(t("acceptance.build.settingInvalid"));
    const next = addSetting(structure, scope, n, type);
    if (!next) return void toast.error(t("acceptance.build.settingTaken"));
    if (onChange(next)) {
      setName("");
      setOpen(`${scope}:${n}`);
    }
  };
  return (
    <div className="space-y-3 p-3 text-xs" data-testid="build-settings">
      <p className="text-muted-foreground text-[11px]">{t("acceptance.build.settingsHint")}</p>
      {!rows.length && <p className="text-muted-foreground">{t("acceptance.build.settingNone")}</p>}
      <ul className="space-y-1">
        {rows.map((r) => {
          const key = `${r.scope}:${r.name}`;
          const expanded = open === key;
          return (
            <li key={key} className="rounded-md border" data-testid={`build-setting-${r.name}`}>
              <button
                type="button"
                className="hover:bg-accent/50 flex w-full items-center gap-1.5 rounded-md px-2 py-1 text-left"
                aria-expanded={expanded}
                onClick={() => setOpen(expanded ? null : key)}
              >
                {expanded ? <ChevronDown className="size-3.5 shrink-0" /> : <ChevronRight className="size-3.5 shrink-0" />}
                <span className="num truncate font-medium">{r.scope === "channel" ? `ch.${r.name}` : r.name}</span>
                <span className="text-muted-foreground ml-auto shrink-0 text-[10.5px]">
                  {t(`acceptance.build.types.${String(r.spec.type)}`, { defaultValue: String(r.spec.type) })}
                  {typeof r.spec.unit === "string" ? ` · ${r.spec.unit}` : ""}
                </span>
              </button>
              {expanded && (
                <SettingEditor structure={structure} scope={r.scope} name={r.name} spec={r.spec} editable={editable} onChange={onChange} />
              )}
            </li>
          );
        })}
      </ul>
      {editable && (
        <section className="space-y-1.5 border-t pt-3">
          <Input
            value={name}
            onChange={(e) => setName(e.target.value)}
            onKeyDown={(e) => e.key === "Enter" && add()}
            placeholder={t("acceptance.build.settingNew")}
            aria-label={t("acceptance.build.settingNew")}
            className="num h-7 text-xs"
          />
          <div className="flex gap-1.5">
            <Select value={scope} onValueChange={(v) => setScope(v as Scope)}>
              <SelectTrigger className="h-7 flex-1 text-xs" size="sm" aria-label={t("acceptance.build.scopeChannel")}>
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                <SelectItem value="instrument">{t("acceptance.build.scopeInstrument")}</SelectItem>
                <SelectItem value="channel">{t("acceptance.build.scopeChannel")}</SelectItem>
              </SelectContent>
            </Select>
            <TypeSelect value={type} onChange={setType} />
          </div>
          <Button size="sm" className="h-7" onClick={add} disabled={!name.trim()}>
            <Plus className="size-3.5" /> {t("acceptance.build.settingAdd")}
          </Button>
        </section>
      )}
    </div>
  );
}

function TypeSelect({ value, onChange, disabled }: { value: SettingType; onChange: (v: SettingType) => void; disabled?: boolean }) {
  const { t } = useTranslation();
  return (
    <Select value={value} onValueChange={(v) => onChange(v as SettingType)} disabled={disabled}>
      <SelectTrigger className="h-7 flex-1 text-xs" size="sm" aria-label={t("acceptance.build.settingType")}>
        <SelectValue />
      </SelectTrigger>
      <SelectContent>
        {SETTING_TYPES.map((ty) => (
          <SelectItem key={ty} value={ty}>
            {t(`acceptance.build.types.${ty}`)}
          </SelectItem>
        ))}
      </SelectContent>
    </Select>
  );
}

function SettingEditor({
  structure,
  scope,
  name,
  spec,
  editable,
  onChange,
}: {
  structure: Structure;
  scope: Scope;
  name: string;
  spec: SettingSpec;
  editable: boolean;
  onChange: Change;
}) {
  const { t } = useTranslation();
  const type = String(spec.type ?? "float");
  const numeric = type === "float" || type === "int";
  const set = (patch: SettingSpec) => onChange(updateSetting(structure, scope, name, patch));
  const uses = settingUses(structure, scope, name);
  const known = (SETTING_TYPES as readonly string[]).includes(type);
  const field = (key: string, label: string, like: unknown) => (
    <label className="block space-y-0.5">
      <span className="text-muted-foreground text-[10.5px]">{label}</span>
      <DraftInput<unknown>
        text={literalText(spec[key])}
        disabled={!editable}
        className="num h-7 text-xs"
        aria-label={`${name} ${label}`}
        parse={(typed) => {
          const p = parseLiteral(typed, like);
          return p.ok ? commit(p.value) : INVALID;
        }}
        onInvalid={() => toast.error(t("acceptance.build.invalidValue"))}
        onCommit={(v) => {
          if (JSON.stringify(v) !== JSON.stringify(spec[key])) set({ [key]: v });
        }}
      />
    </label>
  );
  return (
    <div className="space-y-2 border-t px-2 py-2">
      <label className="block space-y-0.5">
        <span className="text-muted-foreground text-[10.5px]">{t("acceptance.build.settingType")}</span>
        {known ? (
          <TypeSelect value={type as SettingType} onChange={(v) => set({ type: v })} disabled={!editable} />
        ) : (
          <p className="num">{type}</p>
        )}
      </label>
      {numeric && (
        <div className="grid grid-cols-3 gap-1.5">
          {field("unit", t("acceptance.build.unit"), "")}
          {field("min", t("acceptance.build.min"), 0)}
          {field("max", t("acceptance.build.max"), 0)}
        </div>
      )}
      {type === "enum" && (
        <label className="block space-y-0.5">
          <span className="text-muted-foreground text-[10.5px]">{t("acceptance.build.options")}</span>
          <DraftInput<string[]>
            text={Array.isArray(spec.options) ? spec.options.join(", ") : ""}
            disabled={!editable}
            className="num h-7 text-xs"
            aria-label={`${name} ${t("acceptance.build.options")}`}
            parse={(typed) => {
              const opts = typed.split(",").map((o) => o.trim()).filter(Boolean);
              return opts.length ? commit(opts) : SKIP;
            }}
            onCommit={(opts) => {
              const patch: SettingSpec = { options: opts };
              if (!opts.includes(String(spec.default))) patch.default = opts[0];
              set(patch);
            }}
          />
        </label>
      )}
      {field("default", t("acceptance.build.defaultValue"), numeric ? 0 : type === "bool" ? false : "")}
      <div className="grid grid-cols-2 gap-1.5">
        {field("label", t("acceptance.build.label"), "")}
        {field("label_en", t("acceptance.build.labelEn"), "")}
      </div>
      {editable && (
        <div className="space-y-1">
          {(uses.params > 0 || uses.commands > 0) && (
            <p className="text-muted-foreground text-[10.5px]">{t("acceptance.build.settingUses", uses)}</p>
          )}
          <Button variant="outline" size="sm" className="text-error h-7" onClick={() => onChange(removeSetting(structure, scope, name))}>
            <Trash2 className="size-3.5" /> {t("acceptance.build.settingDelete")}
          </Button>
        </div>
      )}
    </div>
  );
}

// ---- commands ----------------------------------------------------------------------------------

/** The commands (#1206): a header bound to a setting or to an operation, settable and queryable. */
export function CommandsPanel({
  structure,
  docs,
  editable,
  onChange,
}: {
  structure: Structure;
  docs: ReadonlyMap<string, ComponentDoc>;
  editable: boolean;
  onChange: Change;
}) {
  const { t } = useTranslation();
  const [header, setHeader] = useState("");
  const [kind, setKind] = useState<CommandKind>("state");
  const [target, setTarget] = useState("");
  const [open, setOpen] = useState<number | null>(null);
  const settings = commandSettingChoices(structure);
  const ops = operationChoices(structure, docs);
  const choices = kind === "state" ? settings : ops;
  const add = () => {
    const h = commandHeader(header);
    if (!h) return void toast.error(t("acceptance.build.cmdInvalid"));
    const next = addCommand(structure, h, kind, target);
    if (!next) return void toast.error(t("acceptance.build.cmdTaken"));
    if (onChange(next)) {
      setHeader("");
      setOpen(next.commands.length - 1);
    }
  };
  return (
    <div className="space-y-3 p-3 text-xs" data-testid="build-commands">
      <p className="text-muted-foreground text-[11px]">{t("acceptance.build.commandsHint")}</p>
      {!structure.commands.length && <p className="text-muted-foreground">{t("acceptance.build.cmdNone")}</p>}
      <ul className="space-y-1">
        {structure.commands.map((c, i) => {
          const expanded = open === i;
          const { kind: k, target: to } = commandTarget(c);
          return (
            <li key={`${c.header}-${i}`} className="rounded-md border" data-testid="build-command">
              <button
                type="button"
                className="hover:bg-accent/50 flex w-full items-center gap-1.5 rounded-md px-2 py-1 text-left"
                aria-expanded={expanded}
                onClick={() => setOpen(expanded ? null : i)}
              >
                {expanded ? <ChevronDown className="size-3.5 shrink-0" /> : <ChevronRight className="size-3.5 shrink-0" />}
                <span className="num min-w-0 flex-1 truncate font-medium">{c.header}</span>
                {needsChannelSuffix(c) && <AlertTriangle className="text-warning size-3.5 shrink-0" aria-label={t("acceptance.build.cmdNeedsCh")} />}
                <span className={cn("num text-muted-foreground max-w-[45%] shrink-0 truncate text-[10.5px]", k === "other" && "italic")}>{to}</span>
              </button>
              {expanded && (
                <CommandEditor
                  structure={structure}
                  index={i}
                  settings={settings}
                  ops={ops}
                  editable={editable && k !== "other"}
                  onChange={onChange}
                  onRemoved={() => setOpen(null)}
                  canDelete={editable}
                />
              )}
            </li>
          );
        })}
      </ul>
      {editable && (
        <section className="space-y-1.5 border-t pt-3">
          <Input
            value={header}
            onChange={(e) => setHeader(e.target.value)}
            onKeyDown={(e) => e.key === "Enter" && target && add()}
            placeholder={t("acceptance.build.cmdHeader")}
            aria-label={t("acceptance.build.cmdHeader")}
            className="num h-7 text-xs"
          />
          <Select
            value={kind}
            onValueChange={(v) => {
              setKind(v as CommandKind);
              setTarget("");
            }}
          >
            <SelectTrigger className="h-7 w-full text-xs" size="sm" aria-label={t("acceptance.build.cmdKind")}>
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              <SelectItem value="state">{t("acceptance.build.kindState")}</SelectItem>
              <SelectItem value="op">{t("acceptance.build.kindOp")}</SelectItem>
            </SelectContent>
          </Select>
          {choices.length ? (
            <Select value={target} onValueChange={setTarget}>
              <SelectTrigger className="num h-7 w-full text-xs" size="sm" aria-label={t("acceptance.build.cmdTarget")}>
                <SelectValue placeholder={t("acceptance.build.cmdTarget")} />
              </SelectTrigger>
              <SelectContent>
                {choices.map((c) => (
                  <SelectItem key={c} value={c} className="num">
                    {c}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
          ) : (
            <p className="text-muted-foreground text-[11px]">{t("acceptance.build.noTargets")}</p>
          )}
          <Button size="sm" className="h-7" onClick={add} disabled={!header.trim() || !target}>
            <Plus className="size-3.5" /> {t("acceptance.build.cmdAdd")}
          </Button>
        </section>
      )}
    </div>
  );
}

function CommandEditor({
  structure,
  index,
  settings,
  ops,
  editable,
  canDelete,
  onChange,
  onRemoved,
}: {
  structure: Structure;
  index: number;
  settings: string[];
  ops: string[];
  editable: boolean;
  canDelete: boolean;
  onChange: Change;
  onRemoved: () => void;
}) {
  const { t } = useTranslation();
  const c = structure.commands[index];
  const { kind, target } = commandTarget(c);
  const set = (patch: Record<string, unknown>) => onChange(updateCommand(structure, index, patch));
  const choices = kind === "op" ? ops : settings;
  return (
    <div className="space-y-2 border-t px-2 py-2">
      {kind === "other" && <p className="text-muted-foreground text-[11px]">{t("acceptance.build.cmdOther", { target })}</p>}
      <label className="block space-y-0.5">
        <span className="text-muted-foreground text-[10.5px]">{t("acceptance.build.cmdHeader")}</span>
        <DraftInput<string>
          text={c.header ?? ""}
          disabled={!editable}
          className="num h-7 text-xs"
          aria-label={t("acceptance.build.cmdHeader")}
          parse={(typed) => {
            const h = commandHeader(typed);
            return h ? commit(h) : INVALID;
          }}
          onInvalid={() => toast.error(t("acceptance.build.cmdInvalid"))}
          onCommit={(h) => {
            if (h === c.header) return;
            if (structure.commands.some((x, i) => i !== index && x.header === h)) return void toast.error(t("acceptance.build.cmdTaken"));
            set({ header: h });
          }}
        />
      </label>
      {needsChannelSuffix(c) && <p className="text-warning text-[10.5px]">{t("acceptance.build.cmdNeedsCh")}</p>}
      {kind !== "other" && (
        <>
          <div className="grid grid-cols-2 gap-1.5">
            <Select
              value={kind}
              disabled={!editable}
              onValueChange={(v) => {
                const first = (v === "op" ? ops : settings)[0];
                if (first) set(v === "op" ? { op: first } : { state: first });
              }}
            >
              <SelectTrigger className="h-7 text-xs" size="sm" aria-label={t("acceptance.build.cmdKind")}>
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                <SelectItem value="state">{t("acceptance.build.kindState")}</SelectItem>
                <SelectItem value="op">{t("acceptance.build.kindOp")}</SelectItem>
              </SelectContent>
            </Select>
            <Select value={target} disabled={!editable} onValueChange={(v) => set(kind === "op" ? { op: v } : { state: v })}>
              <SelectTrigger className="num h-7 text-xs" size="sm" aria-label={t("acceptance.build.cmdTarget")}>
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                {(choices.includes(target) ? choices : [target, ...choices]).map((o) => (
                  <SelectItem key={o} value={o} className="num">
                    {o}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
          </div>
          <div className="flex items-center gap-4">
            <label className="flex items-center gap-1.5">
              <Switch size="sm" checked={c.set !== false} disabled={!editable} onCheckedChange={(v) => set({ set: v ? undefined : false })} />
              {t("acceptance.build.cmdSet")}
            </label>
            <label className="flex items-center gap-1.5">
              <Switch size="sm" checked={c.query !== false} disabled={!editable} onCheckedChange={(v) => set({ query: v ? undefined : false })} />
              {t("acceptance.build.cmdQuery")}
            </label>
          </div>
        </>
      )}
      {canDelete && (
        <Button
          variant="outline"
          size="sm"
          className="text-error h-7"
          onClick={() => {
            onChange(removeCommand(structure, index));
            onRemoved();
          }}
        >
          <Trash2 className="size-3.5" /> {t("acceptance.build.cmdDelete")}
        </Button>
      )}
    </div>
  );
}

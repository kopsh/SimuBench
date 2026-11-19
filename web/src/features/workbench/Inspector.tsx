import { AlertTriangle, Blocks, Check, ChevronDown, ClipboardCheck, Copy, History, MonitorPlay, MousePointerClick, RotateCcw, Send } from "lucide-react";
import { type ReactNode, useEffect, useRef, useState } from "react";
import { useTranslation } from "react-i18next";
import { toast } from "sonner";
import type { InstrumentInfo, SessionInfo, StateVarInfo } from "@/api/client";
import { scpiOutcome, useScpi, useSession } from "@/api/hooks";
import { remoteInstrument } from "@/components/app/ClientsConfirm";
import { EmptyState } from "@/components/app/StateViews";
import { SaveTemplateButton } from "@/features/assistant/SaveTemplateDialog";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { ScrollArea } from "@/components/ui/scroll-area";
import { channelColor } from "@/lib/channels";
import { pickLabel } from "@/i18n";
import { displayUnit, formatEng } from "@/lib/format";
import { cn } from "@/lib/utils";
import { accessoryLabel, ALL_OPEN_MAX, channelVars, findSettings, globalVars, inactiveUnderAuto, instanceFamilies, type InstanceFamily, instrumentTitle, numeric, groupName, groupOpen, panelSections, type PanelSection, panelSettings, type SettingGroup, slotRows, splitSettings, stateMap } from "./model";
import { loadHistory, pushHistory, QUICK_COMMANDS, recentChips, saveHistory } from "./commandHistory";
import { FaultPanel } from "./faults/FaultPanel";
import { ProbePanel } from "./ProbePanel";
import { RemoteBanner, RemoteMark } from "./RemoteBanner";
import { StateField } from "./StateField";
import { showSetting } from "./reveal";
import { useScreenPaths } from "./onScreen";
import { useWorkbench } from "./store";

export function Inspector({ session, readonly }: { session: SessionInfo; readonly: boolean }) {
  const { t } = useTranslation();
  const selection = useWorkbench((s) => s.selection);
  let body: ReactNode = (
    <EmptyState icon={<MousePointerClick className="size-6" />} title={t("workbench.inspector")} body={t("workbench.noSelection")} />
  );
  if (selection?.kind === "instrument") {
    const inst = session.instruments.find((i) => i.name === selection.id);
    if (inst) body = <InstrumentInspector sid={session.id} inst={inst} readonly={readonly} />;
  } else if (selection?.kind === "part") {
    const dut = session.parts.find((d) => d.name === selection.id);
    if (dut)
      body = (
        <div className="space-y-4 p-3">
          <Header title={dut.label || dut.name} subtitle={`${dut.dut ? "DUT" : t("workbench.part")} · ${dut.type}`} />
          <Section title={t("workbench.params")}>
            <KeyValues entries={Object.entries(dut.params).map(([k, v]) => [k, typeof v === "number" ? formatEng(v, "") : String(v)])} />
          </Section>
          <Section title={t("workbench.ports")}>
            <KeyValues entries={dut.ports.map((p) => [p.name, p.net ?? t("workbench.notConnected")])} />
          </Section>
          <Section title={t("fault.title")} collapsible defaultOpen={false}>
            <FaultPanel
              sid={session.id}
              component={dut.name}
              params={Object.entries(dut.params)
                .filter(([, v]) => typeof v === "number")
                .map(([k]) => k)}
              disabled={readonly}
            />
          </Section>
        </div>
      );
  } else if (selection?.kind === "wire") {
    const w = session.wires.find((x) => x.index === selection.id);
    if (w)
      body = (
        <div className="space-y-4 p-3">
          <Header title={`${w.from} → ${w.to}`} subtitle={`${t("workbench.wires")} #${w.index + 1}`} mono />
          <Section title={t("workbench.net")}>
            <KeyValues entries={[[t("workbench.net"), w.net ?? "—"]]} />
          </Section>
          {w.net && (
            <Section title={t("probe.title")}>
              <ProbePanel sid={session.id} net={w.net} />
            </Section>
          )}
          {w.accessories.length > 0 && (
            <Section title={t("workbench.accessories")}>
              {w.accessories.map((a) => (
                <div key={a.name} className="mb-2">
                  <div className="mb-1 text-xs font-medium">
                    {accessoryLabel(a.type, a.params)} <span className="text-muted-foreground num">{a.type}</span>
                  </div>
                  <KeyValues
                    entries={Object.entries(a.params).map(([k, v]) => [
                      k,
                      typeof v === "number" ? formatEng(numeric(v), k.startsWith("r_") ? displayUnit("OHM") : "") : String(v),
                    ])}
                  />
                </div>
              ))}
            </Section>
          )}
        </div>
      );
  } else if (selection?.kind === "net") {
    const net = session.nets.find((n) => n.id === selection.id);
    if (net)
      body = (
        <div className="space-y-4 p-3">
          <Header title={net.id} subtitle={net.members.join(" · ")} mono />
          <Section title={t("probe.title")}>
            <ProbePanel sid={session.id} net={net.id} />
          </Section>
        </div>
      );
  }
  return <ScrollArea className="h-full">{body}</ScrollArea>;
}

export function InstrumentInspector({ sid, inst, readonly }: { sid: string; inst: InstrumentInfo; readonly: boolean }) {
  const { t } = useTranslation();
  const openScreen = useWorkbench((s) => s.openScreen);
  const openInternals = useWorkbench((s) => s.openInternals);
  const openAcceptance = useWorkbench((s) => s.openAcceptance);
  const bench = useSession(sid).data?.bench_path;
  // Beside its own screen, the settings the screen shows as fields are left out: one place to change a value (#1229).
  const besideScreen = useWorkbench((s) => s.active) === `screen:${inst.name}`;
  const onScreen = useScreenPaths(besideScreen ? inst.name : null);
  const offScreen = (v: { path: string }) => !onScreen.has(v.path);
  const globals = globalVars(inst).filter(offScreen);
  const vars = stateMap(inst);
  const channels = Array.from({ length: inst.channels }, (_, i) => i + 1).filter((n) => channelVars(inst, n).some(offScreen));
  const reveal = useWorkbench((s) => s.reveal);
  const consumeReveal = useWorkbench((s) => s.consumeReveal);
  useEffect(() => {
    if (!reveal || reveal.instrument !== inst.name) return;
    // The channel and the group open on this same request: the field is looked for once they have rendered.
    return showSetting(reveal.path, (found) => {
      // Nothing to show: say so, the focus stays where it is; either way the request is spent (#819).
      if (!found) toast.info(t("workbench.revealMissing", { path: reveal.path }));
      consumeReveal(reveal.n);
    });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [reveal?.n]);
  return (
    <div className="@container space-y-4 p-3">
      {/* Beside its own screen, the screen carries the remote banner and LOCAL; here only a mark (#1273). */}
      {!besideScreen && <RemoteBanner compact sid={sid} inst={inst} className="-mx-3 -mt-3" />}
      <Header
        title={instrumentTitle(inst)}
        subtitle={`${inst.manufacturer} ${inst.model}`}
        aside={
          <>
            {besideScreen && <RemoteMark inst={inst} />}
            <ResetInstrument sid={sid} inst={inst} disabled={readonly} />
          </>
        }
      />
      {/* One primary action (the screen); the rest are less frequent and sit as a row below it (#1252).
          Beside its own screen, opening it is nothing to do: it joins the row as a secondary action (#1355). */}
      {/* A narrow inspector keeps the icons and drops the words, which stay as each button's name and
          tooltip, rather than let them run into each other (#1354, #1355). */}
      <div className="@container grid gap-1.5" data-testid="instrument-actions">
        {!besideScreen && (
          <Button size="sm" onClick={() => openScreen(inst.name)}>
            <MonitorPlay className="size-4" /> {t("workbench.openScreen")}
          </Button>
        )}
        <div
          className={cn(
            "grid gap-1.5",
            besideScreen ? "grid-cols-4 @max-[22rem]:[&_[data-action-label]]:hidden" : "grid-cols-3 @max-[17rem]:[&_[data-action-label]]:hidden",
          )}
        >
          {besideScreen && (
            <Button variant="outline" size="sm" className="px-1 text-xs" aria-label={t("workbench.openScreen")} title={t("workbench.openScreen")} onClick={() => openScreen(inst.name)}>
              <MonitorPlay className="size-4" /> <span data-action-label>{t("workbench.screenShort")}</span>
            </Button>
          )}
          <Button variant="outline" size="sm" className="px-1 text-xs" aria-label={t("internals.open")} title={t("internals.open")} onClick={() => openInternals(inst.name)}>
            <Blocks className="size-4" /> <span data-action-label>{t("internals.short")}</span>
          </Button>
          <Button variant="outline" size="sm" className="px-1 text-xs" aria-label={t("acceptance.open")} title={t("acceptance.open")} onClick={() => openAcceptance(inst.name)}>
            <ClipboardCheck className="size-4" /> <span data-action-label>{t("acceptance.short")}</span>
          </Button>
          {bench && <SaveTemplateButton compact bench={bench} instrument={inst.name} label={inst.label ?? instrumentTitle(inst)} />}
        </div>
      </div>
      <IdentitySection inst={inst} />
      <Section title={t("workbench.sendCommand")}>
        <ScpiConsole key={inst.name} sid={sid} instrument={inst.name} disabled={readonly} />
      </Section>
      {inst.errors.length > 0 && (
        <Section title={`${t("workbench.errors")} · ${inst.errors.length}`}>
          <ul className="space-y-1">
            {inst.errors.map((e, i) => (
              <li key={i} className="text-error flex items-start gap-1.5 text-xs">
                <AlertTriangle className="mt-0.5 size-3.5 shrink-0" />
                <span className="num">{e.code}</span>
                <span className="text-foreground">{e.message}</span>
              </li>
            ))}
          </ul>
        </Section>
      )}
      <Section title={t("fault.title")} collapsible defaultOpen={false}>
        <FaultPanel sid={sid} instrument={inst.name} disabled={readonly} />
      </Section>
      {(globals.length > 0 || channels.length > 0) && (
        <InstrumentSettings sid={sid} inst={inst} readonly={readonly} stateVars={vars} globals={globals} channels={channels} hidden={onScreen} />
      )}
      {onScreen.size > 0 && (
        <p data-testid="inspector-on-screen" className="text-muted-foreground flex items-center gap-1.5 text-xs">
          <MonitorPlay className="size-3.5 shrink-0" /> {t("workbench.onScreenNote", { count: onScreen.size })}
        </p>
      )}
    </div>
  );
}

/**
 * Reset this one instrument: `*RST` through the same path as the command box, so it shows in the
 * communication log and an instrument in remote control refuses it like any command from the UI.
 * The other instruments, the session and its record are left alone; the top bar's reset
 * power-cycles the whole bench.
 */
function ResetInstrument({ sid, inst, disabled }: { sid: string; inst: InstrumentInfo; disabled: boolean }) {
  const { t } = useTranslation();
  const scpi = useScpi(sid, inst.name);
  const name = instrumentTitle(inst);
  return (
    <Button
      variant="ghost"
      size="icon"
      className="size-7 shrink-0"
      aria-label={t("workbench.resetInstrument")}
      title={t("workbench.resetInstrumentHint")}
      disabled={disabled || scpi.isPending}
      onClick={() =>
        scpi.mutate("*RST", {
          onSuccess: (r) => {
            const { ok, message } = scpiOutcome(r);
            if (ok) toast.success(t("workbench.resetInstrumentDone", { name }));
            else toast.error(message ?? t("workbench.resetInstrument"));
          },
          // An instrument in remote control is already reported, with its LOCAL button (useScpi).
          onError: (e) => !remoteInstrument(e) && toast.error((e as Error).message),
        })
      }
    >
      <RotateCcw className={scpi.isPending ? "size-3.5 animate-spin" : "size-3.5"} />
    </Button>
  );
}

function ScpiConsole({ sid, instrument, disabled }: { sid: string; instrument: string; disabled: boolean }) {
  const { t } = useTranslation();
  const scpi = useScpi(sid, instrument);
  const [cmd, setCmd] = useState("");
  const [history, setHistory] = useState<string[]>(() => loadHistory(instrument));
  const [cursor, setCursor] = useState(-1);
  const input = useRef<HTMLInputElement>(null);
  const run = (command: string) => {
    const c = command.trim();
    if (!c) return;
    scpi.mutate(c);
    const next = pushHistory(history, c);
    setHistory(next);
    saveHistory(instrument, next);
    setCursor(-1);
  };
  const recent = recentChips(history);
  return (
    <div className="space-y-2">
      <div className="flex gap-1.5">
        <Input
          ref={input}
          value={cmd}
          onChange={(e) => setCmd(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === "Enter") run(cmd);
            if (e.key === "ArrowUp" && history.length) {
              const next = Math.min(cursor + 1, history.length - 1);
              setCursor(next);
              setCmd(history[next]);
              e.preventDefault();
            }
            if (e.key === "ArrowDown") {
              const next = cursor - 1;
              setCursor(next);
              setCmd(next >= 0 ? history[next] : "");
              e.preventDefault();
            }
          }}
          placeholder={t("workbench.commandPlaceholder")}
          className="num h-8 text-xs"
          disabled={disabled}
          aria-label={t("workbench.sendCommand")}
        />
        <Button size="sm" className="h-8" onClick={() => run(cmd)} disabled={disabled || scpi.isPending} aria-label={t("workbench.send")}>
          <Send className="size-3.5" />
        </Button>
      </div>
      <div className="flex flex-wrap gap-1" data-testid="scpi-quick" aria-label={t("workbench.quickCommands")}>
        {QUICK_COMMANDS.map((c) => (
          <Button key={c} variant="outline" size="sm" className="num h-6 px-1.5 text-[11px]" title={t("workbench.quickHint", { command: c })} disabled={disabled || scpi.isPending} onClick={() => run(c)}>
            {c}
          </Button>
        ))}
      </div>
      {recent.length > 0 && (
        <div className="flex flex-wrap items-center gap-1" data-testid="scpi-recent">
          <History className="text-muted-foreground size-3.5 shrink-0" aria-label={t("workbench.recentCommands")} />
          {recent.map((c) => (
            <Button
              key={c}
              variant="ghost"
              size="sm"
              className="num bg-muted/60 h-6 max-w-full truncate px-1.5 text-[11px]"
              title={t("workbench.recentHint", { command: c })}
              disabled={disabled}
              onClick={() => {
                setCmd(c);
                setCursor(-1);
                input.current?.focus();
              }}
            >
              {c}
            </Button>
          ))}
        </div>
      )}
      {scpi.data && (
        <div className="bg-muted/60 num rounded-md p-2 text-[11.5px] break-all" role="status" aria-label={t("workbench.response")}>
          {scpi.data.response != null && <div>{scpi.data.response}</div>}
          {scpi.data.errors.map((e, i) => (
            <div key={i} className="text-error">
              {e.code},"{e.message}"
            </div>
          ))}
          {scpi.data.link_error && <div className="text-error">{t("fault.linkDown")}: {scpi.data.link_error}</div>}
          {scpi.data.timed_out && <div className="text-warn">{t("fault.timedOut")}</div>}
          {scpi.data.response == null && scpi.data.errors.length === 0 && !scpi.data.timed_out && !scpi.data.link_error && (
            <div className="text-muted-foreground">✓</div>
          )}
        </div>
      )}
    </div>
  );
}

export function Header({ title, subtitle, mono, aside }: { title: string; subtitle: string; mono?: boolean; aside?: ReactNode }) {
  return (
    <div className="flex items-start gap-2">
      <div className="min-w-0 flex-1">
        <div className={cn("text-sm font-semibold break-all", mono && "num")}>{title}</div>
        <div className="text-muted-foreground num text-xs">{subtitle}</div>
      </div>
      {aside}
    </div>
  );
}

export function Section({
  title,
  children,
  collapsible,
  defaultOpen = true,
  reveal,
  group,
}: {
  title: ReactNode;
  children: ReactNode;
  collapsible?: boolean;
  defaultOpen?: boolean;
  /** The number of a reveal request for a setting inside: opens the section (#800). */
  reveal?: number | null;
  /** A settings group's id, as ``data-group`` (tests find a group by it). */
  group?: string;
}) {
  const [open, setOpen] = useState(defaultOpen);
  // A reveal request opens the section: state adjusted while rendering, not in an effect.
  const [seen, setSeen] = useState(reveal);
  if (reveal !== seen) {
    setSeen(reveal);
    if (reveal != null) setOpen(true);
  }
  return (
    <section data-group={group}>
      <button
        type="button"
        className={cn(
          "text-muted-foreground mb-1.5 flex w-full items-center gap-1 text-[11px] font-medium tracking-wide uppercase",
          !collapsible && "pointer-events-none",
        )}
        onClick={() => collapsible && setOpen((o) => !o)}
        aria-expanded={open}
      >
        {collapsible && <ChevronDown className={cn("size-3.5 transition-transform", !open && "-rotate-90")} />}
        {title}
      </button>
      {open && children}
    </section>
  );
}

export function KeyValues({ entries }: { entries: [string, string][] }) {
  return (
    <dl className="grid grid-cols-[auto_1fr] gap-x-3 gap-y-1 text-xs">
      {entries.map(([k, v]) => (
        <div key={k} className="contents">
          <dt className="text-muted-foreground">{k}</dt>
          <dd className="num text-right break-all">{v}</dd>
        </div>
      ))}
    </dl>
  );
}

const IDENTITY_KEY = "simubench:inspector-identity";

function identityOpen(): boolean {
  try {
    return localStorage.getItem(IDENTITY_KEY) === "open";
  } catch {
    return false;
  }
}

/**
 * The instrument's identity and addresses (#1355): folded to the one address a script uses, so the
 * settings come first; opened, *IDN? and every address. The choice is remembered in this browser.
 */
function IdentitySection({ inst }: { inst: InstrumentInfo }) {
  const { t } = useTranslation();
  const [open, setOpen] = useState(identityOpen);
  const toggle = () => {
    const next = !open;
    setOpen(next);
    try {
      localStorage.setItem(IDENTITY_KEY, next ? "open" : "closed");
    } catch {
      // private window or blocked storage: the choice lasts this page only
    }
  };
  const more = [inst.idn, inst.address_hislip, inst.address_vxi11, inst.address_real].filter(Boolean).length;
  return (
    <section data-testid="identity-section" data-open={open || undefined}>
      <button
        type="button"
        className="text-muted-foreground mb-1.5 flex w-full items-center gap-1 text-[11px] font-medium tracking-wide uppercase"
        onClick={toggle}
        aria-expanded={open}
      >
        <ChevronDown className={cn("size-3.5 transition-transform", !open && "-rotate-90")} />
        {t("workbench.identitySection")}
        {!open && <span className="ml-auto font-normal tracking-normal normal-case">{t("workbench.identityMore", { count: more })}</span>}
      </button>
      {open && <CopyRow label="*IDN?" value={inst.idn} />}
      {inst.address_virtual ? (
        <CopyRow label={t("workbench.virtualAddress")} value={inst.address_virtual} />
      ) : (
        <div className="text-muted-foreground mb-1.5 text-[11px]" data-testid="no-scpi-service">
          {t("workbench.virtualAddress")}: {t("workbench.noScpiService")}
        </div>
      )}
      {open && inst.address_hislip && <CopyRow label={t("workbench.hislipAddress")} value={inst.address_hislip} />}
      {open && inst.address_vxi11 && <CopyRow label={t("workbench.vxi11Address")} value={inst.address_vxi11} />}
      {open && inst.address_real && <CopyRow label={t("workbench.realAddress")} value={inst.address_real} />}
    </section>
  );
}

function CopyRow({ label, value }: { label: string; value: string }) {
  const { t } = useTranslation();
  const [done, setDone] = useState(false);
  return (
    <div className="group mb-1.5">
      <div className="text-muted-foreground text-[11px]">{label}</div>
      <div className="flex items-center gap-1">
        <code className="num min-w-0 flex-1 truncate text-xs" title={value}>
          {value}
        </code>
        <Button
          variant="ghost"
          size="icon"
          className="size-6 opacity-60 group-hover:opacity-100"
          aria-label={t("common.copy")}
          onClick={() => {
            void navigator.clipboard?.writeText(value).then(() => {
              setDone(true);
              window.setTimeout(() => setDone(false), 1200);
            });
          }}
        >
          {done ? <Check className="text-ok size-3.5" /> : <Copy className="size-3.5" />}
        </Button>
      </div>
    </div>
  );
}

type SettingsProps = {
  sid: string;
  inst: InstrumentInfo;
  readonly: boolean;
  stateVars: ReturnType<typeof stateMap>;
};

/**
 * The instrument's settings (#1313): the panel holds what a front panel puts first (the summary, a
 * CH selector and the channel's summary, #728); any other setting is found by name, or under
 * 「全部设置」, which mounts its groups only when opened. Listing all of them made a scope's panel
 * thousands of rows long, and every mounted control made the next dropdown slower to open.
 */
function InstrumentSettings({
  sid,
  inst,
  readonly,
  stateVars,
  globals,
  channels,
  hidden,
}: SettingsProps & { globals: StateVarInfo[]; channels: number[]; /** Shown on the screen beside: left out here (#1229). */ hidden: Set<string> }) {
  const { t } = useTranslation();
  const [picked, setPicked] = useState(channels[0]);
  const [query, setQuery] = useState("");
  // Another instrument starts without a search; the picked channel stays (#733).
  const [searched, setSearched] = useState(inst.name);
  if (searched !== inst.name) {
    setSearched(inst.name);
    setQuery("");
  }
  const n = channels.includes(picked) ? picked : channels[0];
  const reveal = useWorkbench((s) => s.reveal);
  // A reveal request picks the setting's channel and leaves a search, so the field is on the page (adjusted while rendering).
  const [seen, setSeen] = useState(reveal?.n);
  if (reveal?.n !== seen) {
    setSeen(reveal?.n);
    if (reveal?.instrument === inst.name) {
      setQuery("");
      const ch = /^ch(\d+)\./.exec(reveal.path);
      if (ch && channels.includes(Number(ch[1]))) setPicked(Number(ch[1]));
    }
  }
  const offScreen = (v: StateVarInfo) => !hidden.has(v.path);
  // The definition's declared panel when it has groups (#1315, D74); else its summary (#1313).
  const declared = inst.display.panel;
  const fromPanel = (sections: PanelSection[], vars: StateVarInfo[]) => {
    const shown = new Set(sections.flatMap((x) => x.vars));
    return { sections, panel: [...shown], rest: vars.filter((v) => !shown.has(v)) };
  };
  const fromSummary = (vars: StateVarInfo[], summary: string[]) => {
    const { panel, rest } = panelSettings(vars, summary);
    return { sections: panel.length ? [{ label: "", vars: panel }] : [], panel, rest };
  };
  const global = declared?.groups?.length
    ? fromPanel(panelSections(declared.groups, stateVars, offScreen), globals)
    : fromSummary(globals, inst.display.summary);
  const channelOwn = n === undefined ? [] : channelVars(inst, n).filter(offScreen);
  const channel =
    n === undefined
      ? { sections: [], panel: [], rest: [] }
      : declared?.channel_groups?.length
        ? fromPanel(panelSections(declared.channel_groups, stateVars, offScreen, n), channelOwn)
        : fromSummary(channelOwn, inst.display.channel_summary);
  const restCount = global.rest.length + channel.rest.length;
  const field = (v: StateVarInfo) => (
    <StateField key={v.path} sid={sid} instrument={inst.name} v={v} disabled={readonly} inactive={inactiveUnderAuto(stateVars, v.path)} />
  );
  const revealHere = reveal?.instrument === inst.name ? reveal : null;
  return (
    <>
      <Section title={t("workbench.settings")}>
        <div className="space-y-3">
          <Input
            type="search"
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            onKeyDown={(e) => e.key === "Escape" && setQuery("")}
            placeholder={t("workbench.findSetting")}
            aria-label={t("workbench.findSetting")}
            className="h-8 text-xs"
            data-testid="setting-find"
          />
          {query.trim() ? (
            <FoundSettings inst={inst} vars={[...globals, ...channels.flatMap((c) => channelVars(inst, c).filter(offScreen))]} query={query} field={field} />
          ) : (
            <>
              <PanelSections sections={global.sections} field={field} />
              {n !== undefined && (
                <section data-testid="channel-settings" className="space-y-1.5">
                  {channels.length > 1 ? (
                    <div role="tablist" aria-label={t("workbench.channels")} className="mb-2 flex gap-1">
                      {channels.map((c) => (
                        <button
                          key={c}
                          type="button"
                          role="tab"
                          aria-selected={c === n}
                          onClick={() => setPicked(c)}
                          className={cn(
                            "num h-7 flex-1 rounded-md border text-xs font-semibold transition-colors",
                            c === n ? "bg-muted" : "text-muted-foreground hover:bg-muted/50",
                          )}
                          style={{ color: channelColor(c), borderColor: c === n ? channelColor(c) : undefined }}
                        >
                          {t("workbench.channelN", { n: c })}
                        </button>
                      ))}
                    </div>
                  ) : (
                    <div className="num mb-1.5 text-[11px] font-semibold" style={{ color: channelColor(n) }}>
                      {t("workbench.channelN", { n })}
                    </div>
                  )}
                  <PanelSections sections={channel.sections} field={field} />
                </section>
              )}
            </>
          )}
        </div>
      </Section>
      {restCount > 0 && (
        <Section
          key={inst.name}
          group="all"
          title={
            <span className="flex items-center gap-1.5">
              {t("workbench.allSettings")}
              <span className="num text-muted-foreground/70 font-normal">{restCount}</span>
            </span>
          }
          collapsible
          defaultOpen={restCount <= ALL_OPEN_MAX}
          reveal={revealHere && [...global.rest, ...channel.rest].some((v) => v.path === revealHere.path) ? revealHere.n : null}
        >
          <div className="space-y-4">
            {global.rest.length > 0 && <SettingsBlock sid={sid} inst={inst} vars={global.rest} summary={[]} readonly={readonly} stateVars={stateVars} />}
            {channel.rest.length > 0 && (
              <div className="space-y-1.5" data-testid="channel-rest">
                <div className="num text-[11px] font-semibold" style={{ color: channelColor(n) }}>
                  {t("workbench.channelN", { n })}
                </div>
                <SettingsBlock key={n} sid={sid} inst={inst} vars={channel.rest} summary={[]} readonly={readonly} stateVars={stateVars} />
              </div>
            )}
          </div>
        </Section>
      )}
    </>
  );
}

/** The panel's groups, each under its title (a summary has none), then its fields. */
function PanelSections({ sections, field }: { sections: PanelSection[]; field: (v: StateVarInfo) => ReactNode }) {
  return (
    <div className="space-y-3" data-testid="panel-sections">
      {sections.map((x, i) => (
        <div key={x.label || i} className="space-y-1.5" data-panel-group={x.label || undefined}>
          {x.label && <div className="text-muted-foreground text-[11px] font-medium">{pickLabel(x.label, x.label_en)}</div>}
          {x.vars.map(field)}
        </div>
      ))}
    </div>
  );
}

/** A search's matches (#1313): the whole-instrument ones first, then each channel's under its CH name. */
function FoundSettings({ inst, vars, query, field }: { inst: InstrumentInfo; vars: StateVarInfo[]; query: string; field: (v: StateVarInfo) => ReactNode }) {
  const { t } = useTranslation();
  const { hits, total } = findSettings(vars, query);
  if (total === 0) return <p className="text-muted-foreground text-xs">{t("workbench.findNone")}</p>;
  const byChannel = new Map<number | null, StateVarInfo[]>();
  for (const v of hits) byChannel.set(v.channel ?? null, [...(byChannel.get(v.channel ?? null) ?? []), v]);
  return (
    <div className="space-y-2" data-testid="setting-found">
      {[...byChannel].map(([ch, list]) => (
        <div key={ch ?? "global"} className="space-y-1.5">
          {ch != null && inst.channels > 1 && (
            <div className="num text-[11px] font-semibold" style={{ color: channelColor(ch) }}>
              {t("workbench.channelN", { n: ch })}
            </div>
          )}
          {list.map(field)}
        </div>
      ))}
      {total > hits.length && <p className="text-muted-foreground text-xs">{t("workbench.findMore", { count: total - hits.length })}</p>}
    </div>
  );
}

/** The main tier open, the groups folded: what a front panel shows first, then the rest. */
function SettingsBlock({
  sid,
  inst,
  vars,
  summary,
  readonly,
  stateVars,
}: SettingsProps & { vars: ReturnType<typeof globalVars>; summary: string[] }) {
  const { main, groups } = splitSettings(vars, summary);
  const reveal = useWorkbench((s) => s.reveal);
  const field = (v: (typeof vars)[number]) => (
    <StateField key={v.path} sid={sid} instrument={inst.name} v={v} disabled={readonly} inactive={inactiveUnderAuto(stateVars, v.path)} />
  );
  return (
    <div className="space-y-3">
      {main.length > 0 && <div className="space-y-1.5">{main.map(field)}</div>}
      {groups.map((g) => (
        <Section
          key={g.id}
          group={g.id}
          title={<GroupTitle group={g} />}
          collapsible
          defaultOpen={groupOpen(g) || (main.length === 0 && g === groups[0])}
          reveal={reveal?.instrument === inst.name && g.vars.some((v) => v.path === reveal.path) ? reveal.n : null}
        >
          <GroupBody sid={sid} inst={inst} group={g} readonly={readonly} stateVars={stateVars} field={field} />
        </Section>
      ))}
    </div>
  );
}

function GroupTitle({ group }: { group: SettingGroup }) {
  const name = groupName(group.id, !!group.numbered);
  return (
    <span className="flex items-center gap-1.5">
      {name ?? <span className="num normal-case">{group.id}</span>}
      <span className="num text-muted-foreground/70 font-normal">{group.vars.length}</span>
    </span>
  );
}

/** A family of numbered settings (#1257): one instance at a time, picked by its number. */
function FamilyBlock({
  inst,
  family,
  field,
  title = family.name,
}: {
  inst: { name: string };
  family: InstanceFamily;
  field: (v: SettingGroup["vars"][number]) => ReactNode;
  /** What the instances are called: the numbered segment's letters, or a joined group's name (#1299). */
  title?: string;
}) {
  const { t } = useTranslation();
  const reveal = useWorkbench((s) => s.reveal);
  const asked =
    reveal?.instrument === inst.name ? family.instances.find((n) => family.byInstance.get(n)!.some((v) => v.path === reveal.path)) : undefined;
  const [chosen, setChosen] = useState(family.instances[0]);
  const [lastAsked, setLastAsked] = useState<string | undefined>(undefined);
  if (asked && asked !== lastAsked) {
    setLastAsked(asked);
    setChosen(asked);
  }
  const shown = family.byInstance.get(chosen) ?? [];
  return (
    <div className="space-y-1.5 rounded-md border p-2" data-testid="setting-family" data-family={family.key}>
      <div className="flex items-center gap-2 text-xs">
        <span className="num text-muted-foreground">{title}</span>
        <Select value={chosen} onValueChange={setChosen}>
          <SelectTrigger size="sm" className="num h-7 w-24 text-xs" aria-label={t("inspector.instance", { name: title })}>
            <SelectValue />
          </SelectTrigger>
          <SelectContent>
            {family.instances.map((n) => (
              <SelectItem key={n} value={n} className="num text-xs">
                {n}
              </SelectItem>
            ))}
          </SelectContent>
        </Select>
        <span className="text-muted-foreground/70">{t("inspector.instanceCount", { count: family.instances.length })}</span>
      </div>
      {shown.map(field)}
    </div>
  );
}

/** A group's settings; measurement slots (P1…P8) are one row of controls each. */
function GroupBody({
  sid,
  inst,
  group,
  readonly,
  stateVars,
  field,
}: SettingsProps & { group: SettingGroup; field: (v: SettingGroup["vars"][number]) => ReactNode }) {
  // Joined numbered groups (function1 … function16, #1299): one instance at a time.
  if (group.numbered) return <FamilyBlock inst={inst} family={group.numbered} field={field} title={groupName(group.id, true) ?? group.id} />;
  const { slots, rest: unslotted } = slotRows(group.vars);
  const { families, rest } = instanceFamilies(unslotted);
  return (
    <div className="space-y-1.5">
      {rest.map(field)}
      {families.map((f) => (
        <FamilyBlock key={f.key} inst={inst} family={f} field={field} />
      ))}
      {slots.map((row) => (
        <div key={row.label} className="grid grid-cols-[2rem_1fr] items-center gap-x-2">
          <span className="num text-muted-foreground text-xs">{row.label}</span>
          <div className="flex min-w-0 gap-1.5">
            {row.vars.map((v) => (
              <div key={v.path} className="min-w-0 flex-1">
                <StateField
                  sid={sid}
                  instrument={inst.name}
                  v={v}
                  disabled={readonly}
                  inactive={inactiveUnderAuto(stateVars, v.path)}
                  bare
                />
              </div>
            ))}
          </div>
        </div>
      ))}
    </div>
  );
}

import { AlertTriangle, Blocks, Check, ChevronDown, Copy, MonitorPlay, MousePointerClick, Send } from "lucide-react";
import { type ReactNode, useState } from "react";
import { useTranslation } from "react-i18next";
import type { InstrumentInfo, SessionInfo } from "@/api/client";
import { useScpi } from "@/api/hooks";
import { EmptyState } from "@/components/app/StateViews";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { ScrollArea } from "@/components/ui/scroll-area";
import { channelColor } from "@/lib/channels";
import { displayUnit, formatEng } from "@/lib/format";
import { cn } from "@/lib/utils";
import { accessoryLabel, channelVars, globalVars, instrumentTitle, numeric } from "./model";
import { FaultPanel } from "./faults/FaultPanel";
import { ProbePanel } from "./ProbePanel";
import { RemoteBanner } from "./RemoteBanner";
import { StateField } from "./StateField";
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

function InstrumentInspector({ sid, inst, readonly }: { sid: string; inst: InstrumentInfo; readonly: boolean }) {
  const { t } = useTranslation();
  const openScreen = useWorkbench((s) => s.openScreen);
  const openInternals = useWorkbench((s) => s.openInternals);
  const globals = globalVars(inst);
  const channels = Array.from({ length: inst.channels }, (_, i) => i + 1);
  return (
    <div className="space-y-4 p-3">
      <RemoteBanner sid={sid} inst={inst} className="-mx-3 -mt-3" />
      <Header title={instrumentTitle(inst)} subtitle={`${inst.manufacturer} ${inst.model}`} />
      <div className="grid grid-cols-2 gap-1.5">
        <Button variant="outline" size="sm" onClick={() => openScreen(inst.name)}>
          <MonitorPlay className="size-4" /> {t("workbench.openScreen")}
        </Button>
        <Button variant="outline" size="sm" onClick={() => openInternals(inst.name)}>
          <Blocks className="size-4" /> {t("internals.open")}
        </Button>
      </div>
      <Section title={t("workbench.identitySection")}>
        <CopyRow label="*IDN?" value={inst.idn} />
        <CopyRow label={t("workbench.virtualAddress")} value={inst.address_virtual} />
        {inst.address_hislip && <CopyRow label={t("workbench.hislipAddress")} value={inst.address_hislip} />}
        {inst.address_real && <CopyRow label={t("workbench.realAddress")} value={inst.address_real} />}
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
      {globals.length > 0 && (
        <Section title={t("workbench.settings")}>
          <div className="space-y-1.5">
            {globals.map((v) => (
              <StateField key={v.path} sid={sid} instrument={inst.name} v={v} disabled={readonly} />
            ))}
          </div>
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
          <div className="space-y-1.5">
            {channelVars(inst, n).map((v) => (
              <StateField key={v.path} sid={sid} instrument={inst.name} v={v} disabled={readonly} />
            ))}
          </div>
        </Section>
      ))}
      <Section title={t("workbench.sendCommand")}>
        <ScpiConsole sid={sid} instrument={inst.name} disabled={readonly} />
      </Section>
      <Section title={t("fault.title")} collapsible defaultOpen={false}>
        <FaultPanel sid={sid} instrument={inst.name} disabled={readonly} />
      </Section>
    </div>
  );
}

function ScpiConsole({ sid, instrument, disabled }: { sid: string; instrument: string; disabled: boolean }) {
  const { t } = useTranslation();
  const scpi = useScpi(sid, instrument);
  const [cmd, setCmd] = useState("");
  const [history, setHistory] = useState<string[]>([]);
  const [cursor, setCursor] = useState(-1);
  const send = () => {
    const c = cmd.trim();
    if (!c) return;
    scpi.mutate(c);
    setHistory((h) => [c, ...h.filter((x) => x !== c)].slice(0, 50));
    setCursor(-1);
  };
  return (
    <div className="space-y-2">
      <div className="flex gap-1.5">
        <Input
          value={cmd}
          onChange={(e) => setCmd(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === "Enter") send();
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
        <Button size="sm" className="h-8" onClick={send} disabled={disabled || scpi.isPending} aria-label={t("workbench.send")}>
          <Send className="size-3.5" />
        </Button>
      </div>
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

export function Header({ title, subtitle, mono }: { title: string; subtitle: string; mono?: boolean }) {
  return (
    <div>
      <div className={cn("text-sm font-semibold break-all", mono && "num")}>{title}</div>
      <div className="text-muted-foreground num text-xs">{subtitle}</div>
    </div>
  );
}

export function Section({
  title,
  children,
  collapsible,
  defaultOpen = true,
}: {
  title: ReactNode;
  children: ReactNode;
  collapsible?: boolean;
  defaultOpen?: boolean;
}) {
  const [open, setOpen] = useState(defaultOpen);
  return (
    <section>
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

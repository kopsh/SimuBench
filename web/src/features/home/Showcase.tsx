import { Bot, Monitor, SquareTerminal } from "lucide-react";
import { type ReactNode, useCallback, useEffect, useState } from "react";
import { useTranslation } from "react-i18next";
import { prefersReducedMotion } from "@/lib/motion";
import { cn } from "@/lib/utils";
import tuiOverview from "./assets/tui-overview.svg";
import tuiSa from "./assets/tui-sa.svg";
import tuiScope from "./assets/tui-scope.svg";
import webCanvas from "./assets/web-canvas.webp";
import webSa from "./assets/web-sa.webp";
import webScope from "./assets/web-scope.webp";
import preview from "./hello-preview.json";

type ViewId = "web" | "tui" | "code";
interface Slide {
  src: string;
  caption: string;
  keys?: string[];
}

const SLIDE_MS = 3000;
const DURATION: Record<ViewId, number> = { web: 9000, tui: 9500, code: 11000 };

/**
 * One bench, every interface: the web workbench, the terminal console and a script, each showing
 * the same Hello World bench. The screenshots are real (web: the app, TUI: `docs/tui/hello.steps.yaml`),
 * the script's replies are the engine's. Tabs advance on their own and pause while pointed at;
 * under prefers-reduced-motion nothing advances or types.
 */
export function Showcase() {
  const { t } = useTranslation();
  const [still] = useState(prefersReducedMotion);
  const [view, setView] = useState<ViewId>("web");
  const [paused, setPaused] = useState(false);
  const [round, setRound] = useState(0); // restarts the progress bar and the view's story

  const tabs: { id: ViewId; icon: ReactNode; command: string }[] = [
    { id: "web", icon: <Monitor className="size-4" />, command: "simubench serve --open" },
    { id: "tui", icon: <SquareTerminal className="size-4" />, command: "simubench tui hello.bench.yaml" },
    { id: "code", icon: <Bot className="size-4" />, command: "simubench mcp --workspace ." },
  ];
  const go = useCallback((id: ViewId) => {
    setView(id);
    setRound((r) => r + 1);
  }, []);

  useEffect(() => {
    if (still || paused) return;
    const order: ViewId[] = ["web", "tui", "code"];
    const timer = setTimeout(() => go(order[(order.indexOf(view) + 1) % order.length]), DURATION[view]);
    return () => clearTimeout(timer);
  }, [view, paused, round, still, go]);

  return (
    <div className="grid gap-6 lg:grid-cols-[300px_minmax(0,1fr)]" data-testid="showcase">
      <div className="flex gap-2 overflow-x-auto lg:flex-col" role="tablist" aria-label={t("home.show.title")}>
        {tabs.map((tab) => (
          <button
            key={tab.id}
            type="button"
            role="tab"
            aria-selected={view === tab.id}
            onClick={() => go(tab.id)}
            className={cn(
              "relative min-w-56 overflow-hidden rounded-xl border p-3.5 text-left transition-colors",
              view === tab.id ? "border-primary/60 bg-primary/10" : "bg-card/70 hover:border-foreground/20",
            )}
          >
            <span className="flex items-center gap-2 text-sm font-semibold">
              {tab.icon}
              {t(`home.show.${tab.id}.title`)}
            </span>
            <span className="text-muted-foreground mt-1 block text-[12.5px] leading-snug">{t(`home.show.${tab.id}.body`)}</span>
            <code className="num text-muted-foreground/80 mt-2 block text-[11px]">{tab.command}</code>
            {view === tab.id && !still && (
              <i
                key={round}
                className="bg-primary absolute bottom-0 left-0 h-0.5"
                style={{ animation: `home-progress ${DURATION[tab.id]}ms linear forwards`, animationPlayState: paused ? "paused" : "running" }}
              />
            )}
          </button>
        ))}
      </div>
      <div
        className="bg-plot relative aspect-[16/10] overflow-hidden rounded-xl border shadow-2xl shadow-black/30"
        role="tabpanel"
        onPointerEnter={() => setPaused(true)}
        onPointerLeave={() => setPaused(false)}
      >
        {view === "web" && (
          <Slides
            key={`web-${round}`}
            still={still}
            fit="cover"
            slides={[
              { src: webCanvas, caption: t("home.show.web.canvas") },
              { src: webScope, caption: t("home.show.web.scope") },
              { src: webSa, caption: t("home.show.web.sa") },
            ]}
          />
        )}
        {view === "tui" && (
          <Slides
            key={`tui-${round}`}
            still={still}
            fit="contain"
            slides={[
              { src: tuiOverview, caption: t("home.show.tui.overview") },
              { src: tuiScope, caption: t("home.show.tui.scope"), keys: ["2", "⏎"] },
              { src: tuiSa, caption: t("home.show.tui.sa"), keys: ["Esc", "3", "⏎"] },
            ]}
          />
        )}
        {view === "code" && <Session key={`code-${round}`} still={still} />}
      </div>
    </div>
  );
}

function Slides({ slides, still, fit }: { slides: Slide[]; still: boolean; fit: "cover" | "contain" }) {
  const [shown, setShown] = useState(0);
  const [keys, setKeys] = useState<string[]>([]);
  useEffect(() => {
    if (still) return;
    const timers: ReturnType<typeof setTimeout>[] = [];
    slides.forEach((s, i) => {
      if (!i) return;
      timers.push(
        setTimeout(() => {
          setKeys(s.keys ?? []);
          timers.push(setTimeout(() => setShown(i), s.keys?.length ? 650 : 0));
        }, i * SLIDE_MS),
      );
    });
    return () => timers.forEach(clearTimeout);
  }, [slides.length, still]); // eslint-disable-line react-hooks/exhaustive-deps
  return (
    <>
      {slides.map((s, i) => (
        <img
          key={s.src}
          src={s.src}
          alt={s.caption}
          className={cn(
            "absolute inset-0 h-full w-full transition-opacity duration-500",
            fit === "cover" ? "object-cover object-top-left" : "object-contain p-[4%]",
            i === shown ? "opacity-100" : "opacity-0",
          )}
        />
      ))}
      <div className="bg-background/85 absolute bottom-3.5 left-3.5 rounded-lg border px-2.5 py-1.5 text-xs backdrop-blur" aria-live="polite">
        {slides[shown].caption}
      </div>
      {keys.length > 0 && (
        <div className="absolute right-4 bottom-3.5 flex gap-1.5" aria-hidden>
          {keys.map((k, i) => (
            <kbd
              key={`${shown}-${k}`}
              className="bg-card num grid h-7 min-w-7 place-items-center rounded-md border border-b-[3px] px-2 text-xs"
              style={{ animation: `home-keypress 0.45s ease-out ${i * 0.22}s both` }}
            >
              {k}
            </kbd>
          ))}
        </div>
      )}
    </>
  );
}

/** A PyVISA session against the Hello World bench: only the resource manager line changes, the
 * replies are the engine's (hello-preview.json). Typed out line by line. */
function Session({ still }: { still: boolean }) {
  const { t } = useTranslation();
  const lines: { kind: "in" | "hl" | "out" | "note"; text: string }[] = [
    { kind: "note", text: `# ${t("home.show.code.note1")}` },
    { kind: "in", text: ">>> import pyvisa" },
    { kind: "hl", text: '>>> rm = pyvisa.ResourceManager("hello.bench.yaml@simubench")' },
    { kind: "in", text: '>>> sa = rm.open_resource("sa")' },
    { kind: "in", text: '>>> sa.query("*IDN?")' },
    { kind: "out", text: `'${preview.idn.sa}'` },
    { kind: "in", text: '>>> sa.write(":CALC:MARK:MAX")' },
    { kind: "in", text: '>>> sa.query(":CALC:MARK:X?"), sa.query(":CALC:MARK:Y?")' },
    { kind: "out", text: `('${preview.marker.x}', '${preview.marker.y}')` },
    { kind: "note", text: `# ${t("home.show.code.note2")}` },
  ];
  const [chars, setChars] = useState(still ? Infinity : 0);
  useEffect(() => {
    if (still) return;
    const id = setInterval(() => setChars((c) => c + 3), 30);
    return () => clearInterval(id);
  }, [still]);
  let budget = chars;
  return (
    <div className="num absolute inset-0 flex flex-col justify-center gap-0.5 overflow-hidden p-6 text-[13px] leading-relaxed text-[var(--plot-text)] sm:p-10">
      {lines.map((l, i) => {
        // Commands type out; replies and notes appear whole once the command before them is done.
        const typed = l.kind === "in" || l.kind === "hl";
        if (budget <= 0) return null;
        const text = typed ? l.text.slice(0, budget) : l.text;
        budget -= typed ? l.text.length : 12;
        return (
          <div
            key={i}
            className={cn(
              "truncate",
              l.kind === "note" && "opacity-60",
              l.kind === "out" && "text-[var(--plot-strong)]",
              l.kind === "hl" && "bg-ok/15 -mx-3 px-3 shadow-[inset_2px_0_var(--status-ok)]",
            )}
          >
            {text}
          </div>
        );
      })}
    </div>
  );
}

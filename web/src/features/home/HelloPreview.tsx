import { useEffect, useRef, useState } from "react";
import { useTranslation } from "react-i18next";
import { formatEng } from "@/lib/format";
import { prefersReducedMotion, sweepProgress } from "@/lib/motion";
import preview from "./hello-preview.json";

/**
 * The Hello World bench, playing on the home page before it is opened: the scope's acquisitions
 * and the analyzer's sweeps are the engine's own (scripts/capture_hello_preview.py), drawn the way
 * the workbench screens draw them — persistence on the scope, a sweep line on the analyzer. Under
 * prefers-reduced-motion it is one still frame. Drawing pauses while it is off screen.
 */
export function HelloPreview() {
  const { t } = useTranslation();
  const root = useRef<HTMLDivElement>(null);
  const time = useRef<SVGSVGElement>(null);
  const freq = useRef<SVGSVGElement>(null);
  const [acq, setAcq] = useState(0);
  const [sweep, setSweep] = useState(0);

  useEffect(() => {
    const t1 = time.current!;
    const f1 = freq.current!;
    const still = prefersReducedMotion();
    let visible = true;
    const io = new IntersectionObserver(([e]) => (visible = e.isIntersecting));
    io.observe(root.current!);
    const scope = makeScope(t1);
    const sa = makeSpectrum(f1);
    scope.show(0, 1);
    sa.show(0, 1);
    if (still) return () => io.disconnect();
    let k = 0;
    let j = 0;
    let tScope = -0.6;
    let tSa = -0.6;
    let last = performance.now();
    let raf = 0;
    const frame = (now: number) => {
      const dt = Math.min(0.05, (now - last) / 1000);
      last = now;
      if (visible && !document.hidden) {
        tScope += dt;
        tSa += dt;
        scope.show(k, sweepProgress(tScope * 1000, SCOPE_SWEEP_MS));
        sa.show(j, sweepProgress(tSa * 1000, SA_SWEEP_MS));
        if (tScope * 1000 > SCOPE_SWEEP_MS + 300) {
          scope.retire(k);
          k = (k + 1) % preview.scope.length;
          setAcq(k);
          tScope = 0;
        }
        if (tSa * 1000 > SA_SWEEP_MS + 150) {
          j = (j + 1) % preview.sa.length;
          setSweep(j);
          tSa = 0;
        }
      }
      raf = requestAnimationFrame(frame);
    };
    raf = requestAnimationFrame(frame);
    return () => {
      cancelAnimationFrame(raf);
      io.disconnect();
    };
  }, []);

  const cap = preview.scope[acq];
  const vpp = Math.max(...cap) - Math.min(...cap);
  const rms = Math.sqrt(cap.reduce((s, v) => s + v * v, 0) / cap.length);
  const tr = preview.sa[sweep];
  const peak = (mhz: number) => {
    const i = Math.round((mhz / 8) * (tr.length - 1));
    return Math.max(...tr.slice(i - 3, i + 4));
  };

  return (
    <div ref={root} className="bg-card/85 relative overflow-hidden rounded-2xl border shadow-2xl shadow-black/30" aria-label={t("home.preview.label")}>
      <div className="text-muted-foreground flex items-center gap-2 border-b px-4 py-2 text-[11.5px]">
        <span className="text-foreground font-semibold">hello.bench.yaml</span>
        {t("home.preview.title")}
        <span className="text-muted-foreground/80 ml-auto hidden text-[10.5px] sm:inline">{t("home.preview.real")}</span>
        <span className="text-ok num inline-flex items-center gap-1.5 text-[10.5px]">
          <span className="bg-ok size-1.5 rounded-full" aria-hidden />
          {t("home.preview.acquisition", { n: acq + 1 })}
        </span>
      </div>
      <div className="grid grid-cols-[auto_1fr_auto] items-center gap-x-3 px-4 pt-3.5 pb-1.5">
        <Node className="row-span-2" title="SDG2042X" sub={`Siglent · ${t("acceptance.class.function_generator")}`} />
        <Wire color="var(--ch1)" />
        <Node tag="CH1" tagColor="var(--ch1)" title="SDS2104X Plus" sub={`Siglent · ${t("acceptance.class.oscilloscope")}`} />
        <Wire color="var(--ch2)" />
        <Node tag="CH2" tagColor="var(--ch2)" title="N9320B" sub={`Keysight · ${t("acceptance.class.spectrum_analyzer")}`} />
      </div>
      <div className="grid grid-cols-1 gap-2.5 px-3.5 pt-2 pb-3.5 sm:grid-cols-2">
        <Pane title={t("home.preview.time")} scale="500 ns/div · 250 mV/div">
          <svg ref={time} viewBox="0 0 320 160" preserveAspectRatio="none" className="block h-40 w-full" aria-hidden />
          <Readouts items={[["Vpp", formatEng(vpp, "V", 3)], ["RMS", formatEng(rms, "V", 4)]]} />
        </Pane>
        <Pane title={t("home.preview.freq")} scale="CF 4 MHz · SPAN 8 MHz">
          <svg ref={freq} viewBox="0 0 320 160" preserveAspectRatio="none" className="block h-40 w-full" aria-hidden />
          <Readouts items={[["M1 1 MHz", `${peak(1).toFixed(2)} dBm`], ["M2 3 MHz", `${peak(3).toFixed(2)} dBm`]]} />
        </Pane>
      </div>
    </div>
  );
}

const SCOPE_SWEEP_MS = 1100;
const SA_SWEEP_MS = 1600;
const W = 320;
const H = 160;
const NS = "http://www.w3.org/2000/svg";

function el<K extends keyof SVGElementTagNameMap>(tag: K, attrs: Record<string, string | number>, parent: Element) {
  const e = document.createElementNS(NS, tag);
  for (const [k, v] of Object.entries(attrs)) e.setAttribute(k, String(v));
  parent.appendChild(e);
  return e;
}

function graticule(svg: SVGSVGElement, nx: number, ny: number) {
  svg.replaceChildren();
  for (let i = 1; i < nx; i++)
    el("line", { x1: (W / nx) * i, y1: 0, x2: (W / nx) * i, y2: H, stroke: "var(--plot-grid)", "stroke-width": i === nx / 2 ? 1 : 0.6, "stroke-dasharray": i === nx / 2 ? "" : "2 3" }, svg);
  for (let j = 1; j < ny; j++)
    el("line", { x1: 0, y1: (H / ny) * j, x2: W, y2: (H / ny) * j, stroke: "var(--plot-grid)", "stroke-width": j === ny / 2 ? 1 : 0.6, "stroke-dasharray": j === ny / 2 ? "" : "2 3" }, svg);
}

/** Scope: 10 × 8 divisions, 250 mV/div; a beam draws each acquisition, the last two stay faint. */
function makeScope(svg: SVGSVGElement) {
  graticule(svg, 10, 8);
  const y = (v: number) => H / 2 - (v / 0.25) * (H / 8);
  const path = (c: number[]) => c.map((v, i) => `${i ? "L" : "M"}${((i / (c.length - 1)) * W).toFixed(1)} ${y(v).toFixed(1)}`).join("");
  const id = `beam-${Math.random().toString(36).slice(2)}`;
  const clip = el("rect", { x: 0, y: 0, width: 0, height: H }, el("clipPath", { id }, el("defs", {}, svg)));
  const ghosts = [0.25, 0.1].map((o) => el("path", { fill: "none", stroke: "var(--ch1)", "stroke-width": 1.2, opacity: o }, svg));
  const live = el("path", { fill: "none", stroke: "var(--ch1)", "stroke-width": 1.6, "clip-path": `url(#${id})` }, svg);
  const head = el("circle", { r: 2.5, fill: "var(--ch1)" }, svg);
  const history: string[] = [];
  let shown = -1;
  return {
    show(k: number, progress: number) {
      if (shown !== k) {
        live.setAttribute("d", path(preview.scope[k]));
        shown = k;
      }
      clip.setAttribute("width", String(progress * W));
      const c = preview.scope[k];
      const i = Math.min(c.length - 1, Math.round(progress * (c.length - 1)));
      head.setAttribute("cx", String(progress * W));
      head.setAttribute("cy", String(y(c[i])));
      head.setAttribute("opacity", progress > 0 && progress < 1 ? "1" : "0");
    },
    retire(k: number) {
      history.unshift(path(preview.scope[k]));
      history.length = Math.min(history.length, ghosts.length);
      ghosts.forEach((g, n) => g.setAttribute("d", history[n] ?? ""));
    },
  };
}

/** Analyzer: 10 × 10 divisions, reference +10 dBm at the top, 10 dB/div; a sweep line repaints. */
function makeSpectrum(svg: SVGSVGElement) {
  graticule(svg, 10, 10);
  const y = (db: number) => Math.min(H, ((10 - db) / 100) * H);
  const path = (tr: number[]) => tr.map((d, i) => `${i ? "L" : "M"}${((i / (tr.length - 1)) * W).toFixed(1)} ${y(d).toFixed(1)}`).join("");
  const defs = el("defs", {}, svg);
  const idN = `sn-${Math.random().toString(36).slice(2)}`;
  const idO = `so-${Math.random().toString(36).slice(2)}`;
  const clipN = el("rect", { x: 0, y: 0, width: 0, height: H }, el("clipPath", { id: idN }, defs));
  const clipO = el("rect", { x: 0, y: 0, width: W, height: H }, el("clipPath", { id: idO }, defs));
  const old = el("path", { fill: "none", stroke: "var(--ch1)", "stroke-width": 1.1, opacity: 0.5, "clip-path": `url(#${idO})` }, svg);
  const now = el("path", { fill: "none", stroke: "var(--ch1)", "stroke-width": 1.3, "clip-path": `url(#${idN})` }, svg);
  const line = el("line", { x1: 0, y1: 0, x2: 0, y2: H, stroke: "var(--plot-text)", "stroke-width": 0.8, opacity: 0.5 }, svg);
  [1, 3].forEach((mhz, n) => {
    const tr = preview.sa[0];
    const x = (mhz / 8) * W;
    const py = Math.max(12, y(tr[Math.round((mhz / 8) * (tr.length - 1))]));
    el("path", { d: `M${x - 4} ${py - 9} L${x + 4} ${py - 9} L${x} ${py - 3} Z`, fill: "var(--plot-text)" }, svg);
    el("text", { x: x + 6, y: py - 4, fill: "var(--plot-text)", "font-size": 8.5, "font-family": "var(--font-mono)" }, svg).textContent = `M${n + 1}`;
  });
  let shown = -1;
  return {
    show(k: number, progress: number) {
      if (shown !== k) {
        now.setAttribute("d", path(preview.sa[k]));
        old.setAttribute("d", path(preview.sa[(k + preview.sa.length - 1) % preview.sa.length]));
        shown = k;
      }
      const x = progress * W;
      clipN.setAttribute("width", String(x));
      clipO.setAttribute("x", String(x));
      clipO.setAttribute("width", String(W - x));
      line.setAttribute("x1", String(x));
      line.setAttribute("x2", String(x));
      line.setAttribute("opacity", progress > 0 && progress < 1 ? "0.5" : "0");
    },
  };
}

function Node({ title, sub, tag, tagColor, className }: { title: string; sub: string; tag?: string; tagColor?: string; className?: string }) {
  return (
    <div className={`bg-background/60 rounded-lg border px-2.5 py-1.5 text-[11.5px] leading-snug whitespace-nowrap ${className ?? ""}`}>
      {tag && (
        <span className="num mr-1.5 rounded px-1 text-[9.5px] font-semibold text-[var(--plot-bg)]" style={{ background: tagColor }}>
          {tag}
        </span>
      )}
      {title}
      <span className="text-muted-foreground block text-[10.5px]">{sub}</span>
    </div>
  );
}

function Wire({ color }: { color: string }) {
  return (
    <svg className="h-3.5 w-full overflow-visible" aria-hidden>
      <line x1="0" y1="7" x2="100%" y2="7" stroke={color} strokeWidth="2" className="wire-live" />
    </svg>
  );
}

function Pane({ title, scale, children }: { title: string; scale: string; children: React.ReactNode }) {
  return (
    <div className="bg-plot overflow-hidden rounded-lg border border-white/5">
      <div className="flex justify-between border-b border-white/5 px-2.5 py-1 text-[10.5px] text-[var(--plot-text)]">
        <span>{title}</span>
        <span className="num">{scale}</span>
      </div>
      {children}
    </div>
  );
}

function Readouts({ items }: { items: [string, string][] }) {
  return (
    <div className="num flex gap-3.5 border-t border-white/5 px-2.5 py-1 text-[10.5px] whitespace-nowrap text-[var(--plot-text)]">
      {items.map(([k, v]) => (
        <span key={k}>
          {k} <b className="font-medium text-[var(--plot-strong)]">{v}</b>
        </span>
      ))}
    </div>
  );
}

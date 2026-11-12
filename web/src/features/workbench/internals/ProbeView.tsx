import { Activity, AlertTriangle, Crosshair, Lock, Radio, WifiOff, X } from "lucide-react";
import { type KeyboardEvent, type PointerEvent, type ReactNode, useDeferredValue, useState } from "react";
import { useTranslation } from "react-i18next";
import { ApiError } from "@/api/client";
import { useEvents } from "@/api/events";
import { useInternalProbe } from "@/api/hooks";
import { commit, DraftInput, INVALID, type Parsed, SKIP } from "@/components/app/DraftInput";
import { ErrorState } from "@/components/app/StateViews";
import { Button } from "@/components/ui/button";
import { Skeleton } from "@/components/ui/skeleton";
import { formatEng } from "@/lib/format";
import { cn } from "@/lib/utils";
import {
  type Axis,
  type InternalProbe,
  type InternalTuning,
  type Tune,
  CENTER,
  CLOSE_UP,
  SPECTRUM_RANGE_DB,
  binAt,
  binOf,
  binX,
  codeLines,
  dbc,
  envelope,
  flowTag,
  formatFormat,
  formatRate,
  formatTune,
  freqAt,
  fullScaleText,
  inTuningRange,
  iqStairs,
  levelY,
  lineStems,
  lineWindow,
  lsbOf,
  nearestLine,
  noiseFloor,
  onRadioAxis,
  parseTune,
  spectrumAxis,
  spectrumBars,
  spectrumColumns,
  stairs,
  strongestPeaks,
  trace,
  tuneHz,
  tuneParam,
  withheldError,
  xOf,
} from "./probe";

/** What the probe panel has to show (signal-chain.md 1.3): every state is drawn, not just the signal. */
export type ProbeState =
  | { kind: "loading" }
  | { kind: "withheld" }
  | { kind: "error"; error: unknown }
  | { kind: "ready"; probe: InternalProbe; stale?: boolean; retuning?: boolean };

const W = 280;
const H = 88;
const PLOT = "bg-background h-[88px] w-full rounded border";

/** A plot's graticule: quarter divisions, like an instrument screen; quiet enough to sit behind the trace. */
function Graticule({ rows = 4, columns = 8 }: { rows?: number; columns?: number }) {
  const lines: string[] = [];
  for (let i = 1; i < rows; i++) lines.push(`M0,${((i * H) / rows).toFixed(1)}H${W}`);
  for (let i = 1; i < columns; i++) lines.push(`M${((i * W) / columns).toFixed(1)},0V${H}`);
  return <path d={lines.join("")} fill="none" stroke="var(--border)" strokeWidth="0.5" strokeDasharray="1 3" />;
}

/** Where 0 lies in a plot of `lo`..`hi` (none when 0 is outside it). */
function zeroY(lo: number, hi: number, pad = 4): number | null {
  if (!(lo < 0 && hi > 0)) return null;
  return H - pad - ((0 - lo) / (hi - lo)) * (H - 2 * pad);
}
const HEADING = "text-muted-foreground mb-1 text-[11px] font-medium tracking-wide uppercase";

/** A receiver's tuning as the panel is handed it: the context, the choice, and how to change it (#959). */
export type Tuned = { tuning: InternalTuning; tune: Tune; onTune: (tune: Tune) => void };

/** The internal probe of a session: the signal at one output port, re-read on every event version (D64). */
export function InternalProbePanel({
  sid,
  instrument,
  port,
  heading,
  tag,
  tuned,
  onClose,
}: {
  sid: string;
  instrument: string;
  port: string;
  heading: string;
  tag: string;
  tuned?: Tuned;
  onClose: () => void;
}) {
  const version = useDeferredValue(useEvents((s) => s.version));
  const status = useEvents((s) => s.status);
  const q = useInternalProbe(sid, instrument, port, version, tuned ? tuneParam(tuned.tune, tuned.tuning, port) : undefined);
  const state: ProbeState =
    q.error && withheldError(q.error)
      ? { kind: "withheld" }
      : q.data
        ? { kind: "ready", probe: q.data, stale: status !== "connected", retuning: q.isPlaceholderData }
        : q.error
          ? { kind: "error", error: q.error }
          : { kind: "loading" };
  return (
    <InternalProbeView
      port={port}
      heading={heading}
      tag={tag}
      state={state}
      tuned={tuned}
      onClose={onClose}
      onRetry={() => void q.refetch()}
    />
  );
}

/**
 * The probe panel itself, from data already loaded (stories render every state). ``tag`` is the
 * wire's label (analog, or digital with rate and format); a ready sample stream states its own.
 */
export function InternalProbeView({
  port,
  heading,
  tag,
  state,
  tuned,
  onClose,
  onRetry,
}: {
  port: string;
  heading: string;
  tag: string;
  state: ProbeState;
  /** A receiver's tuning (#959); the "tuned to" row shows on the ports that depend on it. */
  tuned?: Tuned;
  onClose: () => void;
  onRetry?: () => void;
}) {
  const { t } = useTranslation();
  const probe = state.kind === "ready" ? state.probe : null;
  const tunable = tuned && tuned.tuning.ports?.includes(port) ? tuned : undefined;
  const shownTag = probe?.medium === "samples" ? flowTag(t, probe) : tag;
  return (
    <section
      className="space-y-3 border-b p-3"
      aria-label={t("internals.probe.title")}
      data-testid="internal-probe"
      data-port={port}
      data-state={state.kind}
    >
      <div className="flex items-start justify-between gap-2">
        <div className="min-w-0">
          <div className="text-muted-foreground flex items-center gap-1 text-[11px] font-medium tracking-wide uppercase">
            <Activity className="size-3" aria-hidden />
            {t("internals.probe.title")}
          </div>
          <h3 className="mt-0.5 truncate text-sm font-semibold" title={port} data-testid="internal-probe-heading">
            {heading}
          </h3>
          <div className="num text-muted-foreground text-xs" data-testid="internal-probe-tag">
            {shownTag}
          </div>
        </div>
        <Button
          variant="ghost"
          size="icon"
          className="size-7 shrink-0"
          aria-label={t("internals.probe.close")}
          title={t("internals.probe.close")}
          onClick={onClose}
        >
          <X className="size-4" />
        </Button>
      </div>
      {tunable && <TuneBar {...tunable} />}
      {state.kind === "loading" && (
        <div className="space-y-2" aria-busy="true" aria-label={t("common.loading")}>
          <Skeleton className="h-[88px] w-full" />
          <Skeleton className="h-16 w-full" />
        </div>
      )}
      {state.kind === "withheld" && (
        <div className="bg-muted/50 rounded-md border p-3 text-xs" role="status" data-testid="internal-probe-withheld">
          <div className="flex items-center gap-1.5 font-medium">
            <Lock className="size-3.5 shrink-0" aria-hidden />
            {t("internals.probe.withheld")}
          </div>
          <p className="text-muted-foreground mt-1 leading-relaxed">{t("internals.probe.withheldBody")}</p>
        </div>
      )}
      {state.kind === "error" &&
        (state.error instanceof ApiError ? (
          // The service says what is wrong with this port (no such port, no signal with these
          // settings): its words, not a generic "not found".
          <div className="rounded-md border p-3 text-xs" role="alert" data-testid="internal-probe-error">
            <p className="flex items-center gap-1.5 font-medium">
              <AlertTriangle className="size-3.5 shrink-0" aria-hidden />
              {t("internals.probe.error", { port })}
            </p>
            <p className="text-muted-foreground num mt-1 break-words">{state.error.message}</p>
            {onRetry && (
              <Button variant="outline" size="sm" className="mt-2" onClick={onRetry}>
                {t("common.retry")}
              </Button>
            )}
          </div>
        ) : (
          <div className="rounded-md border" data-testid="internal-probe-error">
            <ErrorState error={state.error} onRetry={onRetry} />
          </div>
        ))}
      {probe && (
        <div
          className={cn("space-y-3 transition-opacity motion-reduce:transition-none", state.kind === "ready" && state.retuning && "opacity-60")}
          aria-busy={state.kind === "ready" && !!state.retuning}
        >
          <ProbeBody probe={probe} stale={state.kind === "ready" && !!state.stale} tuned={tunable} />
        </div>
      )}
    </section>
  );
}

function ProbeBody({ probe, stale, tuned }: { probe: InternalProbe; stale: boolean; tuned?: Tuned }) {
  const { t } = useTranslation();
  const samples = probe.medium === "samples";
  const kind = probe.format?.kind;
  // IQ samples and a transform's bins are volts too (complex baseband; a bin's magnitude).
  const unit = !samples || kind !== "codes" ? "V" : "";
  const bins = samples && kind === "bins";
  const iq = samples && kind === "iq" && probe.quadrature ? iqStairs(probe.waveform.volts, probe.quadrature, W, H) : null;
  const volts = probe.waveform.volts;
  // A frame longer than the picture is wide is drawn as its envelope (every extreme kept); the
  // steps themselves are shown in the close-up of its first samples below.
  const long = samples && volts.length > W;
  const plot = samples ? (long ? envelope(volts, W, H) : stairs(volts, W, H)) : trace(volts, W, H);
  const span = probe.waveform.dt_s * volts.length;
  const fullScale = fullScaleText(t, probe.full_scale_used);
  const zero = zeroY(plot.lo, plot.hi);
  const lsb = samples ? lsbOf(probe) : null;
  const first = volts.slice(0, CLOSE_UP);
  const close = long ? stairs(first, W, H) : null;
  const codes = close ? codeLines(close.lo, close.hi, lsb, H) : !long && samples ? codeLines(plot.lo, plot.hi, lsb, H) : [];
  return (
    <>
      {stale && (
        <p className="text-warn flex items-start gap-1.5 text-[11px]" role="status" data-testid="internal-probe-stale">
          <WifiOff className="mt-0.5 size-3 shrink-0" aria-hidden />
          {t("internals.probe.stale")}
        </p>
      )}
      {iq ? (
        <div>
          <h4 className={HEADING}>{t("internals.probe.iq", { count: iq.count })}</h4>
          <svg viewBox={`0 0 ${W} ${H}`} preserveAspectRatio="none" className={PLOT} role="img" aria-label={t("internals.probe.iq", { count: iq.count })} data-testid="internal-probe-iq">
            <Graticule />
            {zeroY(iq.lo, iq.hi) != null && <path d={`M0,${zeroY(iq.lo, iq.hi)!.toFixed(1)}H${W}`} fill="none" stroke="var(--muted-foreground)" strokeOpacity="0.6" strokeWidth="0.75" />}
            <path d={iq.q} fill="none" stroke="var(--foreground)" strokeOpacity="0.55" strokeWidth="1.25" strokeDasharray="3 2" strokeLinejoin="miter" vectorEffect="non-scaling-stroke" data-trace="q" />
            <path d={iq.i} fill="none" stroke="var(--primary)" strokeWidth="1.5" strokeLinejoin="miter" vectorEffect="non-scaling-stroke" data-trace="i" />
          </svg>
          <div className="num text-muted-foreground mt-0.5 flex items-center justify-between gap-2 text-[10px]">
            {/* Told apart by line and by name, not by colour alone. */}
            <span className="flex items-center gap-2">
              <span className="flex items-center gap-1">
                <svg width="14" height="6" aria-hidden><path d="M0,3H14" stroke="var(--primary)" strokeWidth="1.5" /></svg>I
              </span>
              <span className="flex items-center gap-1">
                <svg width="14" height="6" aria-hidden><path d="M0,3H14" stroke="var(--foreground)" strokeOpacity="0.55" strokeWidth="1.25" strokeDasharray="3 2" /></svg>Q
              </span>
              <span>{formatEng(probe.waveform.dt_s * iq.count, "s", 4)}</span>
            </span>
            <span>
              {formatEng(iq.lo, unit, 4)} {t("internals.probe.to")} {formatEng(iq.hi, unit, 4)}
            </span>
          </div>
        </div>
      ) : bins ? null : (
      <div>
        <h4 className={HEADING}>{samples ? (long ? t("internals.probe.frame") : t("internals.probe.samples")) : t("internals.probe.waveform")}</h4>
        <svg
          viewBox={`0 0 ${W} ${H}`}
          preserveAspectRatio="none"
          className={PLOT}
          role="img"
          aria-label={samples ? t("internals.probe.frame") : t("internals.probe.waveform")}
          data-testid="internal-probe-wave"
        >
          <Graticule />
          {!long && codes.length > 0 && <path d={codes.map((y) => `M0,${y.toFixed(1)}H${W}`).join("")} fill="none" stroke="var(--sim)" strokeOpacity="0.35" strokeWidth="0.5" />}
          {zero != null && <path d={`M0,${zero.toFixed(1)}H${W}`} fill="none" stroke="var(--muted-foreground)" strokeOpacity="0.6" strokeWidth="0.75" />}
          <path d={plot.d} fill="none" stroke="var(--primary)" strokeWidth={long ? 1 : 1.5} strokeLinejoin="miter" vectorEffect="non-scaling-stroke" />
        </svg>
        <div className="num text-muted-foreground mt-0.5 flex justify-between text-[10px]">
          <span>
            {samples
              ? t("internals.probe.samplesCount", { count: volts.length, span: formatEng(span, "s", 4) })
              : formatEng(span, "s", 4)}
          </span>
          <span>
            <span data-testid="internal-probe-lo">{formatEng(plot.lo, unit, 4)}</span> {t("internals.probe.to")}{" "}
            <span data-testid="internal-probe-hi">{formatEng(plot.hi, unit, 4)}</span>
          </span>
        </div>
      </div>
      )}
      {close && !iq && !bins && (
        <div>
          <h4 className={HEADING}>{t("internals.probe.closeUp", { count: first.length })}</h4>
          <svg
            viewBox={`0 0 ${W} ${H}`}
            preserveAspectRatio="none"
            className={PLOT}
            role="img"
            aria-label={t("internals.probe.closeUp", { count: first.length })}
            data-testid="internal-probe-closeup"
          >
            {codes.length > 0 ? (
              <path d={codes.map((y) => `M0,${y.toFixed(1)}H${W}`).join("")} fill="none" stroke="var(--sim)" strokeOpacity="0.4" strokeWidth="0.5" />
            ) : (
              <Graticule />
            )}
            <path d={close.d} fill="none" stroke="var(--primary)" strokeWidth="1.5" strokeLinejoin="miter" vectorEffect="non-scaling-stroke" />
          </svg>
          <div className="num text-muted-foreground mt-0.5 flex justify-between text-[10px]">
            <span>{formatEng(probe.waveform.dt_s * first.length, "s", 4)}</span>
            <span data-testid="internal-probe-lsb">
              {codes.length > 0 && lsb
                ? t("internals.probe.codes", { count: codes.length, lsb: formatEng(lsb, unit, 4) })
                : `${formatEng(close.lo, unit, 4)} ${t("internals.probe.to")} ${formatEng(close.hi, unit, 4)}`}
            </span>
          </div>
        </div>
      )}
      <dl className="num grid grid-cols-2 gap-x-3 gap-y-1 text-xs" data-testid="internal-probe-values">
        {bins ? (
          <>
            <dt className="text-muted-foreground">{t("internals.probe.binCount")}</dt>
            <dd className="text-right">{volts.length}</dd>
            <dt className="text-muted-foreground">{t("internals.probe.binSpacing")}</dt>
            <dd className="text-right">{formatEng(binSpacing(probe), "Hz", 4)}</dd>
            <dt className="text-muted-foreground">{t("internals.probe.binPeak")}</dt>
            <dd className="text-right">{formatEng(probe.peak, unit, 5)}</dd>
          </>
        ) : (
          <>
            <dt className="text-muted-foreground">DC</dt>
            <dd className="text-right">{formatEng(probe.dc, unit, 5)}</dd>
            <dt className="text-muted-foreground">RMS</dt>
            <dd className="text-right">{formatEng(probe.rms, unit, 5)}</dd>
            <dt className="text-muted-foreground">{t("probe.peak")}</dt>
            <dd className="text-right">{formatEng(probe.peak, unit, 5)}</dd>
          </>
        )}
        {samples && probe.sample_rate_hz != null && (
          <>
            <dt className="text-muted-foreground">{t("internals.probe.rate")}</dt>
            <dd className="text-right">{formatRate(probe.sample_rate_hz)}</dd>
          </>
        )}
        {samples && probe.format && (
          <>
            <dt className="text-muted-foreground">{t("internals.probe.format")}</dt>
            <dd className="text-right" data-testid="internal-probe-format">
              {formatFormat(t, probe.format)}
            </dd>
          </>
        )}
      </dl>
      {samples && (fullScale || probe.format?.bits) ? (
        <div className="space-y-1 text-xs">
          {fullScale && (
            <>
              <div className="flex items-baseline justify-between gap-2">
                <span data-testid="internal-probe-fullscale">{fullScale}</span>
                <span className={cn("flex items-center gap-1", probe.clipped ? "text-destructive font-medium" : "text-muted-foreground")}>
                  {probe.clipped && <AlertTriangle className="size-3 shrink-0" aria-hidden />}
                  {probe.clipped ? t("internals.probe.clipped") : t("internals.probe.notClipped")}
                </span>
              </div>
              {/* The converter's range as a bar, the share this frame spans filled in (never thinner than a hairline). */}
              <div className="bg-muted h-1.5 overflow-hidden rounded-full" aria-hidden>
                <div
                  className={cn("h-full rounded-full", probe.clipped ? "bg-destructive" : "bg-primary")}
                  style={{ width: `${Math.min(100, Math.max(1.5, (probe.full_scale_used ?? 0) * 100))}%` }}
                />
              </div>
            </>
          )}
          {!fullScale && (
            <div className={cn("flex items-center gap-1", probe.clipped ? "text-destructive font-medium" : "text-muted-foreground")}>
              {probe.clipped && <AlertTriangle className="size-3 shrink-0" aria-hidden />}
              {probe.clipped ? t("internals.probe.clipped") : t("internals.probe.notClipped")}
            </div>
          )}
        </div>
      ) : null}
      {samples ? <FrameSpectrum probe={probe} tuned={tuned} /> : <LineSpectrum probe={probe} />}
      {probe.silence.length > 0 && (
        <div data-testid="internal-probe-silence">
          <h4 className={HEADING}>{t("internals.probe.silent")}</h4>
          <ul className="text-muted-foreground space-y-0.5 text-xs">
            {probe.silence.map((r, i) => (
              <li key={`${r.kind}-${i}`}>
                {t(`scope.silence.${r.kind}`, { source: r.source ?? "", fault: r.fault_id ?? "" })}
                {r.path.length > 1 && <span className="num ml-1 text-[11px]">({r.path.join(" ← ")})</span>}
              </li>
            ))}
          </ul>
        </div>
      )}
      {probe.notes.length > 0 && (
        <div>
          <h4 className={HEADING}>{t("internals.probe.notes")}</h4>
          <ul className="text-sim space-y-0.5 text-[11px]">
            {probe.notes.map((n) => (
              <li key={n}>≈ {n}</li>
            ))}
          </ul>
        </div>
      )}
      <p className="text-muted-foreground text-[10px] leading-relaxed">
        {t("internals.probe.at", { t: formatEng(probe.t_ns * 1e-9, "s", 6) })} · {t("internals.probe.passive")}
      </p>
    </>
  );
}

/** The spacing of a transform's bins: what one point of the picture stands for. */
function binSpacing(probe: InternalProbe): number {
  const f = probe.spectrum?.freq_hz ?? [];
  return f.length > 1 ? (f[f.length - 1] - f[0]) / (f.length - 1) : 0;
}

/**
 * "Tuned to" (signal-chain.md 1.3, #959): the signal after a receiver's mixer depends on where its
 * oscillator is tuned. One field always shows the tuning in force and takes a typed one; beside it
 * the frequencies the instrument already has a name for — the center (the default: it follows the
 * setting) and each marker that is on (it follows the marker).
 */
function TuneBar({ tuning, tune, onTune }: Tuned) {
  const { t } = useTranslation();
  const [refused, setRefused] = useState<"range" | "text" | null>(null);
  const now = tuneHz(tune, tuning);
  const range = t("internals.probe.tuneRange", {
    lo: formatEng(tuning.min_hz ?? 0, "Hz", 4),
    hi: tuning.max_hz == null ? "∞" : formatEng(tuning.max_hz, "Hz", 4),
  });
  const pick = (next: Tune) => {
    setRefused(null);
    onTune(next);
  };
  const parse = (typed: string): Parsed<number> => {
    if (!typed.trim()) return SKIP;
    const hz = parseTune(typed, tuning);
    if (hz === "range" || hz === null) {
      setRefused(hz === "range" ? "range" : "text");
      return INVALID;
    }
    return commit(hz);
  };
  const marks = tuning.marks ?? [];
  const chip = (key: string, label: string, hz: number, on: boolean, next: Tune) => (
    <button
      key={key}
      type="button"
      role="radio"
      aria-checked={on}
      data-testid={`internal-probe-tune-${key}`}
      title={formatTune(hz)}
      onClick={() => pick(next)}
      className={cn(
        "focus-visible:ring-ring flex h-7 min-w-0 items-baseline gap-1.5 rounded-md border px-2 text-xs outline-none focus-visible:ring-2",
        on ? "border-primary bg-primary/10 text-foreground font-medium" : "text-muted-foreground hover:bg-accent hover:text-foreground",
      )}
    >
      <span className="shrink-0">{label}</span>
      <span className="num truncate text-[11px] opacity-80">{formatEng(hz, "Hz", 5)}</span>
    </button>
  );
  return (
    <div className="bg-muted/40 space-y-1.5 rounded-md border p-2" data-testid="internal-probe-tune" data-tune={tune.kind} data-hz={now}>
      <div className="flex items-center justify-between gap-2">
        <label htmlFor="internal-probe-tune-input" className="flex shrink-0 items-center gap-1 text-xs font-medium">
          <Radio className="text-muted-foreground size-3.5" aria-hidden />
          {t("internals.probe.tunedTo")}
        </label>
        <DraftInput
          id="internal-probe-tune-input"
          text={formatTune(now)}
          parse={parse}
          onCommit={(hz) => pick({ kind: "manual", hz })}
          className={cn("num h-7 w-36 text-right text-xs", refused && "border-destructive")}
          aria-label={t("internals.probe.tuneInput")}
          aria-invalid={refused != null}
          aria-describedby="internal-probe-tune-note"
          title={range}
        />
      </div>
      <div role="radiogroup" aria-label={t("internals.probe.tunedTo")} className="flex flex-wrap gap-1">
        {chip("center", t("internals.probe.tuneCenter"), tuning.center_hz, tune.kind === "center", CENTER)}
        {marks.map((m) =>
          chip(
            `${m.kind}-${m.index ?? 1}`,
            t(`internals.probe.tuneMark.${m.kind}`, { index: m.index ?? 1 }),
            m.freq_hz,
            tune.kind === "mark" && tune.mark === m.kind && tune.index === (m.index ?? 1),
            { kind: "mark", mark: m.kind, index: m.index ?? 1 },
          ),
        )}
        {tune.kind === "manual" && (
          <span className="border-primary bg-primary/10 flex h-7 items-center rounded-md border px-2 text-xs font-medium" role="radio" aria-checked="true" data-testid="internal-probe-tune-manual">
            {t("internals.probe.tuneManual")}
          </span>
        )}
      </div>
      <p id="internal-probe-tune-note" className={cn("text-[10px] leading-relaxed", refused ? "text-destructive" : "text-muted-foreground")} role={refused ? "alert" : undefined}>
        {refused === "range" ? t("internals.probe.tuneOutOfRange", { range }) : refused === "text" ? t("internals.probe.tuneNotNumber") : t("internals.probe.tuneHint", { range })}
      </p>
    </div>
  );
}

/** Where the pointer is on a picture drawn in a `W`-wide viewBox stretched to the element. */
function pointerX(e: PointerEvent<SVGSVGElement>): number {
  const box = e.currentTarget.getBoundingClientRect();
  return box.width ? ((e.clientX - box.left) / box.width) * W : 0;
}

/** A picture's foot: the axis ends, and between them what the cursor reads (or how to read). */
function PlotFoot({ axis, digits, children }: { axis: Axis; digits: number; children: ReactNode }) {
  return (
    <div className="num text-muted-foreground mt-0.5 grid grid-cols-[auto_1fr_auto] items-baseline gap-2 text-[10px]">
      <span>{formatEng(axis.lo, "Hz", digits)}</span>
      <span className="truncate text-center">{children}</span>
      <span>{formatEng(axis.hi, "Hz", digits)}</span>
    </div>
  );
}

/**
 * A sample stream's spectrum (signal-chain.md 1.3): real samples' FFT from 0 to half the rate, an IQ
 * stream's two-sided FFT, or a transform's bins as they are (no second transform). The picture is
 * read with a cursor — the pointer, or the arrow keys — that reports the strongest bin under it,
 * and the median level is drawn as the noise floor. On a radio-frequency axis (IQ, bins) a click, or
 * Enter, tunes the receiver to the cursor: the tone under it moves to the middle of the next frame.
 */
function FrameSpectrum({ probe, tuned }: { probe: InternalProbe; tuned?: Tuned }) {
  const { t } = useTranslation();
  const [cursor, setCursor] = useState<number | null>(null);
  const spectrum = probe.spectrum;
  if (!spectrum || !spectrum.level.length) return null;
  const kind = probe.format?.kind;
  const plot = spectrumBars(spectrum.level, W, H);
  const axis = spectrumAxis(spectrum);
  const n = spectrum.level.length;
  const peaks = strongestPeaks(spectrum);
  const floor = noiseFloor(spectrum);
  const radio = onRadioAxis(probe);
  const retune = radio && tuned ? tuned : undefined;
  // A bin is a few hundred hertz to a few kilohertz wide: digits that tell bins apart, no more.
  const digits = radio ? 6 : 4;
  const at = cursor != null && cursor < n ? cursor : null;
  const y = (level: number) => levelY(level, plot.lo, H);
  const tuneTo = (bin: number) => {
    const hz = spectrum.freq_hz[bin];
    if (retune && inTuningRange(hz, retune.tuning)) retune.onTune({ kind: "manual", hz });
  };
  const columns = spectrumColumns(n, W);
  const onKey = (e: KeyboardEvent<SVGSVGElement>) => {
    const step = (e.shiftKey ? 10 : 1) * (e.key === "ArrowLeft" ? -1 : e.key === "ArrowRight" ? 1 : 0);
    if (step) {
      // One bar at a time, reading the bar's strongest bin — as the pointer does.
      const from = at ?? peaks[0]?.bin ?? n >> 1;
      const column = Math.min(columns - 1, Math.max(0, Math.floor((from * columns) / n) + step));
      setCursor(binAt(spectrum, ((column + 0.5) * W) / columns, W));
    } else if (e.key === "Home" || e.key === "End") {
      // The strongest peak, then the next ones: where the eye goes first.
      const order = peaks.map((p) => p.bin);
      const i = at == null ? -1 : order.indexOf(at);
      setCursor(order[e.key === "Home" ? 0 : Math.min(order.length - 1, i + 1)] ?? null);
    } else if (e.key === "Enter" && at != null && retune) tuneTo(at);
    else if (e.key === "Escape") setCursor(null);
    else return;
    e.preventDefault();
  };
  const title = kind === "bins" ? t("internals.probe.spectrumBins") : kind === "iq" ? t("internals.probe.spectrumIq") : t("internals.probe.spectrum");
  const hint = t(kind === "bins" ? "internals.probe.spectrumHintBins" : kind === "iq" ? "internals.probe.spectrumHintIq" : "internals.probe.spectrumHint", {
    unit: spectrum.unit,
    range: SPECTRUM_RANGE_DB,
  });
  const top = peaks[0];
  const shown = at ?? top?.bin ?? null;
  const tuneX = radio && probe.tune_hz != null ? xOf(probe.tune_hz, axis, W) : null;
  const marks = radio ? (tuned?.tuning.marks ?? []).filter((m) => m.freq_hz >= axis.lo && m.freq_hz <= axis.hi) : [];
  return (
    <div>
      <h4 className={HEADING}>{title}</h4>
      <svg
        viewBox={`0 0 ${W} ${H}`}
        preserveAspectRatio="none"
        className={cn(PLOT, "focus-visible:ring-ring touch-none outline-none focus-visible:ring-2", retune ? "cursor-crosshair" : "cursor-default")}
        role="img"
        tabIndex={0}
        aria-label={`${title} — ${t(retune ? "internals.probe.readKeysTune" : "internals.probe.readKeys")}`}
        data-testid="internal-probe-spectrum"
        data-axis={radio ? "radio" : "baseband"}
        onPointerMove={(e) => setCursor(binAt(spectrum, pointerX(e), W))}
        onPointerLeave={() => setCursor(null)}
        onClick={(e) => {
          if (!retune) return;
          const box = e.currentTarget.getBoundingClientRect();
          tuneTo(binAt(spectrum, box.width ? ((e.clientX - box.left) / box.width) * W : 0, W));
        }}
        onKeyDown={onKey}
        onBlur={() => setCursor(null)}
      >
        <Graticule rows={5} />
        <path d={plot.d} fill="none" stroke="var(--primary)" strokeOpacity="0.85" strokeWidth="1" vectorEffect="non-scaling-stroke" />
        {floor != null && floor > plot.lo && (
          <path d={`M0,${y(floor).toFixed(1)}H${W}`} fill="none" stroke="var(--sim)" strokeWidth="0.75" strokeDasharray="4 3" vectorEffect="non-scaling-stroke" data-testid="internal-probe-floor-line" />
        )}
        {marks.map((m) => (
          <path key={`${m.kind}-${m.index}`} d={`M${xOf(m.freq_hz, axis, W).toFixed(1)},0v7`} fill="none" stroke="var(--foreground)" strokeWidth="1.5" vectorEffect="non-scaling-stroke" data-testid="internal-probe-mark" />
        ))}
        {tuneX != null && (
          <g data-testid="internal-probe-tuned-at">
            <path d={`M${tuneX.toFixed(1)},0V${H}`} fill="none" stroke="var(--foreground)" strokeOpacity="0.45" strokeWidth="0.75" strokeDasharray="2 2" vectorEffect="non-scaling-stroke" />
            <path d={`M${(tuneX - 4).toFixed(1)},0h8l-4,6z`} fill="var(--foreground)" fillOpacity="0.7" />
          </g>
        )}
        {at != null && (
          <path d={`M${binX(at, n, W).toFixed(1)},0V${H}`} fill="none" stroke="var(--foreground)" strokeOpacity="0.7" strokeWidth="0.75" vectorEffect="non-scaling-stroke" />
        )}
        {shown != null && (
          <circle
            cx={binX(shown, n, W)}
            cy={y(spectrum.level[shown])}
            r="2.5"
            fill="var(--background)"
            stroke={at != null ? "var(--foreground)" : "var(--primary)"}
            strokeWidth="1.25"
          />
        )}
      </svg>
      <PlotFoot axis={axis} digits={radio ? 5 : 4}>
        {at != null ? (
          <span className="text-foreground" data-testid="internal-probe-cursor">
            {formatEng(spectrum.freq_hz[at], "Hz", digits)} · {spectrum.level[at].toFixed(1)} {spectrum.unit}
          </span>
        ) : retune ? (
          t("internals.probe.clickToTune")
        ) : null}
      </PlotFoot>
      <p className="text-muted-foreground text-[10px]">{hint}</p>
      {floor != null && (
        <div className="num mt-1 flex items-baseline justify-between text-[11px]">
          <span className="text-muted-foreground flex items-center gap-1.5">
            <svg width="14" height="6" aria-hidden><path d="M0,3H14" stroke="var(--sim)" strokeWidth="1" strokeDasharray="4 3" /></svg>
            {t("internals.probe.floor")}
          </span>
          <span data-testid="internal-probe-floor">
            {floor.toFixed(1)} {spectrum.unit}
          </span>
        </div>
      )}
      <table className="num mt-1 w-full text-[11px]" data-testid="internal-probe-peaks">
        <thead className="text-muted-foreground">
          <tr>
            <th className="py-0.5 text-left font-medium">{t("internals.probe.frequency")}</th>
            <th className="py-0.5 text-right font-medium">{t("internals.probe.level")}</th>
            {retune && <th className="w-6" />}
          </tr>
        </thead>
        <tbody>
          {peaks.map((b) => (
            <tr
              key={b.bin}
              className={cn("border-t", at === b.bin && "bg-accent")}
              onPointerEnter={() => setCursor(b.bin)}
              onPointerLeave={() => setCursor(null)}
            >
              <td className="py-0.5">{formatEng(b.freq, "Hz", 6)}</td>
              <td className="py-0.5 text-right">
                {b.level.toFixed(1)} {spectrum.unit}
              </td>
              {retune && (
                <td className="py-0 text-right">
                  <button
                    type="button"
                    className="text-muted-foreground hover:text-foreground focus-visible:ring-ring inline-flex size-5 items-center justify-center rounded outline-none focus-visible:ring-2"
                    aria-label={t("internals.probe.tuneHere", { freq: formatEng(b.freq, "Hz", 6) })}
                    title={t("internals.probe.tuneHere", { freq: formatEng(b.freq, "Hz", 6) })}
                    onClick={() => tuneTo(binOf(spectrum, b.freq))}
                    onFocus={() => setCursor(b.bin)}
                    onBlur={() => setCursor(null)}
                  >
                    <Crosshair className="size-3" aria-hidden />
                  </button>
                </td>
              )}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

/**
 * An analog port: its spectral lines as stems on a dB scale, and the strongest in a table with each
 * one's level under the strongest (dBc) — an intermodulation product reads as what it is. Lines that
 * crowd far from 0 Hz get an axis of their own (`lineWindow`); the cursor reads the nearest line.
 */
function LineSpectrum({ probe }: { probe: InternalProbe }) {
  const { t } = useTranslation();
  const [whole, setWhole] = useState(false);
  const [cursor, setCursor] = useState<number | null>(null);
  const lines = probe.lines;
  if (!lines.length) return null;
  const axis = lineWindow(lines, whole);
  const plot = lineStems(lines, W, H, axis);
  const at = cursor != null ? (lines.find((l) => l.freq_hz === cursor) ?? null) : null;
  const read = (freq: number) => setCursor(nearestLine(lines, freq, axis)?.freq_hz ?? null);
  const onAxis = lines.filter((l) => l.amplitude > 0 && l.freq_hz >= axis.lo && l.freq_hz <= axis.hi).sort((a, b) => a.freq_hz - b.freq_hz);
  const onKey = (e: KeyboardEvent<SVGSVGElement>) => {
    const step = e.key === "ArrowLeft" ? -1 : e.key === "ArrowRight" ? 1 : 0;
    if (step) {
      const i = at ? onAxis.findIndex((l) => l.freq_hz === at.freq_hz) : -1;
      const next = i < 0 ? onAxis.findIndex((l) => l.freq_hz === lines[0].freq_hz) : Math.min(onAxis.length - 1, Math.max(0, i + step));
      setCursor(onAxis[Math.max(0, next)]?.freq_hz ?? null);
    } else if (e.key === "Escape") setCursor(null);
    else return;
    e.preventDefault();
  };
  const digits = axis.lo > 0 ? 6 : 4;
  return (
    <div>
      <div className="mb-1 flex items-center justify-between gap-2">
        <h4 className={cn(HEADING, "mb-0")}>{t("internals.probe.lines")}</h4>
        {axis.zoomable && (
          <button
            type="button"
            className="text-muted-foreground hover:text-foreground focus-visible:ring-ring rounded px-1 text-[10px] underline-offset-2 outline-none hover:underline focus-visible:ring-2"
            aria-pressed={whole}
            data-testid="internal-probe-lines-whole"
            onClick={() => {
              setWhole(!whole);
              setCursor(null);
            }}
          >
            {whole ? t("internals.probe.linesNear") : t("internals.probe.linesWhole")}
          </button>
        )}
      </div>
      <svg
        viewBox={`0 0 ${W} ${H}`}
        preserveAspectRatio="none"
        className={cn(PLOT, "focus-visible:ring-ring touch-none outline-none focus-visible:ring-2")}
        role="img"
        tabIndex={0}
        aria-label={`${t("internals.probe.lines")} — ${t("internals.probe.readKeys")}`}
        data-testid="internal-probe-lines"
        data-zoomed={axis.zoomable && !whole ? "" : undefined}
        onPointerMove={(e) => read(freqAt(pointerX(e), axis, W))}
        onPointerLeave={() => setCursor(null)}
        onKeyDown={onKey}
        onBlur={() => setCursor(null)}
      >
        <Graticule rows={5} />
        <path d={plot.d} fill="none" stroke="var(--primary)" strokeWidth="2" vectorEffect="non-scaling-stroke" />
        {at && at.amplitude > 0 && at.freq_hz >= axis.lo && at.freq_hz <= axis.hi && (
          <>
            <path
              d={`M${xOf(at.freq_hz, axis, W).toFixed(1)},${H - 4}V${levelY(20 * Math.log10(at.amplitude), plot.lo, H).toFixed(1)}`}
              fill="none"
              stroke="var(--foreground)"
              strokeWidth="2.5"
              vectorEffect="non-scaling-stroke"
            />
            <circle cx={xOf(at.freq_hz, axis, W)} cy={levelY(20 * Math.log10(at.amplitude), plot.lo, H)} r="2.5" fill="var(--background)" stroke="var(--foreground)" strokeWidth="1.25" />
          </>
        )}
      </svg>
      <PlotFoot axis={axis} digits={digits}>
        {at ? (
          <span className="text-foreground" data-testid="internal-probe-cursor">
            {formatEng(at.freq_hz, "Hz", 7)} · {formatEng(at.amplitude, "V", 4)} · {formatDbc(dbc(at, lines))} dBc
          </span>
        ) : null}
      </PlotFoot>
      {axis.outside > 0 && (
        <p className="text-muted-foreground text-[10px]" data-testid="internal-probe-lines-outside">
          {t("internals.probe.linesOutside", { count: axis.outside })}
        </p>
      )}
      <table className="num mt-1 w-full text-[11px]" data-testid="internal-probe-line-table">
        <thead className="text-muted-foreground">
          <tr>
            <th className="py-0.5 text-left font-medium">{t("internals.probe.frequency")}</th>
            <th className="py-0.5 text-right font-medium">{t("internals.probe.amplitude")}</th>
            <th className="py-0.5 text-right font-medium" title={t("internals.probe.dbcTip")}>
              dBc
            </th>
          </tr>
        </thead>
        <tbody>
          {lines.slice(0, 6).map((l) => (
            <tr
              key={l.freq_hz}
              className={cn("border-t", at?.freq_hz === l.freq_hz && "bg-accent")}
              onPointerEnter={() => setCursor(l.freq_hz)}
              onPointerLeave={() => setCursor(null)}
            >
              <td className="py-0.5">{formatEng(l.freq_hz, "Hz", 7)}</td>
              <td className="py-0.5 text-right">{formatEng(l.amplitude, "V", 5)}</td>
              <td className="py-0.5 text-right">{formatDbc(dbc(l, lines))}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

/** A level under the strongest line: `0`, `−60.0`; nothing for a line of no amplitude. */
function formatDbc(value: number): string {
  if (!Number.isFinite(value)) return "—";
  return value === 0 ? "0" : `${value < 0 ? "−" : "+"}${Math.abs(value).toFixed(1)}`;
}

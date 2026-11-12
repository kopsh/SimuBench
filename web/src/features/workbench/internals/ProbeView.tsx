import { Activity, AlertTriangle, Lock, WifiOff, X } from "lucide-react";
import { useDeferredValue } from "react";
import { useTranslation } from "react-i18next";
import { ApiError } from "@/api/client";
import { useEvents } from "@/api/events";
import { useInternalProbe } from "@/api/hooks";
import { ErrorState } from "@/components/app/StateViews";
import { Button } from "@/components/ui/button";
import { Skeleton } from "@/components/ui/skeleton";
import { formatEng } from "@/lib/format";
import { cn } from "@/lib/utils";
import {
  type InternalProbe,
  CLOSE_UP,
  SPECTRUM_RANGE_DB,
  codeLines,
  envelope,
  flowTag,
  formatFormat,
  formatRate,
  fullScaleText,
  lineStems,
  lsbOf,
  spectrumBars,
  stairs,
  strongestPeaks,
  trace,
  withheldError,
} from "./probe";

/** What the probe panel has to show (signal-chain.md 1.3): every state is drawn, not just the signal. */
export type ProbeState =
  | { kind: "loading" }
  | { kind: "withheld" }
  | { kind: "error"; error: unknown }
  | { kind: "ready"; probe: InternalProbe; stale?: boolean };

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

/** The internal probe of a session: the signal at one output port, re-read on every event version (D64). */
export function InternalProbePanel({
  sid,
  instrument,
  port,
  heading,
  tag,
  onClose,
}: {
  sid: string;
  instrument: string;
  port: string;
  heading: string;
  tag: string;
  onClose: () => void;
}) {
  const version = useDeferredValue(useEvents((s) => s.version));
  const status = useEvents((s) => s.status);
  const q = useInternalProbe(sid, instrument, port, version);
  const state: ProbeState =
    q.error && withheldError(q.error)
      ? { kind: "withheld" }
      : q.data
        ? { kind: "ready", probe: q.data, stale: status !== "connected" }
        : q.error
          ? { kind: "error", error: q.error }
          : { kind: "loading" };
  return (
    <InternalProbeView
      port={port}
      heading={heading}
      tag={tag}
      state={state}
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
  onClose,
  onRetry,
}: {
  port: string;
  heading: string;
  tag: string;
  state: ProbeState;
  onClose: () => void;
  onRetry?: () => void;
}) {
  const { t } = useTranslation();
  const probe = state.kind === "ready" ? state.probe : null;
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
      {probe && <ProbeBody probe={probe} stale={state.kind === "ready" && !!state.stale} />}
    </section>
  );
}

function ProbeBody({ probe, stale }: { probe: InternalProbe; stale: boolean }) {
  const { t } = useTranslation();
  const samples = probe.medium === "samples";
  const unit = !samples || probe.format?.kind === "volts" ? "V" : "";
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
      {close && (
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
        <dt className="text-muted-foreground">DC</dt>
        <dd className="text-right">{formatEng(probe.dc, unit, 5)}</dd>
        <dt className="text-muted-foreground">RMS</dt>
        <dd className="text-right">{formatEng(probe.rms, unit, 5)}</dd>
        <dt className="text-muted-foreground">{t("probe.peak")}</dt>
        <dd className="text-right">{formatEng(probe.peak, unit, 5)}</dd>
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
      {samples ? <FrameSpectrum probe={probe} /> : <LineSpectrum probe={probe} />}
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

/** A sample stream: the frame's FFT, linear frequency from 0 to half the rate, and its strongest bins. */
function FrameSpectrum({ probe }: { probe: InternalProbe }) {
  const { t } = useTranslation();
  const spectrum = probe.spectrum;
  if (!spectrum) return null;
  const plot = spectrumBars(spectrum.level, W, H);
  const nyquist = spectrum.freq_hz[spectrum.freq_hz.length - 1] ?? 0;
  const peaks = strongestPeaks(spectrum);
  const top = peaks[0];
  // The strongest peak, marked on the picture: where it is, and that the table's first row is this one.
  const mark = top && nyquist > 0 ? { x: (top.freq / nyquist) * W, y: H - 4 - ((Math.max(top.level, plot.lo) - plot.lo) / SPECTRUM_RANGE_DB) * (H - 8) } : null;
  return (
    <div>
      <h4 className={HEADING}>{t("internals.probe.spectrum")}</h4>
      <svg
        viewBox={`0 0 ${W} ${H}`}
        preserveAspectRatio="none"
        className={PLOT}
        role="img"
        aria-label={t("internals.probe.spectrum")}
        data-testid="internal-probe-spectrum"
      >
        <Graticule rows={5} />
        <path d={plot.d} fill="none" stroke="var(--primary)" strokeOpacity="0.85" strokeWidth="1" vectorEffect="non-scaling-stroke" />
        {mark && <circle cx={mark.x} cy={mark.y} r="2.5" fill="var(--background)" stroke="var(--primary)" strokeWidth="1.25" />}
      </svg>
      <div className="num text-muted-foreground mt-0.5 flex justify-between text-[10px]">
        <span>0 Hz</span>
        <span>{formatEng(nyquist, "Hz", 4)}</span>
      </div>
      <p className="text-muted-foreground text-[10px]">
        {t("internals.probe.spectrumHint", { unit: spectrum.unit, range: SPECTRUM_RANGE_DB })}
      </p>
      <table className="num mt-1 w-full text-[11px]">
        <thead className="text-muted-foreground">
          <tr>
            <th className="py-0.5 text-left font-medium">{t("internals.probe.frequency")}</th>
            <th className="py-0.5 text-right font-medium">{t("internals.probe.level")}</th>
          </tr>
        </thead>
        <tbody>
          {peaks.map((b) => (
            <tr key={b.freq} className="border-t">
              <td className="py-0.5">{formatEng(b.freq, "Hz", 6)}</td>
              <td className="py-0.5 text-right">
                {b.level.toFixed(1)} {spectrum.unit}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

/** An analog port: its spectral lines as stems on a dB scale, and the strongest in a table. */
function LineSpectrum({ probe }: { probe: InternalProbe }) {
  const { t } = useTranslation();
  if (!probe.lines.length) return null;
  const plot = lineStems(probe.lines, W, H);
  return (
    <div>
      <h4 className={HEADING}>{t("internals.probe.lines")}</h4>
      <svg
        viewBox={`0 0 ${W} ${H}`}
        preserveAspectRatio="none"
        className={PLOT}
        role="img"
        aria-label={t("internals.probe.lines")}
        data-testid="internal-probe-lines"
      >
        <Graticule rows={5} />
        <path d={plot.d} fill="none" stroke="var(--primary)" strokeWidth="2" vectorEffect="non-scaling-stroke" />
      </svg>
      <div className="num text-muted-foreground mt-0.5 flex justify-between text-[10px]">
        <span>0 Hz</span>
        <span>{formatEng(plot.top, "Hz", 4)}</span>
      </div>
      <table className="num mt-1 w-full text-[11px]">
        <thead className="text-muted-foreground">
          <tr>
            <th className="py-0.5 text-left font-medium">{t("internals.probe.frequency")}</th>
            <th className="py-0.5 text-right font-medium">{t("internals.probe.amplitude")}</th>
          </tr>
        </thead>
        <tbody>
          {probe.lines.slice(0, 6).map((l) => (
            <tr key={l.freq_hz} className="border-t">
              <td className="py-0.5">{formatEng(l.freq_hz, "Hz", 7)}</td>
              <td className="py-0.5 text-right">{formatEng(l.amplitude, "V", 5)}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

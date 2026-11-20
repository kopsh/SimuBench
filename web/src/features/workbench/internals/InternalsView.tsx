/**
 * The workbench's live internals (issue 20): the session around the shared panel
 * (./InternalsPanel) — the internals query with its loading and error states, and the probe
 * wiring (which ports on screen get their signal read) that the acceptance page, without a
 * session, does without.
 */
import { useDeferredValue, useEffect, useMemo, useState } from "react";
import type { InstrumentInfo, InstrumentInternals, SessionInfo } from "@/api/client";
import { useEvents } from "@/api/events";
import { useInternalProbes, useInternals } from "@/api/hooks";
import { ErrorState } from "@/components/app/StateViews";
import { Skeleton } from "@/components/ui/skeleton";
import { useWorkbench } from "../store";
import { InternalsPanel } from "./InternalsPanel";
import { InternalProbePanel } from "./ProbeView";
import { miniOf } from "./probe";

/** Read-only view of how an instrument is built from components (issue 20). */
export function InternalsView({
  session,
  inst,
}: {
  session: SessionInfo;
  inst: InstrumentInfo;
}) {
  const version = useDeferredValue(useEvents((s) => s.version));
  const revealSetting = useWorkbench((s) => s.revealSetting);
  const query = useInternals(session.id, inst.name, version);
  if (query.error)
    return (
      <ErrorState error={query.error} onRetry={() => void query.refetch()} />
    );
  if (!query.data) return <Skeleton className="m-3 h-[calc(100%-1.5rem)]" />;
  return <LiveInternals session={session} inst={inst} view={query.data} onLocate={(path) => revealSetting(inst.name, path)} />;
}

/** A value once it has stopped changing for `ms`: a burst of events asks for the thumbnails once. */
function useSettled<V>(value: V, ms: number): V {
  const [settled, setSettled] = useState(value);
  useEffect(() => {
    const id = window.setTimeout(() => setSettled(value), ms);
    return () => window.clearTimeout(id);
  }, [value, ms]);
  return settled;
}

/**
 * The internals of a running session: the diagram, the signal at every output on screen (the
 * thumbnails), and the probe panel. The thumbnails follow the event version (D64) once a burst of
 * events has settled; when the service withholds internal signals (validation mode) they are not
 * asked for again and the diagram shows its structure alone.
 */
function LiveInternals({
  session,
  inst,
  view,
  onLocate,
}: {
  session: SessionInfo;
  inst: InstrumentInfo;
  view: InstrumentInternals;
  onLocate: (setting: string) => void;
}) {
  const version = useSettled(useEvents((s) => s.version), 200);
  const [ports, setPorts] = useState<string[]>([]);
  const [withheld, setWithheld] = useState(false);
  const read = useInternalProbes(session.id, inst.name, ports, version, !withheld);
  // Refused once is refused for the session's mode: remembered, so the next version does not ask again.
  if (read.withheld && !withheld) setWithheld(true);
  const signals = useMemo(() => new Map([...read.probes].map(([port, probe]) => [port, miniOf(probe)])), [read.probes]);
  return (
    <InternalsPanel
      view={view}
      inst={inst}
      onLocate={onLocate}
      signals={signals}
      onPorts={setPorts}
      renderProbe={(probe) => (
        <InternalProbePanel
          key={probe.port}
          sid={session.id}
          instrument={inst.name}
          port={probe.port}
          heading={probe.heading}
          tag={probe.tag}
          tuned={probe.tuned}
          onClose={probe.close}
        />
      )}
    />
  );
}

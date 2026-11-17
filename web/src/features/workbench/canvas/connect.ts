import type { SessionInfo } from "@/api/client";

/**
 * The ports that drive a net which a new wire `a` – `b` would join into one (#1269), when there would
 * be more than one: every driving port already on the net of either end, and the ends themselves when
 * they drive (`PortInfo.drives`: a source's electrical output, an optical output). Empty: the wire is
 * fine. Two or more is a net with two drivers — the canvas refuses the wire before it is dropped, as the
 * bench checks would (E-NET-MULTIDRIVER).
 */
export function driversJoined(view: SessionInfo, a: string, b: string): string[] {
  const ports = [
    ...view.instruments.flatMap((i) => i.ports.map((p) => ({ ref: `${i.name}.${p.name}`, ...p }))),
    ...view.parts.flatMap((d) => d.ports.map((p) => ({ ref: `${d.name}.${p.name}`, ...p }))),
  ];
  const netOf = new Map(ports.map((p) => [p.ref, p.net]));
  const nets = new Set([netOf.get(a), netOf.get(b)].filter((n): n is string => !!n));
  const joined = ports.filter((p) => p.ref === a || p.ref === b || (!!p.net && nets.has(p.net)));
  for (const medium of ["electrical", "optical"]) {
    const drivers = [...new Set(joined.filter((p) => p.drives && p.medium === medium).map((p) => p.ref))];
    if (drivers.length > 1) return drivers.sort();
  }
  return [];
}

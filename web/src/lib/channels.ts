// Channel colors are the same on cards, traces, wires and legends (ui-design.md 4.2).
export const CHANNEL_COLORS = ["var(--ch1)", "var(--ch2)", "var(--ch3)", "var(--ch4)"];

export function channelColor(channel: number | null | undefined): string {
  if (!channel) return "var(--medium-electrical)";
  return CHANNEL_COLORS[(channel - 1) % CHANNEL_COLORS.length];
}

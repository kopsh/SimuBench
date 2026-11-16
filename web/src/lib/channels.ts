// Channel colors are the same on cards, traces, wires and legends (ui-design.md 4.2).
export const CHANNEL_COLORS = ["var(--ch1)", "var(--ch2)", "var(--ch3)", "var(--ch4)"];

/** A math function's traces (FUNCtion<f>): one color for every function, as their labels say which. */
export const FUNCTION_COLOR = "var(--math)";

/** A channel's color; a negative number is a math function (-f, the measurements' key). */
export function channelColor(channel: number | null | undefined): string {
  if (!channel) return "var(--medium-electrical)";
  if (channel < 0) return FUNCTION_COLOR;
  return CHANNEL_COLORS[(channel - 1) % CHANNEL_COLORS.length];
}

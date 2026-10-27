import type { Meta, StoryObj } from "@storybook/react-vite";

const COLORS = [
  ["background", "foreground"],
  ["card", "card-foreground"],
  ["primary", "primary-foreground"],
  ["muted", "muted-foreground"],
  ["accent", "accent-foreground"],
  ["status-ok", "background"],
  ["status-warn", "background"],
  ["status-error", "background"],
  ["medium-electrical", "background"],
  ["medium-reference", "background"],
] as const;

/** Design tokens (globals.css): every color is a token, redefined for the dark theme. */
function Foundations() {
  return (
    <div className="space-y-6">
      <section>
        <h2 className="mb-2 text-sm font-semibold">Color tokens</h2>
        <div className="grid grid-cols-2 gap-2 md:grid-cols-5">
          {COLORS.map(([bg, fg]) => (
            <div key={bg} className="rounded-md border p-3 text-xs" style={{ background: `var(--${bg})`, color: `var(--${fg})` }}>
              --{bg}
            </div>
          ))}
        </div>
      </section>
      <section className="space-y-1">
        <h2 className="mb-2 text-sm font-semibold">Type</h2>
        <p className="text-lg font-semibold">测试台 Bench · 18 / semibold</p>
        <p className="text-sm">正文 Body text · 14</p>
        <p className="text-muted-foreground text-xs">说明 Secondary · 12</p>
        <p className="num text-2xl">1.000002000 GHz · −44.99° · 353.51 mV</p>
      </section>
    </div>
  );
}

const meta: Meta<typeof Foundations> = { title: "Foundations/Tokens", component: Foundations };
export default meta;
export const Tokens: StoryObj<typeof Foundations> = {};

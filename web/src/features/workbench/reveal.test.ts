import { afterEach, describe, expect, it, vi } from "vitest";
import { showSetting } from "./reveal";

afterEach(() => {
  document.body.innerHTML = "";
});

const frames = (n: number) => new Promise<void>((r) => setTimeout(r, n * 20));

describe("showSetting (#800, #819)", () => {
  it("focuses the control of the row and marks it", async () => {
    document.body.innerHTML = `<div data-setting="timebase.scale"><input id="f"></div><input id="other">`;
    (document.getElementById("other") as HTMLElement).focus();
    const done = vi.fn();
    showSetting("timebase.scale", done);
    await frames(3);
    expect(done).toHaveBeenCalledExactlyOnceWith(true);
    expect(document.activeElement).toBe(document.getElementById("f"));
    expect(document.querySelector("[data-setting]")!.hasAttribute("data-revealed")).toBe(true);
  });

  it("waits for a row that renders a few frames later", async () => {
    const done = vi.fn();
    showSetting("ch3.scale", done);
    setTimeout(() => (document.body.innerHTML = `<div data-setting="ch3.scale"><input id="f"></div>`), 30);
    await frames(6);
    expect(done).toHaveBeenCalledExactlyOnceWith(true);
  });

  it("says it found nothing and leaves the focus alone when the row never comes", async () => {
    document.body.innerHTML = `<input id="other">`;
    (document.getElementById("other") as HTMLElement).focus();
    const done = vi.fn();
    showSetting("no.such", done, 3);
    await frames(6);
    expect(done).toHaveBeenCalledExactlyOnceWith(false);
    expect(document.activeElement).toBe(document.getElementById("other"));
  });

  it("stops looking when cancelled", async () => {
    const done = vi.fn();
    const cancel = showSetting("late", done, 3);
    cancel();
    await frames(6);
    expect(done).not.toHaveBeenCalled();
  });
});

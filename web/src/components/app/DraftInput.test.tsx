import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { commit, DraftInput, INVALID, SKIP } from "./DraftInput";

afterEach(cleanup);

function setup(
  parse = (s: string) =>
    s.trim() === "bad" ? INVALID : s.trim() === "10" ? SKIP : commit(s.trim()),
) {
  const onCommit = vi.fn();
  const onInvalid = vi.fn();
  render(
    <DraftInput
      text="10"
      parse={parse}
      onCommit={onCommit}
      onInvalid={onInvalid}
      aria-label="f"
    />,
  );
  const input = screen.getByLabelText("f") as HTMLInputElement;
  fireEvent.focus(input);
  return { input, onCommit, onInvalid };
}

describe("DraftInput", () => {
  it("Enter commits the typed text", () => {
    const { input, onCommit } = setup();
    fireEvent.change(input, { target: { value: " 25 " } });
    fireEvent.keyDown(input, { key: "Enter" });
    fireEvent.blur(input);
    expect(onCommit).toHaveBeenCalledExactlyOnceWith("25");
  });

  it("blur commits the typed text", () => {
    const { input, onCommit } = setup();
    fireEvent.change(input, { target: { value: "30" } });
    fireEvent.blur(input);
    expect(onCommit).toHaveBeenCalledExactlyOnceWith("30");
    expect(input.value).toBe("10");
  });

  it("Esc drops the draft and restores the value", () => {
    const { input, onCommit } = setup();
    fireEvent.change(input, { target: { value: "99" } });
    fireEvent.keyDown(input, { key: "Escape" });
    fireEvent.blur(input);
    expect(onCommit).not.toHaveBeenCalled();
    expect(input.value).toBe("10");
  });

  it("invalid text is not committed and is reported", () => {
    const { input, onCommit, onInvalid } = setup();
    fireEvent.change(input, { target: { value: "bad" } });
    fireEvent.blur(input);
    expect(onCommit).not.toHaveBeenCalled();
    expect(onInvalid).toHaveBeenCalledOnce();
  });

  it("an unchanged value is skipped", () => {
    const { input, onCommit, onInvalid } = setup();
    fireEvent.blur(input);
    expect(onCommit).not.toHaveBeenCalled();
    expect(onInvalid).not.toHaveBeenCalled();
  });
});

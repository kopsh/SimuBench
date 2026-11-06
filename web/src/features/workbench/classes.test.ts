import { describe, expect, it } from "vitest";
import { en } from "@/i18n/en";
import { zh } from "@/i18n/zh";
import { CLASS_IDS, classScreen } from "./classes";

describe("instrument class list", () => {
  it("every class has a label in both languages, and no label is orphaned", () => {
    expect(Object.keys(zh.acceptance.class).sort()).toEqual([...CLASS_IDS].sort());
    expect(Object.keys(en.acceptance.class).sort()).toEqual([...CLASS_IDS].sort());
  });
  it("an unknown class has no screen", () => {
    const props = { session: {}, inst: {}, readonly: false } as never;
    expect(classScreen("toString", props)).toBeNull();
    expect(classScreen(undefined, props)).toBeNull();
    expect(classScreen("oscilloscope", props)).not.toBeNull();
  });
});

import { readdirSync, readFileSync } from "node:fs";
import { join } from "node:path";
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
  it("every class a definition declares is in the list, so no raw id reaches the page (#1222)", () => {
    const root = join(__dirname, "..", "..", "..", "..");
    const declared = new Set<string>();
    for (const dir of ["src/simubench/library/instruments", "validation/instruments", "examples/instruments"]) {
      let names: string[];
      try {
        names = readdirSync(join(root, dir));
      } catch {
        continue; // a directory a checkout does not have
      }
      for (const name of names.filter((n) => n.endsWith(".yaml") && !n.endsWith(".coverage.yaml") && !n.endsWith(".cases.yaml"))) {
        const line = readFileSync(join(root, dir, name), "utf-8").split("\n").find((l) => l.startsWith("class: "));
        if (line) declared.add(line.slice("class: ".length).trim());
      }
    }
    expect(declared.size).toBeGreaterThan(10);
    expect([...declared].filter((c) => !(CLASS_IDS as readonly string[]).includes(c)).sort()).toEqual([]);
  });
});

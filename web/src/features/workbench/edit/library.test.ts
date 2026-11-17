import { expect, it } from "vitest";
import type { Catalog } from "@/api/client";
import { libraryGroups } from "./library";

type Item = Catalog["instruments"][number];
const item = (id: string, model: string, cls: string) => ({ id, model, instrument_class: cls, manufacturer: "M", channels: 1 }) as unknown as Item;

// The build mode's library groups by class, as the home page does (#1356).
it("groups the library's instruments by class in the classes' order, each by model", () => {
  const groups = libraryGroups([item("a", "SDG2042X", "function_generator"), item("b", "UXR1104B", "oscilloscope"), item("c", "DSO-X 2024A", "oscilloscope")], ["oscilloscope", "function_generator"]);
  expect(groups.map((g) => [g.cls, g.instruments.map((i) => i.model)])).toEqual([
    ["oscilloscope", ["DSO-X 2024A", "UXR1104B"]],
    ["function_generator", ["SDG2042X"]],
  ]);
});

import { describe, expect, it } from "vitest";
import { fieldKind, getAt, parseList, pointerOf, problemsAt, resolve, setAt, suggestId, type JsonSchema } from "./schemaForm";

const root: JsonSchema = {
  $defs: {
    Row: { type: "object", properties: { v_div: { type: "number" }, mode: { type: "string", enum: ["a", "b"] } } },
    Adc: { type: "object", title: "ADC", properties: { bits: { type: "integer" } } },
  },
};

describe("the template inputs form (P2-5)", () => {
  it("resolves a reference and an optional field, keeping the field's own title", () => {
    const r = resolve({ anyOf: [{ $ref: "#/$defs/Adc" }, { type: "null" }], title: "转换器" }, root);
    expect(r.optional).toBe(true);
    expect(r.node.title).toBe("转换器");
    expect(Object.keys(r.node.properties ?? {})).toEqual(["bits"]);
  });

  it("chooses a widget per kind of field", () => {
    expect(fieldKind({ type: "array", items: { $ref: "#/$defs/Row" } }, root).kind).toBe("rows");
    expect(fieldKind({ type: "array", prefixItems: [{ type: "number" }, { type: "number" }] }, root).kind).toBe("tuple");
    expect(fieldKind({ type: "array", items: { type: "string" } }, root).kind).toBe("list");
    expect(fieldKind({ type: "array", items: { type: "array", items: { type: "integer" } } }, root).kind).toBe("json");
    expect(fieldKind({ type: "string", enum: ["error", "clamp"] }, root)).toEqual({ kind: "enum", options: ["error", "clamp"] });
    expect(fieldKind({ type: "integer" }, root)).toEqual({ kind: "number", integer: true });
  });

  it("writes a value at a path without touching the original", () => {
    const before = { vertical: { table: [{ v_div: 1 }, { v_div: 2 }] } };
    const after = setAt(before, ["vertical", "table", 1, "v_div"], 5);
    expect(getAt(after, ["vertical", "table", 1, "v_div"])).toBe(5);
    expect(before.vertical.table[1].v_div).toBe(2);
    expect(setAt({ a: 1, b: 2 }, ["a"], undefined)).toEqual({ b: 2 });
  });

  it("finds the diagnostics that belong to a field by their pointer", () => {
    const problems = [{ pointer: "/template/inputs/vertical/table/1/v_div" }, { pointer: "/template/inputs/vertical" }];
    expect(pointerOf(["vertical", "table", 1, "v_div"])).toBe("/template/inputs/vertical/table/1/v_div");
    expect(problemsAt(problems, ["vertical", "table", 1, "v_div"])).toHaveLength(1);
    expect(problemsAt(problems, ["vertical"])).toHaveLength(1);
  });

  it("reads a typed list and suggests an id", () => {
    expect(parseList("100, 250，500", { type: "integer" })).toEqual([100, 250, 500]);
    expect(parseList("1, x", { type: "number" })).toBeNull();
    expect(parseList("AUTO, NORMal", { type: "string" })).toEqual(["AUTO", "NORMal"]);
    expect(suggestId("Zhiyuan", "ZY-DSO3104")).toBe("zhiyuan-zy-dso3104");
  });
});

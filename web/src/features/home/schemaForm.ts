// The "new instrument" form over a template's inputs (D3 layer 2, P2-5): the template's own JSON
// Schema decides the fields, its titles (Chinese and English) and units label them, and the
// template's checks judge the result on the server — this module only reads the schema, writes a
// value at a path and finds which diagnostics belong to a field. Pure: unit-tested.

export type JsonSchema = {
  $ref?: string;
  $defs?: Record<string, JsonSchema>;
  type?: string | string[];
  anyOf?: JsonSchema[];
  properties?: Record<string, JsonSchema>;
  required?: string[];
  items?: JsonSchema;
  prefixItems?: JsonSchema[];
  enum?: unknown[];
  title?: string;
  title_en?: string;
  description?: string;
  unit?: string;
  default?: unknown;
};

/** What a schema node is, after `$ref` and an optional (`anyOf: [X, null]`) are resolved. */
export type FieldKind =
  | { kind: "object"; properties: [string, JsonSchema][] }
  | { kind: "rows"; columns: [string, JsonSchema][] } // a list of flat objects: a table
  | { kind: "tuple"; items: JsonSchema[] } // [min, max]
  | { kind: "list"; item: JsonSchema } // a list of scalars: comma-separated
  | { kind: "enum"; options: string[] }
  | { kind: "boolean" }
  | { kind: "number"; integer: boolean }
  | { kind: "text" }
  | { kind: "json" }; // anything else (a list of lists): edited as JSON text

export function resolve(node: JsonSchema, root: JsonSchema): { node: JsonSchema; optional: boolean } {
  let optional = false;
  let n = node;
  for (let guard = 0; guard < 8; guard++) {
    if (n.$ref) {
      const name = n.$ref.split("/").pop() ?? "";
      // The field's own title and unit win over the referenced definition's.
      n = { ...(root.$defs?.[name] ?? {}), ...without(n, "$ref") };
      continue;
    }
    if (n.anyOf) {
      const real = n.anyOf.filter((a) => a.type !== "null");
      optional ||= real.length < n.anyOf.length;
      if (real.length === 1) {
        n = { ...real[0], ...without(n, "anyOf") };
        continue;
      }
    }
    break;
  }
  return { node: n, optional };
}

function without(n: JsonSchema, key: "$ref" | "anyOf"): JsonSchema {
  const rest = { ...n };
  delete rest[key];
  return rest;
}

const SCALAR = new Set(["string", "number", "integer", "boolean"]);

export function fieldKind(node: JsonSchema, root: JsonSchema): FieldKind {
  const n = resolve(node, root).node;
  if (n.enum) return { kind: "enum", options: n.enum.map(String) };
  if (n.type === "object" && n.properties) return { kind: "object", properties: Object.entries(n.properties) };
  if (n.type === "array") {
    if (n.prefixItems) return { kind: "tuple", items: n.prefixItems };
    const item = n.items ? resolve(n.items, root).node : {};
    if (item.type === "object" && item.properties) {
      const columns = Object.entries(item.properties);
      if (columns.every(([, c]) => SCALAR.has(String(resolve(c, root).node.type)) || resolve(c, root).node.enum))
        return { kind: "rows", columns };
    }
    if (SCALAR.has(String(item.type)) || item.enum) return { kind: "list", item };
    return { kind: "json" };
  }
  if (n.type === "boolean") return { kind: "boolean" };
  if (n.type === "number" || n.type === "integer") return { kind: "number", integer: n.type === "integer" };
  if (n.type === "string") return { kind: "text" };
  return { kind: "json" };
}

export type Path = (string | number)[];

/** `value` with `v` at `path` (copies along the path; `undefined` removes the key). */
export function setAt(value: unknown, path: Path, v: unknown): unknown {
  if (!path.length) return v;
  const [head, ...rest] = path;
  if (typeof head === "number") {
    const list = Array.isArray(value) ? [...value] : [];
    list[head] = setAt(list[head], rest, v);
    return list;
  }
  const obj = value && typeof value === "object" && !Array.isArray(value) ? { ...(value as Record<string, unknown>) } : {};
  const next = setAt(obj[head], rest, v);
  if (next === undefined) delete obj[head];
  else obj[head] = next;
  return obj;
}

export function getAt(value: unknown, path: Path): unknown {
  let v = value;
  for (const p of path) {
    if (v == null || typeof v !== "object") return undefined;
    v = (v as Record<string | number, unknown>)[p];
  }
  return v;
}

/** The JSON pointer a template diagnostic names for a field (`/template/inputs/a/0/b`). */
export function pointerOf(path: Path): string {
  return ["/template/inputs", ...path.map((p) => String(p).replace(/~/g, "~0").replace(/\//g, "~1"))].join("/");
}

/** The diagnostics on this field itself (not on a field inside it). */
export function problemsAt<T extends { pointer: string }>(problems: readonly T[], path: Path): T[] {
  const p = pointerOf(path);
  return problems.filter((d) => d.pointer === p);
}

/** A list of scalars as typed, comma-separated: numbers where the item is one. */
export function parseList(text: string, item: JsonSchema): unknown[] | null {
  const parts = text
    .split(/[,，]/)
    .map((s) => s.trim())
    .filter(Boolean);
  if (item.type === "number" || item.type === "integer") {
    const nums = parts.map(Number);
    return nums.every(Number.isFinite) ? nums : null;
  }
  return parts;
}

/** A suggested definition id from the maker and model: lower case, words joined by "-". */
export function suggestId(manufacturer: string, model: string): string {
  return `${manufacturer} ${model}`
    .toLowerCase()
    .replace(/[^a-z0-9.]+/g, "-")
    .replace(/^-+|-+$/g, "")
    .slice(0, 48);
}

import { pickLabel } from "@/i18n";

/** What the API says about a component type's names (#1251): `ComponentDoc`, `InternalComponent`. */
export interface TypeNames {
  type: string;
  label?: string | null;
  label_en?: string | null;
  about?: string | null;
  about_en?: string | null;
}

/** The type's name in the UI's language; the bare type only for a type that has none (a plugin's). */
export function typeName(c: TypeNames): string {
  return pickLabel(c.label, c.label_en, c.type);
}

/** One sentence on what the type does, in the UI's language. */
export function typeAbout(c: TypeNames): string | null {
  return pickLabel(c.about, c.about_en) || null;
}

/** The fields a search over component types matches: the type, both names, both sentences, the docstring. */
export function typeSearchText(c: TypeNames & { doc?: string | null }): string[] {
  return [c.type, c.label, c.label_en, c.about, c.about_en, c.doc].filter((s): s is string => !!s);
}

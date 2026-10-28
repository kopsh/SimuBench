import createClient, { type Middleware } from "openapi-fetch";
import type { components, paths } from "./generated/openapi";
import { getToken } from "./token";

export type Schemas = components["schemas"];
export type SessionInfo = Schemas["SessionInfo"];
export type InstrumentInfo = Schemas["InstrumentInfo"];
export type StateVarInfo = Schemas["StateVarInfo"];
export type ComponentInfo = Schemas["ComponentInfo"];
export type InstrumentInternals = Schemas["InstrumentInternals"];
export type InternalComponent = Schemas["InternalComponent"];
export type WireInfo = Schemas["WireInfo"];
export type BenchFile = Schemas["BenchFile"];
export type ScopeScreen = Schemas["ScopeScreen"];
export type Trace = Schemas["Trace"];
export type NetLevel = Schemas["NetLevel"];
export type ScpiResponse = Schemas["ScpiResponse"];
export type ConfigureResponse = Schemas["ConfigureResponse"];
export type BenchDocument = Schemas["BenchDocument"];
export type BenchPreview = Schemas["BenchPreview"];
export type Catalog = Schemas["Catalog"];
export type ComponentTypeInfo = Schemas["ComponentTypeInfo"];
export type DefinitionSummary = Schemas["DefinitionSummary"];
export type Diagnostic = Schemas["Diagnostic"];
export type EditOp = Schemas["EditBenchRequest"]["ops"][number];
export type BenchData = Record<string, unknown>;
export type FaultSpec = Schemas["Fault"];
export type FaultInfo = Schemas["FaultInfo"];

export class ApiError extends Error {
  constructor(
    public status: number,
    message: string,
    /** The service's structured detail, e.g. diagnostics (422) or the current hash (409). */
    public detail?: unknown,
  ) {
    super(message);
  }
}

const auth: Middleware = {
  onRequest({ request }) {
    const t = getToken();
    if (t) request.headers.set("x-simubench-token", t);
    return request;
  },
};

export const api = createClient<paths>({ baseUrl: "" });
api.use(auth);

/** Unwrap an openapi-fetch result into data or a thrown ApiError. */
export async function unwrap<T>(p: Promise<{ data?: T; error?: unknown; response: Response }>): Promise<T> {
  let res;
  try {
    res = await p;
  } catch {
    throw new ApiError(0, "network");
  }
  if (res.error !== undefined || !res.response.ok) {
    const detail = (res.error as { detail?: unknown } | undefined)?.detail;
    const message =
      typeof detail === "string"
        ? detail
        : typeof (detail as { message?: unknown } | undefined)?.message === "string"
          ? (detail as { message: string }).message
          : res.response.statusText;
    throw new ApiError(res.response.status, message, detail);
  }
  return res.data as T;
}

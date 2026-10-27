import createClient, { type Middleware } from "openapi-fetch";
import type { components, paths } from "./generated/openapi";
import { getToken } from "./token";

export type Schemas = components["schemas"];
export type SessionInfo = Schemas["SessionInfo"];
export type InstrumentInfo = Schemas["InstrumentInfo"];
export type StateVarInfo = Schemas["StateVarInfo"];
export type ComponentInfo = Schemas["ComponentInfo"];
export type WireInfo = Schemas["WireInfo"];
export type BenchFile = Schemas["BenchFile"];
export type ScopeScreen = Schemas["ScopeScreen"];
export type Trace = Schemas["Trace"];
export type ScpiResponse = Schemas["ScpiResponse"];
export type ConfigureResponse = Schemas["ConfigureResponse"];

export class ApiError extends Error {
  constructor(
    public status: number,
    message: string,
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
    throw new ApiError(res.response.status, typeof detail === "string" ? detail : res.response.statusText);
  }
  return res.data as T;
}

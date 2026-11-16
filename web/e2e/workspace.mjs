// The workspace e2e/serve.mjs builds for the end-to-end tests: a fixed directory under the OS
// temp folder, so a test can edit or remove a definition the way an agent would. One place for
// the path (#1216).
import { tmpdir } from "node:os";
import { join } from "node:path";

export const WORKSPACE = join(tmpdir(), "simubench-e2e-workspace");

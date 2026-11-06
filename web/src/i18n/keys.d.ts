/** `t()` keys typechecked against the Chinese resource (issue 639): a key the interface does not
 * have is a compile error, not a runtime string on screen. Dynamic families
 * (`t(\`scope.${x}\`)`) keep their `string` typing — those callers guard the families themselves. */
import type { Messages } from "./zh";

declare module "i18next" {
  interface CustomTypeOptions {
    resources: { translation: Messages };
  }
}

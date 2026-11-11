import js from "@eslint/js";
import reactHooks from "eslint-plugin-react-hooks";
import globals from "globals";
import tseslint from "typescript-eslint";

/** A test judges values and text by parsing and comparing them, never by a regular expression
 *  (D49, #926): Playwright's string `name` / `getByText` match a substring, `exact: true` the whole
 *  text, and a URL is checked by its parts. */
const NO_REGEX_IN_TESTS = [
  "error",
  { selector: "Literal[regex]", message: "Tests use no regular expressions: compare strings or parsed values (D49)." },
  {
    selector: "NewExpression[callee.name='RegExp'], CallExpression[callee.name='RegExp']",
    message: "Tests use no regular expressions: compare strings or parsed values (D49).",
  },
];

export default tseslint.config(
  { ignores: ["dist", "storybook-static", "src/api/generated", "src/components/ui"] },
  {
    extends: [js.configs.recommended, ...tseslint.configs.recommended],
    files: ["**/*.{ts,tsx}"],
    languageOptions: { ecmaVersion: 2022, globals: globals.browser },
    plugins: { "react-hooks": reactHooks },
    rules: {
      ...reactHooks.configs.recommended.rules,
      "@typescript-eslint/no-unused-vars": ["error", { argsIgnorePattern: "^_" }],
    },
  },
  {
    files: ["e2e/**/*.{ts,tsx,mjs}", "visual/**/*.{ts,tsx}", "**/*.test.{ts,tsx}", "**/*.spec.{ts,tsx}"],
    rules: { "no-restricted-syntax": NO_REGEX_IN_TESTS },
  },
);

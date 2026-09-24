import base from "./playwright.config";

/** Layout-review screenshots; see tests/screenshots.manual.ts. */
export default { ...base, testMatch: /screenshots\.manual\.ts$/, reporter: "list" };

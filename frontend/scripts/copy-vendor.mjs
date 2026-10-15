// Copy the prebuilt files Sympraxi serves from its own domain. Nothing loads
// from a CDN: self-hosted installs make no third-party requests.
import { cpSync, mkdirSync, rmSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

const here = dirname(fileURLToPath(import.meta.url));
const modules = join(here, "..", "node_modules");
const target = join(here, "..", "..", "sympraxi", "static", "vendor");

rmSync(target, { recursive: true, force: true });

const copies = [
  ["@uswds/uswds/dist/css/uswds.min.css", "uswds/css/uswds.min.css"],
  ["@uswds/uswds/dist/js/uswds.min.js", "uswds/js/uswds.min.js"],
  ["@uswds/uswds/dist/js/uswds-init.min.js", "uswds/js/uswds-init.min.js"],
  ["@uswds/uswds/dist/fonts", "uswds/fonts"],
  ["@uswds/uswds/dist/img", "uswds/img"],
  ["@uswds/uswds/LICENSE.md", "uswds/LICENSE.md"],
  ["htmx.org/dist/htmx.min.js", "htmx/htmx.min.js"],
  ["htmx.org/LICENSE", "htmx/LICENSE"],
];

for (const [from, to] of copies) {
  const dest = join(target, to);
  mkdirSync(dirname(dest), { recursive: true });
  cpSync(join(modules, from), dest, { recursive: true });
  console.log(`vendor/${to}`);
}

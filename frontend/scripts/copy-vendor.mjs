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
  // The source maps come along because the minified files reference them, and
  // Django's manifest storage refuses to build when a referenced file is missing.
  ["@uswds/uswds/dist/css/uswds.min.css", "uswds/css/uswds.min.css"],
  ["@uswds/uswds/dist/css/uswds.min.css.map", "uswds/css/uswds.min.css.map"],
  ["@uswds/uswds/dist/js/uswds.min.js", "uswds/js/uswds.min.js"],
  ["@uswds/uswds/dist/js/uswds.min.js.map", "uswds/js/uswds.min.js.map"],
  ["@uswds/uswds/dist/js/uswds-init.min.js", "uswds/js/uswds-init.min.js"],
  ["@uswds/uswds/dist/js/uswds-init.min.js.map", "uswds/js/uswds-init.min.js.map"],
  ["@uswds/uswds/dist/fonts", "uswds/fonts"],
  ["@uswds/uswds/dist/img", "uswds/img"],
  ["@uswds/uswds/LICENSE.md", "uswds/LICENSE.md"],
  // The site's two typefaces, as variable fonts under the SIL Open Font License. Only the
  // Latin and Latin Extended pieces, which cover English, Spanish and the names of students
  // from most of the world, come along; the page asks no one else for a font.
  ["@fontsource-variable/source-serif-4/files/source-serif-4-latin-wght-normal.woff2", "fonts/source-serif-4-latin-wght-normal.woff2"],
  ["@fontsource-variable/source-serif-4/files/source-serif-4-latin-wght-italic.woff2", "fonts/source-serif-4-latin-wght-italic.woff2"],
  ["@fontsource-variable/source-serif-4/files/source-serif-4-latin-ext-wght-normal.woff2", "fonts/source-serif-4-latin-ext-wght-normal.woff2"],
  ["@fontsource-variable/source-serif-4/LICENSE", "fonts/LICENSE-source-serif-4.txt"],
  ["@fontsource-variable/inter/files/inter-latin-wght-normal.woff2", "fonts/inter-latin-wght-normal.woff2"],
  ["@fontsource-variable/inter/files/inter-latin-ext-wght-normal.woff2", "fonts/inter-latin-ext-wght-normal.woff2"],
  ["@fontsource-variable/inter/LICENSE", "fonts/LICENSE-inter.txt"],
  ["htmx.org/dist/htmx.min.js", "htmx/htmx.min.js"],
  ["htmx.org/LICENSE", "htmx/LICENSE"],
  // PDF.js draws PDFs for the comment viewer. The worker, the character maps, the
  // standard fonts, the color profile and the WebAssembly decoders load by name next
  // to the library, so they stay in one folder.
  ["pdfjs-dist/build/pdf.min.mjs", "pdfjs/pdf.min.mjs"],
  ["pdfjs-dist/build/pdf.worker.min.mjs", "pdfjs/pdf.worker.min.mjs"],
  ["pdfjs-dist/cmaps", "pdfjs/cmaps"],
  ["pdfjs-dist/standard_fonts", "pdfjs/standard_fonts"],
  ["pdfjs-dist/iccs", "pdfjs/iccs"],
  ["pdfjs-dist/wasm", "pdfjs/wasm"],
  ["pdfjs-dist/LICENSE", "pdfjs/LICENSE"],
];

for (const [from, to] of copies) {
  const dest = join(target, to);
  mkdirSync(dirname(dest), { recursive: true });
  cpSync(join(modules, from), dest, { recursive: true });
  console.log(`vendor/${to}`);
}

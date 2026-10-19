// Bundle the shared-document page's script (Tiptap, Yjs and the Hocuspocus provider
// with the glue in src/room.js) into one file served from our own domain. The
// licenses of what went in are written next to it (LICENSES.txt).
import { build } from "esbuild";
import { readFileSync, writeFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

const here = dirname(fileURLToPath(import.meta.url));

await build({
  entryPoints: [join(here, "..", "src", "room.js")],
  outfile: join(here, "..", "..", "sympraxi", "static", "vendor", "room", "room.js"),
  bundle: true,
  minify: true,
  format: "esm",
  target: "es2022",
  legalComments: "external",
  logLevel: "info",
});

// Each package's own license text, in one file next to the bundle.
const modules = join(here, "..", "node_modules");
const notices = [
  ["@tiptap/core", "LICENSE.md"],
  ["yjs", "LICENSE"],
  ["@hocuspocus/provider", "LICENSE.md"],
].map(([name, file]) => {
  const text = readFileSync(join(modules, name, file), "utf8").trim();
  return `${name}\n${"=".repeat(name.length)}\n${text}\n`;
});
writeFileSync(
  join(here, "..", "..", "sympraxi", "static", "vendor", "room", "LICENSES.txt"),
  [
    "The shared-document editor bundles Tiptap, Yjs and the Hocuspocus provider, and\n" +
      "ProseMirror, y-protocols and their dependencies, under their MIT and similar licenses.\n" +
      "The notices of the three main packages follow.\n",
    ...notices,
  ].join("\n"),
);


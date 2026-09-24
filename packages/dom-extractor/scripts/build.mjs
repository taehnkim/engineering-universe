import { copyFile, mkdir, readFile, writeFile } from "node:fs/promises";
import { build } from "esbuild";

await mkdir(new URL("../dist/", import.meta.url), { recursive: true });
await build({
  entryPoints: [new URL("../src/index.js", import.meta.url).pathname],
  outfile: new URL("../dist/index.js", import.meta.url).pathname,
  bundle: true,
  minify: true,
  platform: "node",
  format: "esm",
  target: "node20",
  legalComments: "none",
});
await Promise.all([
  copyFile(new URL("../src/model.weights.bin", import.meta.url),
    new URL("../dist/model.weights.bin", import.meta.url)),
  copyFile(new URL("../src/index.d.ts", import.meta.url),
    new URL("../dist/index.d.ts", import.meta.url)),
]);

const bundledPackages = [
  "linkedom", "htmlparser2", "css-select", "cssom", "html-escaper", "uhyphen",
  "domhandler", "domutils", "dom-serializer", "entities", "nth-check", "boolbase",
];
const notices = [];
for (const name of bundledPackages) {
  const base = new URL(`../node_modules/${name}/`, import.meta.url);
  const metadata = JSON.parse(await readFile(new URL("package.json", base), "utf8"));
  let licenseText = "";
  for (const filename of ["LICENSE", "LICENSE.txt"]) {
    try {
      licenseText = await readFile(new URL(filename, base), "utf8");
      break;
    } catch (error) {
      if (error.code !== "ENOENT") throw error;
    }
  }
  if (!licenseText && name === "boolbase") {
    licenseText = `Copyright (c) Felix Boehm

Permission to use, copy, modify, and/or distribute this software for any purpose
with or without fee is hereby granted, provided that the above copyright notice
and this permission notice appear in all copies.

THE SOFTWARE IS PROVIDED "AS IS" AND THE AUTHOR DISCLAIMS ALL WARRANTIES WITH
REGARD TO THIS SOFTWARE INCLUDING ALL IMPLIED WARRANTIES OF MERCHANTABILITY
AND FITNESS. IN NO EVENT SHALL THE AUTHOR BE LIABLE FOR ANY SPECIAL, DIRECT,
INDIRECT, OR CONSEQUENTIAL DAMAGES OR ANY DAMAGES WHATSOEVER RESULTING FROM
LOSS OF USE, DATA OR PROFITS, WHETHER IN AN ACTION OF CONTRACT, NEGLIGENCE OR
OTHER TORTIOUS ACTION, ARISING OUT OF OR IN CONNECTION WITH THE USE OR
PERFORMANCE OF THIS SOFTWARE.`;
  }
  notices.push(`${name} ${metadata.version} (${metadata.license})\n${licenseText}`);
}
await writeFile(new URL("../dist/THIRD_PARTY_LICENSES.txt", import.meta.url),
  notices.join("\n\n-----\n\n") + "\n");

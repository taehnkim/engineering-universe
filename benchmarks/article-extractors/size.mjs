import { spawnSync } from "node:child_process";
import { mkdir, readdir, readFile, stat, writeFile } from "node:fs/promises";
import path from "node:path";
import { fileURLToPath } from "node:url";

const here = fileURLToPath(new URL(".", import.meta.url));
const root = path.resolve(here, "../..");
const lock = JSON.parse(await readFile(path.join(here, "package-lock.json"), "utf8"));
const packages = lock.packages;

async function bytesIn(directory) {
  let total = 0;
  for (const entry of await readdir(directory, { withFileTypes: true })) {
    // uv creates bytecode when these benchmark runners import their libraries.
    // It is not part of the installed distribution.
    if (entry.name === "__pycache__" || entry.name.endsWith(".pyc")) continue;
    const filename = path.join(directory, entry.name);
    if (entry.isDirectory()) total += await bytesIn(filename);
    else if (entry.isFile()) total += (await stat(filename)).size;
  }
  return total;
}

function resolveDependency(from, name) {
  let location = from;
  while (location) {
    const candidate = `${location}/node_modules/${name}`;
    if (packages[candidate]) return candidate;
    const previous = location.lastIndexOf("/node_modules/");
    location = previous < 0 ? "" : location.slice(0, previous);
  }
  const top = `node_modules/${name}`;
  if (packages[top]) return top;
  throw new Error(`Cannot resolve ${name} from ${from}`);
}

async function npmClosure(roots) {
  const pending = roots.map((name) => resolveDependency("", name));
  const seen = new Set();
  while (pending.length) {
    const location = pending.pop();
    if (seen.has(location)) continue;
    seen.add(location);
    const metadata = packages[location];
    for (const name of Object.keys(metadata.dependencies ?? {})) {
      pending.push(resolveDependency(location, name));
    }
    for (const name of Object.keys(metadata.optionalDependencies ?? {})) {
      try { pending.push(resolveDependency(location, name)); } catch { /* Not installed on this OS. */ }
    }
  }
  let bytes = 0;
  for (const location of seen) bytes += await bytesIn(path.join(here, location));
  return { installedBytes: bytes, packages: seen.size };
}

const modelDir = path.join(root, "packages/dom-extractor");
const packed = spawnSync("npm", ["pack", "--dry-run", "--json", "--ignore-scripts"], {
  cwd: modelDir,
  encoding: "utf8",
  env: { ...process.env, npm_config_cache: path.join(here, ".local/npm-cache") },
});
if (packed.status !== 0) throw new Error(packed.stderr || packed.stdout);
const artifact = JSON.parse(packed.stdout)[0];

async function sitePackages(name) {
  const lib = path.join(here, "python", name, ".venv/lib");
  const python = (await readdir(lib)).find((entry) => entry.startsWith("python"));
  if (!python) throw new Error(`No Python environment for ${name}`);
  return bytesIn(path.join(lib, python, "site-packages"));
}

const result = {
  method: "Installed package files plus mandatory dependencies; no Node/Python interpreter or standard library. Generated Python bytecode excluded. Model includes bundled parser and weights.",
  libraries: {
    "dom-extractor": {
      installedBytes: artifact.unpackedSize,
      compressedTarballBytes: artifact.size,
      packages: 1,
    },
    readability: await npmClosure(["@mozilla/readability", "jsdom"]),
    extractus: await npmClosure(["@extractus/article-extractor"]),
    trafilatura: { installedBytes: await sitePackages("trafilatura") },
    newspaper: { installedBytes: await sitePackages("newspaper") },
  },
};
await mkdir(path.join(here, "results"), { recursive: true });
await writeFile(path.join(here, "results/sizes.json"), JSON.stringify(result, null, 2) + "\n");
console.log(JSON.stringify(result, null, 2));

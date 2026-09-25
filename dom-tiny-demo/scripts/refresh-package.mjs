import { spawnSync } from "node:child_process";
import { createHash } from "node:crypto";
import { readFile, stat, writeFile } from "node:fs/promises";
import { tmpdir } from "node:os";
import { dirname, join, resolve } from "node:path";
import { fileURLToPath } from "node:url";

const demoDir = resolve(dirname(fileURLToPath(import.meta.url)), "..");
const packageDir = resolve(demoDir, "../packages/dom-extractor");
const packageName = "@eng-universe/dom-extractor";
const lockPath = join(demoDir, "package-lock.json");
const npmCache = process.env.DOM_TINY_NPM_CACHE ?? join(tmpdir(), "dom-tiny-demo-npm-cache");

function run(args, cwd, capture = false) {
  const result = spawnSync("npm", args, {
    cwd,
    encoding: "utf8",
    env: { ...process.env, npm_config_cache: npmCache },
    stdio: capture ? ["inherit", "pipe", "inherit"] : "inherit",
  });
  if (result.error) throw result.error;
  if (result.status !== 0) throw new Error(`npm ${args.join(" ")} failed in ${cwd}`);
  return result.stdout;
}

try {
  await stat(join(packageDir, "node_modules", "esbuild"));
} catch (error) {
  if (error.code !== "ENOENT") throw error;
  run(["ci"], packageDir);
}

run(["run", "build"], packageDir);
const [packed] = JSON.parse(run(["pack", "--ignore-scripts", "--json"], packageDir, true));
if (!packed?.filename) throw new Error("npm pack did not return a tarball filename");

const tarball = join(packageDir, packed.filename);
const integrity = `sha512-${createHash("sha512")
  .update(await readFile(tarball)).digest("base64")}`;
const lock = JSON.parse(await readFile(lockPath, "utf8"));
const entry = lock.packages?.[`node_modules/${packageName}`];
const expected = `file:../packages/dom-extractor/${packed.filename}`;
if (lock.packages?.[""]?.dependencies?.[packageName] !== expected ||
    entry?.resolved !== expected) {
  throw new Error(`demo dependency must point to ${expected}`);
}
if (entry.integrity !== integrity) {
  entry.integrity = integrity;
  await writeFile(lockPath, `${JSON.stringify(lock, null, 2)}\n`);
}

run(["ci", "--offline"], demoDir);
const installed = await readFile(join(demoDir, "node_modules", packageName, "dist", "index.js"));
const built = await readFile(join(packageDir, "dist", "index.js"));
if (!installed.equals(built)) throw new Error("installed demo package differs from the local build");
console.log(`Demo now uses ${packageName}@${packed.version} from ${packed.filename}`);

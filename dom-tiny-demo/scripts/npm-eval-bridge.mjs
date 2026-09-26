import { createInterface } from "node:readline";
import { extract, modelVersion } from "@eng-universe/dom-extractor";

const input = createInterface({ input: process.stdin, crlfDelay: Infinity });
console.log(JSON.stringify({ ready: true, modelVersion }));

for await (const line of input) {
  try {
    const { html } = JSON.parse(line);
    const result = await extract(html, { include: ["source"] });
    console.log(JSON.stringify(result));
  } catch (error) {
    console.log(JSON.stringify({ error: error?.message ?? String(error) }));
  }
}

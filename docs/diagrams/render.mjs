// Render the hand-authored SVG diagrams to high-resolution PNG.
//
//   npm i sharp
//   node render.mjs            # all diagrams @2x
//   node render.mjs 3          # all diagrams @3x (print / large format)
//   node render.mjs 2 flow-topology
//
// The SVGs are the source of truth and are vector — they scale without limit.
// PNGs exist only for tools that cannot consume SVG (slide decks, some wikis).

import sharp from "sharp";
import { readFileSync, readdirSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

const DIR = dirname(fileURLToPath(import.meta.url));
const scale = Number(process.argv[2]) || 2;
const only = process.argv[3];

const targets = readdirSync(DIR)
  .filter((f) => f.endsWith(".svg"))
  .map((f) => f.replace(/\.svg$/, ""))
  .filter((n) => !only || n === only);

if (targets.length === 0) {
  console.error(only ? `No such diagram: ${only}` : "No .svg files found");
  process.exit(1);
}

for (const name of targets) {
  const svg = readFileSync(join(DIR, `${name}.svg`));
  const vb = svg.toString().match(/viewBox="0 0 (\d+(?:\.\d+)?) (\d+(?:\.\d+)?)"/);
  if (!vb) {
    console.error(`${name}: no viewBox — skipped`);
    continue;
  }
  const width = Math.round(Number(vb[1]) * scale);

  const info = await sharp(svg, { density: 72 * scale })
    .resize({ width })
    .png({ compressionLevel: 9 })
    .toFile(join(DIR, `${name}.png`));

  console.log(`${name}.png  ${info.width}×${info.height}  (@${scale}x)`);
}

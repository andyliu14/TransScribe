// Offline conversion through the same notation engine used by dl-librescore.
// Never route native notation through MIDI.
import fs from "node:fs/promises";
import path from "node:path";

// This engine predates Node's browser globals. Its own DOM shim needs a writable
// navigator, and its Emscripten loader must read WASM from disk rather than fetch
// a filesystem path. These changes are confined to this conversion subprocess.
Object.defineProperty(globalThis, "navigator", {
  value: {}, writable: true, configurable: true,
});
globalThis.fetch = undefined;
const imported = await import("webmscore");
const WebMscore = imported.default.default ?? imported.default;

const [input, output] = process.argv.slice(2);
if (!input || !output || path.resolve(input) === path.resolve(output)) {
  throw new Error("Usage: node convert.mjs INPUT OUTPUT (distinct files)");
}
const format = path.extname(input).slice(1).toLowerCase();
if (!["mscz", "mscx", "mxl", "xml", "musicxml"].includes(format)) {
  throw new Error("Only notation formats are accepted");
}
const outFormat = path.extname(output).toLowerCase();
if (![".mxl", ".mscz"].includes(outFormat)) {
  throw new Error("Output must be .mxl or .mscz");
}
await WebMscore.ready;
const score = await WebMscore.load(format, new Uint8Array(await fs.readFile(input)));
try {
  await score.setExcerptId(-1); // Always preserve the full score.
  const data = outFormat === ".mxl" ? await score.saveMxl() : await score.saveMsc("mscz");
  await fs.writeFile(output, data, { flag: "wx" });
} finally {
  score.destroy();
}

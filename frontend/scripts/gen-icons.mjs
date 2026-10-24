// Generates the PWA icons into public/ using only Node built-ins (zlib for PNG deflate).
// Run: npm run icons
import { deflateSync } from "node:zlib";
import { writeFileSync, mkdirSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

const OUT = join(dirname(fileURLToPath(import.meta.url)), "..", "public");
const BG = [0x2f, 0x5d, 0x50]; // --accent (deep calm green)
const FG = [0xf7, 0xf4, 0xee]; // warm off-white

const CRC_TABLE = Array.from({ length: 256 }, (_, n) => {
  let c = n;
  for (let k = 0; k < 8; k++) c = c & 1 ? 0xedb88320 ^ (c >>> 1) : c >>> 1;
  return c >>> 0;
});
function crc32(buf) {
  let c = 0xffffffff;
  for (const b of buf) c = CRC_TABLE[(c ^ b) & 0xff] ^ (c >>> 8);
  return (c ^ 0xffffffff) >>> 0;
}
function chunk(type, data) {
  const len = Buffer.alloc(4);
  len.writeUInt32BE(data.length);
  const td = Buffer.concat([Buffer.from(type, "ascii"), data]);
  const crc = Buffer.alloc(4);
  crc.writeUInt32BE(crc32(td));
  return Buffer.concat([len, td, crc]);
}
function png(size, pixel) {
  const raw = Buffer.alloc(size * (size * 4 + 1));
  for (let y = 0; y < size; y++) {
    raw[y * (size * 4 + 1)] = 0; // filter: none
    for (let x = 0; x < size; x++) {
      const [r, g, b, a] = pixel(x, y);
      const o = y * (size * 4 + 1) + 1 + x * 4;
      raw[o] = r; raw[o + 1] = g; raw[o + 2] = b; raw[o + 3] = a;
    }
  }
  const ihdr = Buffer.alloc(13);
  ihdr.writeUInt32BE(size, 0);
  ihdr.writeUInt32BE(size, 4);
  ihdr[8] = 8; ihdr[9] = 6; ihdr[10] = 0; ihdr[11] = 0; ihdr[12] = 0;
  return Buffer.concat([
    Buffer.from([0x89, 0x50, 0x4e, 0x47, 0x0d, 0x0a, 0x1a, 0x0a]),
    chunk("IHDR", ihdr),
    chunk("IDAT", deflateSync(raw, { level: 9 })),
    chunk("IEND", Buffer.alloc(0)),
  ]);
}

// Shapes in unit coordinates (0..1).
function inRoundedSquare(u, v, radius) {
  const cx = Math.min(Math.max(u, radius), 1 - radius);
  const cy = Math.min(Math.max(v, radius), 1 - radius);
  return (u - cx) ** 2 + (v - cy) ** 2 <= radius ** 2;
}
function inD(u, v, scale) {
  // Letter box centred, height 0.46 * scale.
  const h = 0.46 * scale, w = 0.38 * scale, t = 0.09 * scale;
  const x0 = 0.5 - w / 2 + 0.01 * scale, y0 = 0.5 - h / 2, y1 = 0.5 + h / 2, yc = 0.5;
  const xm = x0 + w * 0.42, rx = x0 + w - xm, ry = h / 2;
  const outer = (u >= x0 && u <= xm && v >= y0 && v <= y1) ||
    (u >= xm && ((u - xm) / rx) ** 2 + ((v - yc) / ry) ** 2 <= 1);
  const ix0 = x0 + t, irx = rx - t, iry = ry - t;
  const inner = (u >= ix0 && u <= xm && v >= y0 + t && v <= y1 - t) ||
    (u >= xm && ((u - xm) / irx) ** 2 + ((v - yc) / iry) ** 2 <= 1);
  return outer && !inner;
}
function icon(size, { maskable = false } = {}) {
  const S = 4; // supersampling
  const radius = maskable ? 0 : 0.22;
  const scale = maskable ? 0.8 : 1;
  return png(size, (x, y) => {
    let bg = 0, fg = 0;
    for (let sy = 0; sy < S; sy++) for (let sx = 0; sx < S; sx++) {
      const u = (x + (sx + 0.5) / S) / size, v = (y + (sy + 0.5) / S) / size;
      if (maskable || inRoundedSquare(u, v, radius)) {
        if (inD(u, v, scale)) fg++; else bg++;
      }
    }
    const n = S * S, cover = (bg + fg) / n;
    if (cover === 0) return [0, 0, 0, 0];
    const mix = (i) => Math.round((BG[i] * bg + FG[i] * fg) / (bg + fg));
    return [mix(0), mix(1), mix(2), Math.round(cover * 255)];
  });
}

mkdirSync(OUT, { recursive: true });
const files = {
  "pwa-192x192.png": icon(192),
  "pwa-512x512.png": icon(512),
  "maskable-512x512.png": icon(512, { maskable: true }),
  // iOS ignores transparency; a full-bleed square is what it expects (it rounds the corners itself).
  "apple-touch-icon.png": icon(180, { maskable: true }),
  "favicon-32x32.png": icon(32),
};
for (const [name, buf] of Object.entries(files)) {
  writeFileSync(join(OUT, name), buf);
  console.log(`wrote public/${name} (${buf.length} bytes)`);
}

#!/usr/bin/env node
'use strict';
/* Writes one QR PNG per wallet, made with the SAME vendored library the website uses (assets/vendor/qrcode.js).
 *   node scripts/gen_qr_png.js assets/qr          regenerate the fallback images from config.json
 *   node scripts/gen_qr_png.js /tmp/out --all     also disabled assets
 * Node built-ins only. The payload of each QR is exactly `qr_uri` (or the address) from config.json. Nothing is sent anywhere. */
const fs = require('fs'), path = require('path'), vm = require('vm'), zlib = require('zlib');
const root = path.resolve(__dirname, '..');

function loadLib() {
  const ctx = vm.createContext({});
  vm.runInContext(fs.readFileSync(path.join(root, 'assets/vendor/qrcode.js'), 'utf8'), ctx, { timeout: 5000 });
  if (typeof ctx.qrcode !== 'function') throw new Error('vendored qrcode library did not load');
  return ctx.qrcode;
}

const CRC = new Uint32Array(256);
for (let n = 0; n < 256; n++) { let c = n; for (let k = 0; k < 8; k++) c = c & 1 ? 0xEDB88320 ^ (c >>> 1) : c >>> 1; CRC[n] = c >>> 0; }
const crc32 = b => { let c = 0xFFFFFFFF; for (const x of b) c = CRC[(c ^ x) & 255] ^ (c >>> 8); return (c ^ 0xFFFFFFFF) >>> 0; };
function chunk(type, data) {
  const len = Buffer.alloc(4); len.writeUInt32BE(data.length);
  const body = Buffer.concat([Buffer.from(type, 'ascii'), data]);
  const crc = Buffer.alloc(4); crc.writeUInt32BE(crc32(body));
  return Buffer.concat([len, body, crc]);
}
function png(w, h, gray) {
  const raw = Buffer.alloc((w + 1) * h);
  for (let y = 0; y < h; y++) { raw[y * (w + 1)] = 0; Buffer.from(gray.buffer, gray.byteOffset + y * w, w).copy(raw, y * (w + 1) + 1); }
  const ihdr = Buffer.alloc(13); ihdr.writeUInt32BE(w, 0); ihdr.writeUInt32BE(h, 4); ihdr[8] = 8; /* 8-bit grayscale */
  return Buffer.concat([Buffer.from([137, 80, 78, 71, 13, 10, 26, 10]), chunk('IHDR', ihdr), chunk('IDAT', zlib.deflateSync(raw, { level: 9 })), chunk('IEND', Buffer.alloc(0))]);
}
function qrPng(lib, payload) {
  const q = lib(0, 'M'); q.addData(payload); q.make();
  const n = q.getModuleCount(), S = 10, M = 4, W = (n + 2 * M) * S, px = new Uint8Array(W * W).fill(255);
  for (let r = 0; r < n; r++) for (let c = 0; c < n; c++) if (q.isDark(r, c))
    for (let y = 0; y < S; y++) px.fill(0, ((r + M) * S + y) * W + (c + M) * S, ((r + M) * S + y) * W + (c + M) * S + S);
  return png(W, W, px);
}

const args = process.argv.slice(2), all = args.includes('--all'), out = args.find(a => !a.startsWith('--'));
if (!out) { console.error('usage: node scripts/gen_qr_png.js <output folder> [--all]'); process.exit(2); }
const cfg = JSON.parse(fs.readFileSync(path.join(root, 'config.json'), 'utf8')), lib = loadLib();
fs.mkdirSync(out, { recursive: true });
for (const a of cfg.assets) {
  if (a.enabled === false && !all) continue;
  if (!/^[A-Za-z0-9-]+$/.test(a.id)) throw new Error('unsafe asset id: ' + a.id);
  fs.writeFileSync(path.join(out, a.id + '.png'), qrPng(lib, a.qr_uri || a.address));
  console.log('wrote', path.join(out, a.id + '.png'));
}

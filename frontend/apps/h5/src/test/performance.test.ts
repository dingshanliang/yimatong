import { describe, it, expect } from "vitest";
import fs from "fs";
import path from "path";

/**
 * Performance budget test for H5 frontend.
 *
 * Targets:
 * - H5 scan page first-load JS < 500 KB (translates to ~1s on 3G)
 * - Largest individual chunk < 200 KB
 * - Total static assets (JS + CSS) < 700 KB
 */

describe("H5 build performance budget", () => {
  const nextDir = path.resolve(__dirname, "../../.next");
  const chunksDir = path.join(nextDir, "static/chunks");

  function getTotalSize(dir: string, ext: string): number {
    let total = 0;
    if (!fs.existsSync(dir)) return 0;
    for (const f of fs.readdirSync(dir)) {
      const p = path.join(dir, f);
      const stat = fs.statSync(p);
      if (stat.isDirectory()) {
        total += getTotalSize(p, ext);
      } else if (f.endsWith(ext)) {
        total += stat.size;
      }
    }
    return total;
  }

  function getLargestChunk(): { name: string; size: number } {
    let largest = { name: "", size: 0 };
    if (!fs.existsSync(chunksDir)) return largest;
    for (const f of fs.readdirSync(chunksDir)) {
      const p = path.join(chunksDir, f);
      const stat = fs.statSync(p);
      if (stat.isFile() && f.endsWith(".js") && stat.size > largest.size) {
        largest = { name: f, size: stat.size };
      }
    }
    return largest;
  }

  it("should have built output to measure", () => {
    expect(fs.existsSync(chunksDir)).toBe(true);
  });

  it("first-load JS total should be under 500 KB", () => {
    const jsTotal = getTotalSize(chunksDir, ".js");
    const kb = jsTotal / 1024;
    console.log(`H5 first-load JS: ${kb.toFixed(0)} KB`);
    expect(kb).toBeLessThan(500);
  });

  it("largest individual chunk should be under 200 KB", () => {
    const largest = getLargestChunk();
    const kb = largest.size / 1024;
    console.log(`H5 largest chunk (${largest.name}): ${kb.toFixed(0)} KB`);
    expect(kb).toBeLessThan(200);
  });

  it("total static assets (JS + CSS) should be under 700 KB", () => {
    const jsTotal = getTotalSize(chunksDir, ".js");
    const cssTotal = getTotalSize(path.join(nextDir, "static/css"), ".css");
    const kb = (jsTotal + cssTotal) / 1024;
    console.log(
      `H5 total static assets: ${kb.toFixed(0)} KB (JS: ${(jsTotal / 1024).toFixed(0)} KB, CSS: ${(cssTotal / 1024).toFixed(0)} KB)`,
    );
    expect(kb).toBeLessThan(700);
  });
});

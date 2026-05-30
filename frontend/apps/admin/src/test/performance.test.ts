import { describe, it, expect } from "vitest";
import fs from "fs";
import path from "path";

/**
 * Performance budget test for Admin frontend.
 *
 * Targets:
 * - Admin first-load JS < 1,500 KB (translates to ~2s on 3G)
 * - Largest individual chunk < 300 KB
 * - Total static assets (JS + CSS) < 2,000 KB
 */

describe("Admin build performance budget", () => {
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

  it("first-load JS total should be under 1,500 KB", () => {
    const jsTotal = getTotalSize(chunksDir, ".js");
    const kb = jsTotal / 1024;
    console.log(`Admin first-load JS: ${kb.toFixed(0)} KB`);
    expect(kb).toBeLessThan(1500);
  });

  it("largest individual chunk should be under 300 KB", () => {
    const largest = getLargestChunk();
    const kb = largest.size / 1024;
    console.log(`Admin largest chunk (${largest.name}): ${kb.toFixed(0)} KB`);
    expect(kb).toBeLessThan(300);
  });

  it("total static assets (JS + CSS) should be under 2,000 KB", () => {
    const jsTotal = getTotalSize(chunksDir, ".js");
    const cssTotal = getTotalSize(path.join(nextDir, "static/css"), ".css");
    const kb = (jsTotal + cssTotal) / 1024;
    console.log(`Admin total static assets: ${kb.toFixed(0)} KB (JS: ${(jsTotal / 1024).toFixed(0)} KB, CSS: ${(cssTotal / 1024).toFixed(0)} KB)`);
    expect(kb).toBeLessThan(2000);
  });
});

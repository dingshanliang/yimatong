import { describe, it, expect } from "vitest";
import fs from "fs";
import path from "path";

/**
 * Performance budget test for Admin frontend.
 *
 * Targets:
 * - Admin route first-load JS < 1,500 KB (translates to ~2s on 3G)
 * - Largest individual chunk < 300 KB
 * - Admin route first-load static assets (JS + CSS) < 2,000 KB
 */

describe("Admin build performance budget", () => {
  const nextDir = path.resolve(__dirname, "../../.next");
  const chunksDir = path.join(nextDir, "static/chunks");
  const appServerDir = path.join(nextDir, "server/app");

  function getFileSize(assetPath: string): number {
    const absolutePath = path.join(nextDir, assetPath);
    return fs.existsSync(absolutePath) ? fs.statSync(absolutePath).size : 0;
  }

  function findBuildManifests(dir: string): string[] {
    if (!fs.existsSync(dir)) return [];

    let manifests: string[] = [];
    for (const f of fs.readdirSync(dir)) {
      const p = path.join(dir, f);
      const stat = fs.statSync(p);
      if (stat.isDirectory()) {
        manifests = manifests.concat(findBuildManifests(p));
      } else if (f === "build-manifest.json") {
        manifests.push(p);
      }
    }
    return manifests;
  }

  function getRouteAssets(manifestPath: string): string[] {
    const manifest = JSON.parse(fs.readFileSync(manifestPath, "utf-8")) as {
      polyfillFiles?: string[];
      rootMainFiles?: string[];
      pages?: Record<string, string[]>;
    };
    const pageFiles = Object.values(manifest.pages || {}).flat();
    return Array.from(new Set([...(manifest.polyfillFiles || []), ...(manifest.rootMainFiles || []), ...pageFiles]));
  }

  function getLargestRouteAssetTotal(exts: string[]): number {
    return findBuildManifests(appServerDir).reduce((largest, manifestPath) => {
      const total = getRouteAssets(manifestPath)
        .filter((assetPath) => exts.some((ext) => assetPath.endsWith(ext)))
        .reduce((sum, assetPath) => sum + getFileSize(assetPath), 0);
      return Math.max(largest, total);
    }, 0);
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
    expect(findBuildManifests(appServerDir).length).toBeGreaterThan(0);
  });

  it("largest route first-load JS should be under 1,500 KB", () => {
    const jsTotal = getLargestRouteAssetTotal([".js"]);
    const kb = jsTotal / 1024;
    console.log(`Admin largest route first-load JS: ${kb.toFixed(0)} KB`);
    expect(kb).toBeLessThan(1500);
  });

  it("largest individual chunk should be under 300 KB", () => {
    const largest = getLargestChunk();
    const kb = largest.size / 1024;
    console.log(`Admin largest chunk (${largest.name}): ${kb.toFixed(0)} KB`);
    expect(kb).toBeLessThan(300);
  });

  it("largest route first-load static assets should be under 2,000 KB", () => {
    const total = getLargestRouteAssetTotal([".js", ".css"]);
    const kb = total / 1024;
    console.log(`Admin largest route first-load static assets: ${kb.toFixed(0)} KB`);
    expect(kb).toBeLessThan(2000);
  });
});

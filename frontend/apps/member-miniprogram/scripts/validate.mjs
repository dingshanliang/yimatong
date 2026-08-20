import { readFileSync } from "node:fs";
import { resolve } from "node:path";

const root = resolve(import.meta.dirname, "..");
const app = JSON.parse(readFileSync(resolve(root, "app.json"), "utf8"));
const project = JSON.parse(
  readFileSync(resolve(root, "project.config.json"), "utf8")
);
const source = readFileSync(resolve(root, "pages/member/index.js"), "utf8");

if (project.compileType !== "miniprogram")
  throw new Error("project must compile as a mini-program");
for (const page of ["pages/member/index", "pages/webview/index"]) {
  if (!app.pages.includes(page)) throw new Error(`missing page: ${page}`);
}
for (const required of [
  "miniprogram-session",
  "miniprogram-bind",
  "membership/coupons",
  "membership/notifications",
  "commerce-handoffs",
]) {
  if (!source.includes(required))
    throw new Error(`missing member journey capability: ${required}`);
}
if (
  /points|积分/i.test(source) ||
  /points|积分/i.test(
    readFileSync(resolve(root, "pages/member/index.wxml"), "utf8")
  )
) {
  throw new Error("first release must not reintroduce points");
}
console.log("member mini-program contract validated");

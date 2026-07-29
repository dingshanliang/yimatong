import assert from "node:assert/strict";
import test from "node:test";

import { primitives } from "../tokens.ts";

const STOPS = [50, 100, 200, 300, 400, 500, 600, 700, 800, 900, 950];

test("品牌色板：brand/accent 各含 50-950 共 11 档合法 hex", () => {
  for (const scale of [primitives.color.brand, primitives.color.accent]) {
    for (const stop of STOPS) {
      assert.match(scale[stop], /^#[0-9a-f]{6}$/i, `missing stop ${stop}`);
    }
  }
});

test("品牌锚点精确：绿 500=#16a34a，琥珀橙 500=#f59e0b", () => {
  assert.equal(primitives.color.brand[500], "#16a34a");
  assert.equal(primitives.color.accent[500], "#f59e0b");
});

test("间距全部为 4px 整数倍，且覆盖 4/8/12/16/24/32/48/64", () => {
  for (const [key, px] of Object.entries(primitives.space)) {
    assert.equal(px % 4, 0, `space.${key}=${px} 不是 4 的倍数`);
  }
  for (const expected of [4, 8, 12, 16, 24, 32, 48, 64]) {
    assert.ok(
      Object.values(primitives.space).includes(expected),
      `缺少间距 ${expected}`
    );
  }
});

test("方向 A 圆角刻度 sm6/md10/lg14/xl18 + pill", () => {
  assert.deepEqual(primitives.radius, {
    sm: 6,
    md: 10,
    lg: 14,
    xl: 18,
    pill: 999,
  });
});

test("提供 sm/md/lg 三级柔和阴影", () => {
  assert.deepEqual(Object.keys(primitives.shadow).sort(), ["lg", "md", "sm"]);
  for (const value of Object.values(primitives.shadow)) {
    assert.match(value, /rgb\(/);
  }
});

test("字号刻度 12/13/14/16/18/20/24/30/36", () => {
  assert.deepEqual(
    Object.values(primitives.font.size),
    [12, 13, 14, 16, 18, 20, 24, 30, 36]
  );
});

test("行高三档 1.3/1.6/1.4，字重 400/500/600/700", () => {
  assert.deepEqual(primitives.font.lineHeight, {
    heading: 1.3,
    dense: 1.4,
    body: 1.6,
  });
  assert.deepEqual(Object.values(primitives.font.weight), [400, 500, 600, 700]);
});

test("字体栈：body 系统字体优先含苹方，mono 为等宽栈", () => {
  assert.ok(primitives.font.family.body.includes("-apple-system"));
  assert.ok(primitives.font.family.body.includes("PingFang SC"));
  assert.match(primitives.font.family.mono, /mono/i);
});

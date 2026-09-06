/**
 * scan_token 按码隔离的本地存储。
 *
 * scan_token 绑定单个码（public_id）与其 live release，跨码复用必然 401
 * 甚至串码。历史实现存全局 `scan_token` 键，靠 401 自愈兜底；现改为
 * `yimatong:scan-token:{publicId}` 按码存取，并以"当前激活码"作用域供
 * axios 拦截器注入隐式凭证。旧全局键在设置作用域时清除（一次性迁移）。
 */

const KEY_PREFIX = "yimatong:scan-token:";
const LEGACY_GLOBAL_KEY = "scan_token";

let activeScope: string | null = null;

function keyFor(publicId: string): string {
  return `${KEY_PREFIX}${publicId}`;
}

function purgeLegacyGlobalKey(): void {
  try {
    window.localStorage.removeItem(LEGACY_GLOBAL_KEY);
  } catch {
    // 存储不可用时不阻断扫码链路。
  }
}

/** 标记当前页面正在服务的码；后续隐式注入只使用该码的凭证。 */
export function setScanTokenScope(publicId: string): void {
  activeScope = publicId;
  purgeLegacyGlobalKey();
}

/** 离开码页时清除作用域，防止其他页面误用上一个码的凭证。 */
export function clearScanTokenScope(): void {
  activeScope = null;
}

function currentScopeKey(): string | null {
  return activeScope ? keyFor(activeScope) : null;
}

/** 保存当前码的 scan_token；不带作用域时为无操作。 */
export function saveScanToken(publicId: string, token: string): void {
  if (!publicId || !token) return;
  try {
    window.localStorage.setItem(keyFor(publicId), token);
  } catch {
    // 隐私模式/存储禁用：token 仍可在内存中随 payload 传递。
  }
}

/**
 * 读取隐式注入用的 scan_token：仅限当前激活码的键。
 * activeScope 未设置（如非码页）时返回 null，绝不回退到其他码。
 */
export function readActiveScanToken(): string | null {
  const key = currentScopeKey();
  if (!key || typeof window === "undefined") return null;
  try {
    return window.localStorage.getItem(key);
  } catch {
    return null;
  }
}

/** 清除当前激活码的凭证（401 自愈）；无作用域时为无操作。 */
export function clearActiveScanToken(): void {
  const key = currentScopeKey();
  if (!key || typeof window === "undefined") return;
  try {
    window.localStorage.removeItem(key);
  } catch {
    // 清理失败无安全影响：token 有服务端时效。
  }
}

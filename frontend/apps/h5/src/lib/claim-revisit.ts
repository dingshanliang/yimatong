/**
 * 回访凭证存取：领取受理后保存，结果页/回访入口读取。
 *
 * 凭证仅绑定单笔 claim 与领取者主体、只授予该笔发放状态的读权限，且服务端
 * 7 天过期；存 localStorage 以覆盖"关掉页面隔天回来重扫"的回访场景
 * （Story 7）。不写入 URL（避免进入浏览器历史）。
 */

const KEY_PREFIX = "yimatong:claim-revisit:";

/** window 守卫 + 隐私模式容错的存储访问（读写清理共用一个形状）。 */
function withStorage<R>(run: (store: Storage) => R, fallback: R): R {
  if (typeof window === "undefined") return fallback;
  try {
    return run(window.localStorage);
  } catch {
    // 隐私模式/存储禁用时不阻断领取，结果页仍可用 scan_token 轮询。
    return fallback;
  }
}

export function saveClaimRevisitCredential(
  claimId: string,
  credential: string
): void {
  if (!claimId || !credential) return;
  withStorage(
    (store) => store.setItem(`${KEY_PREFIX}${claimId}`, credential),
    undefined
  );
}

export function readClaimRevisitCredential(claimId: string): string | null {
  if (!claimId) return null;
  return withStorage((store) => store.getItem(`${KEY_PREFIX}${claimId}`), null);
}

export function clearClaimRevisitCredential(claimId: string): void {
  if (!claimId) return;
  // 清理失败无安全影响：凭证本身带服务端时效。
  withStorage(
    (store) => store.removeItem(`${KEY_PREFIX}${claimId}`),
    undefined
  );
}

/** 每个码最近一笔领取的引用（回访入口用：重扫该码时提示"查看我的红包"）。 */
function latestClaimKey(publicId: string): string {
  return `${KEY_PREFIX}latest:${publicId}`;
}

export function saveLatestClaimRef(publicId: string, claimId: string): void {
  if (!publicId || !claimId) return;
  withStorage(
    (store) => store.setItem(latestClaimKey(publicId), claimId),
    undefined
  );
}

export function readLatestClaimRef(publicId: string): string | null {
  if (!publicId) return null;
  return withStorage((store) => store.getItem(latestClaimKey(publicId)), null);
}

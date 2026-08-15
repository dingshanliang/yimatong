/**
 * 回访凭证存取：领取受理后保存，结果页/回访入口读取。
 *
 * 凭证仅绑定单笔 claim 与领取者主体，存在 sessionStorage（随会话），
 * 不写入 URL 历史之外的持久存储。
 */

const KEY_PREFIX = "yimatong:claim-revisit:";

function claimRevisitKey(claimId: string): string {
  return `${KEY_PREFIX}${claimId}`;
}

export function saveClaimRevisitCredential(
  claimId: string,
  credential: string
): void {
  if (typeof window === "undefined" || !claimId || !credential) return;
  try {
    window.sessionStorage.setItem(claimRevisitKey(claimId), credential);
  } catch {
    // 隐私模式/存储禁用时不阻断领取，结果页仍可用 scan_token 轮询。
  }
}

export function readClaimRevisitCredential(claimId: string): string | null {
  if (typeof window === "undefined" || !claimId) return null;
  try {
    return window.sessionStorage.getItem(claimRevisitKey(claimId));
  } catch {
    return null;
  }
}

export function clearClaimRevisitCredential(claimId: string): void {
  if (typeof window === "undefined" || !claimId) return;
  try {
    window.sessionStorage.removeItem(claimRevisitKey(claimId));
  } catch {
    // 忽略：凭证本身带时效，清理失败无安全影响。
  }
}

/**
 * 微信授权后的自动续领意图。
 *
 * 用户点击领取 → 后端要求微信授权 → 跳出页面授权，回跳后凭新 scan_token
 * 自动续上刚才那笔领取，省掉"再点一次"。意图存 sessionStorage（仅本标签
 * 页可续，防跨页误触发），带 TTL 且读取即焚：只有明确保存过意图的领取
 * 才允许自动续领。
 */

const KEY_PREFIX = "yimatong:claim-resume:";
/** 授权往返通常几十秒；超时的意图视为过期，不再自动发起。 */
const RESUME_TTL_MS = 10 * 60 * 1000;

export interface ClaimResumeIntent {
  benefit_id: string;
  saved_at: number;
}

function storageKey(publicId: string): string {
  return `${KEY_PREFIX}${publicId}`;
}

export function saveClaimResumeIntent(
  publicId: string,
  benefitId: string
): void {
  if (!publicId || !benefitId) return;
  if (typeof window === "undefined") return;
  try {
    const intent: ClaimResumeIntent = {
      benefit_id: benefitId,
      saved_at: Date.now(),
    };
    window.sessionStorage.setItem(storageKey(publicId), JSON.stringify(intent));
  } catch {
    // 隐私模式/存储禁用：只损失自动续领，不阻断授权跳转。
  }
}

/**
 * 读取并消费续领意图。仅当意图与 benefitId 匹配且未过期时返回 true；
 * 无论是否可续，意图都会被清除（用后即焚，避免重复自动领取）。
 */
export function consumeClaimResumeIntent(
  publicId: string,
  benefitId: string,
  now = Date.now()
): boolean {
  if (!publicId || !benefitId) return false;
  if (typeof window === "undefined") return false;
  try {
    const raw = window.sessionStorage.getItem(storageKey(publicId));
    window.sessionStorage.removeItem(storageKey(publicId));
    if (!raw) return false;
    const parsed = JSON.parse(raw) as Partial<ClaimResumeIntent> | null;
    if (!parsed || typeof parsed.benefit_id !== "string") return false;
    if (
      parsed.benefit_id.trim().toLowerCase() !== benefitId.trim().toLowerCase()
    ) {
      return false;
    }
    if (
      typeof parsed.saved_at !== "number" ||
      now - parsed.saved_at > RESUME_TTL_MS
    ) {
      return false;
    }
    return true;
  } catch {
    return false;
  }
}

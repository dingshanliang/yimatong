/**
 * AI 助手 API — 资料识别、文案生成、页面方案、活动方案
 */

import api from "./api";
import { extractErrorMessage } from "./api";

const AI_REQUEST_TIMEOUT_MS = 60000;

// ──────────────────── Types ────────────────────

/** 从文本/图片提取的产品字段 */
export interface ExtractedFields {
  product_name?: string;
  origin?: string;
  weight?: string;
  shelf_life?: string;
  category?: string;
  confidence?: number;
  source?: string;
  [key: string]: unknown;
}

/** 提取结果 */
export interface ExtractResult {
  fields: ExtractedFields;
  generation_id: string;
}

/** 文案类型 */
export type CopywritingType = "brand_story" | "selling_points";

/** 文案生成结果 */
export type CopywritingItem = string | { title?: string; detail?: string; [key: string]: unknown };

export interface CopywritingResult {
  content: string | { items: CopywritingItem[] };
  generation_id: string;
}

/** 页面文案方案结果 */
export interface PageCopyResult {
  result: {
    copywriting: {
      brand_story: string;
      selling_points: Array<{ title: string; detail: string }>;
      hero_subtitle?: string;
    };
    recommended_template: {
      template_type: string;
      name: string;
    };
    page_suggestion: {
      modules: Array<{ id: string; type: string; enabled: boolean }>;
    };
  };
  generation_id: string;
}

/** 页面结构建议结果 */
export interface PageSuggestResult {
  suggestion: {
    modules?: string[];
    [key: string]: unknown;
  };
  generation_id: string;
}

/** 活动目标类型 */
export type CampaignGoal = "promotion" | "retention" | "brand_awareness" | "festival";

/** 活动方案结果 */
export interface CampaignResult {
  campaign: {
    name?: string;
    description?: string;
    duration?: string;
    rules?: string;
    prizes?: string[];
    [key: string]: unknown;
  };
  generation_id: string;
}

export interface AITargetContext {
  target_type?: string;
  target_id?: string;
}

// ──────────────────── API Functions ────────────────────

/** AI-01a: 从文本提取产品信息 */
export async function extractFromText(text: string): Promise<ExtractResult> {
  const { data } = await api.post<ExtractResult>("/ai/extract", { text }, { timeout: AI_REQUEST_TIMEOUT_MS });
  return data;
}

/** AI-01b: 从图片识别产品信息（需先上传到 MinIO） */
export async function recognizeImage(imageUrl: string, filename: string): Promise<ExtractResult> {
  const { data } = await api.post<ExtractResult>("/ai/recognize-image", {
    image_url: imageUrl,
    filename,
  }, { timeout: AI_REQUEST_TIMEOUT_MS });
  return data;
}

/** AI-01b: 上传本地图片并识别产品信息 */
export async function recognizeImageFile(file: File): Promise<ExtractResult> {
  const formData = new FormData();
  formData.append("file", file);
  const { data } = await api.post<ExtractResult>("/ai/recognize-image-upload", formData, {
    headers: { "Content-Type": "multipart/form-data" },
    timeout: AI_REQUEST_TIMEOUT_MS,
  });
  return data;
}

/** AI-02: 生成文案（品牌故事/产品卖点） */
export async function generateCopywriting(
  type: CopywritingType,
  productName: string,
  keywords: string[],
  target?: AITargetContext,
): Promise<CopywritingResult> {
  const { data } = await api.post<CopywritingResult>("/ai/copywriting", {
    type,
    product_name: productName,
    keywords,
    ...target,
  }, { timeout: AI_REQUEST_TIMEOUT_MS });
  return data;
}

/** AI-02+03: 生成页面文案和推荐模板 */
export async function generatePageCopy(
  productName: string,
  category: string,
  keywords: string[],
  target?: AITargetContext,
): Promise<PageCopyResult> {
  const { data } = await api.post<PageCopyResult>("/ai/page-copy", {
    product_name: productName,
    category,
    keywords,
    ...target,
  }, { timeout: AI_REQUEST_TIMEOUT_MS });
  return data;
}

/** AI-03: 页面结构建议 */
export async function suggestPageStructure(
  productName: string,
  category: string,
  target?: AITargetContext,
): Promise<PageSuggestResult> {
  const { data } = await api.post<PageSuggestResult>("/ai/page-suggest", {
    product_name: productName,
    category,
    ...target,
  }, { timeout: AI_REQUEST_TIMEOUT_MS });
  return data;
}

/** AI-04: 生成活动方案 */
export async function generateCampaign(
  productName: string,
  goal: CampaignGoal,
  targetAudience: string,
  target?: AITargetContext,
): Promise<CampaignResult> {
  const { data } = await api.post<CampaignResult>("/ai/campaign", {
    product_name: productName,
    goal,
    target_audience: targetAudience,
    ...target,
  }, { timeout: AI_REQUEST_TIMEOUT_MS });
  return data;
}

// ──────────────────── Error Helpers ────────────────────

/** 将 AI API 错误转换为用户友好的中文提示 */
export function getAIErrorMessage(err: unknown): string {
  if (typeof err === "object" && err !== null) {
    const axiosErr = err as {
      code?: string;
      message?: string;
      response?: { status?: number; data?: { detail?: string } };
    };
    if (axiosErr.code === "ECONNABORTED") return "AI 生成耗时较长，请稍后重试";
    const status = axiosErr.response?.status;
    if (status === 401) return "登录已过期，请重新登录后再试";
    if (status === 429) return "今日 AI 调用次数已达上限，请明天再试";
    if (status === 503) return "AI 服务暂时不可用，请稍后重试";
    if (status === 500) return "AI 服务出现错误，请稍后重试";
  }
  return extractErrorMessage(err, "AI 请求失败，请重试");
}

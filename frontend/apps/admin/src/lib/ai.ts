/**
 * AI 资料识别与文案生成 API
 */

import api from "./api";

/** 提取产品字段结果 */
export interface ExtractedFields {
  product_name?: string;
  origin?: string;
  weight?: string;
  shelf_life?: string;
  category?: string;
  confidence?: number;
  source?: string;
}

export interface ExtractResult {
  fields: ExtractedFields;
}

/** 页面文案生成结果 */
export interface PageCopyResult {
  copywriting: {
    brand_story: string;
    selling_points: { items: string[] };
  };
  recommended_template: {
    template_type: string;
    name: string;
  };
  page_suggestion: {
    modules: string[];
  };
}

/** 文案类型 */
export type CopywritingType = "brand_story" | "selling_points";

/** 从文本提取产品信息 */
export async function extractFromText(text: string): Promise<ExtractResult> {
  const { data } = await api.post<ExtractResult>("/ai/extract", { text });
  return data;
}

/** 从图片识别产品信息 */
export async function recognizeImage(file: File): Promise<ExtractResult> {
  const formData = new FormData();
  formData.append("file", file);
  const { data } = await api.post<ExtractResult>("/ai/recognize-image", formData, {
    headers: { "Content-Type": "multipart/form-data" },
  });
  return data;
}

/** 生成页面文案和推荐模板 */
export async function generatePageCopy(
  productName: string,
  category: string,
  keywords: string[],
): Promise<PageCopyResult> {
  const { data } = await api.post<PageCopyResult>("/ai/page-copy", {
    product_name: productName,
    category,
    keywords,
  });
  return data;
}

/** 生成文案 */
export async function generateCopywriting(
  type: CopywritingType,
  productName: string,
  keywords: string[],
): Promise<{ content: string | { items: string[] } }> {
  const { data } = await api.post<{ content: string | { items: string[] } }>("/ai/copywriting", {
    type,
    product_name: productName,
    keywords,
  });
  return data;
}

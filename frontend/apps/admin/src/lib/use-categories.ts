"use client";

import useSWR from "swr";

export interface CategoriesResponse {
  categories: string[];
}

/**
 * 获取当前租户品类列表。
 * SWR 自动缓存，使用全局 fetcher。
 */
export function useCategories() {
  const { data, isLoading, mutate } = useSWR<CategoriesResponse>(
    "/tenants/me/categories"
  );

  return {
    categories: data?.categories ?? [],
    loading: isLoading,
    mutate,
  };
}

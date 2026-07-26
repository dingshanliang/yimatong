import { useState, useEffect, useCallback } from "react";

interface UsePaginatedListOptions {
  pageSize?: number;
}

interface UsePaginatedListReturn<T> {
  items: T[];
  total: number;
  page: number;
  pageSize: number;
  loading: boolean;
  setPage: (p: number) => void;
  refresh: () => void;
  fetcher: (fetchFn: (params: { page: number; page_size: number }) => Promise<{ items: T[]; total: number }>) => void;
}

export function usePaginatedList<T>(
  fetchFn: (params: { page: number; page_size: number }) => Promise<{ items: T[]; total: number }>,
  deps: unknown[] = [],
  options: UsePaginatedListOptions = {}
): UsePaginatedListReturn<T> {
  const pageSize = options.pageSize ?? 20;
  const [items, setItems] = useState<T[]>([]);
  const [total, setTotal] = useState(0);
  const [page, setPage] = useState(1);
  const [loading, setLoading] = useState(false);
  const [refreshKey, setRefreshKey] = useState(0);

  const fetcher = useCallback(
    async (fn: typeof fetchFn) => {
      setLoading(true);
      try {
        const result = await fn({ page, page_size: pageSize });
        setItems(result.items || []);
        setTotal(result.total || 0);
      } finally {
        setLoading(false);
      }
    },
    [page, pageSize]
  );

  useEffect(() => {
    fetcher(fetchFn);
  }, [page, fetchFn, refreshKey, ...deps]);

  const refresh = useCallback(() => {
    setRefreshKey((k) => k + 1);
  }, []);

  return { items, total, page, pageSize, loading, setPage, refresh, fetcher };
}

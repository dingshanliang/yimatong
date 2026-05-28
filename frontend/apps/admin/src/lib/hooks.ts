import { useState, useEffect, useCallback } from "react";

export function usePaginatedList<T>(
  fetchFn: (params: { page: number; page_size: number }) => Promise<{ items: T[]; total: number }>,
  deps: unknown[] = [],
  pageSize = 20
) {
  const [items, setItems] = useState<T[]>([]);
  const [total, setTotal] = useState(0);
  const [page, setPage] = useState(1);
  const [loading, setLoading] = useState(false);
  const [refreshKey, setRefreshKey] = useState(0);

  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    fetchFn({ page, page_size: pageSize })
      .then((result) => {
        if (!cancelled) {
          setItems(result.items || []);
          setTotal(result.total || 0);
        }
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => { cancelled = true; };
  }, [page, pageSize, refreshKey, fetchFn, ...deps]);

  const refresh = useCallback(() => setRefreshKey((k) => k + 1), []);

  return { items, total, page, pageSize, loading, setPage, refresh };
}

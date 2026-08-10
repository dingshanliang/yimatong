import { useState, useEffect, useCallback, useRef } from "react";
import useSWR from "swr";
import api from "./api";

// ---------------------------------------------------------------------------
// Legacy hook — preserved for gradual migration
// ---------------------------------------------------------------------------

export function usePaginatedList<T>(
  fetchFn: (params: {
    page: number;
    page_size: number;
  }) => Promise<{ items: T[]; total: number }>,
  deps: unknown[] = [],
  pageSize = 20
) {
  const [items, setItems] = useState<T[]>([]);
  const [total, setTotal] = useState(0);
  const [page, setPage] = useState(1);
  const [loading, setLoading] = useState(false);
  const [refreshKey, setRefreshKey] = useState(0);

  const fetchFnRef = useRef(fetchFn);
  fetchFnRef.current = fetchFn;

  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    fetchFnRef
      .current({ page, page_size: pageSize })
      .then((result) => {
        if (!cancelled) {
          setItems(result.items || []);
          setTotal(result.total || 0);
        }
      })
      .catch(() => {
        if (!cancelled) {
          setItems([]);
          setTotal(0);
        }
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [page, pageSize, refreshKey, ...deps]);

  const refresh = useCallback(() => setRefreshKey((k) => k + 1), []);

  return { items, total, page, pageSize, loading, setPage, refresh };
}

// ---------------------------------------------------------------------------
// SWR-based hooks
// ---------------------------------------------------------------------------

interface PaginatedResponse<T> {
  items: T[];
  total: number;
}

/**
 * Generic CRUD hook backed by SWR.
 *
 * - List:   GET    {basePath}?page=1&page_size=20&{filters}
 * - Create: POST   {basePath}
 * - Update: PATCH  {basePath}/{id}
 * - Delete: DELETE  {basePath}/{id}
 */
export function useCrud<T extends { id: string }>(
  basePath: string,
  opts: { pageSize?: number } = {}
) {
  const { pageSize = 20 } = opts;
  const [page, setPage] = useState(1);
  const [filters, setFilters] = useState<Record<string, string | number>>({});

  const filterStr = new URLSearchParams(
    Object.entries(filters)
      .sort()
      .map(([k, v]) => [k, String(v)])
  ).toString();
  const swrKey = `${basePath}?page=${page}&page_size=${pageSize}${filterStr ? `&${filterStr}` : ""}`;

  const { data, error, isLoading, mutate } = useSWR<PaginatedResponse<T> | T[]>(
    swrKey
  );

  const items = Array.isArray(data) ? data : (data?.items ?? []);
  const total = Array.isArray(data) ? data.length : (data?.total ?? 0);

  const create = useCallback(
    (body: Record<string, unknown>) =>
      api.post(basePath, body).then(() => mutate()),
    [basePath, mutate]
  );

  const update = useCallback(
    (id: string, body: Record<string, unknown>) =>
      api.patch(`${basePath}/${id}`, body).then(() => mutate()),
    [basePath, mutate]
  );

  const remove = useCallback(
    (id: string) => api.delete(`${basePath}/${id}`).then(() => mutate()),
    [basePath, mutate]
  );

  const setFilter = useCallback((next: Record<string, string | number>) => {
    setFilters(next);
    setPage(1);
  }, []);

  const resetFilters = useCallback(() => {
    setFilters({});
    setPage(1);
  }, []);

  return {
    items,
    total,
    page,
    pageSize,
    loading: isLoading,
    error: error as unknown,
    filters,
    setPage,
    setFilter,
    resetFilters,
    mutate,
    retry: () => mutate(),
    create,
    update,
    remove,
  };
}

/**
 * Single-item fetch hook.
 */
export function useItem<T>(url: string | null) {
  const { data, isLoading, mutate } = useSWR<T>(url);
  return { data: data ?? null, loading: isLoading, mutate };
}

import api from "./api";

/** SWR fetcher: url → axios response data */
export const fetcher = <T = unknown>(url: string): Promise<T> =>
  api.get<T>(url).then((r) => r.data);

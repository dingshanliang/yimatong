import axios from "axios";

/** Only a received 4xx proves that this request was rejected before creation. */
export function shouldRotateTenantOpeningKey(error: unknown): boolean {
  if (!axios.isAxiosError(error)) return false;
  const status = error.response?.status;
  return status !== undefined && status >= 400 && status < 500;
}

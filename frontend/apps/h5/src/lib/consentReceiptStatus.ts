const RECEIPT_STATUS_RETRY_DELAYS_MS = [100, 250] as const;

type ReceiptStatusFailure = {
  response?: {
    status?: number;
    data?: { detail?: string };
  };
};

export type ReceiptStatusLoadResult<T> =
  | { kind: "success"; data: T }
  | { kind: "clear" }
  | { kind: "preserve" }
  | { kind: "cancelled" };

function failureKind(error: unknown): "retry" | "clear" | "preserve" {
  const response = (error as ReceiptStatusFailure)?.response;
  if (
    response?.status === 409 &&
    response.data?.detail === "consent_authority_retry"
  ) {
    return "retry";
  }
  if (
    (response?.status === 403 &&
      response.data?.detail === "consent_authority_denied") ||
    (response?.status === 404 &&
      response.data?.detail === "consent_authority_not_found") ||
    (response?.status === 409 &&
      response.data?.detail === "consent_authority_invalid")
  ) {
    return "clear";
  }
  return "preserve";
}

function wait(delayMs: number) {
  return new Promise<void>((resolve) => {
    window.setTimeout(resolve, delayMs);
  });
}

export async function loadConsentReceiptStatus<T>(
  request: () => Promise<{ data: T }>,
  isCurrent: () => boolean
): Promise<ReceiptStatusLoadResult<T>> {
  for (
    let attempt = 0;
    attempt <= RECEIPT_STATUS_RETRY_DELAYS_MS.length;
    attempt += 1
  ) {
    if (!isCurrent()) return { kind: "cancelled" };
    try {
      const response = await request();
      return isCurrent()
        ? { kind: "success", data: response.data }
        : { kind: "cancelled" };
    } catch (error) {
      if (!isCurrent()) return { kind: "cancelled" };
      const kind = failureKind(error);
      if (kind === "clear") return { kind: "clear" };
      if (kind === "preserve") return { kind: "preserve" };
      const delayMs = RECEIPT_STATUS_RETRY_DELAYS_MS[attempt];
      if (delayMs === undefined) return { kind: "preserve" };
      await wait(delayMs);
    }
  }
  return { kind: "preserve" };
}

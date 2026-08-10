interface TraceabilityConfig {
  fields?: string[];
}

interface TraceabilitySectionProps {
  codeData: Record<string, unknown>;
  config?: TraceabilityConfig;
}

interface TraceabilityBatch extends Record<string, unknown> {
  origin?: string | null;
  production_date?: string | null;
  expiry_date?: string | null;
  batch_code?: string | null;
}

const FIELD_LABELS: Record<string, string> = {
  origin: "产地",
  production_date: "生产日期",
  expiry_date: "保质期至",
  batch_code: "生产批次",
};

const DEFAULT_FIELDS = [
  "origin",
  "production_date",
  "expiry_date",
  "batch_code",
];

export function TraceabilitySection({
  codeData,
  config,
}: TraceabilitySectionProps) {
  const batch = codeData?.batch as TraceabilityBatch | undefined;
  const fields = config?.fields || DEFAULT_FIELDS;

  const data: Record<string, string> = {};
  for (const f of fields) {
    if (f === "origin")
      data[f] = batch ? batch.origin?.trim() || "未提供批次产地" : "";
    else if (f === "production_date")
      data[f] = (batch?.production_date as string) || "";
    else if (f === "expiry_date")
      data[f] = (batch?.expiry_date as string) || "";
    else if (f === "batch_code") data[f] = (batch?.batch_code as string) || "";
  }

  const hasData = Object.values(data).some(Boolean);

  // 明确空态：缺失权威溯源数据时不静默隐藏，给出明确提示（yimatong-zgb1.2）。
  if (!hasData) {
    return (
      <div className="mx-4 mt-3 rounded-2xl bg-surface p-4 shadow-sm">
        <h2 className="text-base font-semibold text-foreground">溯源信息</h2>
        <p className="mt-3 text-sm text-foreground-secondary">暂无溯源信息</p>
      </div>
    );
  }

  return (
    <div className="mx-4 mt-3 rounded-2xl bg-surface p-4 shadow-sm">
      <h2 className="text-base font-semibold text-foreground">溯源信息</h2>
      <div className="mt-3 space-y-2">
        {fields.map(
          (f) =>
            data[f] && (
              <div
                key={f}
                className="flex items-center justify-between text-sm"
              >
                <span className="text-foreground-secondary">
                  {FIELD_LABELS[f] || f}
                </span>
                <span className="font-medium text-foreground">{data[f]}</span>
              </div>
            )
        )}
        <div className="flex items-center justify-between text-sm">
          <span className="text-foreground-secondary">码编号</span>
          <span className="font-medium text-foreground">
            {(codeData?.public_id as string) || ""}
          </span>
        </div>
      </div>
    </div>
  );
}

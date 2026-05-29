interface TraceabilityConfig {
  fields?: string[];
}

interface TraceabilitySectionProps {
  codeData: Record<string, unknown>;
  config?: TraceabilityConfig;
}

const FIELD_LABELS: Record<string, string> = {
  origin: "产地",
  production_date: "生产日期",
  expiry_date: "保质期至",
  batch_no: "生产批次",
};

const DEFAULT_FIELDS = ["origin", "production_date", "expiry_date", "batch_no"];

export function TraceabilitySection({ codeData, config }: TraceabilitySectionProps) {
  const batch = codeData?.batch as Record<string, unknown> | undefined;
  const product = codeData?.product as Record<string, unknown> | undefined;
  const fields = config?.fields || DEFAULT_FIELDS;

  const data: Record<string, string> = {};
  for (const f of fields) {
    if (f === "origin") data[f] = (product?.origin as string) || (batch?.origin as string) || "";
    else if (f === "production_date") data[f] = (batch?.production_date as string) || "";
    else if (f === "expiry_date") data[f] = (batch?.expiry_date as string) || "";
    else if (f === "batch_no") data[f] = (batch?.batch_no as string) || "";
  }

  const hasData = Object.values(data).some(Boolean);
  if (!hasData) return null;

  return (
    <div className="mx-4 mt-3 rounded-2xl bg-white p-4 shadow-sm">
      <h2 className="text-base font-semibold text-gray-900">溯源信息</h2>
      <div className="mt-3 space-y-2">
        {fields.map(
          (f) =>
            data[f] && (
              <div key={f} className="flex items-center justify-between text-sm">
                <span className="text-gray-500">{FIELD_LABELS[f] || f}</span>
                <span className="font-medium text-gray-900">{data[f]}</span>
              </div>
            ),
        )}
        <div className="flex items-center justify-between text-sm">
          <span className="text-gray-500">码编号</span>
          <span className="font-medium text-gray-900">{(codeData?.public_id as string) || ""}</span>
        </div>
      </div>
    </div>
  );
}

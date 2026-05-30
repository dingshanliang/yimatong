export function formatDate(dateStr: string | null): string {
  if (!dateStr) return "-";
  try { return new Date(dateStr).toLocaleString("zh-CN"); }
  catch { return dateStr; }
}

export function maskId(id: string): string {
  if (!id || id.length <= 8) return id;
  return id.slice(0, 4) + "****" + id.slice(-4);
}

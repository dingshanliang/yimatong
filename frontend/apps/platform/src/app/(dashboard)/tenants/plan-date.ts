const CHINA_TIMEZONE = "Asia/Shanghai";

export function toChinaBusinessDate(instant: string): string {
  const parts = new Intl.DateTimeFormat("en-US", {
    timeZone: CHINA_TIMEZONE,
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
  }).formatToParts(new Date(instant));
  const get = (type: Intl.DateTimeFormatPartTypes) =>
    parts.find((part) => part.type === type)?.value;
  return `${get("year")}-${get("month")}-${get("day")}`;
}

export function planExpiryLabel(instant: string | null): string {
  return instant
    ? `至 ${toChinaBusinessDate(instant)}（北京时间当天 23:59:59）`
    : "长期有效";
}

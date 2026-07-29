function channelToLinear(channel: number): number {
  const normalized = channel / 255;
  return normalized <= 0.04045
    ? normalized / 12.92
    : ((normalized + 0.055) / 1.055) ** 2.4;
}

function relativeLuminance(hex: string): number {
  const normalized = hex.replace("#", "");
  if (!/^[0-9a-f]{6}$/i.test(normalized)) {
    throw new Error(`Only six-digit hex colors are supported: ${hex}`);
  }

  const [red, green, blue] = [0, 2, 4].map((offset) =>
    channelToLinear(Number.parseInt(normalized.slice(offset, offset + 2), 16))
  );
  return 0.2126 * red + 0.7152 * green + 0.0722 * blue;
}

export function contrastRatio(foreground: string, background: string): number {
  const foregroundLuminance = relativeLuminance(foreground);
  const backgroundLuminance = relativeLuminance(background);
  const lighter = Math.max(foregroundLuminance, backgroundLuminance);
  const darker = Math.min(foregroundLuminance, backgroundLuminance);
  return (lighter + 0.05) / (darker + 0.05);
}

export function pickAccessibleForeground(
  background: string,
  minimum = 4.5
): "#000000" | "#ffffff" {
  const blackRatio = contrastRatio("#000000", background);
  const whiteRatio = contrastRatio("#ffffff", background);
  const foreground = blackRatio >= whiteRatio ? "#000000" : "#ffffff";

  if (Math.max(blackRatio, whiteRatio) < minimum) {
    throw new Error(`No safe black/white foreground for ${background}`);
  }
  return foreground;
}

export function getTokenValue(source: object, path: string): string | number {
  const value = path.split(".").reduce<unknown>((current, segment) => {
    if (
      typeof current !== "object" ||
      current === null ||
      !(segment in current)
    ) {
      throw new Error(`Unknown token path: ${path}`);
    }
    return (current as Record<string, unknown>)[segment];
  }, source);

  if (typeof value !== "string" && typeof value !== "number") {
    throw new Error(`Token path does not resolve to a scalar: ${path}`);
  }
  return value;
}

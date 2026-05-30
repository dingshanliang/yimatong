export function yuanToFen(yuan: number): number {
  return Math.round(yuan * 100);
}

export function fenToYuan(fen: number): number {
  return fen / 100;
}

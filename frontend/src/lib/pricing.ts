/**
 * List prices in USD per million tokens (Gemini API, standard tier, November 2026). Thinking
 * tokens are billed as output. A free-tier key costs nothing; the estimate shows what the
 * same check would cost on a paid key.
 */
const PRICES: [prefix: string, input: number, output: number][] = [
  ["gemini-3.5-flash-lite", 0.3, 2.5],
  ["gemini-3.5-flash", 1.5, 9.0],
];

/** Estimated cost of one call, or 0 for a model without a known price. */
export function callCost(model: string | undefined, input: number, output: number): number {
  const price = PRICES.find(([prefix]) => model?.startsWith(prefix));
  return price ? (input * price[1] + output * price[2]) / 1_000_000 : 0;
}

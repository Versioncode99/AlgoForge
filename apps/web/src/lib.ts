export const money = (v: number) =>
  `${v < 0 ? '-' : ''}$${Math.abs(v).toLocaleString(undefined, { maximumFractionDigits: 2 })}`
export const pct = (v: number) => `${(v * 100).toFixed(1)}%`
export const num = (v: number) => v.toLocaleString()
export const signed = (v: number) => `${v >= 0 ? '+' : ''}${v.toFixed(2)}`
export const shortHash = (v: string) => (v ? v.slice(0, 10) : '—')
export const clock = (iso: string) => (iso ? iso.slice(11, 19) : '--:--:--')
export const day = (iso: string) => (iso ? iso.slice(0, 10) : '')
/** Date and time together. A trade list spanning sixteen years needs the date;
 *  a time alone makes every row look like it happened today. */
export const stamp = (iso: string) => (iso ? `${iso.slice(0, 10)} ${iso.slice(11, 16)}` : '—')

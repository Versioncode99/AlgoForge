export const money = (v: number) =>
  `${v < 0 ? '-' : ''}$${Math.abs(v).toLocaleString(undefined, { maximumFractionDigits: 2 })}`
export const pct = (v: number) => `${(v * 100).toFixed(1)}%`
export const num = (v: number) => v.toLocaleString()
export const signed = (v: number) => `${v >= 0 ? '+' : ''}${v.toFixed(2)}`
export const shortHash = (v: string) => (v ? v.slice(0, 10) : '—')
export const clock = (iso: string) => (iso ? iso.slice(11, 19) : '--:--:--')
export const day = (iso: string) => (iso ? iso.slice(0, 10) : '')

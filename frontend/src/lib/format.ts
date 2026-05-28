/** Date + number formatting helpers, replacing Angular's DatePipe / currency / number pipes. */

export function fmtShort(iso: string | null | undefined): string {
  if (!iso) return '—';
  return new Date(iso).toLocaleString(undefined, {
    dateStyle: 'short',
    timeStyle: 'short',
  });
}

export function fmtMedium(iso: string | null | undefined): string {
  if (!iso) return '—';
  return new Date(iso).toLocaleString(undefined, {
    dateStyle: 'medium',
    timeStyle: 'medium',
  });
}

export function fmtShortTime(iso: string | null | undefined): string {
  if (!iso) return '—';
  return new Date(iso).toLocaleString(undefined, { timeStyle: 'short' });
}

/** USD with 4 decimal places — matches the old `currency:'USD':'symbol':'1.4-4'`. */
export function fmtUsd(n: number): string {
  return new Intl.NumberFormat(undefined, {
    style: 'currency',
    currency: 'USD',
    minimumFractionDigits: 4,
    maximumFractionDigits: 4,
  }).format(n);
}

/** Thousands-separated integer — matches the old `number` pipe. */
export function fmtNum(n: number): string {
  return new Intl.NumberFormat().format(n);
}

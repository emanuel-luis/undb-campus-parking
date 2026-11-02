export type Cell = string | number | null | undefined

export interface Column<T> {
  label: string
  value: (row: T) => Cell
}

/** UTF-8 byte order mark: without it Excel reads the file as Windows-1252 and breaks the accents. */
export const BOM = String.fromCharCode(0xfeff)

const decimal = new Intl.NumberFormat('pt-BR', { useGrouping: false, maximumFractionDigits: 6 })

function cell(v: Cell): string {
  if (v === null || v === undefined) return ''
  const text = typeof v === 'number' ? decimal.format(v) : v
  return /[";\r\n]/.test(text) ? `"${text.replace(/"/g, '""')}"` : text
}

/** CSV that Excel pt-BR opens as columns: `;` separator, decimal comma, CRLF and a UTF-8 BOM. */
export function toCsv<T>(columns: Column<T>[], rows: T[]): string {
  const lines = [columns.map((c) => cell(c.label)), ...rows.map((r) => columns.map((c) => cell(c.value(r))))]
  return `${BOM}${lines.map((l) => l.join(';')).join('\r\n')}\r\n`
}

export function downloadCsv(filename: string, csv: string): void {
  const url = URL.createObjectURL(new Blob([csv], { type: 'text/csv;charset=utf-8' }))
  const a = document.createElement('a')
  a.href = url
  a.download = filename
  a.click()
  URL.revokeObjectURL(url)
}

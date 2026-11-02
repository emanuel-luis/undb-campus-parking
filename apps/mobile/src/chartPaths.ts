// Pure geometry for the hand-drawn SVG charts, so the exact output is testable.

const r1 = (n: number): number => Math.round(n * 10) / 10

/** Map a value in [0, max] to a y coordinate in [height, 0]. */
export function yScale(value: number, max: number, height: number): number {
  return max <= 0 ? height : r1(height - (value / max) * height)
}

/** x of point i out of n spread over width (a single point sits in the middle). */
export function xAt(i: number, n: number, width: number): number {
  return n <= 1 ? r1(width / 2) : r1((i / (n - 1)) * width)
}

/** SVG path through the values; a null breaks the line and the next value starts a new segment. */
export function linePath(values: (number | null)[], width: number, height: number, max: number): string {
  let path = ''
  let pen = false
  values.forEach((v, i) => {
    if (v === null) {
      pen = false
      return
    }
    path += `${pen ? 'L' : 'M'}${xAt(i, values.length, width)} ${yScale(v, max, height)}`
    pen = true
  })
  return path
}

export interface Bar {
  x: number
  y: number
  w: number
  h: number
}

/** One bar per value, equal slots with `gap` points between them. */
export function bars(values: number[], width: number, height: number, max: number, gap = 2): Bar[] {
  if (values.length === 0) return []
  const slot = width / values.length
  const w = r1(Math.max(1, slot - gap))
  return values.map((v, i) => {
    const y = yScale(v, max, height)
    return { x: r1(i * slot + gap / 2), y, w, h: r1(height - y) }
  })
}

/** A max for the y axis that is never 0, so empty series still draw. */
export function niceMax(values: (number | null)[]): number {
  const m = Math.max(0, ...values.filter((v): v is number => v !== null))
  return m === 0 ? 1 : m
}

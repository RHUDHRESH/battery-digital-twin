/* eslint-disable @typescript-eslint/no-explicit-any */
import { useEffect, useRef, useState } from 'react'

export type Any = any

async function req(method: string, url: string, body?: unknown) {
  const r = await fetch(url, {
    method,
    headers: body instanceof FormData || body === undefined ? undefined : { 'Content-Type': 'application/json' },
    body: body instanceof FormData ? body : body === undefined ? undefined : JSON.stringify(body),
  })
  const text = await r.text()
  const data = text ? JSON.parse(text) : null
  if (!r.ok) throw new Error(data?.detail ?? `${r.status} ${r.statusText}`)
  return data
}

export const api = {
  get: (u: string) => req('GET', u),
  post: (u: string, b?: unknown) => req('POST', u, b),
}

export type Live = { t: number; link: Any; battery: Any | null; twin?: Any | null; log?: Any | null; models?: Any[] }

/** Live telemetry + battery card over WebSocket, with auto-reconnect. */
export function useLive(): [Live | null, boolean] {
  const [live, setLive] = useState<Live | null>(null)
  const [up, setUp] = useState(false)
  const ref = useRef<WebSocket | null>(null)
  useEffect(() => {
    let stop = false
    let timer: number | undefined
    const connect = () => {
      const ws = new WebSocket(`${location.protocol === 'https:' ? 'wss' : 'ws'}://${location.host}/ws`)
      ref.current = ws
      ws.onopen = () => setUp(true)
      ws.onmessage = (e) => setLive(JSON.parse(e.data))
      ws.onclose = () => {
        setUp(false)
        if (!stop) timer = window.setTimeout(connect, 1500)
      }
    }
    connect()
    return () => {
      stop = true
      window.clearTimeout(timer)
      ref.current?.close()
    }
  }, [])
  return [live, up]
}

export const fmt = (v: number | null | undefined, d = 1, unit = '') =>
  v === null || v === undefined || Number.isNaN(v) ? '—' : `${v.toFixed(d)}${unit ? ' ' + unit : ''}`

export const kw = (w: number | null | undefined, d = 2) => fmt(w == null ? null : w / 1000, d, 'kW')
export const pct = (x: number | null | undefined, d = 1) => fmt(x == null ? null : x * 100, d, '%')

export const statusColor = (s?: string | null) =>
  s === 'PASS' ? 'var(--pass)' : s === 'MARGINAL' || s === 'UNKNOWN' ? 'var(--warn)' : s === 'FAIL' ? 'var(--fail)' : 'var(--mute)'

/** cool -> hot ramp for cells, t in 0..1 */
export function ramp(t: number) {
  const stops = [
    [0.0, [29, 78, 216]],
    [0.35, [14, 165, 164]],
    [0.6, [234, 179, 8]],
    [0.8, [234, 88, 12]],
    [1.0, [217, 48, 37]],
  ] as const
  const x = Math.min(1, Math.max(0, t))
  for (let i = 1; i < stops.length; i++) {
    if (x <= stops[i][0]) {
      const [a, ca] = stops[i - 1]
      const [b, cb] = stops[i]
      const k = (x - a) / (b - a)
      const c = ca.map((v, j) => Math.round(v + (cb[j] - v) * k))
      return `rgb(${c[0]},${c[1]},${c[2]})`
    }
  }
  return 'rgb(217,48,37)'
}

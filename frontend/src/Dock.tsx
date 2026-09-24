import { useEffect, useMemo, useState } from 'react'
import {
  Area, AreaChart, Bar, BarChart, CartesianGrid, Cell, Line, LineChart, ReferenceLine, ResponsiveContainer, Tooltip, XAxis, YAxis,
} from 'recharts'
import { api, fmt, pct, type Any, type Live } from './api'

type Tab = 'compat' | 'live' | 'twin' | 'ask' | 'models' | 'data'
const TABS: [Tab, string][] = [
  ['compat', 'Compatibility'], ['live', 'Live'], ['twin', 'Twin fidelity'], ['ask', 'Ask the twin'], ['models', 'Models'], ['data', 'Data'],
]

export default function Dock({ live, compat, say }: { live: Live | null; compat: React.ReactNode; say: (m: string) => void }) {
  const [tab, setTab] = useState<Tab>(() => (localStorageGet('dock-tab') as Tab) || 'compat')
  useEffect(() => { localStorageSet('dock-tab', tab) }, [tab])
  const fid = live?.twin?.fidelity
  const log = live?.log
  return (
    <footer className="dock">
      <div className="dock-bar">
        <div className="dock-tabs" role="tablist">
          {TABS.map(([id, label]) => (
            <button key={id} role="tab" aria-selected={tab === id} onClick={() => setTab(id)}>
              {label}{id === 'data' && log && <span className="rec-dot" aria-label="recording" />}
            </button>
          ))}
        </div>
        <div className="row" style={{ gap: 14 }}>
          {fid && ((fid.ocv_calibration ?? []).length === 0
            ? <span className="label" title="The twin corrects its generic OCV table after 5 min of rest">Twin calibrating OCV · rest {fmt((fid.resting_s ?? 0) / 60, 1)} of 5 min</span>
            : <span className="label" title="RMS difference between measured and twin-predicted pack voltage, last 10 min">
              Twin tracking <b style={{ color: fidColor(fid.rmse_mV, live?.battery?.identity?.series) }}>±{fmt(fid.rmse_mV, 1)} mV</b></span>)}
          {log && <span className="label"><span className="rec-dot" /> recording {log.rows} rows</span>}
        </div>
      </div>
      <div className="dock-body">
        {tab === 'compat' ? compat
          : tab === 'live' ? <LiveTab live={live} />
            : tab === 'twin' ? <TwinTab live={live} />
              : tab === 'ask' ? <AskTab live={live} say={say} />
                : tab === 'models' ? <ModelsTab live={live} say={say} />
                  : <DataTab live={live} say={say} />}
      </div>
    </footer>
  )
}

function localStorageGet(k: string) { try { return localStorage.getItem(k) } catch { return null } }
function localStorageSet(k: string, v: string) { try { localStorage.setItem(k, v) } catch { /* private mode */ } }
const fidColor = (mv: number, n = 1) => (mv / Math.max(n, 1) < 3 ? 'var(--pass)' : mv / Math.max(n, 1) < 8 ? 'var(--warn)' : 'var(--fail)')

function useHistory(window: number, live: Live | null) {
  const [rows, setRows] = useState<Any[]>([])
  const connected = !!live?.twin
  useEffect(() => {
    if (!connected) return
    let stop = false
    const pull = () => api.get(`/api/twin/history?window=${window}&points=400`).then((r) => { if (!stop) setRows(r) }).catch(() => {})
    pull()
    const id = window_setInterval(pull, 2000)
    return () => { stop = true; clearInterval(id) }
  }, [window, connected])
  return useMemo(() => {
    const t0 = rows.length ? rows[rows.length - 1].t : 0
    return rows.map((r) => ({ ...r, x: (r.t - t0) / 60, soc_bms: r.soc_bms == null ? null : r.soc_bms * 100, soc_twin: r.soc_twin * 100 }))
  }, [rows])
}
const window_setInterval = (f: () => void, ms: number) => window.setInterval(f, ms)

const axis = { tick: { fontSize: 11 }, stroke: 'var(--line-strong)' }
const xAxis = <XAxis dataKey="x" type="number" domain={['dataMin', 0]} tickFormatter={(v: number) => `${v.toFixed(v > -10 ? 1 : 0)}m`} {...axis} />

function Mini({ title, value, children }: { title: string; value?: React.ReactNode; children: React.ReactElement }) {
  return (
    <div className="mini">
      <div className="section-head" style={{ marginBottom: 2 }}><h3>{title}</h3><span className="num-m" style={{ fontSize: 16 }}>{value}</span></div>
      <div style={{ height: 150 }}><ResponsiveContainer>{children}</ResponsiveContainer></div>
    </div>
  )
}

/* ------------------------------------------------------------------ live */
function LiveTab({ live }: { live: Live | null }) {
  const [win, setWin] = useState(600)
  const rows = useHistory(win, live)
  const b = live?.battery
  if (!b) return <Empty />
  return (
    <div>
      <div className="row" style={{ marginBottom: 6 }}>
        <div className="seg">{[[120, '2 min'], [600, '10 min'], [3600, '1 h'], [21600, '6 h']].map(([s, l]) => (
          <button key={s} aria-pressed={win === s} onClick={() => setWin(s as number)}>{l}</button>))}</div>
        <span className="hint">Measured in ink, twin prediction in cobalt. Time runs to now (0).</span>
      </div>
      <div className="minis">
        <Mini title="Pack voltage" value={`${fmt(b.electrical.pack_v, 2)} V`}>
          <LineChart data={rows}><CartesianGrid stroke="var(--line)" vertical={false} />{xAxis}<YAxis domain={['auto', 'auto']} width={44} {...axis} /><Tooltip />
            <Line dataKey="V" name="measured" stroke="var(--ink)" dot={false} strokeWidth={1.6} isAnimationActive={false} />
            <Line dataKey="V_pred" name="twin" stroke="var(--cobalt)" dot={false} strokeWidth={1.4} strokeDasharray="4 3" isAnimationActive={false} /></LineChart>
        </Mini>
        <Mini title="Current" value={`${fmt(b.electrical.current, 1)} A`}>
          <AreaChart data={rows}><CartesianGrid stroke="var(--line)" vertical={false} />{xAxis}<YAxis width={40} {...axis} /><Tooltip /><ReferenceLine y={0} stroke="var(--line-strong)" />
            <Area dataKey="I" name="A (+discharge)" stroke="var(--cobalt)" fill="var(--cobalt-soft)" isAnimationActive={false} /></AreaChart>
        </Mini>
        <Mini title="Temperature" value={`${fmt(b.thermal.t_max, 1)} °C`}>
          <LineChart data={rows}><CartesianGrid stroke="var(--line)" vertical={false} />{xAxis}<YAxis domain={['auto', 'auto']} width={40} {...axis} /><Tooltip />
            <Line dataKey="T" name="max °C" stroke="var(--warn)" dot={false} strokeWidth={1.6} isAnimationActive={false} /></LineChart>
        </Mini>
        <Mini title="Cell spread" value={`${fmt(b.electrical.dv_mv, 0)} mV`}>
          <AreaChart data={rows}><CartesianGrid stroke="var(--line)" vertical={false} />{xAxis}<YAxis width={40} {...axis} /><Tooltip />
            <Area dataKey="dv_mV" name="max − min mV" stroke="var(--ink)" fill="#eef0f3" isAnimationActive={false} /></AreaChart>
        </Mini>
      </div>
    </div>
  )
}

/* ------------------------------------------------------------------ twin fidelity */
function TwinTab({ live }: { live: Live | null }) {
  const rows = useHistory(1800, live)
  const f = live?.twin?.fidelity
  const b = live?.battery
  if (!b || !f) return <Empty />
  const n = b.identity.series
  const bias: number[] = f.cell_bias_mV ?? []
  const ecm = b.electrical.ecm
  return (
    <div className="twin-grid">
      <div className="mini">
        <div className="section-head"><h3>Measured − twin, pack</h3><span className="hint">last 30 min</span></div>
        <div style={{ height: 150 }}><ResponsiveContainer>
          <AreaChart data={rows}><CartesianGrid stroke="var(--line)" vertical={false} />{xAxis}<YAxis width={40} unit="" {...axis} /><Tooltip /><ReferenceLine y={0} stroke="var(--ink)" />
            <Area dataKey="res_mV" name="residual mV" stroke="var(--cobalt)" fill="var(--cobalt-soft)" isAnimationActive={false} /></AreaChart>
        </ResponsiveContainer></div>
        <div className="row" style={{ gap: 18, marginTop: 4 }}>
          <Kpi v={`${fmt(f.rmse_mV, 1)} mV`} k="RMS, pack" s={`${fmt(f.rmse_mV / n, 2)} mV per cell`} />
          <Kpi v={f.rmse_loaded_mV == null ? '—' : `${fmt(f.rmse_loaded_mV, 1)} mV`} k="RMS under load" />
          <Kpi v={`${fmt(f.max_abs_mV, 0)} mV`} k="Worst" />
        </div>
      </div>
      <div className="mini">
        <div className="section-head"><h3>Per-cell bias</h3><span className="hint">mean measured − twin</span></div>
        <div style={{ height: 150 }}><ResponsiveContainer>
          <BarChart data={bias.map((v, i) => ({ i: i + 1, v }))}><CartesianGrid stroke="var(--line)" vertical={false} /><XAxis dataKey="i" {...axis} interval={0} /><YAxis width={36} {...axis} /><Tooltip /><ReferenceLine y={0} stroke="var(--ink)" />
            <Bar dataKey="v" name="bias mV" isAnimationActive={false}>{bias.map((v, i) => <Cell key={i} fill={Math.abs(v) > 5 ? 'var(--fail)' : Math.abs(v) > 2 ? 'var(--warn)' : 'var(--ink)'} />)}</Bar></BarChart>
        </ResponsiveContainer></div>
        <p className="hint" style={{ margin: '4px 0 0' }}>A cell that reads low at rest is at a lower SOC. One that drops further under load has more resistance than the model gives it.</p>
      </div>
      <div className="mini">
        <h3>Twin state and parameters</h3>
        <table className="t" style={{ marginTop: 4 }}><tbody>
          <tr><td className="label">SOC twin / BMS</td><td><b>{pct(f.soc_twin)}</b> / {pct(b.state.soc)}</td></tr>
          <tr><td className="label">R0 per cell</td><td><b>{fmt(ecm.cell.R0 * 1000, 3, 'mΩ')}</b> <span className="hint">{ecm.provenance.R0}</span></td></tr>
          <tr><td className="label">R1, τ1</td><td><b>{fmt(ecm.cell.R1 * 1000, 3, 'mΩ')}, {fmt(ecm.cell.tau1, 1, 's')}</b></td></tr>
          <tr><td className="label">R2, τ2</td><td><b>{fmt(ecm.cell.R2 * 1000, 3, 'mΩ')}, {fmt(ecm.cell.tau2, 0, 's')}</b> <span className="hint">{ecm.provenance.RC}</span></td></tr>
          <tr><td className="label">Capacity</td><td><b>{fmt(ecm.cell.Q_Ah, 2, 'Ah')}</b> <span className="hint">{b.degradation.capacity.source}</span></td></tr>
        </tbody></table>
        <h3 style={{ marginTop: 10 }}>OCV self-calibration</h3>
        {(f.ocv_calibration ?? []).length === 0
          ? <p className="hint" style={{ margin: '2px 0' }}>Learns after 5 min of rest (resting {fmt((f.resting_s ?? 0) / 60, 1)} min so far). The generic LFP OCV table is corrected at each SOC it sees.</p>
          : <p className="hint" style={{ margin: '2px 0' }}>{f.ocv_calibration.map((c: Any) => `${Math.round(c.soc_lo * 100)}–${Math.round(c.soc_hi * 100)} %: ${c.offset_mV >= 0 ? '+' : ''}${c.offset_mV.toFixed(1)} mV/cell (${c.n})`).join(' · ')}</p>}
        {b.evidence.missing.length > 0 && <p className="hint">Until measured, the twin uses defaults for: {b.evidence.missing.join(', ')}.</p>}
      </div>
    </div>
  )
}

function Kpi({ v, k, s }: { v: React.ReactNode; k: string; s?: string }) {
  return <div><div className="num-m">{v}</div><div className="label">{k}</div>{s && <div className="hint">{s}</div>}</div>
}

/* ------------------------------------------------------------------ ask the twin */
type Seg = { mode: 'current' | 'power' | 'rest'; value: number; duration_s: number }
function AskTab({ live, say }: { live: Live | null; say: (m: string) => void }) {
  const b = live?.battery
  const Q = b?.identity?.nominal_Ah ?? 18
  const V = b?.identity?.nominal_V ?? 60
  const presets: [string, Seg[]][] = useMemo(() => [
    ['Pulse test (HPPC-like)', [{ mode: 'current', value: +(Q).toFixed(1), duration_s: 10 }, { mode: 'rest', value: 0, duration_s: 40 }, { mode: 'current', value: -(Q * 0.75), duration_s: 10 }, { mode: 'rest', value: 0, duration_s: 60 }]],
    ['0.5C for 30 min', [{ mode: 'current', value: +(Q * 0.5).toFixed(1), duration_s: 1800 }, { mode: 'rest', value: 0, duration_s: 300 }]],
    ['Drain at 1 kW', [{ mode: 'power', value: 1000, duration_s: 4 * 3600 }]],
    ['Peak: 2C for 30 s', [{ mode: 'current', value: +(Q * 2).toFixed(1), duration_s: 30 }, { mode: 'rest', value: 0, duration_s: 120 }]],
    ['Charge 0.3C for 1 h', [{ mode: 'current', value: -(Q * 0.3).toFixed(1), duration_s: 3600 }]],
  ], [Q])
  const [segs, setSegs] = useState<Seg[]>(presets[0][1])
  const [res, setRes] = useState<Any | null>(null)
  const [busy, setBusy] = useState(false)
  const run = async () => {
    setBusy(true)
    try { setRes(await api.post('/api/twin/forecast', { profile: segs })) } catch (e) { say((e as Error).message) } finally { setBusy(false) }
  }
  const upd = (i: number, k: keyof Seg, v: Any) => setSegs(segs.map((s, j) => (j === i ? { ...s, [k]: v } : s)))
  const data = res ? res.trace.t.map((t: number, k: number) => ({ x: t / 60, V: res.trace.V[k], cmin: res.trace.Vcell_min[k], soc: res.trace.soc[k] * 100, T: res.trace.T[k], I: res.trace.I[k] })) : []
  if (!b) return <Empty />
  return (
    <div className="ask-grid">
      <div>
        <div className="row" style={{ marginBottom: 6 }}>{presets.map(([l, p]) => <button key={l} className="btn" style={{ padding: '4px 10px', fontSize: 12.5 }} onClick={() => { setSegs(p); setRes(null) }}>{l}</button>)}</div>
        <table className="t seg-table"><thead><tr><th>Step</th><th>Mode</th><th>Value</th><th>Minutes</th><th /></tr></thead><tbody>
          {segs.map((s, i) => (
            <tr key={i}><td>{i + 1}</td>
              <td><select value={s.mode} onChange={(e) => upd(i, 'mode', e.target.value)} aria-label="Mode"><option value="current">Current (A)</option><option value="power">Power (W)</option><option value="rest">Rest</option></select></td>
              <td><input type="number" step="any" value={s.mode === 'rest' ? 0 : s.value} disabled={s.mode === 'rest'} onChange={(e) => upd(i, 'value', +e.target.value)} aria-label="Value" style={{ width: 80 }} /></td>
              <td><input type="number" step="any" value={+(s.duration_s / 60).toFixed(3)} onChange={(e) => upd(i, 'duration_s', +e.target.value * 60)} aria-label="Minutes" style={{ width: 70 }} /></td>
              <td><button className="btn" style={{ padding: '2px 8px' }} onClick={() => setSegs(segs.filter((_, j) => j !== i))} aria-label="Remove step">✕</button></td></tr>
          ))}
        </tbody></table>
        <div className="row" style={{ marginTop: 8 }}>
          <button className="btn" onClick={() => setSegs([...segs, { mode: 'current', value: +(Q * 0.5).toFixed(1), duration_s: 600 }])}>Add step</button>
          <button className="btn primary" onClick={run} disabled={busy || !segs.length}>{busy ? 'Simulating…' : 'Run on the twin'}</button>
        </div>
        <p className="hint">Positive = discharge, negative = charge. Starts from the twin's current state ({pct(live?.twin?.fidelity?.soc_twin)} SOC). 1C here = {fmt(Q, 1, 'A')} ≈ {fmt(Q * V / 1000, 2, 'kW')}.</p>
      </div>
      <div>
        {!res ? <div className="note">Build a load profile and run it on the twin. Nothing is sent to the real battery.</div> : (
          <>
            <div className="row" style={{ gap: 20, marginBottom: 4 }}>
              <Kpi v={res.first_violation ? <span style={{ color: 'var(--fail)' }}>{res.first_violation.mode}</span> : <span style={{ color: 'var(--pass)' }}>within limits</span>}
                k={res.first_violation ? `at ${fmt(res.first_violation.t_s / 60, 1)} min${res.first_violation.cell ? `, cell ${res.first_violation.cell}` : ''}` : 'no limit reached'} />
              <Kpi v={pct(res.end_soc)} k="End SOC" />
              <Kpi v={`${fmt(res.min_cell, 3)} V`} k="Lowest cell" />
              <Kpi v={`${fmt(res.peak_T, 1)} °C`} k="Peak temperature" />
              <Kpi v={`${fmt(res.energy_wh, 0)} Wh`} k="Energy out" />
            </div>
            <div style={{ height: 170 }}><ResponsiveContainer>
              <LineChart data={data}><CartesianGrid stroke="var(--line)" vertical={false} />
                <XAxis dataKey="x" type="number" domain={[0, 'dataMax']} tickFormatter={(v: number) => `${v.toFixed(0)}m`} {...axis} />
                <YAxis yAxisId="c" domain={['auto', 'auto']} width={44} {...axis} /><YAxis yAxisId="s" orientation="right" domain={[0, 100]} width={34} {...axis} /><Tooltip />
                <ReferenceLine yAxisId="c" y={res.v_cut} stroke="var(--fail)" strokeDasharray="4 4" />
                <Line yAxisId="c" dataKey="cmin" name="lowest cell V" stroke="var(--ink)" dot={false} strokeWidth={1.6} isAnimationActive={false} />
                <Line yAxisId="s" dataKey="soc" name="SOC %" stroke="var(--cobalt)" dot={false} strokeWidth={1.4} isAnimationActive={false} />
              </LineChart>
            </ResponsiveContainer></div>
            <p className="hint" style={{ margin: 0 }}>Model output, not a measurement. Resistances: {res.assumptions.params.R0}; RC: {res.assumptions.params.RC}; thermal: {res.assumptions.thermal}.</p>
          </>
        )}
      </div>
    </div>
  )
}

/* ------------------------------------------------------------------ models */
function ModelsTab({ live, say }: { live: Live | null; say: (m: string) => void }) {
  const [list, setList] = useState<Any[]>([])
  useEffect(() => { api.get('/api/models').then(setList) }, [])
  const models: Any[] = live?.models ?? list
  const reload = async () => { const r = await api.post('/api/models/reload'); setList(r); say(`${r.length} model plugins loaded`) }
  const toggle = async (k: string, on: boolean) => setList(await api.post(`/api/models/enable?key=${k}&on=${on}`))
  const meta = (k: string) => list.find((m) => m.key === k) ?? {}
  return (
    <div>
      <div className="row" style={{ marginBottom: 8, justifyContent: 'space-between' }}>
        <span className="hint">Python files in <b>backend/plugins/</b> run on every live update (~1 s). Start from <b>_template_ml_model.py</b> to plug in your own trained model.</span>
        <button className="btn" onClick={reload}>Reload models</button>
      </div>
      <div className="models">
        {models.map((m) => {
          const info = meta(m.key)
          const r = m.result
          return (
            <div key={m.key} className="mini">
              <div className="section-head"><h3>{m.name}</h3>
                <label className="row" style={{ gap: 4 }}><input type="checkbox" checked={info.enabled ?? true} onChange={(e) => toggle(m.key, e.target.checked)} /> <span className="hint">on</span></label></div>
              <div className="hint">{info.description} {info.kind && `(${info.kind})`}</div>
              {m.load_error ? <div className="failbox" style={{ marginTop: 6 }}>{m.load_error}</div>
                : !r ? <div className="hint" style={{ marginTop: 6 }}>waiting for data</div>
                  : !r.ok ? <div className="failbox" style={{ marginTop: 6 }}>{r.error}</div>
                    : <>
                      <div className="num-m" style={{ margin: '8px 0 2px', fontSize: 17 }}>{r.out?.label ?? String(r.out?.value)}</div>
                      <div className="hint">{fmt(r.ms, 1)} ms · {new Date(r.t * 1000).toLocaleTimeString()}</div>
                      {r.out?.detail && <details style={{ marginTop: 4 }}><summary className="hint" style={{ cursor: 'pointer' }}>Detail</summary>
                        <pre style={{ fontSize: 11, whiteSpace: 'pre-wrap', margin: 0 }}>{JSON.stringify(r.out.detail, null, 1)}</pre></details>}
                    </>}
            </div>
          )
        })}
        {!models.length && <div className="note">No plugins found in backend/plugins/.</div>}
      </div>
    </div>
  )
}

/* ------------------------------------------------------------------ data */
function DataTab({ live, say }: { live: Live | null; say: (m: string) => void }) {
  const [label, setLabel] = useState('')
  const [notes, setNotes] = useState('')
  const [list, setList] = useState<Any[]>([])
  const refresh = () => api.get('/api/log/list').then(setList)
  useEffect(() => { refresh() }, [])
  const log = live?.log
  const start = async () => { try { const r = await api.post('/api/log/start', { label, notes }); say(`Recording ${r.name}`); refresh() } catch (e) { say((e as Error).message) } }
  const stop = async () => { const r = await api.post('/api/log/stop'); say(`Saved ${r.name}`); refresh() }
  return (
    <div className="ask-grid">
      <div>
        <h3>Research session</h3>
        <div className="form" style={{ gridTemplateColumns: '1fr', marginTop: 6 }}>
          <label className="field"><span className="label">Label</span><input value={label} onChange={(e) => setLabel(e.target.value)} placeholder="e.g. 20 A discharge, 26 °C" disabled={!!log} /></label>
          <label className="field"><span className="label">Notes: setup, load, ambient, anything you'll want later</span><textarea className="in" rows={2} value={notes} onChange={(e) => setNotes(e.target.value)} disabled={!!log} /></label>
        </div>
        <div className="row" style={{ marginTop: 8 }}>
          {log ? <button className="btn danger" onClick={stop}>Stop and save ({log.rows} rows)</button>
            : <button className="btn primary" onClick={start} disabled={!live?.battery}>Start recording</button>}
        </div>
        <p className="hint">Each session saves a CSV (every cell and sensor, current, SOC, and the twin's prediction and residual) plus a JSON sidecar with the battery, link settings, chemistry profile, and model parameters at start and stop. Raw bus bytes are recorded separately under Hardware.</p>
      </div>
      <div style={{ overflow: 'auto', maxHeight: 250 }}>
        <table className="t"><thead><tr><th>Session</th><th>Rows</th><th>Battery</th><th /></tr></thead><tbody>
          {list.length === 0 && <tr><td colSpan={4} className="hint">No sessions yet. Record one while you run a test.</td></tr>}
          {list.map((s) => (
            <tr key={s.name}><td><b>{s.label || s.name}</b><div className="hint">{s.name}{s.notes ? ` · ${s.notes}` : ''}</div></td><td>{s.rows ?? '…'}</td><td className="hint">{s.battery}</td>
              <td style={{ whiteSpace: 'nowrap' }}><a className="btn" href={`/api/log/download?name=${encodeURIComponent(s.name)}`}>CSV</a> <a className="btn" href={`/api/log/download?name=${encodeURIComponent(s.name)}&kind=json`}>Metadata</a></td></tr>
          ))}
        </tbody></table>
      </div>
    </div>
  )
}

function Empty() {
  return <div className="note">Connect the battery (Hardware) or start the demo. The twin synchronises on the first BMS reading.</div>
}

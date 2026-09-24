import { motion } from 'framer-motion'
import { useEffect, useMemo, useState } from 'react'
import {
  Area, AreaChart, Bar, BarChart, CartesianGrid, Cell, Line, LineChart, ReferenceDot, ReferenceLine,
  ResponsiveContainer, Scatter, ScatterChart, Tooltip, XAxis, YAxis, ZAxis,
} from 'recharts'
import { api, fmt, kw, pct, ramp, statusColor, type Any, type Live } from './api'
import Hardware from './Hardware'
import VehicleEditor from './VehicleEditor'

const citeMods = import.meta.glob('./citations.json', { eager: true, import: 'default' }) as Record<string, Any[]>
const CITES: Any[] = Object.values(citeMods)[0] ?? []

const TABS: [string, string][] = [
  ['overview', 'Overview'], ['energy', 'Energy'], ['power', 'Power'], ['thermal', 'Thermal'], ['cells', 'Cell consistency'],
  ['simulation', 'Mission replay'], ['fleet', 'Fleet sorting'], ['evidence', 'Raw evidence'], ['method', 'Algorithms & proof'],
  ['hardware', 'Hardware'], ['vehicle', 'Application'],
]

type P = {
  tab: string; setTab: (t: string) => void; close: () => void; res: Any; live: Live | null
  vehicles: Any[]; vid: string; setVid: (v: string) => void; reloadVehicles: () => Promise<Any>; say: (m: string) => void
  rerun: (a: 'A' | 'B' | 'C' | 'ALL') => void
}

export default function Details(p: P) {
  useEffect(() => {
    const k = (e: KeyboardEvent) => e.key === 'Escape' && p.close()
    window.addEventListener('keydown', k)
    return () => window.removeEventListener('keydown', k)
  }, [p])
  const tab = p.tab === 'vehicle-new' ? 'vehicle' : p.tab
  return (
    <>
      <motion.div className="drawer-backdrop" onClick={p.close} initial={{ opacity: 0 }} animate={{ opacity: 1 }} exit={{ opacity: 0 }} />
      <motion.div className="drawer" role="dialog" aria-modal initial={{ y: 40, opacity: 0 }} animate={{ y: 0, opacity: 1 }} exit={{ y: 30, opacity: 0 }}
        transition={{ type: 'spring', stiffness: 260, damping: 28 }}>
        <div className="drawer-head">
          <h2 style={{ fontSize: 20 }}>{p.res ? `${p.res.battery.identity.label}, tested against ${p.res.vehicle.name}` : 'Workbench'}</h2>
          <div className="row">
            {p.res && <span className="hint">{p.res.cycle}</span>}
            <button className="btn" onClick={p.close}>Close</button>
          </div>
        </div>
        <div className="tabs" role="tablist">
          {TABS.map(([id, label]) => (
            <button key={id} role="tab" aria-selected={tab === id} onClick={() => p.setTab(id)}>{label}</button>
          ))}
        </div>
        <div className="drawer-body">
          {tab === 'hardware' ? <Hardware say={p.say} live={p.live} />
            : tab === 'vehicle' ? <VehicleEditor vehicles={p.vehicles} vid={p.tab === 'vehicle-new' ? null : p.vid}
              onSaved={async (id: string) => { await p.reloadVehicles(); p.setVid(id); p.say('Vehicle saved') }} />
              : tab === 'method' ? <Method />
                : !p.res ? <div className="note">Run an evaluation first: connect a battery and pick a vehicle.</div>
                  : tab === 'overview' ? <Overview res={p.res} rerun={p.rerun} />
                    : tab === 'energy' ? <Energy res={p.res} />
                      : tab === 'power' ? <Power res={p.res} />
                        : tab === 'thermal' ? <Thermal res={p.res} />
                          : tab === 'cells' ? <Cells bat={p.live?.battery ?? p.res.battery} />
                            : tab === 'simulation' ? <Replay res={p.res} rerun={p.rerun} />
                              : tab === 'fleet' ? <Fleet res={p.res} rerun={p.rerun} say={p.say} />
                                : <Evidence bat={p.live?.battery ?? p.res.battery} />}
        </div>
      </motion.div>
    </>
  )
}

/* ================================================================== overview */
function Overview({ res, rerun }: { res: Any; rerun: P['rerun'] }) {
  const g = res.gate
  const R = res.results
  const algs = [
    { k: 'A', name: 'SafeFit-R2 weakest link', r: R.A },
    { k: 'B', name: 'Application-aware sorting', r: R.B },
    { k: 'C', name: 'Probabilistic digital twin', r: R.C },
  ]
  return (
    <div className="grid2">
      <div className="panel">
        <div className="section-head"><h3>Qualification gate</h3>
          <span className="verdict" style={{ background: statusColor(g.verdict) }}>{g.meaning}</span></div>
        <table className="t"><tbody>
          {g.checks.map((c: Any) => (
            <tr key={c.key}><td style={{ width: 90 }}><b style={{ color: statusColor(c.status) }}>{c.status}</b></td><td><b>{c.name}</b><div className="hint">{c.evidence}</div></td></tr>
          ))}
        </tbody></table>
        <p className="hint">FAIL means incompatible. UNKNOWN means a test is required. A score can never override the gate.</p>
      </div>
      <div className="panel">
        <div className="section-head"><h3>Three engines, three questions</h3>
          <button className="btn" onClick={() => rerun('ALL')}>Run all three</button></div>
        {algs.map(({ k, name, r }) => (
          <div key={k} style={{ padding: '12px 0', borderBottom: '1px solid var(--line)' }}>
            <div className="row" style={{ justifyContent: 'space-between' }}>
              <span><b>{k}</b> {name}</span>
              <span className="num-m">{r?.score != null ? `${Math.round(r.score)}%` : r?.error ? 'n/a' : 'not run'}</span>
            </div>
            <div className="meter" style={{ height: 8, marginTop: 6 }}>
              <motion.div initial={{ width: 0 }} animate={{ width: `${r?.score ?? 0}%` }} style={{ background: 'var(--ink)' }} />
            </div>
            <div className="hint" style={{ marginTop: 4 }}>{r?.error ?? r?.meaning ?? `Run engine ${k} to answer its question.`}</div>
          </div>
        ))}
        <p className="hint">{res.fusion.rule}</p>
      </div>
      {R.A && (
        <div className="panel" style={{ gridColumn: '1 / -1' }}>
          <h3>Engine A margins</h3>
          <div style={{ height: 220 }}>
            <ResponsiveContainer>
              <BarChart data={Object.entries(R.A.dimensions).map(([k, d]: [string, Any]) => ({ k, score: (d.score ?? 0) * 100, status: d.status }))} layout="vertical" margin={{ left: 20 }}>
                <CartesianGrid horizontal={false} stroke="var(--line)" />
                <XAxis type="number" domain={[0, 100]} tick={{ fontSize: 12 }} />
                <YAxis type="category" dataKey="k" tick={{ fontSize: 12 }} width={70} />
                <ReferenceLine x={60} stroke="var(--mute)" strokeDasharray="4 4" label={{ value: 'pass', fontSize: 11, fill: 'var(--mute)' }} />
                <Tooltip />
                <Bar dataKey="score" radius={[0, 6, 6, 0]}>
                  {Object.values(R.A.dimensions).map((d: Any, i) => <Cell key={i} fill={statusColor(d.status)} />)}
                </Bar>
              </BarChart>
            </ResponsiveContainer>
          </div>
          <p className="hint">Compatibility = the lowest bar. A deficient dimension cannot be averaged away.</p>
        </div>
      )}
    </div>
  )
}

/* ================================================================== energy / power / thermal */
function KV({ rows }: { rows: [string, React.ReactNode, string?][] }) {
  return <table className="t"><tbody>{rows.map(([k, v, h]) => <tr key={k}><td className="label">{k}</td><td><b>{v}</b>{h && <div className="hint">{h}</div>}</td></tr>)}</tbody></table>
}

function Energy({ res }: { res: Any }) {
  const A = res.results.A
  if (!A) return <div className="note">Run engine A to see the energy budget.</div>
  const e = A.details.energy, rq = A.details.requirements
  const data = [
    { k: 'Mission need', v: e.E_mission_wh / 1000 },
    { k: 'Usable from full', v: e.E_usable_full_wh / 1000 },
    { k: 'Usable right now', v: e.E_ready_now_wh / 1000 },
  ]
  return (
    <div className="grid2">
      <div className="panel">
        <h3>Energy budget (kWh)</h3>
        <div style={{ height: 240 }}>
          <ResponsiveContainer>
            <BarChart data={data}><CartesianGrid vertical={false} stroke="var(--line)" /><XAxis dataKey="k" tick={{ fontSize: 12 }} /><YAxis tick={{ fontSize: 12 }} /><Tooltip />
              <Bar dataKey="v" radius={[6, 6, 0, 0]}>{data.map((_, i) => <Cell key={i} fill={i === 0 ? 'var(--ink)' : 'var(--cobalt)'} />)}</Bar></BarChart>
          </ResponsiveContainer>
        </div>
        <p className="hint">Compatibility uses energy from full charge. "Right now" is readiness: a healthy battery at low charge is compatible but not ready.</p>
      </div>
      <div className="panel">
        <KV rows={[
          ['Energy margin', `×${A.dimensions.energy.margin.toFixed(2)}`, 'usable above reserve / mission need'],
          ['Consumption', fmt(e.wh_per_km, 1, 'Wh/km'), rq.cycle],
          ['Range from full', fmt(e.range_full_km, 0, 'km')],
          ['Range right now', fmt(e.range_now_km, 0, 'km')],
          ['Ready for mission now', e.ready_now ? 'Yes' : 'No — charge first'],
          ['Mission', `${res.vehicle.target_range_km} km with ${pct(res.vehicle.reserve_soc, 0)} reserve`],
        ]} />
      </div>
    </div>
  )
}

function Power({ res }: { res: Any }) {
  const A = res.results.A
  const sop = res.battery.state.sop
  if (!A) return <div className="note">Run engine A to see the power budget.</div>
  const d = A.details.power
  const data = [
    { k: 'Peak need', need: d.P_peak_req_w / 1000 },
    { k: '10 s need', need: d.P_10s_req_w / 1000, cap: d.SOP_10s_eom_w / 1000 },
    { k: '30 s need', need: d.P_30s_req_w / 1000, cap: d.SOP_30s_eom_w / 1000 },
  ]
  return (
    <div className="grid2">
      <div className="panel">
        <h3>Demand vs safe capability at end of mission (kW)</h3>
        <div style={{ height: 240 }}>
          <ResponsiveContainer>
            <BarChart data={data}><CartesianGrid vertical={false} stroke="var(--line)" /><XAxis dataKey="k" tick={{ fontSize: 12 }} /><YAxis tick={{ fontSize: 12 }} /><Tooltip />
              <Bar dataKey="need" name="vehicle needs" fill="var(--ink)" radius={[6, 6, 0, 0]} />
              <Bar dataKey="cap" name="battery can give" fill="var(--cobalt)" radius={[6, 6, 0, 0]} /></BarChart>
          </ResponsiveContainer>
        </div>
        <p className="hint">Capability is evaluated at {pct(d.eom_soc, 0)} SOC and {res.vehicle.ambient_c} °C, the worst point of the mission. Limiter: {d.limiter}.</p>
      </div>
      <div className="panel">
        <h3>State of power right now</h3>
        <KV rows={[
          ['Instantaneous (1 s)', kw(sop['1s'].P_w), sop['1s'].limiter],
          ['10 s pulse', kw(sop['10s'].P_w), sop['10s'].limiter],
          ['30 s pulse', kw(sop['30s'].P_w), sop['30s'].limiter],
          ['Continuous safe', kw(sop.continuous.P_w), sop.continuous.limiter],
          ['Charge / regen 10 s', kw(sop.charge_10s.P_w), sop.charge_10s.limiter],
        ]} />
        <p className="hint">Largest constant current for which every cell stays above the {res.battery.identity.chemistry} cut-off at the end of the pulse, using the identified 2RC model.</p>
      </div>
    </div>
  )
}

function Thermal({ res }: { res: Any }) {
  const A = res.results.A
  const th = res.battery.thermal
  const C = res.results.C
  return (
    <div className="grid2">
      <div className="panel">
        <h3>Thermal budget</h3>
        {A ? <KV rows={[
          ['Predicted rise over mission', fmt(A.details.thermal.dT_mission, 1, '°C'), `first order, τ = ${fmt(A.details.thermal.tau_th_s / 60, 0, 'min')}`],
          ['Allowed rise', fmt(A.details.thermal.allowed_rise, 1, '°C'), `to ${55} °C from ${res.vehicle.ambient_c} °C ambient`],
          ['RMS current', fmt(A.details.thermal.I_rms, 1, 'A')],
          ['Steady-state rise', fmt(A.details.thermal.dT_steady, 1, '°C'), 'if the duty never stopped'],
          ['Twin p95 peak temperature', C ? fmt(C.monte_carlo.peak_T_p95, 1, '°C') : 'run engine C'],
        ]} /> : <div className="note">Run engine A.</div>}
      </div>
      <div className="panel">
        <h3>Measured now</h3>
        <KV rows={[
          ['Max temperature', fmt(th.t_max, 1, '°C')],
          ['Sensor spread', fmt(th.dt_cells, 1, '°C')],
          ['Heating rate', fmt(th.dTdt_c_per_min, 2, '°C/min'), 'least squares, last 5 min'],
          ['Thermal mass', fmt(th.c_th / 1000, 1, 'kJ/K'), 'assumed until calibrated'],
          ['Cooling', fmt(th.hA, 1, 'W/K'), 'assumed until calibrated'],
        ]} />
      </div>
    </div>
  )
}

/* ================================================================== cells */
function Cells({ bat }: { bat: Any }) {
  const el = bat.electrical
  const rc: number[] | null = el.cell_dcir_mohm
  const data = el.cells.map((v: number, i: number) => ({ i: i + 1, mv: v * 1000, r: rc?.[i] }))
  const mean = el.cells.reduce((a: number, b: number) => a + b, 0) / el.cells.length
  return (
    <div className="grid2">
      <div className="panel">
        <h3>Cell voltage (mV)</h3>
        <div style={{ height: 260 }}>
          <ResponsiveContainer>
            <BarChart data={data}><CartesianGrid vertical={false} stroke="var(--line)" /><XAxis dataKey="i" tick={{ fontSize: 12 }} />
              <YAxis domain={['dataMin - 10', 'dataMax + 10']} tick={{ fontSize: 12 }} /><Tooltip />
              <ReferenceLine y={mean * 1000} stroke="var(--mute)" strokeDasharray="4 4" />
              <Bar dataKey="mv" radius={[4, 4, 0, 0]}>{data.map((d: Any, i: number) => <Cell key={i} fill={ramp(0.45 + (mean * 1000 - d.mv) / 40)} />)}</Bar></BarChart>
          </ResponsiveContainer>
        </div>
        <p className="hint">Spread {fmt(el.dv_mv, 0, 'mV')} now{el.rest_dv_mv != null ? `, ${fmt(el.rest_dv_mv, 0, 'mV')} at rest` : ''}. Spread under load mostly reflects resistance mismatch; spread at rest reflects SOC or capacity mismatch.</p>
      </div>
      <div className="panel">
        <h3>Cell DC resistance (mΩ, from load steps)</h3>
        {rc ? (
          <div style={{ height: 260 }}>
            <ResponsiveContainer>
              <BarChart data={data}><CartesianGrid vertical={false} stroke="var(--line)" /><XAxis dataKey="i" tick={{ fontSize: 12 }} /><YAxis tick={{ fontSize: 12 }} /><Tooltip />
                <Bar dataKey="r" radius={[4, 4, 0, 0]}>{data.map((_: Any, i: number) => <Cell key={i} fill={i === el.weakest_cell ? 'var(--fail)' : 'var(--ink)'} />)}</Bar></BarChart>
            </ResponsiveContainer>
          </div>
        ) : <div className="note">Waiting for current steps of at least 8 % of capacity.</div>}
        <p className="hint">Weakest cell: #{el.weakest_cell + 1}. It sets the pack's voltage floor and therefore its power.</p>
      </div>
    </div>
  )
}

/* ================================================================== mission replay */
function Replay({ res, rerun }: { res: Any; rerun: P['rerun'] }) {
  const C = res.results.C
  const [i, setI] = useState(0)
  const [play, setPlay] = useState(true)
  const tr = C?.nominal?.trace
  const n = tr?.t?.length ?? 0
  useEffect(() => {
    if (!play || !n) return
    const id = window.setInterval(() => setI((k) => (k + 1) % n), 40)
    return () => window.clearInterval(id)
  }, [play, n])
  const rows = useMemo(() => (tr ? tr.t.map((t: number, k: number) => ({
    t: t / 60, x: tr.x_km[k], elev: tr.elev_m[k], v: tr.v_kmh[k], P: tr.P_w[k] / 1000, V: tr.V[k], cell: tr.Vcell_min[k],
    soc: tr.soc[k] * 100, T: tr.T[k], I: tr.I[k],
  })) : []), [tr])
  if (!C) return <div className="note">The mission replay comes from engine C. <button className="btn" onClick={() => rerun('C')}>Run the digital twin</button></div>
  const mc = C.monte_carlo
  const f = C.nominal.failure
  const cur = rows[i] ?? rows[0]
  const elevMin = Math.min(...rows.map((r: Any) => r.elev)), elevMax = Math.max(...rows.map((r: Any) => r.elev))
  const W = 900, H = 150
  const px = (x: number) => (x / (rows[rows.length - 1]?.x || 1)) * (W - 40) + 20
  const py = (e: number) => H - 20 - ((e - elevMin) / (elevMax - elevMin || 1)) * (H - 50)
  const path = rows.map((r: Any, k: number) => `${k ? 'L' : 'M'}${px(r.x).toFixed(1)},${py(r.elev).toFixed(1)}`).join(' ')
  return (
    <div style={{ display: 'grid', gap: 18 }}>
      <div className="panel">
        <div className="section-head">
          <h3>Nominal run: {f ? <span style={{ color: 'var(--fail)' }}>fails at {fmt(f.x_km, 1, 'km')}: {C.nominal.fail_mode}</span> : 'mission completed'}</h3>
          <div className="row">
            <button className="btn" onClick={() => setPlay(!play)}>{play ? 'Pause' : 'Play'}</button>
            <input type="range" min={0} max={Math.max(0, n - 1)} value={i} onChange={(e) => { setPlay(false); setI(+e.target.value) }} aria-label="Scrub" style={{ width: 220 }} />
          </div>
        </div>
        <svg viewBox={`0 0 ${W} ${H}`} width="100%" role="img" aria-label="Route elevation with vehicle position">
          <path d={`${path} L${px(rows[rows.length - 1]?.x ?? 0)},${H - 8} L20,${H - 8} Z`} fill="var(--cobalt-soft)" />
          <path d={path} fill="none" stroke="var(--cobalt)" strokeWidth="2" />
          {f && <g><circle cx={px(f.x_km)} cy={py(rows.find((r: Any) => r.x >= f.x_km)?.elev ?? 0)} r="9" fill="var(--fail)" opacity=".25"><animate attributeName="r" values="6;14;6" dur="1.4s" repeatCount="indefinite" /></circle>
            <circle cx={px(f.x_km)} cy={py(rows.find((r: Any) => r.x >= f.x_km)?.elev ?? 0)} r="4" fill="var(--fail)" /></g>}
          {cur && <g transform={`translate(${px(cur.x)},${py(cur.elev) - 12})`}>
            <rect x="-14" y="-9" width="28" height="12" rx="4" fill="var(--ink)" /><circle cx="-8" cy="4" r="3.5" fill="var(--ink)" /><circle cx="8" cy="4" r="3.5" fill="var(--ink)" /></g>}
        </svg>
        {cur && <div className="grid3" style={{ gridTemplateColumns: 'repeat(6, 1fr)', marginTop: 8 }}>
          {[['Distance', fmt(cur.x, 1, 'km')], ['Speed', fmt(cur.v, 0, 'km/h')], ['Battery power', fmt(cur.P, 2, 'kW')], ['Pack voltage', fmt(cur.V, 1, 'V')], ['Lowest cell', fmt(cur.cell, 3, 'V')], ['SOC', fmt(cur.soc, 1, '%')]]
            .map(([k, v]) => <div key={k}><div className="num-m">{v}</div><div className="label">{k}</div></div>)}
        </div>}
      </div>
      <div className="grid2">
        <div className="panel"><h3>Lowest cell voltage and SOC</h3>
          <div style={{ height: 200 }}><ResponsiveContainer><LineChart data={rows}><CartesianGrid stroke="var(--line)" vertical={false} />
            <XAxis dataKey="t" tickFormatter={(v) => `${Math.round(v)}m`} tick={{ fontSize: 11 }} /><YAxis yAxisId="v" domain={['auto', 'auto']} tick={{ fontSize: 11 }} /><YAxis yAxisId="s" orientation="right" domain={[0, 100]} tick={{ fontSize: 11 }} /><Tooltip />
            <ReferenceLine yAxisId="v" y={2.7} stroke="var(--fail)" strokeDasharray="4 4" />
            <Line yAxisId="v" dataKey="cell" stroke="var(--ink)" dot={false} strokeWidth={1.4} isAnimationActive={false} />
            <Line yAxisId="s" dataKey="soc" stroke="var(--cobalt)" dot={false} strokeWidth={1.4} isAnimationActive={false} />
            {cur && <ReferenceLine yAxisId="v" x={cur.t} stroke="var(--mute)" />}</LineChart></ResponsiveContainer></div></div>
        <div className="panel"><h3>Power demand and temperature</h3>
          <div style={{ height: 200 }}><ResponsiveContainer><AreaChart data={rows}><CartesianGrid stroke="var(--line)" vertical={false} />
            <XAxis dataKey="t" tickFormatter={(v) => `${Math.round(v)}m`} tick={{ fontSize: 11 }} /><YAxis yAxisId="p" tick={{ fontSize: 11 }} /><YAxis yAxisId="T" orientation="right" domain={['auto', 'auto']} tick={{ fontSize: 11 }} /><Tooltip />
            <Area yAxisId="p" dataKey="P" stroke="var(--cobalt)" fill="var(--cobalt-soft)" isAnimationActive={false} />
            <Line yAxisId="T" dataKey="T" stroke="var(--warn)" dot={false} isAnimationActive={false} />
            {cur && <ReferenceLine yAxisId="p" x={cur.t} stroke="var(--mute)" />}</AreaChart></ResponsiveContainer></div></div>
      </div>
      <div className="grid2">
        <div className="panel"><h3>Monte Carlo: {mc.successes} of {mc.N} scenarios passed</h3>
          <KV rows={[['P(success)', `${pct(mc.p_success)} [${pct(mc.ci95[0], 1)}–${pct(mc.ci95[1], 1)}] Wilson 95%`],
            ...Object.entries(mc.failure_modes).map(([m, c]) => [m, `${c} runs`] as [string, string]),
            ['Median final SOC', pct(mc.final_soc_median)], ['Median power-deficit time', fmt(mc.deficit_s_median, 0, 's'), 'controller could not deliver demanded power'],
          ]} />
          {mc.worst_run?.state && <div className="failbox" style={{ marginTop: 10 }}>
            Worst run: {mc.worst_run.fail_mode} at t = {fmt(mc.worst_run.state.t_s, 0, 's')}, {fmt(mc.worst_run.state.grade_pct, 1, '% grade')}, demand {kw(mc.worst_run.state.P_w)}, lowest cell #{mc.worst_run.state.weakest_cell} at {fmt(mc.worst_run.state.Vcell_min, 3, 'V')}, SOC {pct(mc.worst_run.state.soc)}.
          </div>}
        </div>
        <div className="panel"><h3>What drives the risk</h3>
          <div style={{ height: 220 }}><ResponsiveContainer><BarChart data={(mc.sensitivity ?? []).map((s: Any) => ({ k: s.label, v: s.share * 100, rho: s.rho }))} layout="vertical" margin={{ left: 40 }}>
            <XAxis type="number" tick={{ fontSize: 11 }} unit="%" /><YAxis type="category" dataKey="k" width={130} tick={{ fontSize: 11 }} /><Tooltip />
            <Bar dataKey="v" fill="var(--ink)" radius={[0, 5, 5, 0]} /></BarChart></ResponsiveContainer></div>
          <p className="hint">Share of squared Spearman correlation with each run's minimum safety margin. An approximate attribution, not a variance decomposition.</p>
        </div>
      </div>
    </div>
  )
}

/* ================================================================== fleet (B) */
function Fleet({ res, rerun, say }: { res: Any; rerun: P['rerun']; say: (m: string) => void }) {
  const B = res.results.B
  const act = async (u: string, m: string) => { try { await api.post(u); say(m); rerun('B') } catch (e) { say((e as Error).message) } }
  const colors = ['#1d4ed8', '#0f9d58', '#e08a00', '#7c3aed', '#0ea5a4']
  return (
    <div style={{ display: 'grid', gap: 18 }}>
      <div className="row">
        <button className="btn primary" onClick={() => act('/api/fleet/snapshot', 'This battery was added to the fleet')}>Add this battery to fleet</button>
        <button className="btn" onClick={() => act('/api/fleet/seed', 'Synthetic demo fleet added')}>Add synthetic demo fleet</button>
        <button className="btn" onClick={() => act('/api/fleet/clear-synthetic', 'Synthetic batteries removed')}>Remove synthetic</button>
        <span className="hint">Measured batteries are kept; synthetic ones are tagged and never count as evidence.</span>
      </div>
      {!B ? <div className="note">Run engine B.</div> : B.error ? <div className="warnbox">{B.error}</div> : (
        <>
          {B.synthetic_share > 0 && <div className="warnbox">{pct(B.synthetic_share, 0)} of this population is synthetic. Its clusters demonstrate the method, not your batteries.</div>}
          <div className="grid2">
            <div className="panel"><h3>Population: capacity vs resistance</h3>
              <div style={{ height: 320 }}><ResponsiveContainer><ScatterChart margin={{ left: 0, bottom: 10 }}>
                <CartesianGrid stroke="var(--line)" /><XAxis type="number" dataKey="q" name="Capacity" unit=" Ah" tick={{ fontSize: 11 }} domain={['auto', 'auto']} />
                <YAxis type="number" dataKey="r" name="DCIR" unit=" mΩ" tick={{ fontSize: 11 }} domain={['auto', 'auto']} /><ZAxis range={[30, 30]} /><Tooltip />
                {B.clusters.map((c: Any) => <Scatter key={c.k} name={c.name} data={B.points.filter((p: Any) => p.cluster === c.k).map((p: Any) => ({ q: p.x[0], r: p.x[1], id: p.id }))} fill={colors[c.k % 5]} />)}
                <Scatter name="outliers" data={B.points.filter((p: Any) => p.outlier).map((p: Any) => ({ q: p.x[0], r: p.x[1] }))} fill="var(--fail)" shape="cross" />
                {B.current && <ReferenceDot x={B.current.x[0]} y={B.current.x[1]} r={9} fill="none" stroke="var(--ink)" strokeWidth={3} />}
              </ScatterChart></ResponsiveContainer></div>
              <p className="hint">Ring = this battery. Red crosses = rejected in stage 1 ({B.n_outliers} of {B.n}). {B.c} clusters chosen by the Xie–Beni index ({B.xie_beni.toFixed(3)}).</p>
            </div>
            <div className="panel"><h3>Share of each cluster that fits each vehicle</h3>
              <table className="t"><thead><tr><th>Vehicle</th>{B.clusters.map((c: Any) => <th key={c.k} style={{ color: colors[c.k % 5] }}>{c.name}<div className="hint">{c.size} batteries</div></th>)}</tr></thead>
                <tbody>{B.matrix.map((m: Any) => <tr key={m.vehicle}><td>{m.name}</td>{m.pass_rate.map((r: number, k: number) => <td key={k} style={{ background: `rgba(15,157,88,${r * 0.35})` }}>{pct(r, 0)}</td>)}</tr>)}</tbody></table>
              {B.current && <>
                <h3 style={{ marginTop: 16 }}>This battery</h3>
                <KV rows={[...B.clusters.map((c: Any, k: number) => [c.name, pct(B.current.membership[k], 0), 'fuzzy membership'] as [string, string, string]),
                  ['Outlier vs population', B.current.outlier ? 'Yes: score forced to 0' : 'No'],
                  ['Meets this vehicle directly', B.current.direct_pass ? 'Yes' : 'No']]} />
              </>}
            </div>
          </div>
        </>
      )}
    </div>
  )
}

/* ================================================================== evidence */
function Evidence({ bat }: { bat: Any }) {
  if (!bat) return <div className="note">No battery data.</div>
  const ev = bat.evidence, ecm = bat.electrical.ecm, tr = ev.truth
  const fit = ev.last_fit
  const curve = fit ? fit.curve.t.map((t: number, k: number) => ({ t, v: fit.curve.v[k], fit: fit.curve.fit[k] })) : []
  const cell = ecm.cell, n = bat.identity.series
  const est = { R0: bat.electrical.dcir ? cell.R0 * n * 1000 : null, R1: cell.R1 * n * 1000, tau1: cell.tau1, R2: cell.R2 * n * 1000, tau2: cell.tau2 }
  return (
    <div className="grid2">
      <div className="panel">
        <h3>Identified 2RC model (pack)</h3>
        <table className="t"><thead><tr><th>Parameter</th><th>Estimated</th>{tr && <th>Simulator truth</th>}<th>Source</th></tr></thead><tbody>
          <tr><td>R0</td><td>{fmt(est.R0, 2, 'mΩ')}</td>{tr && <td>{fmt(tr.R0_pack_mohm, 2, 'mΩ')} at 25 °C</td>}<td className="hint">{ecm.provenance.R0}</td></tr>
          <tr><td>R1 / τ1</td><td>{fmt(est.R1, 2, 'mΩ')} / {fmt(est.tau1, 1, 's')}</td>{tr && <td>{fmt(tr.R1_pack_mohm, 2)} / {fmt(tr.tau1, 1)}</td>}<td className="hint" rowSpan={2}>{ecm.provenance.RC}</td></tr>
          <tr><td>R2 / τ2</td><td>{fmt(est.R2, 2, 'mΩ')} / {fmt(est.tau2, 0, 's')}</td>{tr && <td>{fmt(tr.R2_pack_mohm, 2)} / {fmt(tr.tau2, 0)}</td>}</tr>
          <tr><td>Capacity</td><td>{fmt(bat.degradation.capacity.value, 1, 'Ah')}</td>{tr && <td>{fmt(tr.Q_Ah_mean, 1, 'Ah')}</td>}<td className="hint">{bat.degradation.capacity.source} ({bat.degradation.capacity.confidence} confidence)</td></tr>
          <tr><td>Weakest cell</td><td>#{bat.electrical.weakest_cell + 1}</td>{tr && <td>#{tr.weak_cell + 1}</td>}<td className="hint">highest per-cell DCIR</td></tr>
        </tbody></table>
        {tr && <p className="hint">Demo mode: the simulator's true parameters are shown so you can judge the estimator. Real hardware has no truth column.</p>}
        {ev.missing.length > 0 && <div className="warnbox" style={{ marginTop: 10 }}>Missing evidence: {ev.missing.join(', ')}.</div>}
      </div>
      <div className="panel">
        <h3>Last relaxation fit {fit && <span className="hint">rmse {fmt(fit.rmse_mV, 2, 'mV')} · {fit.n} points · after {fmt(fit.I_load, 0, 'A')} for {fmt(fit.T_load, 0, 's')}</span>}</h3>
        {fit ? <div style={{ height: 260 }}><ResponsiveContainer><LineChart data={curve}><CartesianGrid stroke="var(--line)" vertical={false} />
          <XAxis dataKey="t" unit="s" tick={{ fontSize: 11 }} /><YAxis domain={['auto', 'auto']} tick={{ fontSize: 11 }} /><Tooltip />
          <Line dataKey="v" stroke="var(--mute)" dot={{ r: 1.5 }} strokeWidth={0} isAnimationActive={false} name="measured" />
          <Line dataKey="fit" stroke="var(--cobalt)" dot={false} strokeWidth={2} isAnimationActive={false} name="2RC fit" /></LineChart></ResponsiveContainer></div>
          : <div className="note">Waiting for a load followed by at least 90 s of rest.</div>}
        <table className="t" style={{ marginTop: 10 }}><tbody>
          <tr><td className="label">Source</td><td>{ev.source}</td></tr>
          <tr><td className="label">DCIR events</td><td>{ev.n_dcir_events}</td></tr>
          <tr><td className="label">BMS faults</td><td>{ev.faults.length ? ev.faults.join(', ') : 'none'}</td></tr>
          <tr><td className="label">OCV anchors</td><td>{ev.anchors.length}</td></tr>
        </tbody></table>
      </div>
    </div>
  )
}

/* ================================================================== method & proof */
function Method() {
  const cite = (id: string | number) => CITES.find((c) => String(c.id) === String(id))
  const Ref = ({ ids }: { ids: (string | number)[] }) => (
    <ul className="cite">{ids.map((id) => {
      const c = cite(id)
      if (!c) return <li key={id}>[{id}] verification pending</li>
      return <li key={id}><b style={{ color: statusColor(c.status === 'VERIFIED' ? 'PASS' : c.status === 'PARTIAL' ? 'MARGINAL' : 'FAIL') }}>{c.status}</b>{' '}
        {c.authors} ({c.year}). <a href={c.url} target="_blank" rel="noreferrer">{c.title}</a>. <i>{c.venue}</i>. {c.claim}{c.note && <span className="hint"> ({c.note})</span>}</li>
    })}</ul>
  )
  return (
    <div style={{ display: 'grid', gap: 22, maxWidth: 1100 }}>
      {CITES.length === 0 && <div className="warnbox">Citation verification has not finished yet. Every reference below is unverified until it has.</div>}
      <section className="grid2">
        <div><h2>Gate, shared by every engine</h2>
          <div className="math">Gᵢ ∈ {'{'}PASS, FAIL, UNKNOWN{'}'}  for chemistry, voltage window, current limit,{'\n'}connector, protocol, mechanical, telemetry integrity, cell faults, temperature</div>
          <p>A FAIL makes the pair incompatible regardless of any score. UNKNOWN means test required: scores are shown but marked provisional. Limits are chemistry profiles, not universal constants.</p>
          <Ref ids={[2]} /></div>
        <div><h2>A. SafeFit-R2, weakest link</h2>
          <div className="math">BSS = 100 (H_Q · H_R · H_B · H_T)^¼        SOC excluded{'\n'}F = m a + m g C_rr cos θ + ½ ρ C_d A v² + m g sin θ{'\n'}P_bat = F v / η + P_aux,    E_mission = ∫ P_bat dt{'\n'}M_E = E_usable / E_mission,   M_P = SOP₃₀ₛ / P₃₀ₛ,req{'\n'}C_A = 100 · G · min(S_E, S_P, S_V, S_T, S_H)</div>
          <p><b>Why it is defensible:</b> with a min-aggregator, a deficient dimension bounds the score. Health 100, energy 100 and power 42 gives 42, where a weighted mean could give 85. Given correct constraints and measurements, a FAIL gate forces 0, so an unsafe pairing cannot be scored as compatible by construction. Each score S is a piecewise-linear map of its margin with explicit fail, pass and target anchors.</p>
          <Ref ids={[1, 3, 10]} /></div>
      </section>
      <section className="grid2">
        <div><h2>B. Application-aware sorting</h2>
          <div className="math">x_b = [Q, DCIR, dT/dt at 1C, ΔV at rest]   (robust-scaled){'\n'}Stage 1: physics check + Local Outlier Factor{'\n'}Stage 2: fuzzy C-means, c by Xie–Beni{'\n'}   u_ik = 1 / Σ_j (‖x_i − c_k‖ / ‖x_i − c_j‖)^(2/(m−1)){'\n'}Stage 3: C_B = 100 · Σ_k u_bk · passrate_k(vehicle)</div>
          <p><b>Why:</b> it learns similarity from measured behaviour rather than hand-set weights. The score means "batteries that behave like this one meet this vehicle's derived limits C_B % of the time". It needs a population; with one battery it reports that rather than a number.</p>
          <Ref ids={[4, 5, 6, 7, 'fcm', 'xb', 'lof']} /></div>
        <div><h2>C. Probabilistic electro-thermal twin</h2>
          <div className="math">V_t = OCV(z,T) − I R₀ − V₁ − V₂{'\n'}dV_k/dt = −V_k/(R_k C_k) + I/C_k,    dz/dt = −I/(3600 Q){'\n'}C_th dT/dt = I²R₀ + I(V₁+V₂) − hA (T − T_amb){'\n'}C_C = P(success) = k/N,   Wilson 95 % interval</div>
          <p><b>Why:</b> it answers the question you actually care about: can this battery execute this vehicle's mission, with what margin, and what fails first. Parameters come from HPPC-style pulses or natural load steps and relaxation. The score is not an index. It is the fraction of declared scenarios that passed the declared constraints, with an interval and a failure location.</p>
          <Ref ids={[8, 9, 11, 12, 13, 14, 'wilson']} /></div>
      </section>
      <section><h2>What is and isn't proven</h2>
        <p style={{ maxWidth: 820 }}>The literature supports the building blocks: multi-factor sorting, clustering, equivalent-circuit identification, constrained SOP and physics-informed modelling. It does not validate this integrated engine. That requires comparing its predictions against real missions on your batteries. Until then every score is a model output. The Raw evidence tab shows exactly what each one rests on.</p>
        <Ref ids={[15]} /></section>
    </div>
  )
}

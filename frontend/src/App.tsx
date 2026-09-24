import { AnimatePresence, motion } from 'framer-motion'
import { lazy, Suspense, useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { api, fmt, kw, pct, ramp, statusColor, useLive, type Any } from './api'
import Details from './Details'
import Dock from './Dock'

const Battery3D = lazy(() => import('./Battery3D'))

type Alg = 'A' | 'B' | 'C' | 'ALL'
const ALG_LABEL: Record<Alg, string> = { A: 'A  SafeFit', B: 'B  Sorting', C: 'C  Twin', ALL: 'Compare all' }

export default function App() {
  const [live, wsUp] = useLive()
  const [vehicles, setVehicles] = useState<Any[]>([])
  const [vid, setVid] = useState('trolley-12')
  const [alg, setAlg] = useState<Alg>('A')
  const [evalRes, setEvalRes] = useState<Any | null>(null)
  const [busy, setBusy] = useState(false)
  const [err, setErr] = useState<string | null>(null)
  const [exploded, setExploded] = useState(false)
  const [cellMode, setCellMode] = useState<'voltage' | 'resistance'>('voltage')
  const [drawer, setDrawer] = useState<string | null>(null)
  const [dropStage, setDropStage] = useState(false)
  const [dropBat, setDropBat] = useState(false)
  const [toast, setToast] = useState<string | null>(null)

  const bat = live?.battery && !live.battery.error ? live.battery : null
  const link = live?.link
  const veh = vehicles.find((v) => v.id === vid)

  const say = (m: string) => { setToast(m); window.setTimeout(() => setToast(null), 2600) }
  const loadVehicles = useCallback(() => api.get('/api/vehicles').then(setVehicles), [])
  useEffect(() => { loadVehicles() }, [loadVehicles])

  const evaluate = useCallback(async (which: Alg = alg, quiet = false) => {
    if (!bat) return
    if (!quiet) setBusy(true)
    try {
      const algorithms = which === 'ALL' ? ['A', 'B', 'C'] : which === 'A' ? ['A'] : ['A', which]
      const r = await api.post('/api/evaluate', { vehicle: vid, algorithms, runs: 200 })
      setEvalRes(r)
      setErr(null)
    } catch (e) {
      setErr((e as Error).message)
    } finally {
      setBusy(false)
    }
  }, [alg, vid, bat])

  // re-evaluate when the question changes; keep the cheap engine (A) fresh while live data flows
  const hasBat = !!bat
  const series = bat?.identity?.series
  useEffect(() => {
    if (!series || !vehicles.length) return
    const vnom = series * 3.2
    const cur = vehicles.find((v) => v.id === vid)
    if (cur && vnom >= cur.bus_v_min && series * 3.65 <= cur.bus_v_max) return
    const fit = vehicles.find((v) => vnom >= v.bus_v_min && series * 3.65 <= v.bus_v_max)
    if (fit) setVid(fit.id)
  }, [series, vehicles.length]) // eslint-disable-line react-hooks/exhaustive-deps
  useEffect(() => { if (hasBat) evaluate(alg) }, [vid, alg, hasBat]) // eslint-disable-line react-hooks/exhaustive-deps
  const evalRef = useRef(evaluate)
  evalRef.current = evaluate
  useEffect(() => {
    if (alg !== 'A') return
    const t = window.setInterval(() => evalRef.current('A', true), 6000)
    return () => window.clearInterval(t)
  }, [alg])

  const startDemo = async (series = 4) => { await api.post(`/api/link/demo?speed=8&series=${series}`); say(`Demo ${series}-cell battery connected on a virtual Daly bus`) }

  const onStageDrop = async (e: React.DragEvent) => {
    e.preventDefault(); setDropStage(false)
    const id = e.dataTransfer.getData('vehicle-id')
    if (id) { setVid(id); return }
    const f = e.dataTransfer.files[0]
    if (f && f.name.endsWith('.json')) {
      try {
        const v = await api.post('/api/vehicles', JSON.parse(await f.text()))
        await loadVehicles(); setVid(v.id); say(`Vehicle profile ${v.name} loaded`)
      } catch (x) { say(`Vehicle profile rejected: ${(x as Error).message}`) }
    }
  }
  const onBatDrop = async (e: React.DragEvent) => {
    e.preventDefault(); setDropBat(false)
    const f = e.dataTransfer.files[0]
    if (!f) return
    const fd = new FormData(); fd.append('file', f); fd.append('series', String(bat?.identity?.series ?? 16))
    try {
      const r = await api.post('/api/ingest', fd)
      say(`${r.rows} rows loaded from ${f.name}${r.current_flipped ? ' (current sign flipped to discharge-positive)' : ''}`)
    } catch (x) { say(`File not loaded: ${(x as Error).message}`) }
  }

  // ---------- score shown in the band
  const shown = useMemo(() => {
    if (!evalRes) return null
    const R = evalRes.results
    if (alg === 'ALL') return { score: evalRes.fusion.conservative, label: 'most conservative of A, B, C' }
    const r = R[alg]
    if (!r) return null
    if (r.error) return { score: null, label: r.error }
    return { score: r.score, label: r.meaning, provisional: r.provisional }
  }, [evalRes, alg])

  const A = evalRes?.results?.A
  const gate = evalRes?.gate
  const scoreColor = gate?.verdict === 'FAIL' ? 'var(--fail)' : shown?.score == null ? 'var(--mute)'
    : shown.score >= 75 ? 'var(--pass)' : shown.score >= 45 ? 'var(--warn)' : 'var(--fail)'

  return (
    <div className="app">
      {/* ================================================================ top */}
      <header className="topbar">
        <div className="brand">
          <b>Battery Workbench</b>
          <span className="hint">{!wsUp ? 'engine offline'
            : link?.kind && link.kind !== 'none' ? `${link.kind === 'rs485' ? `RS485 ${link.port ?? ''}` : link.kind} · ${link.codec ?? ''}`
              : link?.watchdog === 'waiting' ? 'waiting for the adapter…' : link?.watchdog === 'lost' ? 'adapter unplugged, will reconnect' : 'no battery connected'}</span>
        </div>
        <div className="cards">
          <button className="idcard" onClick={() => setDrawer('evidence')} title="Battery evidence">
            <span className="swatch" style={{ background: 'var(--cobalt)' }}>{bat ? `${bat.identity.series}s` : '—'}</span>
            <span style={{ minWidth: 0 }}>
              <div className="t">{bat?.identity.label ?? 'No battery'}</div>
              <div className="s">{bat ? `${bat.identity.chemistry} · ${fmt(bat.identity.nominal_V, 1, 'V')} · ${fmt(bat.identity.nominal_Ah, 0, 'Ah')} · ${fmt(bat.identity.nominal_kWh, 2, 'kWh')} nominal` : 'Start the demo, open RS485, or drop a test file'}</div>
            </span>
          </button>
          <button className="idcard" onClick={() => setDrawer('vehicle')} title="Edit the application this battery is tested against">
            <span className="swatch" style={{ background: veh?.color ?? 'var(--ink)' }}>{veh ? veh.name.slice(0, 1) : '?'}</span>
            <span style={{ minWidth: 0 }}>
              <div className="t">Tested against: {veh?.name ?? 'choose an application'}</div>
              <div className="s">{veh ? `${veh.bus_v_min}–${veh.bus_v_max} V · ${veh.peak_power_kw} kW peak · ${veh.target_range_km} km duty` : ''}</div>
            </span>
          </button>
        </div>
        <div className="controls">
          <div className="seg" role="group" aria-label="Algorithm">
            {(Object.keys(ALG_LABEL) as Alg[]).map((a) => (
              <button key={a} aria-pressed={alg === a} onClick={() => setAlg(a)}>{ALG_LABEL[a]}</button>
            ))}
          </div>
          <button className="btn" onClick={() => setDrawer('hardware')}>Hardware</button>
        </div>
      </header>

      {/* ================================================================ main */}
      <main className="main">
        <section
          className={`stage ${dropStage ? 'drop-hot' : ''}`}
          onDragOver={(e) => { e.preventDefault(); setDropStage(true) }}
          onDragLeave={() => setDropStage(false)}
          onDrop={onStageDrop}
          aria-label="Battery under test"
        >
          <Suspense fallback={<div style={{ display: 'grid', placeItems: 'center', height: '100%' }}><span className="spin" /></div>}>
            <Battery3D
              open={exploded}
              cells={bat?.electrical.cells ?? Array(4).fill(3.3)}
              cellMode={cellMode}
              cellR={bat?.electrical.cell_dcir_mohm}
              weakest={bat?.electrical.weakest_cell}
              current={bat?.electrical.current ?? 0}
              soc={bat?.state.soc ?? 0.5}
              cRate={bat ? Math.abs(bat.electrical.current) / (bat.identity.nominal_Ah || 100) : 0}
            />
          </Suspense>
          <div className="stage-overlay">
            <div>
              <h1 className="num-l">{bat ? `${fmt(bat.electrical.pack_v, 2)} V` : 'No battery'}</h1>
              <div className="label" style={{ marginTop: 6 }}>
                {bat ? `${bat.identity.series === 1 ? 'single cell' : `${bat.identity.series} cells in series`} · ${bat.identity.chemistry} · ${
                  Math.abs(bat.electrical.current) < 1 ? 'resting' : bat.electrical.current > 0 ? `discharging at ${fmt(bat.electrical.current / (bat.identity.nominal_Ah || 100), 2)}C` : `charging at ${fmt(-bat.electrical.current / (bat.identity.nominal_Ah || 100), 2)}C`}` : 'Connect RS485 or start the demo'}
              </div>
            </div>
            <div className="row">
              <div className="seg">
                <button aria-pressed={!exploded} onClick={() => setExploded(false)}>Battery</button>
                <button aria-pressed={exploded} onClick={() => setExploded(true)}>Open cells</button>
              </div>
              {exploded && (
                <div className="seg">
                  <button aria-pressed={cellMode === 'voltage'} onClick={() => setCellMode('voltage')}>Voltage</button>
                  <button aria-pressed={cellMode === 'resistance'} onClick={() => setCellMode('resistance')}>Resistance</button>
                </div>
              )}
            </div>
          </div>
          <div className="stage-foot">
            <span className="hint" style={{ marginRight: 4 }} title="Drag a chip onto the battery, or drop a profile .json">Test against</span>
            {vehicles.map((v) => (
              <span key={v.id} className={`chip ${v.id === vid ? 'active' : ''}`} draggable
                onDragStart={(e) => e.dataTransfer.setData('vehicle-id', v.id)}
                onClick={() => setVid(v.id)} role="button" tabIndex={0}
                onKeyDown={(e) => e.key === 'Enter' && setVid(v.id)}>
                <span className="dot" style={{ background: v.color }} />{v.name}
              </span>
            ))}
            <span className="chip" role="button" tabIndex={0} onClick={() => setDrawer('vehicle-new')}>+ New application</span>
          </div>
        </section>

        <aside
          className={`side ${dropBat ? 'drop-hot' : ''}`}
          onDragOver={(e) => { e.preventDefault(); setDropBat(true) }}
          onDragLeave={() => setDropBat(false)}
          onDrop={onBatDrop}
          aria-label="Battery"
        >
          {bat ? <BatteryPanel bat={bat} /> : <EmptyBattery wsUp={wsUp} onDemo={startDemo} onHw={() => setDrawer('hardware')} />}
        </aside>
      </main>

      {/* ================================================================ band */}
      <Dock live={live} say={say} compat={<div className="band">
        <div>
          <div className="score">
            <motion.span className="num-xl" style={{ color: scoreColor }} key={Math.round(shown?.score ?? -1)}
              initial={{ opacity: 0.2, y: 6 }} animate={{ opacity: 1, y: 0 }} transition={{ duration: 0.35 }}>
              {gate?.verdict === 'FAIL' ? '0' : shown?.score == null ? '—' : Math.round(shown.score)}
            </motion.span>
            <small>%</small>
          </div>
          <div className="row" style={{ marginTop: 4 }}>
            {gate && <span className="verdict" style={{ background: statusColor(gate.verdict === 'UNKNOWN' ? 'UNKNOWN' : gate.verdict) }}>
              {evalRes.fusion.verdict === 'SCORED' ? 'Gate passed' : evalRes.fusion.verdict === 'INCOMPATIBLE' ? 'Incompatible' : 'Test required'}
            </span>}
            {shown?.provisional && <span className="hint">provisional</span>}
            {busy && <span className="spin" />}
          </div>
        </div>
        <div style={{ minWidth: 0 }}>
          <div className="meter" aria-label="Compatibility">
            <motion.div animate={{ width: `${Math.max(0, Math.min(100, gate?.verdict === 'FAIL' ? 0 : shown?.score ?? 0))}%` }}
              transition={{ type: 'spring', stiffness: 60, damping: 16 }} style={{ background: scoreColor }} />
          </div>
          {A && (
            <div className="checks">
              {['voltage', 'energy', 'power', 'thermal', 'health'].map((k) => {
                const d = A.dimensions[k]
                const icon = d.status === 'PASS' ? '✓' : d.status === 'FAIL' ? '✕' : d.status ? '!' : '?'
                return (
                  <span className="check" key={k}>
                    <i style={{ background: statusColor(d.status) }}>{icon}</i>
                    {k[0].toUpperCase() + k.slice(1)}
                    <span className="hint">{d.margin == null ? '' : `×${d.margin.toFixed(2)}`}</span>
                  </span>
                )
              })}
              {gate?.checks.filter((c: Any) => c.status === 'FAIL' || c.status === 'UNKNOWN').map((c: Any) => (
                <span className="check" key={c.key}><i style={{ background: statusColor(c.status) }}>{c.status === 'FAIL' ? '✕' : '?'}</i>{c.name}</span>
              ))}
            </div>
          )}
          <div className="limiting">
            {err ? <span style={{ color: 'var(--fail)' }}>{err}</span>
              : !evalRes ? (bat ? 'Evaluating…' : 'Connect a battery to see how well it fits this vehicle.')
                : gate.verdict === 'FAIL' ? <>Incompatible: <b>{gate.checks.filter((c: Any) => c.status === 'FAIL').map((c: Any) => `${c.name} (${c.evidence})`).join('; ')}</b></>
                  : <LimitLine res={evalRes} alg={alg} />}
          </div>
        </div>
        <div className="row">
          <button className="btn" onClick={() => evaluate(alg)} disabled={!bat || busy}>Re-run</button>
          <button className="btn primary" onClick={() => setDrawer('overview')} disabled={!evalRes}>Details</button>
        </div>
      </div>} />

      <AnimatePresence>
        {drawer && (
          <Details key="d" tab={drawer} setTab={setDrawer} close={() => setDrawer(null)} res={evalRes} live={live}
            vehicles={vehicles} vid={vid} setVid={setVid} reloadVehicles={loadVehicles} say={say}
            rerun={(a: Alg) => { setAlg(a); evaluate(a) }} />
        )}
      </AnimatePresence>
      {toast && <div className="toast" role="status">{toast}</div>}
    </div>
  )
}

function LimitLine({ res, alg }: { res: Any; alg: Alg }) {
  const A = res.results.A
  const C = res.results.C
  const B = res.results.B
  if (alg === 'C' && C) {
    const mc = C.monte_carlo
    const top = Object.entries(mc.failure_modes)[0] as [string, number] | undefined
    const s = mc.sensitivity?.[0]
    return <>P(mission success) {pct(mc.p_success)} <span className="hint">[95% CI {pct(mc.ci95[0], 0)}–{pct(mc.ci95[1], 0)}, N={mc.N}]</span>
      {top && <> · most common failure: <b>{top[0]}</b> ({top[1]} runs)</>}{s && <> · biggest driver: <b>{s.label}</b></>}</>
  }
  if (alg === 'B' && B) {
    if (B.error) return <span className="hint">{B.error}</span>
    const u = B.current?.membership ?? []
    const k = u.indexOf(Math.max(...u))
    return <>Behaves like <b>{B.clusters[k]?.name}</b> ({pct(u[k], 0)} membership){B.current?.outlier && <> · <b style={{ color: 'var(--fail)' }}>outlier vs population</b></>}
      {B.synthetic_share > 0.5 && <span className="hint"> · population is mostly synthetic</span>}</>
  }
  if (!A) return null
  const d = A.details
  const lim = A.limiting
  const reqTxt: Record<string, string> = {
    power: `Required ${kw(d.power.P_30s_req_w)} for 30 s · safe capability ${kw(d.power.SOP_30s_eom_w)} at ${pct(d.power.eom_soc, 0)} SOC`,
    energy: `Mission needs ${fmt(d.energy.E_mission_wh / 1000, 2, 'kWh')} · usable ${fmt(d.energy.E_usable_full_wh / 1000, 2, 'kWh')} from full`,
    voltage: `Loaded ${fmt(d.voltage.v_loaded_eom, 1, 'V')} vs cut-off ${fmt(d.voltage.bus_v_min, 0, 'V')}`,
    thermal: `Predicted rise ${fmt(d.thermal.dT_mission, 1, '°C')} vs allowed ${fmt(d.thermal.allowed_rise, 1, '°C')}`,
    health: `Battery Stress Score ${fmt(d.health.value, 0)}`,
  }
  return <>Limiting factor: <b>{A.limiting_text}</b> · {reqTxt[lim]}</>
}

/* ------------------------------------------------------------------ battery panel */
function Gauge({ label, value, max = 1, text, sub }: { label: string; value: number | null; max?: number; text: string; sub?: string }) {
  const f = value == null ? 0 : Math.max(0, Math.min(1, value / max))
  const R = 42, C = Math.PI * R // half circle
  return (
    <div style={{ textAlign: 'center' }}>
      <svg viewBox="0 0 100 58" width="100%" aria-hidden>
        <path d="M8 52 A42 42 0 0 1 92 52" fill="none" stroke="var(--wash)" strokeWidth="9" strokeLinecap="round" />
        <motion.path d="M8 52 A42 42 0 0 1 92 52" fill="none" stroke={ramp(0.05 + (1 - f) * 0.9)} strokeWidth="9" strokeLinecap="round"
          strokeDasharray={C} animate={{ strokeDashoffset: C * (1 - f) }} transition={{ type: 'spring', stiffness: 50, damping: 14 }} />
      </svg>
      <div className="num-m" style={{ marginTop: -24 }}>{text}</div>
      <div className="label">{label}</div>
      {sub && <div className="hint">{sub}</div>}
    </div>
  )
}

function BatteryPanel({ bat }: { bat: Any }) {
  const st = bat.state, el = bat.electrical, th = bat.thermal, dg = bat.degradation
  const cells: number[] = el.cells
  const mean = cells.reduce((a, b) => a + b, 0) / cells.length
  const I = el.current
  return (
    <>
      <div className="section-head">
        <h2>Battery</h2>
        <span className="label">{Math.abs(I) < 1 ? 'resting' : I > 0 ? `discharging ${fmt(I, 1, 'A')}` : `charging ${fmt(-I, 1, 'A')}`}</span>
      </div>
      <div className="gauges">
        <Gauge label="State of charge" value={st.soc} text={pct(st.soc, 1)} sub={st.soc_source === 'BMS' ? 'from BMS' : 'from OCV'} />
        <Gauge label="Capacity health" value={st.soh_q} text={pct(st.soh_q, 1)} sub={dg.capacity.source.split(' (')[0]} />
        <Gauge label="Power, 30 s" value={st.sop['30s'].P_w} max={bat.identity.nominal_V * (bat.identity.i_max_dis ?? 150)} text={kw(st.sop['30s'].P_w, 1)} sub={el.dcir ? `limit: ${st.sop['30s'].limiter}` : 'assumed resistance, not measured yet'} />
      </div>
      <div className="stats">
        <Stat k="Pack voltage" v={fmt(el.pack_v, 2, 'V')} />
        <Stat k="Power" v={kw(el.power_w, 2)} />
        <Stat k="Energy stored" v={fmt(st.soe_wh / 1000, 2, 'kWh')} />
        <Stat k="DC resistance" v={el.dcir ? fmt(el.dcir.value * 1000, 1, 'mΩ') : '—'} s={el.dcir ? `${el.dcir.n} steps` : 'waiting for load step'} />
        <Stat k="Resistance health" v={pct(st.soh_r, 0)} />
        <Stat k="SOP 10 s" v={kw(st.sop['10s'].P_w, 1)} />
        <Stat k="Continuous safe" v={kw(st.sop.continuous.P_w, 1)} s={st.sop.continuous.limiter} />
        <Stat k="Cell spread" v={fmt(el.dv_mv, 0, 'mV')} s={el.rest_dv_mv != null ? `${fmt(el.rest_dv_mv, 0)} at rest` : ''} />
        <Stat k="Temperature" v={fmt(th.t_max, 1, '°C')} s={th.dt_cells != null ? `ΔT ${fmt(th.dt_cells, 1)}` : ''} />
        <Stat k="Heating rate" v={fmt(th.dTdt_c_per_min, 2, '°C/min')} />
        <Stat k="Cycles" v={dg.cycles ?? '—'} />
        <Stat k="Remaining life" v="—" s="needs history" />
      </div>
      <div>
        <div className="section-head">
          <h3>Cells</h3>
          <span className="hint">min {fmt(el.cell_min, 3)} · max {fmt(el.cell_max, 3)} V · outlined = weakest</span>
        </div>
        <div className="cells">
          {cells.map((v, i) => (
            <div key={i} className={`cell ${i === el.weakest_cell ? 'weak' : ''}`} style={{ background: ramp(0.45 + (mean - v) / 0.04) }}>
              <span style={{ opacity: 0.85 }}>{i + 1}</span>
              <b>{v.toFixed(3)}</b>
            </div>
          ))}
        </div>
      </div>
      {bat.evidence.missing.length > 0 && (
        <div className="hint">Still estimating: {bat.evidence.missing.join(', ')}.</div>
      )}
    </>
  )
}

function Stat({ k, v, s }: { k: string; v: React.ReactNode; s?: string }) {
  return <div className="stat"><span className="label">{k}</span><span className="v">{v}{s && <small>{s}</small>}</span></div>
}

function EmptyBattery({ wsUp, onDemo, onHw }: { wsUp: boolean; onDemo: (series: number) => void; onHw: () => void }) {
  const [series, setSeries] = useState(4)
  return (
    <div style={{ margin: 'auto 0', display: 'flex', flexDirection: 'column', gap: 14 }}>
      <h2 style={{ fontSize: 24 }}>Connect a battery</h2>
      {!wsUp && <div className="failbox">The analysis engine is not running. Start it with <b>start.ps1</b> or <code>npm run engine</code>.</div>}
      <p className="label" style={{ margin: 0, maxWidth: 420 }}>
        Plug your USB‑RS485 adapter in and scan it from Hardware, drop a cycler export (.csv, .xlsx) on this panel,
        or run a demo battery: a simulated LFP battery behind a virtual Daly BMS. The cell count is read from
        the BMS, so the view adapts to whatever you connect.
      </p>
      <div className="row">
        <div className="seg" role="group" aria-label="Demo cell count">
          {[1, 4, 8, 16].map((s) => <button key={s} aria-pressed={series === s} onClick={() => setSeries(s)}>{s === 1 ? '1 cell' : `${s}s · ${(s * 3.2).toFixed(1)} V`}</button>)}
        </div>
      </div>
      <div className="row">
        <button className="btn primary" onClick={() => onDemo(series)} disabled={!wsUp}>Start demo battery</button>
        <button className="btn" onClick={onHw}>Open Hardware</button>
      </div>
    </div>
  )
}

import { useEffect, useState } from 'react'
import { api, type Any } from './api'

const GROUPS: [string, [string, string, string?][]][] = [
  ['Identity', [['name', 'Name'], ['kind', 'Body shape', 'kind'], ['color', 'Colour', 'color'], ['cycle', 'Duty cycle', 'cycle']]],
  ['Mass and road load', [['mass_kg', 'Kerb mass without battery (kg)'], ['payload_kg', 'Payload (kg)'], ['CdA', 'Drag area CdA (m²)'], ['Crr', 'Rolling resistance Crr'],
    ['top_speed_kmh', 'Top speed (km/h)'], ['accel_max', 'Max acceleration (m/s²)'], ['max_grade_pct', 'Max grade (%)']]],
  ['Drivetrain', [['eta_drive', 'Drive efficiency'], ['eta_regen', 'Regen efficiency'], ['regen_max_kw', 'Regen limit (kW)'], ['peak_power_kw', 'Peak power (kW)'],
    ['cont_power_kw', 'Continuous power (kW)'], ['max_current_a', 'Controller current limit (A)'], ['aux_w', 'Auxiliary load (W)']]],
  ['Electrical interface', [['bus_v_min', 'Controller cut-off (V)'], ['bus_v_max', 'Controller max (V)'], ['connector', 'Required connector', 'text'], ['protocol', 'Required BMS protocol', 'text'],
    ['battery_mass_allow_kg', 'Battery mass allowance (kg, 0 = any)']]],
  ['Mission', [['target_range_km', 'Mission distance (km)'], ['reserve_soc', 'Reserve SOC (0–1)'], ['ambient_c', 'Ambient (°C)']]],
]

const BLANK = {
  id: '', name: 'My vehicle', kind: 'custom', mass_kg: 600, payload_kg: 200, CdA: 0.9, Crr: 0.015, eta_drive: 0.82, eta_regen: 0.55, regen_max_kw: 2,
  aux_w: 80, bus_v_min: 42, bus_v_max: 60, peak_power_kw: 4, cont_power_kw: 2, max_current_a: 100, target_range_km: 40, reserve_soc: 0.1,
  max_grade_pct: 8, top_speed_kmh: 45, accel_max: 1.2, ambient_c: 32, chemistry_allowed: ['LFP', 'NMC'], connector: '', protocol: '',
  battery_mass_allow_kg: 0, cycle: 'urban', color: '#1d4ed8', notes: '',
}

export default function VehicleEditor({ vehicles, vid, onSaved }: { vehicles: Any[]; vid: string | null; onSaved: (id: string) => void }) {
  const [v, setV] = useState<Any>(BLANK)
  const [cycles, setCycles] = useState<Any[]>([])
  const [req, setReq] = useState<Any | null>(null)
  useEffect(() => {
    const base = vid ? vehicles.find((x) => x.id === vid) : null
    setV(base ? { ...base } : { ...BLANK, id: `custom-${Date.now().toString(36)}` })
  }, [vid, vehicles])
  useEffect(() => { api.get('/api/cycles').then(setCycles) }, [])
  useEffect(() => {
    if (!vehicles.some((x) => x.id === v.id)) { setReq(null); return }
    api.get(`/api/requirements?vehicle=${v.id}`).then(setReq).catch(() => setReq(null))
  }, [v.id, vehicles])

  const set = (k: string, val: Any) => setV((o: Any) => ({ ...o, [k]: val }))
  const save = async () => { const r = await api.post('/api/vehicles', v); onSaved(r.id) }
  const saveAsNew = async () => { const r = await api.post('/api/vehicles', { ...v, id: `custom-${Date.now().toString(36)}`, name: `${v.name} (copy)` }); onSaved(r.id) }
  const download = () => {
    const a = document.createElement('a')
    a.href = URL.createObjectURL(new Blob([JSON.stringify(v, null, 1)], { type: 'application/json' }))
    a.download = `${v.id}.json`; a.click()
  }

  return (
    <div className="grid2" style={{ gridTemplateColumns: '1.6fr 1fr' }}>
      <div style={{ display: 'grid', gap: 18 }}>
        {GROUPS.map(([g, fields]) => (
          <div key={g}>
            <h3 style={{ marginBottom: 8 }}>{g}</h3>
            <div className="form">
              {fields.map(([k, label, type]) => (
                <label className="field" key={k}><span className="label">{label}</span>
                  {type === 'kind' ? <select value={v.kind} onChange={(e) => set('kind', e.target.value)}>{['rickshaw', 'cart', 'scooter', 'car', 'custom'].map((o) => <option key={o}>{o}</option>)}</select>
                    : type === 'cycle' ? <select value={v.cycle} onChange={(e) => set('cycle', e.target.value)}>{cycles.map((c) => <option key={c.id} value={c.id}>{c.name}</option>)}</select>
                      : type === 'color' ? <input type="color" value={v.color} onChange={(e) => set('color', e.target.value)} style={{ height: 34, padding: 2 }} />
                        : <input type={type === 'text' || k === 'name' ? 'text' : 'number'} step="any" value={v[k] ?? ''}
                          onChange={(e) => set(k, type === 'text' || k === 'name' ? e.target.value : e.target.value === '' ? 0 : +e.target.value)} />}
                </label>
              ))}
            </div>
          </div>
        ))}
        <div className="row">
          <button className="btn primary" onClick={save}>Save vehicle</button>
          <button className="btn" onClick={saveAsNew}>Save as new</button>
          <button className="btn" onClick={download}>Download profile</button>
          <span className="hint">Drop a downloaded profile on the vehicle stage to load it again.</span>
        </div>
      </div>
      <div className="panel" style={{ alignSelf: 'start' }}>
        <h3>What this vehicle asks of a battery</h3>
        {req ? <table className="t"><tbody>
          {[['Consumption', `${req.wh_per_km.toFixed(1)} Wh/km`], ['Mission energy', `${(req.E_mission_wh / 1000).toFixed(2)} kWh`], ['Peak power', `${(req.P_peak_w / 1000).toFixed(2)} kW`],
            ['30 s power', `${(req.P_30s_w / 1000).toFixed(2)} kW`], ['RMS power', `${(req.P_rms_w / 1000).toFixed(2)} kW`], ['Regen', `${(req.P_regen_w / 1000).toFixed(2)} kW`], ['Duty cycle', req.cycle]]
            .map(([k, x]) => <tr key={k}><td className="label">{k}</td><td><b>{x}</b></td></tr>)}
        </tbody></table> : <p className="hint">Save the vehicle to compute its requirements from the road-load model.</p>}
        <p className="hint">Computed from F = m·a + m·g·Crr·cos θ + ½·ρ·CdA·v² + m·g·sin θ over the selected cycle, clipped to this vehicle's top speed, acceleration and peak power. Preset numbers are illustrative; replace them with your vehicle's data.</p>
      </div>
    </div>
  )
}

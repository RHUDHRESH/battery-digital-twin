import { useEffect, useRef, useState } from 'react'
import { api, type Any, type Live } from './api'

export default function Hardware({ say, live }: { say: (m: string) => void; live: Live | null }) {
  const [ports, setPorts] = useState<Any[]>([])
  const [form, setForm] = useState({ port: '', baud: 9600, protocol: 'daly', slave: 1, poll_hz: 1, invert_current: false })
  const [sniff, setSniff] = useState<Any | null>(null)
  const [sniffing, setSniffing] = useState(false)
  const [frames, setFrames] = useState<Any[]>([])
  const [armed, setArmed] = useState(false)
  const [hex, setHex] = useState('A5 40 90 08 00 00 00 00 00 00 00 00')
  const [append, setAppend] = useState('daly_sum')
  const [recs, setRecs] = useState<Any[]>([])
  const [meta, setMeta] = useState<Any>({})
  const [map, setMap] = useState('[]')
  const [audit, setAudit] = useState<Any[]>([])
  const [prof, setProf] = useState<Any | null>(null)
  const remote = !['localhost', '127.0.0.1', '::1', '[::1]'].includes(location.hostname)
  const seq = useRef(0)
  const box = useRef<HTMLDivElement>(null)
  const link = live?.link ?? {}

  const refresh = async () => {
    const p = await api.get('/api/ports'); setPorts(p)
    if (!form.port && p.length) setForm((f) => ({ ...f, port: p[0].device }))
    setRecs(await api.get('/api/record/list'))
    setProf(await api.get('/api/link/profile'))
    setAudit(await api.get('/api/link/audit'))
  }
  useEffect(() => {
    refresh()
    api.get('/api/battery/meta').then(setMeta)
    api.get('/api/modbus/map').then((m) => setMap(JSON.stringify(m, null, 1)))
  }, []) // eslint-disable-line react-hooks/exhaustive-deps

  useEffect(() => {
    const id = window.setInterval(async () => {
      const f = await api.get(`/api/link/frames?since=${seq.current}&limit=200`)
      if (f.length) {
        seq.current = f[f.length - 1].seq
        setFrames((old) => [...old, ...f].slice(-400))
        requestAnimationFrame(() => box.current && (box.current.scrollTop = box.current.scrollHeight))
      }
    }, 600)
    return () => window.clearInterval(id)
  }, [])

  const run = async (fn: () => Promise<Any>, ok?: string) => {
    try { const r = await fn(); if (ok) say(ok); return r } catch (e) { say((e as Error).message) }
  }
  const doSniff = async () => {
    setSniffing(true); setSniff(null)
    const r = await run(() => api.post(`/api/link/sniff?port=${encodeURIComponent(form.port)}`))
    setSniffing(false)
    if (r) {
      setSniff(r)
      if (r.best) {
        const proto = r.best.protocol.startsWith('modbus') ? 'modbus' : r.best.protocol.startsWith('jbd') ? 'jbd' : 'daly'
        const slave = +(r.best.protocol.match(/slave (\d+)/)?.[1] ?? 1)
        setForm((f) => ({ ...f, baud: r.best.baud, protocol: proto, slave }))
      }
    }
  }
  const set = (k: string, v: Any) => setForm((f) => ({ ...f, [k]: v }))

  return (
    <div style={{ display: 'grid', gap: 18 }}>
      <div className="grid2">
        <div className="panel">
          <div className="section-head"><h3>RS485 adapter</h3><button className="btn" onClick={refresh}>Rescan ports</button></div>
          {ports.length === 0 && <div className="warnbox">No serial ports found. Plug in the USB‑RS485 adapter. A CH340 needs the WCH driver on Windows.</div>}
          <table className="t"><tbody>
            {ports.map((p) => (
              <tr key={p.device} onClick={() => set('port', p.device)} style={{ cursor: 'pointer', background: form.port === p.device ? 'var(--cobalt-soft)' : undefined }}>
                <td><b>{p.device}</b></td><td>{p.chip ?? p.description}<div className="hint">{p.vid ? `VID ${p.vid} PID ${p.pid}` : p.hwid}</div></td>
              </tr>
            ))}
          </tbody></table>
          <div className="form" style={{ marginTop: 12 }}>
            <label className="field"><span className="label">Port</span><input value={form.port} onChange={(e) => set('port', e.target.value)} /></label>
            <label className="field"><span className="label">Baud</span>
              <select value={form.baud} onChange={(e) => set('baud', +e.target.value)}>{[4800, 9600, 19200, 38400, 57600, 115200].map((b) => <option key={b}>{b}</option>)}</select></label>
            <label className="field"><span className="label">Protocol</span>
              <select value={form.protocol} onChange={(e) => set('protocol', e.target.value)}>
                <option value="daly">Daly smart BMS</option><option value="jbd">JBD / Xiaoxiang</option><option value="modbus">Modbus RTU (map below)</option></select></label>
            {form.protocol === 'modbus' && <label className="field"><span className="label">Slave ID</span><input type="number" value={form.slave} onChange={(e) => set('slave', +e.target.value)} /></label>}
            <label className="field"><span className="label">Polls per second</span><input type="number" step="0.5" value={form.poll_hz} onChange={(e) => set('poll_hz', +e.target.value)} /></label>
            <label className="field"><span className="label">Current sign</span>
              <select value={String(form.invert_current)} onChange={(e) => set('invert_current', e.target.value === 'true')}>
                <option value="false">BMS: + is charge</option><option value="true">BMS: + is discharge</option></select></label>
          </div>
          <div className="row" style={{ marginTop: 12 }}>
            <button className="btn" onClick={doSniff} disabled={!form.port || sniffing}>{sniffing ? <><span className="spin" /> Probing 6 baud rates…</> : 'Identify device'}</button>
            <button className="btn primary" disabled={!form.port} onClick={() => run(() => api.post('/api/link/open', form), `Connected to ${form.port}`)}>Connect</button>
            <button className="btn" onClick={() => run(() => api.post('/api/link/demo?speed=8&series=4'), 'Demo 4-cell battery connected')}>Demo battery</button>
            <button className="btn danger" onClick={() => run(() => api.post('/api/link/close'), 'Disconnected')}>Disconnect</button>
          </div>
          {sniff && (
            <div style={{ marginTop: 12 }}>
              {sniff.best ? <div className="note">Found <b>{sniff.best.protocol}</b> at <b>{sniff.best.baud}</b> baud ({sniff.best.frames} valid frames). Settings filled in; press Connect.</div>
                : <div className="warnbox">No known protocol answered. Check A/B wiring (swap them if unsure), common ground, and the BMS's RS485 enable. Bytes seen per baud are listed below.</div>}
              <table className="t" style={{ marginTop: 8 }}><thead><tr><th>Baud</th><th>Passive bytes</th><th>Replies</th></tr></thead><tbody>
                {sniff.per_baud.map((b: Any) => <tr key={b.baud}><td>{b.baud}</td><td>{b.passive_bytes}{b.passive_hex && <div className="hint">{b.passive_hex}</div>}</td>
                  <td>{b.error ? <span style={{ color: 'var(--fail)' }}>{b.error}</span> : b.hits.map((h: Any, i: number) => <div key={i}>{h.protocol} ({h.mode}{h.frames ? `, ${h.frames} frames` : ''}){h.reply && <div className="hint">{h.reply}</div>}</div>)}</td></tr>)}
              </tbody></table>
            </div>
          )}
        </div>

        <div className="panel">
          <div className="section-head"><h3>Remembered adapter</h3>
            {prof?.profile && <span className="label">{prof.watchdog.state}</span>}</div>
          {prof?.profile ? (
            <>
              <p className="hint" style={{ margin: '0 0 6px' }}>
                {prof.profile.adapter.chip ?? 'USB serial'} ({prof.profile.adapter.vid}:{prof.profile.adapter.pid}), {prof.profile.params.protocol.toUpperCase()} at {prof.profile.params.baud} baud.
                Found by USB ID, not COM number, so it works on any PC or USB socket.
                {prof.resolved_port ? ` Right now it is ${prof.resolved_port}.` : ' Not plugged in right now.'}
                {prof.watchdog.message && ` ${prof.watchdog.message}.`}
              </p>
              <div className="row">
                <label className="row" style={{ gap: 6 }}><input type="checkbox" checked={prof.profile.auto !== false}
                  onChange={async (e) => setProf(await api.post(`/api/link/profile/auto?on=${e.target.checked}`))} /> Reconnect automatically</label>
                <button className="btn" onClick={async () => { await fetch('/api/link/profile', { method: 'DELETE' }); refresh(); say('Adapter forgotten') }}>Forget</button>
              </div>
            </>
          ) : <p className="hint" style={{ margin: 0 }}>Connect once and the workbench remembers the adapter and protocol, then reconnects by itself, even when Windows gives it a different COM number.</p>}
          <div className="section-head" style={{ marginTop: 16 }}><h3>Link</h3>
            <span className="label">{link.kind ?? 'none'} {link.port ?? ''} {link.baud ? `@ ${link.baud}` : ''}</span></div>
          <table className="t"><tbody>
            <tr><td className="label">Status</td><td>{link.running ? 'running' : 'stopped'}{link.error && <span style={{ color: 'var(--fail)' }}>: {link.error}</span>}</td></tr>
            <tr><td className="label">Traffic</td><td>{link.tx_bytes ?? 0} B out · {link.rx_bytes ?? 0} B in · {link.good_frames ?? 0} valid frames</td></tr>
            <tr><td className="label">Recording</td><td>{link.recording ?? 'off'}</td></tr>
          </tbody></table>
          <div className="row" style={{ marginTop: 10 }}>
            <button className="btn" onClick={() => run(() => api.post(`/api/link/polling?on=${!link.polling}`))}>{link.polling ? 'Stop polling (listen only)' : 'Resume polling'}</button>
            {link.recording ? <button className="btn" onClick={() => run(() => api.post('/api/record/stop'), 'Recording saved').then(refresh)}>Stop recording</button>
              : <button className="btn" onClick={() => run(() => api.post('/api/record/start'), 'Recording every frame')}>Record session</button>}
          </div>
          <h3 style={{ marginTop: 16 }}>Recordings</h3>
          <table className="t"><tbody>
            {recs.length === 0 && <tr><td className="hint">No recordings yet. Recorded sessions replay through the same decoder and estimator.</td></tr>}
            {recs.slice(0, 8).map((r) => <tr key={r.file}><td>{r.file}<div className="hint">{(r.bytes / 1024).toFixed(0)} kB</div></td>
              <td style={{ textAlign: 'right' }}><button className="btn" onClick={() => run(() => api.post(`/api/record/replay?file=${encodeURIComponent(r.file)}&speed=4`), 'Replaying at 4×')}>Replay</button></td></tr>)}
          </tbody></table>
          <h3 style={{ marginTop: 16 }}>Battery nameplate</h3>
          <div className="form">
            {[['label', 'Label', 'text'], ['nominal_Ah', 'Nominal capacity (Ah)', 'number'], ['i_max_dis', 'BMS discharge limit (A)', 'number'], ['i_max_chg', 'BMS charge limit (A)', 'number'],
              ['mass_kg', 'Mass (kg)', 'number'], ['connector', 'Connector', 'text'], ['protocol', 'Protocol name', 'text']].map(([k, l, t]) => (
              <label className="field" key={k}><span className="label">{l}</span>
                <input type={t} value={meta[k] ?? ''} onChange={(e) => setMeta({ ...meta, [k]: t === 'number' ? (e.target.value === '' ? null : +e.target.value) : e.target.value })} /></label>
            ))}
          </div>
          <button className="btn" style={{ marginTop: 10 }} onClick={() => run(() => api.post('/api/battery/meta', meta), 'Nameplate saved')}>Save nameplate</button>
        </div>
      </div>

      <div className="grid2">
        <div className="panel">
          <div className="section-head"><h3>Bus traffic</h3><button className="btn" onClick={() => setFrames([])}>Clear</button></div>
          <div className="console" ref={box} aria-live="off">
            {frames.map((f) => (
              <div key={f.seq} className={f.dir}>
                {f.dir === 'tx' ? '→' : '←'} {f.hex}
                {f.decoded && <span style={{ color: 'var(--ink-2)' }}>  {f.decoded.map((d: Any) => d.name).join(', ')}</span>}
              </div>
            ))}
          </div>
        </div>
        <div className="panel">
          <div className="section-head"><h3>Write to the BMS</h3>
            <label className="row" style={{ gap: 6 }}><input type="checkbox" checked={armed} disabled={remote} onChange={(e) => { setArmed(e.target.checked); api.post(`/api/link/arm?on=${e.target.checked}`) }} />
              <b style={{ color: armed ? 'var(--fail)' : 'var(--ink-2)' }}>{armed ? 'Writes armed' : 'Writes disarmed'}</b></label></div>
          <p className="hint">Raw writes can change protection settings or disable the BMS. Every write is logged to data/write_audit.jsonl.</p>
          {remote && <div className="warnbox" style={{ marginBottom: 8 }}>You're viewing from another PC. Writing to the BMS only works on the PC the battery is plugged into.</div>}
          <div className="row">
            {['charge_mos_on', 'charge_mos_off', 'discharge_mos_on', 'discharge_mos_off'].map((c) => (
              <button key={c} className={`btn ${c.endsWith('off') ? 'danger' : ''}`} disabled={!armed} onClick={() => run(() => api.post(`/api/link/cmd?name=${c}`), `Sent ${c.replace(/_/g, ' ')}`)}>
                {c.replace('_mos_', ' MOSFET ').replace('_', ' ')}</button>
            ))}
          </div>
          <label className="field" style={{ marginTop: 12 }}><span className="label">Raw hex frame</span><input value={hex} onChange={(e) => setHex(e.target.value)} spellCheck={false} /></label>
          <div className="row" style={{ marginTop: 8 }}>
            <select value={append} onChange={(e) => setAppend(e.target.value)} aria-label="Checksum">
              <option value="none">Send as typed</option><option value="daly_sum">Append Daly checksum</option><option value="jbd_sum">Append JBD checksum + 77</option><option value="modbus_crc">Append Modbus CRC</option></select>
            <button className="btn primary" disabled={!armed} onClick={() => run(() => api.post('/api/link/raw', { hex, append }).then((r: Any) => { say(`Sent ${r.sent}`); return r }))}>Send frame</button>
          </div>
          <h3 style={{ marginTop: 16 }}>Write log</h3>
          <div className="console" style={{ height: 120 }}>
            {audit.slice(-40).map((a, i) => <div key={i}>{new Date(a.t * 1000).toLocaleTimeString()} {a.why}: {a.hex}</div>)}
          </div>
        </div>
      </div>

      <div className="panel">
        <div className="section-head"><h3>Modbus register map</h3><span className="hint">Used when the protocol is Modbus RTU</span></div>
        <p className="hint">One entry per value: {'{'}"name", "fn": 3 or 4, "addr", "type": u16, i16, u32 or i32, "scale", "field"{'}'}. Field is pack_v, current, current_charge_positive, soc_pct, cycles, cell:0…, or temp:0….</p>
        <textarea className="in" style={{ width: '100%', height: 140, fontFamily: 'Consolas, monospace', fontSize: 12 }} value={map} onChange={(e) => setMap(e.target.value)} spellCheck={false} />
        <button className="btn" onClick={() => run(() => api.post('/api/modbus/map', JSON.parse(map)), 'Register map saved')}>Save map</button>
      </div>
    </div>
  )
}

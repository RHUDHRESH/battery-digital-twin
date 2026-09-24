import { Canvas, useFrame } from '@react-three/fiber'
import { ContactShadows, Html, OrbitControls, RoundedBox } from '@react-three/drei'
import { useMemo, useRef } from 'react'
import * as THREE from 'three'
import { ramp } from './api'

type Props = {
  cells: number[]           // cell voltages (V); length = series count
  cellR?: number[] | null   // mΩ per cell
  cellMode: 'voltage' | 'resistance'
  weakest?: number
  current: number           // A, + = discharge
  soc: number               // 0..1
  cRate: number             // |I| / Q
  open: boolean             // exploded to cells
}

const ease = (a: number, b: number, k: number) => a + (b - a) * k

/** Cell grid for n series cells: rows of up to 4 (n<=8) or 8, snaking for a series path. */
function layout(n: number) {
  const cols = n <= 1 ? 1 : n <= 8 ? Math.min(n, 4) : Math.ceil(n / 2) > 8 ? 8 : Math.ceil(n / 2)
  const rows = Math.ceil(n / cols)
  const cw = n === 1 ? 0.9 : 0.34
  const ch = n === 1 ? 1.6 : 0.95
  const cd = n === 1 ? 0.55 : 0.62
  const fit = Math.min(1, 2.9 / (cols * cw), 2.2 / (rows * cd))
  return { cols, rows, cw: cw * fit, ch: ch * Math.max(fit, 0.7), cd: cd * fit }
}

function cellPos(i: number, L: ReturnType<typeof layout>, gap: number): [number, number] {
  const r = Math.floor(i / L.cols)
  const cRaw = i % L.cols
  const c = r % 2 === 0 ? cRaw : L.cols - 1 - cRaw // serpentine = series order
  return [(c - (L.cols - 1) / 2) * L.cw * gap, (r - (L.rows - 1) / 2) * L.cd * gap]
}

export default function Battery3D(p: Props) {
  const n = Math.max(p.cells.length, 1)
  const L = useMemo(() => layout(n), [n])
  const W = L.cols * L.cw + 0.22
  const D = L.rows * L.cd + 0.22
  const H = L.ch + 0.12
  const dist = Math.max(W, D, H) * 2.6 + 2.2
  return (
    <Canvas shadows camera={{ position: [dist * 0.72, dist * 0.52, dist * 0.78], fov: 34 }} dpr={[1, 2]} key={n}>
      <ambientLight intensity={0.8} />
      <directionalLight position={[4, 8, 5]} intensity={1.5} castShadow shadow-mapSize={[1024, 1024]} />
      <directionalLight position={[-5, 3, -4]} intensity={0.45} />
      <group position={[0, 0, 0]}>
        <Case W={W} D={D} H={H} open={p.open} soc={p.soc} current={p.current} />
        <Cells {...p} L={L} n={n} H={H} />
        <Flow {...p} L={L} n={n} W={W} H={H} />
      </group>
      <ContactShadows position={[0, 0, 0]} opacity={0.32} scale={10} blur={2.6} far={4} />
      <OrbitControls enablePan={false} minDistance={1.5} maxDistance={14} maxPolarAngle={Math.PI / 2.05}
        autoRotate autoRotateSpeed={0.45} target={[0, H * 0.6, 0]} />
    </Canvas>
  )
}

/* ------------------------------------------------------------------ enclosure */
function Case({ W, D, H, open, soc, current }: { W: number; D: number; H: number; open: boolean; soc: number; current: number }) {
  const body = useMemo(() => new THREE.MeshStandardMaterial({ color: '#f4f6f9', roughness: 0.4, metalness: 0.05, transparent: true }), [])
  const band = useMemo(() => new THREE.MeshStandardMaterial({ color: '#1d4ed8', roughness: 0.35, transparent: true }), [])
  const lidMat = useMemo(() => new THREE.MeshStandardMaterial({ color: '#e7ebf0', roughness: 0.45, transparent: true }), [])
  const socMat = useMemo(() => new THREE.MeshStandardMaterial({ color: '#1d4ed8', emissive: '#1d4ed8', emissiveIntensity: 0.6 }), [])
  const lid = useRef<THREE.Group>(null)
  const bar = useRef<THREE.Mesh>(null)
  useFrame(({ clock }) => {
    const k = 0.08
    body.opacity = ease(body.opacity, open ? 0.07 : 1, k)
    band.opacity = ease(band.opacity, open ? 0 : 1, k)
    band.visible = band.opacity > 0.02
    lidMat.opacity = ease(lidMat.opacity, open ? 0.25 : 1, k)
    body.depthWrite = band.depthWrite = body.opacity > 0.9
    if (lid.current) {
      lid.current.position.y = ease(lid.current.position.y, open ? H + 0.75 : H, k)
      lid.current.rotation.x = ease(lid.current.rotation.x, open ? -0.35 : 0, k)
    }
    if (bar.current) {
      bar.current.scale.x = ease(bar.current.scale.x, Math.max(0.02, soc), 0.05)
      bar.current.position.x = -(W * 0.6) / 2 + (W * 0.6 * bar.current.scale.x) / 2
      const pulse = Math.abs(current) > 1 ? 0.45 + 0.35 * Math.sin(clock.elapsedTime * (2 + Math.min(Math.abs(current) / 20, 6))) : 0.5
      socMat.emissiveIntensity = pulse
      socMat.color.set(current < -1 ? '#0f9d58' : '#1d4ed8')
      socMat.emissive.copy(socMat.color)
    }
  })
  return (
    <group>
      <RoundedBox args={[W, H, D]} radius={0.06} position={[0, H / 2, 0]} material={body} castShadow receiveShadow />
      <RoundedBox args={[W + 0.012, 0.09, D + 0.012]} radius={0.03} position={[0, H * 0.28, 0]} material={band} />
      {/* state-of-charge light strip on the front face */}
      <group position={[0, H * 0.62, D / 2 + 0.006]}>
        <mesh><planeGeometry args={[W * 0.6 + 0.04, 0.09]} /><meshStandardMaterial color="#1c2127" transparent opacity={open ? 0.1 : 1} /></mesh>
        <mesh ref={bar} position={[0, 0, 0.002]}><planeGeometry args={[W * 0.6, 0.05]} /><primitive object={socMat} attach="material" /></mesh>
      </group>
      <group ref={lid} position={[0, H, 0]}>
        <RoundedBox args={[W + 0.03, 0.08, D + 0.03]} radius={0.03} position={[0, 0.04, 0]} material={lidMat} castShadow />
        {/* terminals: + red on the right, - graphite on the left */}
        <mesh position={[W * 0.36, 0.14, 0]} castShadow><cylinderGeometry args={[0.07, 0.08, 0.14, 24]} /><meshStandardMaterial color="#d93025" roughness={0.35} /></mesh>
        <mesh position={[-W * 0.36, 0.14, 0]} castShadow><cylinderGeometry args={[0.07, 0.08, 0.14, 24]} /><meshStandardMaterial color="#2a2f36" roughness={0.35} /></mesh>
        {/* BMS board */}
        <RoundedBox args={[Math.min(W * 0.35, 0.8), 0.03, Math.min(D * 0.45, 0.4)]} radius={0.01} position={[0, 0.1, 0]}>
          <meshStandardMaterial color="#1f5f4a" roughness={0.6} />
        </RoundedBox>
        <mesh position={[0, 0.13, 0]}><boxGeometry args={[0.1, 0.03, 0.1]} /><meshStandardMaterial color="#1c2127" /></mesh>
      </group>
    </group>
  )
}

/* ------------------------------------------------------------------ cells */
function Cells(p: Props & { L: ReturnType<typeof layout>; n: number; H: number }) {
  const { L, n } = p
  const meshes = useRef<(THREE.Group | null)[]>([])
  const mats = useMemo(() => Array.from({ length: n }, () => new THREE.MeshStandardMaterial({ roughness: 0.35, metalness: 0.1 })), [n])
  const colors = useMemo(() => {
    let vals: number[]
    if (p.cellMode === 'resistance' && p.cellR && p.cellR.length === n) {
      const lo = Math.min(...p.cellR), hi = Math.max(...p.cellR)
      vals = p.cellR.map((r) => (hi - lo < 1e-9 ? 0.3 : (r - lo) / (hi - lo)))
    } else if (n === 1) {
      vals = [0.2]
    } else {
      const mean = p.cells.reduce((a, b) => a + b, 0) / n
      vals = p.cells.map((v) => 0.45 + (mean - v) / 0.04)
    }
    return vals.map((t) => new THREE.Color(ramp(t)))
  }, [p.cells, p.cellR, p.cellMode, n])
  useFrame(() => {
    const k = 0.08
    const gap = p.open ? 1.55 : 1.0
    meshes.current.forEach((g, i) => {
      if (!g) return
      const [x, z] = cellPos(i, L, gap)
      g.position.x = ease(g.position.x, x, k)
      g.position.z = ease(g.position.z, z, k)
      g.position.y = ease(g.position.y, p.open ? 0.25 + Math.sin(i * 0.9) * 0.03 : 0.06, k)
      mats[i].color.lerp(colors[i], 0.1)
      mats[i].emissive.copy(mats[i].color).multiplyScalar(p.open ? 0.2 : 0.04)
    })
  })
  return (
    <>
      {p.cells.map((v, i) => (
        <group key={i} ref={(el) => { meshes.current[i] = el }}>
          <RoundedBox args={[L.cw * 0.9, L.ch, L.cd * 0.92]} radius={Math.min(0.04, L.cw * 0.1)} position={[0, L.ch / 2, 0]} material={mats[i]} castShadow />
          {/* cell terminals */}
          <mesh position={[-L.cw * 0.22, L.ch + 0.02, 0]}><boxGeometry args={[L.cw * 0.18, 0.04, L.cd * 0.2]} /><meshStandardMaterial color="#9aa3ad" metalness={0.7} roughness={0.3} /></mesh>
          <mesh position={[L.cw * 0.22, L.ch + 0.02, 0]}><boxGeometry args={[L.cw * 0.18, 0.04, L.cd * 0.2]} /><meshStandardMaterial color="#c47a2c" metalness={0.7} roughness={0.3} /></mesh>
          {i === p.weakest && n > 1 && p.open && (
            <mesh position={[0, L.ch + 0.28, 0]} rotation={[Math.PI, 0, 0]}><coneGeometry args={[0.06, 0.14, 16]} /><meshStandardMaterial color="#1c2127" /></mesh>
          )}
          {p.open && (
            <Html position={[0, L.ch * 0.55, L.cd * 0.47]} center distanceFactor={3.2} style={{ pointerEvents: 'none' }}>
              <div style={{ font: '700 10px Archivo, sans-serif', color: '#1c2127', background: 'rgba(255,255,255,.88)', padding: '1px 5px', borderRadius: 6, whiteSpace: 'nowrap' }}>
                {n > 1 && <span style={{ opacity: 0.55, marginRight: 3 }}>{i + 1}</span>}{v.toFixed(3)}
              </div>
            </Html>
          )}
        </group>
      ))}
    </>
  )
}

/* ------------------------------------------------------------------ current flow */
function Flow(p: Props & { L: ReturnType<typeof layout>; n: number; W: number; H: number }) {
  const N = 260
  const { L, n, W, H } = p
  // closed: external circuit arcing from + to - over the battery; open: series path through the cells
  const curve = useMemo(() => {
    const pts: THREE.Vector3[] = []
    if (p.open) {
      const y = 0.25 + L.ch + 0.1
      pts.push(new THREE.Vector3(-W * 0.36, H + 0.85, 0))
      for (let i = 0; i < n; i++) {
        const [x, z] = cellPos(i, L, 1.55)
        pts.push(new THREE.Vector3(x - L.cw * 0.22, y, z), new THREE.Vector3(x + L.cw * 0.22, y, z))
      }
      pts.push(new THREE.Vector3(W * 0.36, H + 0.85, 0))
    } else {
      const top = H + 0.2
      pts.push(new THREE.Vector3(W * 0.36, top, 0), new THREE.Vector3(W * 0.3, top + 0.9, 0), new THREE.Vector3(0, top + 1.25, 0),
        new THREE.Vector3(-W * 0.3, top + 0.9, 0), new THREE.Vector3(-W * 0.36, top, 0))
    }
    return new THREE.CatmullRomCurve3(pts, false, 'centripetal')
  }, [p.open, L, n, W, H])
  const phase = useMemo(() => Float32Array.from({ length: N }, (_, i) => i / N), [])
  const geom = useMemo(() => {
    const g = new THREE.BufferGeometry()
    g.setAttribute('position', new THREE.BufferAttribute(new Float32Array(N * 3), 3))
    return g
  }, [])
  const mat = useRef<THREE.PointsMaterial>(null)
  const tmp = useMemo(() => new THREE.Vector3(), [])
  useFrame((_, dt) => {
    const flowing = Math.abs(p.current) > 0.8
    // speed tracks C-rate; conventional current leaves + (discharge) and enters + (charge)
    const speed = Math.min(0.06 + p.cRate * 0.45, 0.9)
    // path runs + -> - outside (open: - -> + inside), so direction flips between views
    const dir = (p.current >= 0 ? 1 : -1) * (p.open ? 1 : 1)
    const arr = geom.attributes.position.array as Float32Array
    for (let i = 0; i < N; i++) {
      phase[i] = (phase[i] + dir * speed * dt + 1) % 1
      curve.getPointAt(phase[i], tmp)
      const j = ((i * 7919) % 97) / 97 - 0.5
      arr[i * 3] = tmp.x + j * 0.05
      arr[i * 3 + 1] = tmp.y + (((i * 104729) % 89) / 89 - 0.5) * 0.05
      arr[i * 3 + 2] = tmp.z + (((i * 1299709) % 83) / 83 - 0.5) * 0.05
    }
    geom.attributes.position.needsUpdate = true
    if (mat.current) {
      mat.current.opacity = ease(mat.current.opacity, flowing ? 0.95 : 0, 0.1)
      mat.current.color.set(p.current >= 0 ? '#1d4ed8' : '#0f9d58')
    }
  })
  return (
    <points geometry={geom}>
      <pointsMaterial ref={mat} size={0.065} transparent opacity={0} depthWrite={false} sizeAttenuation />
    </points>
  )
}

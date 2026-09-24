// Cross-platform launcher for the analysis engine (FastAPI on port 8000).
// Uses backend/.venv on Windows (Scripts\python.exe) or Linux/macOS (bin/python).
// BDT_HOST=0.0.0.0 exposes it on the LAN (see README: LAN mode).
import { spawn } from 'node:child_process'
import { existsSync } from 'node:fs'
import { dirname, join } from 'node:path'
import { fileURLToPath } from 'node:url'

const root = join(dirname(fileURLToPath(import.meta.url)), '..')
const venv = join(root, 'backend', '.venv')
const py = [join(venv, 'Scripts', 'python.exe'), join(venv, 'bin', 'python')].find(existsSync)
if (!py) {
  console.error('No backend/.venv found. Create it first:\n  python -m venv backend/.venv\n  then install backend/requirements.txt (see README)')
  process.exit(1)
}
const host = process.env.BDT_HOST || '127.0.0.1'
const args = ['-m', 'uvicorn', 'bdt.app:app', '--app-dir', join(root, 'backend'), '--host', host, '--port', process.env.BDT_PORT || '8000']
console.log(`engine: ${py} ${args.join(' ')}`)
const child = spawn(py, args, { stdio: 'inherit', cwd: root })
child.on('exit', (code) => process.exit(code ?? 0))

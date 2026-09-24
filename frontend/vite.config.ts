import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

// BDT_LAN=1 (start.ps1 -Lan / start.sh --lan) serves the UI to other PCs on the network.
export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
    host: process.env.BDT_LAN ? '0.0.0.0' : 'localhost',
    proxy: {
      // xfwd: the engine sees each browser's real address, so BMS writes can be refused for remote PCs
      '/api': { target: 'http://127.0.0.1:8000', xfwd: true },
      '/ws': { target: 'ws://127.0.0.1:8000', ws: true, xfwd: true },
    },
  },
})

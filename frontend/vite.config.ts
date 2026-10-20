import tailwindcss from '@tailwindcss/vite'
import react from '@vitejs/plugin-react'
import { defineConfig } from 'vitest/config'

// The page talks to NEXUS through /nexus-ws on its own origin, so the same build works on the laptop
// and from a phone through an HTTPS tunnel (Cloudflare). Vite forwards it to the NEXUS feed.
const proxy = {
  '/nexus-ws': {
    target: 'ws://127.0.0.1:8765',
    ws: true,
    rewrite: (path: string) => path.replace(/^\/nexus-ws/, '') || '/',
  },
}
// Host names the dev/preview server answers to besides localhost (Vite refuses unknown hosts):
// quick tunnels (*.trycloudflare.com) plus your own tunnel domain via NEXUS_ALLOWED_HOSTS=a.com,b.com
const allowedHosts = ['.trycloudflare.com', ...(process.env.NEXUS_ALLOWED_HOSTS ?? '').split(',').map((h) => h.trim()).filter(Boolean)]

export default defineConfig({
  plugins: [react(), tailwindcss()],
  server: { host: '127.0.0.1', port: 5173, proxy, allowedHosts },
  preview: { host: '127.0.0.1', port: 4173, proxy, allowedHosts },
  test: { environment: 'node', include: ['src/**/*.test.ts'] },
})

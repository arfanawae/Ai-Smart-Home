import { RefreshCw, WifiOff } from 'lucide-react'
import { useEffect, useState } from 'react'
import { FEED_URL, reconnectNow } from '../../lib/feed'
import { useNexus } from '../../store/nexus'

/** Shown whenever the feed is down, with the exact command that brings NEXUS up. */
export function ConnectionBanner() {
  const connection = useNexus((s) => s.connection)
  const retryAt = useNexus((s) => s.retryAt)
  const demo = useNexus((s) => s.demo)
  const [now, setNow] = useState(() => Date.now())

  useEffect(() => {
    if (connection !== 'closed') return
    const id = setInterval(() => setNow(Date.now()), 500)
    return () => clearInterval(id)
  }, [connection])

  if (demo || connection === 'open') return null
  const seconds = retryAt ? Math.max(0, Math.ceil((retryAt - now) / 1000)) : null

  return (
    <div role="status" className="flex flex-wrap items-center gap-x-3 gap-y-1 border-b border-red/30 bg-red/10 px-4 py-2 text-sm">
      <WifiOff className="size-4 text-red" />
      <span className="text-text-1">
        {connection === 'connecting' ? 'กำลังเชื่อมต่อกับ NEXUS…' : 'ไม่ได้เชื่อมต่อกับ NEXUS'}
      </span>
      {connection === 'closed' && (
        <span className="text-text-2">
          เปิด <code className="rounded bg-bg-2 px-1.5 py-0.5 font-mono text-xs text-text-1">python -m nexus_voice</code> หรือ{' '}
          <code className="rounded bg-bg-2 px-1.5 py-0.5 font-mono text-xs text-text-1">serve</code> ในโฟลเดอร์ voice
          {seconds !== null && <> · ลองใหม่ใน {seconds} วินาที</>}
        </span>
      )}
      <span className="hidden font-mono text-xs text-text-2 lg:inline">{FEED_URL}</span>
      {connection === 'closed' && (
        <button
          type="button"
          onClick={reconnectNow}
          className="ml-auto flex h-8 items-center gap-1.5 rounded-lg border border-line px-3 text-text-1 hover:border-line-strong"
        >
          <RefreshCw className="size-3.5" /> ลองตอนนี้
        </button>
      )}
    </div>
  )
}

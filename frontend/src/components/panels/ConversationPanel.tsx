import { Keyboard, Mic, SendHorizontal, Settings2 } from 'lucide-react'
import { useEffect, useRef, useState, type FormEvent } from 'react'
import { send } from '../../lib/feed'
import { actionLabel, ROUTE_LABEL, STATUS_COLOR, statusLabel, timeOf } from '../../lib/format'
import { useReducedMotion } from '../../hooks/useReducedMotion'
import { useNexus, type ChatItem } from '../../store/nexus'
import { HudPanel } from '../ui/HudPanel'

const QUICK = ['เปิดไฟ', 'ปิดไฟ', 'หรี่ไฟเหลือ 50', 'ไฟเปิดอยู่ไหม', 'ไฟสว่างเกินไป']

function Message({ item }: { item: ChatItem }) {
  switch (item.type) {
    case 'user':
      return (
        <div className="message-in flex flex-col items-end">
          <div className="mb-1 flex items-center gap-1.5 text-[11px] text-text-2">
            {item.source === 'voice' ? <Mic className="size-3" /> : <Keyboard className="size-3" />}
            คุณ{item.language ? ` · ${item.language}` : ''} · {timeOf(item.ts)}
          </div>
          <div className="max-w-[90%] rounded-xl rounded-tr-sm border-r-2 border-cyan/70 bg-cyan/10 px-3 py-2 text-sm">
            {item.text}
          </div>
        </div>
      )
    case 'reply':
      return (
        <div className="message-in flex flex-col items-start">
          <div className="mb-1 text-[11px] text-text-2">
            <span className="font-display tracking-[0.18em] text-cyan">NEXUS</span> · {ROUTE_LABEL[item.route]} · {timeOf(item.ts)}
          </div>
          <div className="max-w-[92%] rounded-xl rounded-tl-sm border-l-2 border-cyan/70 bg-bg-2/80 px-3 py-2 text-sm whitespace-pre-line">
            {item.text}
          </div>
        </div>
      )
    case 'command':
      return (
        <div className="message-in flex items-center gap-2 self-start rounded-lg border border-line bg-bg-1 px-2.5 py-1.5 font-mono text-[11px]">
          <Settings2 className="size-3.5 text-blue" />
          <span className="text-text-1">{actionLabel(item)}</span>
          <span className={STATUS_COLOR[item.status]}>{statusLabel(item.status, item.simulated)}</span>
          {item.latency_ms !== null && <span className="text-text-2">{item.latency_ms} ms</span>}
        </div>
      )
  }
}

export function ConversationPanel({ className = '', delay = 0 }: { className?: string; delay?: number }) {
  const chat = useNexus((s) => s.chat)
  const connection = useNexus((s) => s.connection)
  const demo = useNexus((s) => s.demo)
  const lastError = useNexus((s) => s.lastError)
  const [text, setText] = useState('')
  const listRef = useRef<HTMLDivElement>(null)

  const usable = connection === 'open' && !demo
  const reducedMotion = useReducedMotion()

  // Follow the newest message. Scroll the list itself rather than scrollIntoView(), which would also
  // scroll the page — on a phone, every voice event would yank the view away from the AI Core.
  const lastId = chat.at(-1)?.id
  useEffect(() => {
    const el = listRef.current
    if (el && lastId !== undefined) el.scrollTo({ top: el.scrollHeight, behavior: reducedMotion ? 'auto' : 'smooth' })
  }, [lastId, reducedMotion])

  const submit = (value: string) => {
    const trimmed = value.trim()
    if (!trimmed || !usable) return
    if (send({ type: 'text', text: trimmed })) setText('')
  }

  return (
    <HudPanel title="Conversation" className={className} delay={delay}>
      <div
        ref={listRef}
        // min-h-0 + flex-1: the list takes the space left in the panel and scrolls inside it — it never grows the panel
        className="-mx-1 flex min-h-0 flex-1 flex-col gap-3 overflow-y-auto overscroll-contain px-1 pb-2"
        aria-live="polite"
      >
        {chat.length === 0 ? (
          <div className="m-auto max-w-60 text-center text-sm text-text-2">
            ลองพูด <span className="text-text-1">"Nexus"</span> แล้วสั่ง <span className="text-text-1">"เปิดไฟห้องหลัก"</span>{' '}
            หรือพิมพ์คำสั่งด้านล่าง
          </div>
        ) : (
          chat.map((item) => <Message key={item.id} item={item} />)
        )}
      </div>
      {lastError && <div className="mb-2 text-xs text-red">ระบบแจ้ง: {lastError}</div>}
      <div className="mb-2 flex shrink-0 gap-2 overflow-x-auto pb-1">
        {QUICK.map((q) => (
          <button
            key={q}
            type="button"
            disabled={!usable}
            onClick={() => submit(q)}
            className="h-8 shrink-0 rounded-full border border-line px-3 text-xs text-text-2 hover:border-line-strong hover:text-text-1 disabled:opacity-40"
          >
            {q}
          </button>
        ))}
      </div>
      <form
        className="flex shrink-0 gap-2"
        onSubmit={(e: FormEvent) => {
          e.preventDefault()
          submit(text)
        }}
      >
        <input
          value={text}
          onChange={(e) => setText(e.target.value)}
          maxLength={500}
          disabled={!usable}
          placeholder={usable ? 'พิมพ์คำสั่งหรือคุยกับ NEXUS…' : demo ? 'โหมดสาธิต' : 'ยังไม่ได้เชื่อมต่อ'}
          aria-label="ข้อความถึง NEXUS"
          className="h-11 min-w-0 flex-1 rounded-lg border border-line bg-bg-0/70 px-3 text-base text-text-1 placeholder:text-text-2/70 focus:border-line-strong focus:outline-none disabled:opacity-50 md:text-sm"
        />
        <button
          type="submit"
          disabled={!usable || !text.trim()}
          aria-label="ส่ง"
          className="grid size-11 shrink-0 place-items-center rounded-lg border border-blue/60 bg-blue/20 text-text-1 hover:bg-blue/30 disabled:opacity-40"
        >
          <SendHorizontal className="size-5" />
        </button>
      </form>
    </HudPanel>
  )
}

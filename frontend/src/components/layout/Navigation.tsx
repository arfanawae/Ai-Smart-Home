import { ChevronsLeft, ChevronsRight } from 'lucide-react'
import { useState } from 'react'
import { PAGES, type Page } from '../../lib/pages'

const STORAGE_KEY = 'nexus.sidebar.expanded'

function readExpanded(): boolean {
  try {
    return localStorage.getItem(STORAGE_KEY) === '1'
  } catch {
    return false
  }
}

interface NavProps {
  page: Page
  onChange: (page: Page) => void
}

/** Desktop / tablet-landscape: collapsible rail (72 px) ↔ 220 px, remembered per browser. */
export function Sidebar({ page, onChange }: NavProps) {
  const [expanded, setExpanded] = useState(readExpanded)
  const toggle = () => {
    setExpanded((e) => {
      try {
        localStorage.setItem(STORAGE_KEY, e ? '0' : '1')
      } catch {
        /* private mode: just don't remember */
      }
      return !e
    })
  }

  return (
    <nav
      className={`sticky top-14 hidden h-[calc(100dvh-3.5rem)] shrink-0 flex-col gap-1 border-r border-line bg-bg-0/60 p-3 md:flex ${expanded ? 'w-56' : 'w-[72px]'} transition-[width] duration-200`}
      aria-label="เมนูหลัก"
    >
      {PAGES.map(({ id, label, icon: Icon }) => {
        const active = id === page
        return (
          <button
            key={id}
            type="button"
            onClick={() => onChange(id)}
            aria-current={active ? 'page' : undefined}
            title={expanded ? undefined : label}
            className={`group relative flex h-11 items-center gap-3 rounded-lg px-3 text-sm transition-colors focus-visible:outline-2 focus-visible:outline-line-strong ${
              active ? 'bg-bg-2 text-text-1' : 'text-text-2 hover:bg-bg-1 hover:text-text-1'
            }`}
          >
            {active && <span className="absolute top-2 bottom-2 left-0 w-[3px] rounded-full bg-cyan" />}
            <Icon className={`size-5 shrink-0 ${active ? 'text-cyan drop-shadow-[0_0_6px_var(--color-cyan)]' : ''}`} />
            {expanded && <span className="truncate">{label}</span>}
          </button>
        )
      })}
      <button
        type="button"
        onClick={toggle}
        className="mt-auto flex h-10 items-center justify-center rounded-lg text-text-2 hover:bg-bg-1 hover:text-text-1"
        aria-label={expanded ? 'ย่อเมนู' : 'ขยายเมนู'}
      >
        {expanded ? <ChevronsLeft className="size-5" /> : <ChevronsRight className="size-5" />}
      </button>
    </nav>
  )
}

const MOBILE: Page[] = ['dashboard', 'devices', 'assistant', 'activity', 'settings']

/** Mobile: bottom navigation with 56 px touch targets and safe-area padding. */
export function BottomNav({ page, onChange }: NavProps) {
  return (
    <nav
      className="fixed inset-x-0 bottom-0 z-20 grid grid-cols-5 border-t border-line bg-bg-0/90 pb-[env(safe-area-inset-bottom)] backdrop-blur-md md:hidden"
      aria-label="เมนูหลัก"
    >
      {PAGES.filter((p) => MOBILE.includes(p.id)).map(({ id, label, icon: Icon }) => {
        const active = id === page
        return (
          <button
            key={id}
            type="button"
            onClick={() => onChange(id)}
            aria-current={active ? 'page' : undefined}
            className={`flex h-14 flex-col items-center justify-center gap-0.5 text-[11px] ${active ? 'text-cyan' : 'text-text-2'}`}
          >
            <Icon className="size-5" />
            <span className="max-w-full truncate px-1">{label}</span>
          </button>
        )
      })}
    </nav>
  )
}

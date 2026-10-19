import { useEffect, useState, type ReactNode } from 'react'
import { CoreStage } from './components/core/CoreStage'
import { ConnectionBanner } from './components/layout/ConnectionBanner'
import { BottomNav, Sidebar } from './components/layout/Navigation'
import { PAGES, type Page } from './lib/pages'
import { TopBar } from './components/layout/TopBar'
import { ActivityList } from './components/panels/ActivityList'
import { ConversationPanel } from './components/panels/ConversationPanel'
import { LightCard } from './components/panels/LightCard'
import { SystemPanel } from './components/panels/SystemPanel'
import { HudPanel } from './components/ui/HudPanel'
import { demoParams, startDemo } from './lib/demo'
import { startFeed } from './lib/feed'

/**
 * Desktop (≥1280): Device + System | AI Core | Conversation, Activity across the bottom (Blueprint §4).
 * Tablet: core + device left, conversation right. Mobile: one column, core first, bottom navigation (§5).
 */
function Dashboard() {
  return (
    // minmax(0,1fr): grid items must be allowed to shrink, or the scrolling chip row widens the page on phones
    <div className="grid grid-cols-[minmax(0,1fr)] gap-4 lg:grid-cols-12 xl:min-h-[calc(100dvh-7rem)] xl:grid-rows-[1fr_auto]">
      <div className="order-2 flex flex-col gap-4 lg:order-1 lg:col-span-4 xl:col-span-3">
        <LightCard delay={60} />
        <SystemPanel delay={120} />
      </div>
      <div className="order-1 flex items-center justify-center py-2 lg:order-2 lg:col-span-8 xl:col-span-6">
        <CoreStage />
      </div>
      {/* xl: h-0 + min-h-full — the conversation fills the row the device column and the core define,
          but its own content never adds height, so a long chat can't push or shrink the AI Core. */}
      <ConversationPanel className="order-3 h-[480px] lg:col-span-12 xl:col-span-3 xl:h-0 xl:min-h-full" delay={180} />
      {/* fixed on xl: rows reserved up front so the first commands do not shift the core; 2 rows on short laptops (§10) */}
      <ActivityList limit={5} className="order-4 lg:col-span-12 xl:h-56 xl:[@media(max-height:800px)]:h-36" delay={240} />
    </div>
  )
}

function ComingLater({ title, milestone, children }: { title: string; milestone: string; children?: ReactNode }) {
  return (
    <HudPanel title={title} className="mx-auto w-full max-w-2xl">
      <p className="text-text-2">
        หน้านี้จะพัฒนาใน <span className="text-text-1">{milestone}</span>
      </p>
      {children}
    </HudPanel>
  )
}

function PageView({ page }: { page: Page }) {
  switch (page) {
    case 'dashboard':
      return <Dashboard />
    case 'devices':
      return (
        <div className="mx-auto grid w-full max-w-3xl gap-4 md:grid-cols-2">
          <LightCard />
          <HudPanel title="Add Device">
            <p className="text-sm text-text-2">พัดลมและอุปกรณ์อื่นจะเพิ่มได้หลังเชื่อมต่อ Arduino UNO Q (M5–M6)</p>
          </HudPanel>
        </div>
      )
    case 'assistant':
      return (
        <div className="mx-auto grid w-full max-w-5xl gap-4 lg:grid-cols-[280px_1fr]">
          <div className="hidden lg:block">
            <CoreStage compact />
          </div>
          <ConversationPanel className="h-[calc(100dvh-9rem)]" />
        </div>
      )
    case 'activity':
      return <ActivityList className="mx-auto w-full max-w-4xl" />
    case 'automation':
      return <ComingLater title="Automation" milestone="M8 (หลังควบคุมอุปกรณ์จริงได้แล้ว)" />
    case 'settings':
      return (
        <ComingLater title="System Settings" milestone="M7–M8">
          <p className="mt-2 text-sm text-text-2">
            ตอนนี้ตั้งค่าผ่านไฟล์ <code className="font-mono text-text-1">backend/.env</code> (คีย์ API, โมเดล, ภาษา, อุปกรณ์เสียง)
          </p>
        </ComingLater>
      )
  }
}

export default function App() {
  const [page, setPage] = useState<Page>('dashboard')

  useEffect(() => {
    const { enabled, frozen } = demoParams()
    return enabled ? startDemo(frozen) : startFeed()
  }, [])

  useEffect(() => {
    document.title = `NEXUS · ${PAGES.find((p) => p.id === page)?.label ?? ''}`
  }, [page])

  return (
    <div className="min-h-dvh">
      <TopBar />
      <ConnectionBanner />
      <div className="flex">
        <Sidebar page={page} onChange={setPage} />
        <main className="min-w-0 flex-1 px-4 pt-4 pb-24 md:pb-6 xl:px-6">
          <PageView page={page} />
        </main>
      </div>
      <BottomNav page={page} onChange={setPage} />
    </div>
  )
}

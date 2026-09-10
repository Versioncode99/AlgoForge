import {
  Activity, Archive, BadgeCheck, BookOpen, BrainCircuit, CandlestickChart, ClipboardList,
  Crosshair, Database, FileCheck2, FlaskConical, Gauge, LayoutGrid, Layers3, LineChart,
  MessageSquare, Network, PieChart, PlaySquare, Radar, Scale, ScrollText, Settings,
  ShieldCheck, SlidersHorizontal, TestTubes, Timer, Wallet, Workflow,
} from 'lucide-react'

/* One icon per destination, chosen by route rather than by mode.
 *
 * The rail used to carry its icons inline with the navigation, which worked
 * while there was one navigation. With four, the same route appears in more
 * than one mode and the icon has to be the same in each — a Validation entry
 * drawn with one glyph in Normal and another in Hedge Fund reads as two
 * different screens.
 *
 * `Radar` is the fallback rather than a blank. A missing icon in a rail of
 * icons collapses the row's alignment, which looks like a rendering fault
 * rather than like a route somebody forgot to list here.
 */
const ICONS: Record<string, typeof Radar> = {
  // shared
  overview: Radar,
  workspace: LayoutGrid,
  charts: CandlestickChart,
  trades: Crosshair,
  strategies: Layers3,
  runs: Archive,
  validation: TestTubes,
  evidence: FileCheck2,
  data: Database,
  research: BookOpen,
  experiments: FlaskConical,
  performance: LineChart,
  settings: Settings,
  positions: Wallet,
  book: ClipboardList,
  // prop firm
  account: Wallet,
  rules: Scale,
  drawdown: Gauge,
  daily: Timer,
  target: BadgeCheck,
  risk: ShieldCheck,
  simulation: PlaySquare,
  // ai
  assistant: MessageSquare,
  agents: Network,
  actions: SlidersHorizontal,
  activity: Activity,
  missions: PlaySquare,
  pipeline: Workflow,
  // hedge fund
  fund: PieChart,
  alpha: BrainCircuit,
  portfolio: PieChart,
  gate: ShieldCheck,
  execution: Workflow,
  operations: ClipboardList,
  orchestrator: Network,
  approvals: BadgeCheck,
  audit: ScrollText,
}

export function sectionIcon(route: string): typeof Radar {
  return ICONS[route] ?? Radar
}

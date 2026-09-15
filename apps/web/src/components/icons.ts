import {
  Activity, Archive, BadgeCheck, BookOpen, BrainCircuit, CandlestickChart, ClipboardList,
  Compass, Crosshair, Database, FileCheck2, FlaskConical, Gauge, LayoutGrid, Layers3, LineChart,
  MessageSquare, Network, PieChart, PlaySquare, Radar, Scale, ScrollText, Settings,
  ShieldCheck, SlidersHorizontal, TestTubes, Timer, Wallet, Workflow,
} from 'lucide-react'

/* One icon per destination, chosen by route rather than by mode.
 *
 * The rail used to carry its icons inline with the navigation, which worked
 * while there was one navigation. With four, the same route appears in more
 * than one mode and the icon has to be the same in each — a Validation entry
 * drawn with one glyph in Normal and another in Prop Firm reads as two
 * different screens.
 *
 * `Radar` is the fallback rather than a blank. A missing icon in a rail of
 * icons collapses the row's alignment, which looks like a rendering fault
 * rather than like a route somebody forgot to list here.
 */
const ICONS: Record<string, typeof Radar> = {
  // the rail, as it is now: one icon per destination
  home: Radar,
  chat: MessageSquare,
  research: BookOpen,
  campaigns: Compass,
  strategies: Layers3,
  markets: CandlestickChart,
  trading: PieChart,
  propdesk: Gauge,
  settings: Settings,
  // the routes the mode manifests used to offer, kept because a saved
  // workspace sidebar can still name any of them
  overview: Radar,
  workspace: LayoutGrid,
  charts: CandlestickChart,
  trades: Crosshair,
  runs: Archive,
  validation: TestTubes,
  evidence: FileCheck2,
  data: Database,
  experiments: FlaskConical,
  performance: LineChart,
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
  research_control: Radar,
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

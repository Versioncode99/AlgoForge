import '@testing-library/jest-dom/vitest'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { fireEvent, render, screen } from '@testing-library/react'
import { expect, test, vi } from 'vitest'
import { App } from './App'

vi.mock('echarts-for-react', () => ({default: () => <div data-testid="chart" />}))
const run = {run_id:'run_1',tier:'TRUTH_OOS',labels:['SAMPLE_DATA'],created_at:'2026-09-01'}
const verdict = {verdict_id:'v1',decision:'PASS',grade:'B',dimensions:{edge:60,robustness:70,risk:80,sample:20},metrics:{net_pnl:500,win_rate:.55,profit_factor:1.4,max_drawdown:200},gates:[{gate:'G0',name:'Data',status:'PASS',finding:'Passed'}],labels:[]}
const analysis = {verdict,regimes:[{name:'TREND',trade_count:9,net_pnl:100,confidence:'LOW'}],risk:{equity_paths:[[1,2]],median_path:[1],p05_path:[0],p95_path:[2],path_count:240,terminal_median:500,loss_probability:.1,var_95:200,cvar_95:300,warnings:[]}}

globalThis.fetch = vi.fn(async (input: RequestInfo | URL) => {
  const url = String(input)
  const data = url.endsWith('/runs') ? [run] : url.includes('/analysis/') ? analysis : url.endsWith('/prop/rules') ? [] : url.includes('/agents/') ? {claims:[],roles:[],dissent_present:true,numeric_verdict_locked:true} : {automatic_live_changes:false,paper_only:true,release_count:0,candidate:{repository:'owner/repo',lane:'CLEAN_ROOM',proposed_features:[]}}
  return {ok:true,json:async()=>({data})} as Response
})

test('renders evidence warning and navigates to agent boundary', async () => {
  const client = new QueryClient({defaultOptions:{queries:{retry:false}}})
  render(<QueryClientProvider client={client}><App /></QueryClientProvider>)
  expect(await screen.findByText(/research verdict you can audit/i)).toBeInTheDocument()
  expect(screen.getByText(/sample data · uncalibrated/i)).toBeInTheDocument()
  fireEvent.click(screen.getByRole('button',{name:'Agents'}))
  expect(screen.getByText(/Agents explain; the judge decides/i)).toBeInTheDocument()
})

import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'
import { App } from './App'
// Self-hosted so the workstation renders identically offline.
import '@fontsource-variable/inter'
import '@fontsource/ibm-plex-mono/400.css'
import '@fontsource/ibm-plex-mono/500.css'
import '@fontsource/ibm-plex-mono/600.css'
import './styles/base.css'
import './styles/app.css'
import './styles/workspace.css'
import './styles/command.css'
import './styles/pipeline.css'
import './styles/orchestrator.css'
import './styles/evidence.css'
import './styles/storage.css'
import './styles/chart.css'
import './styles/panels.css'
import './styles/workstation.css'
import './styles/research.css'

const queryClient = new QueryClient({defaultOptions: {queries: {retry: 1, staleTime: 30_000}}})

createRoot(document.getElementById('root')!).render(
  <StrictMode><QueryClientProvider client={queryClient}><App /></QueryClientProvider></StrictMode>,
)

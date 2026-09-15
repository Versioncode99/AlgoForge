import { QueryClient, QueryClientProvider, focusManager } from '@tanstack/react-query'
import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'
import { ApiError } from './api'
import { followVisibility } from './desktop'
import { App } from './App'
import { applyAppearance, cachedAppearance } from './theme'
// Self-hosted so the workstation renders identically offline.
import '@fontsource-variable/inter'
import '@fontsource/ibm-plex-mono/400.css'
import '@fontsource/ibm-plex-mono/500.css'
import '@fontsource/ibm-plex-mono/600.css'
import './styles/base.css'
import './styles/app.css'
import './styles/horizon.css'
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
import './styles/trades.css'
import './styles/lab.css'
import './styles/fund.css'
import './styles/campaign.css'
import './styles/propdesk.css'
import './styles/runtime.css'
import './styles/sidebar.css'
import './styles/research-control.css'

/* Before React mounts. The appearance is stored on the server, and the
 * request for it is in flight while the first frame paints — applying the
 * last known value here is what stops the default theme flashing to the
 * chosen one. It is a cache of a server value, corrected the moment the
 * real one arrives. */
applyAppearance(cachedAppearance())

/* Retry a network fault once; never retry a refusal.
 *
 * `retry: 1` retried everything, including the 4xx answers this API uses to say
 * no: 409 when a dataset needs a credential nobody has set, 422 when no
 * workspace is open. Those cannot become 200 by being asked again, so the retry
 * doubled the requests, doubled the console errors, and delayed the refusal the
 * screen was going to render anyway. A 5xx or a dropped connection is the case
 * a retry is for, and it keeps one. */
const queryClient = new QueryClient({
  defaultOptions: {
    queries: {
      retry: (failures, error) =>
        failures < 1 && !(error instanceof ApiError && error.status >= 400 && error.status < 500),
      staleTime: 30_000,
    },
  },
})

/* A window nobody can see stops polling.
 *
 * The query layer already pauses interval refetching when the document is
 * hidden, which covers a minimised window. Inside the shell there is a state it
 * cannot detect -- a window fully covered by another -- and one it must not act
 * on: a window that is merely not frontmost, which in a workstation is half the
 * screen and still being read.
 *
 * `followVisibility` sends only the first of those through the focus gate, so
 * there is one mechanism deciding this rather than two disagreeing. Outside the
 * shell it does nothing and the browser's own visibility handling stands. */
followVisibility((focused) => focusManager.setFocused(focused))

createRoot(document.getElementById('root')!).render(
  <StrictMode><QueryClientProvider client={queryClient}><App /></QueryClientProvider></StrictMode>,
)

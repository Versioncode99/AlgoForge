import { Component, type ErrorInfo, type ReactNode } from 'react'

type Props = { children: ReactNode; view: string; onOverview: () => void }
type State = { error: Error | null }

/** A broken lazy view must never turn the whole workstation into a blank page. */
export class ViewErrorBoundary extends Component<Props, State> {
  state: State = { error: null }

  static getDerivedStateFromError(error: Error): State {
    return { error }
  }

  componentDidCatch(error: Error, info: ErrorInfo) {
    console.error(`AlgoForge ${this.props.view} view failed`, error, info.componentStack)
  }

  componentDidUpdate(previous: Props) {
    if (previous.view !== this.props.view && this.state.error) this.setState({ error: null })
  }

  render() {
    if (!this.state.error) return this.props.children
    return (
      <div className="state error view-failure" role="alert">
        <strong>{this.props.view} could not be displayed.</strong>
        <span>{this.state.error.message || 'The view hit an unexpected rendering error.'}</span>
        <div>
          <button className="btn primary" onClick={() => this.setState({ error: null })}>Retry view</button>
          <button className="btn" onClick={this.props.onOverview}>Open Overview</button>
        </div>
      </div>
    )
  }
}

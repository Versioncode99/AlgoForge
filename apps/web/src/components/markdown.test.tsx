import '@testing-library/jest-dom/vitest'
import { cleanup, render, screen, within } from '@testing-library/react'
import { afterEach, expect, test } from 'vitest'
import { Markdown } from './Markdown'

/* The renderer, and above all what it refuses to render.
 *
 * A model's reply is untrusted text. It may contain a retrieved abstract, a
 * filename somebody chose, or a sentence aimed at whoever reads it. The
 * component produces React elements rather than HTML — there is no
 * `dangerouslySetInnerHTML` anywhere in it — so the safety tests below are
 * assertions that the grammar is small, not that a sanitiser is configured
 * correctly.
 */

afterEach(cleanup)

test('a fenced block renders as code with a copy control', () => {
  render(<Markdown text={'Here:\n\n```python\nreturn 1\n```'} />)
  expect(screen.getByText('return 1')).toBeInTheDocument()
  expect(screen.getByText('python')).toBeInTheDocument()
  expect(screen.getByRole('button', { name: /copy/i })).toBeInTheDocument()
})

test('a table renders as a table, with its header row apart from its body', () => {
  render(<Markdown text={'| Strategy | Sharpe |\n| --- | --- |\n| orb_1 | 0.42 |'} />)
  const table = screen.getByRole('table')
  expect(within(table).getByRole('columnheader', { name: 'Strategy' })).toBeInTheDocument()
  expect(within(table).getByRole('cell', { name: 'orb_1' })).toBeInTheDocument()
})

test('a table without a separator row is all body rather than losing its first row', () => {
  render(<Markdown text={'| a | b |\n| c | d |'} />)
  expect(screen.getAllByRole('row')).toHaveLength(2)
  expect(screen.queryByRole('columnheader')).not.toBeInTheDocument()
})

test('headings, lists, quotes and inline code all render as themselves', () => {
  render(
    <Markdown
      text={'## Findings\n\n- one\n- two\n\n1. first\n\n> a quote\n\nuse `run_backtest`'}
    />,
  )
  expect(screen.getByRole('heading', { name: 'Findings' })).toBeInTheDocument()
  expect(screen.getAllByRole('listitem')).toHaveLength(3)
  expect(screen.getByText('a quote')).toBeInTheDocument()
  expect(screen.getByText('run_backtest').tagName).toBe('CODE')
})

test('a bold run and a link render without swallowing the text around them', () => {
  render(<Markdown text={'See **this** and [the docs](https://example.com/x) now'} />)
  expect(screen.getByText('this').tagName).toBe('STRONG')
  const link = screen.getByRole('link', { name: 'the docs' })
  expect(link).toHaveAttribute('href', 'https://example.com/x')
  expect(link).toHaveAttribute('rel', expect.stringContaining('noopener'))
  expect(screen.getByText(/now/)).toBeInTheDocument()
})

test('an in-app hash link stays in the app rather than opening a tab', () => {
  render(<Markdown text={'[the campaign](#campaigns?tab=all)'} />)
  const link = screen.getByRole('link', { name: 'the campaign' })
  expect(link).toHaveAttribute('href', '#campaigns?tab=all')
  expect(link).not.toHaveAttribute('target')
})

// ── what it will not do ──────────────────────────────────────────────────────

test('HTML in a reply is characters, not markup', () => {
  const { container } = render(
    <Markdown text={'<script>alert(1)</script> and <img src=x onerror=alert(1)>'} />,
  )
  expect(container.querySelector('script')).toBeNull()
  expect(container.querySelector('img')).toBeNull()
  expect(screen.getByText(/<script>alert\(1\)<\/script>/)).toBeInTheDocument()
})

test('a javascript: link loses its link and keeps its text', () => {
  render(<Markdown text={'[click me](javascript:alert(1))'} />)
  expect(screen.queryByRole('link')).not.toBeInTheDocument()
  // The reader still sees exactly what the model wrote, including the URL.
  expect(screen.getByText(/click me \(javascript:alert\(1\)/)).toBeInTheDocument()
})

test('a data: link loses its link too', () => {
  render(<Markdown text={'[x](data:text/html;base64,PHNjcmlwdD4=)'} />)
  expect(screen.queryByRole('link')).not.toBeInTheDocument()
})

test('an unterminated code fence renders what there is rather than dropping it', () => {
  render(<Markdown text={'```\nhalf a block'} />)
  expect(screen.getByText('half a block')).toBeInTheDocument()
})

test('empty text renders nothing rather than throwing', () => {
  const { container } = render(<Markdown text="" />)
  expect(container.querySelector('.md')?.children).toHaveLength(0)
})

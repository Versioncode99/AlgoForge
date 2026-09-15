import { Check, Copy } from 'lucide-react'
import { useState } from 'react'

/* Markdown, rendered without letting the model write HTML.
 *
 * **Why this is hand-written rather than a dependency.** The model's reply is
 * untrusted text: it may contain a retrieved abstract, a filename somebody
 * chose, or an instruction aimed at whoever reads it. A general Markdown
 * library plus a sanitiser is two dependencies and one attack surface —
 * `dangerouslySetInnerHTML` — and the moment that call exists, every future
 * edit has to keep getting the sanitiser configuration right.
 *
 * So nothing here produces HTML from text. The parser recognises a deliberately
 * small grammar and emits React elements, and anything it does not recognise
 * renders as the characters it is made of. A `<script>` in a reply is a
 * paragraph containing the word script.
 *
 * The grammar, and nothing else:
 *
 *     # ## ###      headings
 *     - * 1.        lists
 *     > quote       blockquote
 *     | a | b |     tables
 *     ```lang       fenced code, with a copy control
 *     `code`        inline code
 *     **bold**      strong
 *     [text](url)   links, http(s) and in-app hashes only
 *
 * Links are the one place a URL from the reply reaches the DOM, so the scheme
 * is checked against an allow-list rather than a deny-list: `javascript:`,
 * `data:` and anything else render as plain text carrying the URL, which the
 * reader can still see and copy.
 */

const SAFE_SCHEME = /^(https?:\/\/|#|\/)/i

type Inline = { text: string; code?: boolean; strong?: boolean; href?: string }

/** Split one line into runs of code, strong text, links and plain text. */
function inlines(line: string): Inline[] {
  const out: Inline[] = []
  const pattern = /(`[^`]+`)|(\*\*[^*]+\*\*)|(\[[^\]]+\]\([^)\s]+\))/g
  let last = 0
  for (const match of line.matchAll(pattern)) {
    const at = match.index ?? 0
    if (at > last) out.push({ text: line.slice(last, at) })
    const token = match[0]
    if (token.startsWith('`')) out.push({ text: token.slice(1, -1), code: true })
    else if (token.startsWith('**')) out.push({ text: token.slice(2, -2), strong: true })
    else {
      const cut = token.indexOf('](')
      const text = token.slice(1, cut)
      const href = token.slice(cut + 2, -1)
      // An unsafe scheme keeps its characters and loses its link. The reader
      // still sees exactly what the model wrote.
      if (SAFE_SCHEME.test(href)) out.push({ text, href })
      else out.push({ text: `${text} (${href})` })
    }
    last = at + token.length
  }
  if (last < line.length) out.push({ text: line.slice(last) })
  return out
}

function Line({ line }: { line: string }) {
  return (
    <>
      {inlines(line).map((run, index) => {
        if (run.code) return <code key={index}>{run.text}</code>
        if (run.strong) return <strong key={index}>{run.text}</strong>
        if (run.href) {
          const external = run.href.startsWith('http')
          return (
            <a
              key={index}
              href={run.href}
              {...(external ? { target: '_blank', rel: 'noreferrer noopener' } : {})}
            >{run.text}</a>
          )
        }
        return <span key={index}>{run.text}</span>
      })}
    </>
  )
}

function CodeBlock({ language, source }: { language: string; source: string }) {
  const [copied, setCopied] = useState(false)
  return (
    <figure className="md-code">
      <figcaption>
        <span className="mono">{language || 'text'}</span>
        <button
          type="button"
          onClick={() => {
            void navigator.clipboard?.writeText(source).then(() => {
              setCopied(true)
              window.setTimeout(() => setCopied(false), 1500)
            })
          }}
        >
          {copied ? <Check aria-hidden="true" /> : <Copy aria-hidden="true" />}
          {copied ? 'Copied' : 'Copy'}
        </button>
      </figcaption>
      <pre><code>{source}</code></pre>
    </figure>
  )
}

function Table({ rows }: { rows: string[] }) {
  const cells = (row: string) =>
    row.replace(/^\||\|$/g, '').split('|').map((cell) => cell.trim())
  // A separator row (`|---|---|`) marks the line above as the header. Without
  // one every row is a body row, which is what a model writing a table without
  // a separator actually meant.
  const hasHeader = rows.length > 1 && /^\|?[\s:-]+\|/.test(rows[1])
  const body = hasHeader ? rows.slice(2) : rows
  return (
    <div className="md-table-scroll">
      <table className="md-table">
        {hasHeader && (
          <thead>
            <tr>{cells(rows[0]).map((cell, index) => <th key={index}><Line line={cell} /></th>)}</tr>
          </thead>
        )}
        <tbody>
          {body.map((row, index) => (
            <tr key={index}>
              {cells(row).map((cell, cellIndex) => <td key={cellIndex}><Line line={cell} /></td>)}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  )
}

export function Markdown({ text }: { text: string }) {
  const lines = text.replace(/\r\n/g, '\n').split('\n')
  const blocks: React.ReactNode[] = []
  let paragraph: string[] = []
  let list: { ordered: boolean; items: string[] } | null = null
  let quote: string[] = []
  let table: string[] = []

  const flush = () => {
    if (paragraph.length) {
      blocks.push(<p key={blocks.length}><Line line={paragraph.join(' ')} /></p>)
      paragraph = []
    }
    if (list) {
      const Tag = list.ordered ? 'ol' : 'ul'
      const items = list.items
      blocks.push(
        <Tag key={blocks.length}>
          {items.map((item, index) => <li key={index}><Line line={item} /></li>)}
        </Tag>,
      )
      list = null
    }
    if (quote.length) {
      blocks.push(<blockquote key={blocks.length}><Line line={quote.join(' ')} /></blockquote>)
      quote = []
    }
    if (table.length) {
      blocks.push(<Table key={blocks.length} rows={table} />)
      table = []
    }
  }

  for (let index = 0; index < lines.length; index += 1) {
    const line = lines[index]

    if (line.startsWith('```')) {
      flush()
      const language = line.slice(3).trim()
      const source: string[] = []
      index += 1
      while (index < lines.length && !lines[index].startsWith('```')) {
        source.push(lines[index])
        index += 1
      }
      blocks.push(<CodeBlock key={blocks.length} language={language} source={source.join('\n')} />)
      continue
    }

    if (!line.trim()) { flush(); continue }

    const heading = /^(#{1,3})\s+(.*)$/.exec(line)
    if (heading) {
      flush()
      const level = heading[1].length
      const Tag = (['h3', 'h4', 'h5'] as const)[level - 1]
      blocks.push(<Tag key={blocks.length}><Line line={heading[2]} /></Tag>)
      continue
    }

    if (line.trimStart().startsWith('|') && line.includes('|', 1)) {
      if (paragraph.length || list || quote.length) flush()
      table.push(line.trim())
      continue
    }
    if (table.length) flush()

    const bullet = /^\s*[-*]\s+(.*)$/.exec(line)
    const ordered = /^\s*\d+[.)]\s+(.*)$/.exec(line)
    if (bullet || ordered) {
      if (paragraph.length || quote.length) flush()
      const item = (bullet ?? ordered)![1]
      const isOrdered = Boolean(ordered)
      if (!list || list.ordered !== isOrdered) {
        if (list) flush()
        list = { ordered: isOrdered, items: [item] }
      } else list.items.push(item)
      continue
    }
    if (list) flush()

    if (line.trimStart().startsWith('>')) {
      if (paragraph.length) flush()
      quote.push(line.replace(/^\s*>\s?/, ''))
      continue
    }
    if (quote.length) flush()

    paragraph.push(line.trim())
  }
  flush()

  return <div className="md">{blocks}</div>
}

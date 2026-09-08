import { describe, expect, it } from 'vitest'
import { buildTree } from './Experiments'
import type { ExperimentRecord } from '../types'

/* The lineage view used to draw ancestors, the subject and its children as one
 * flat ordered list. That reads as a sequence, and a sequence is a claim: it
 * says the third child came after the second and that both descend from the
 * first. Neither is true of a search that tried four lookbacks from one parent
 * and then went deeper on the third of them.
 *
 * These tests are about the shape only, because the shape is the claim. */

const row = (id: string, parent_id?: string): ExperimentRecord => ({
  id,
  template: 'momentum_breakout',
  parameters: { lookback: Number(id.replace(/\D/g, '')) || 0 },
  parent_id,
})

const shape = (nodes: ReturnType<typeof buildTree>): unknown =>
  nodes.map((node) => ({ id: node.row.id, children: shape(node.children) }))

describe('buildTree', () => {
  it('nests a child under its own parent rather than under the root', () => {
    const tree = buildTree('root', [row('a', 'root'), row('b', 'a'), row('c', 'b')])
    expect(shape(tree)).toEqual([
      { id: 'a', children: [{ id: 'b', children: [{ id: 'c', children: [] }] }] },
    ])
  })

  it('keeps siblings as siblings instead of a chain', () => {
    // The defect the tree exists to fix: four experiments derived from the same
    // parent are four branches, not four steps.
    const tree = buildTree('root', [
      row('a', 'root'),
      row('b', 'root'),
      row('c', 'root'),
      row('d', 'root'),
    ])
    expect(tree).toHaveLength(4)
    expect(tree.every((node) => node.children.length === 0)).toBe(true)
  })

  it('branches where the search widened and follows the branch it went deeper on', () => {
    const tree = buildTree('root', [
      row('a', 'root'),
      row('b', 'root'),
      row('b1', 'b'),
      row('b2', 'b'),
      row('b1x', 'b1'),
    ])
    expect(shape(tree)).toEqual([
      { id: 'a', children: [] },
      {
        id: 'b',
        children: [{ id: 'b1', children: [{ id: 'b1x', children: [] }] }, { id: 'b2', children: [] }],
      },
    ])
  })

  it('attaches an orphan to the root rather than dropping it', () => {
    // A record whose parent is outside the returned set still exists. Hiding it
    // would under-report what the search did, which is worse than showing it in
    // the wrong place.
    const tree = buildTree('root', [row('a', 'root'), row('lost', 'not_in_this_set')])
    expect(tree.map((node) => node.row.id).sort()).toEqual(['a', 'lost'])
  })

  it('returns nothing for a leaf', () => {
    expect(buildTree('root', [])).toEqual([])
  })

  it('does not repeat a node that two parents both claim', () => {
    // Experiments record one parent each, so this should not arise — but a
    // duplicated row must not make the tree infinite or double-count a branch.
    const tree = buildTree('root', [row('a', 'root'), row('a', 'root')])
    expect(tree).toHaveLength(1)
  })

  it('terminates on a cycle instead of recursing for ever', () => {
    const tree = buildTree('root', [row('x', 'y'), row('y', 'x')])
    const ids: string[] = []
    const walk = (nodes: ReturnType<typeof buildTree>) => {
      for (const node of nodes) {
        ids.push(node.row.id)
        walk(node.children)
      }
    }
    walk(tree)
    expect(ids.sort()).toEqual(['x', 'y'])
  })
})

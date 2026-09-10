/* Test environment defaults, applied before every suite.
 *
 * Neither of these is optional, and the project had neither:
 *
 * `cleanup` unmounts what a test rendered. Without it every container stays in
 * the document for the rest of the file, so `getByTestId` starts throwing
 * "found multiple elements" in the *next* test and a suite's outcome depends on
 * the order its cases happen to run in.
 *
 * `IS_REACT_ACT_ENVIRONMENT` is how React 19 is told it is under test. Without
 * it, `act(...)` warns on stderr and state updates outside an act scope are not
 * flushed the way the assertions assume.
 */

import '@testing-library/jest-dom/vitest'
import { cleanup } from '@testing-library/react'
import { afterEach } from 'vitest'

declare global {
  // React 19 reads this to decide whether it is running under a test runner.
  // eslint-disable-next-line no-var
  var IS_REACT_ACT_ENVIRONMENT: boolean
}

globalThis.IS_REACT_ACT_ENVIRONMENT = true

afterEach(cleanup)

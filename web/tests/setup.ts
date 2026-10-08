/**
 * Vitest setup.
 *
 * `globals: false` means Testing Library cannot register its automatic cleanup,
 * so it is done here once for every test file. Without it, a second `render` in
 * the same file leaves the first tree mounted and queries match twice.
 */

import { cleanup } from '@testing-library/react'
import { afterEach } from 'vitest'

afterEach(() => {
  cleanup()
})

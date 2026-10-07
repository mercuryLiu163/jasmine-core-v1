/**
 * Mask-by-default policy for displayed Core text.
 *
 * The taskbook requires the display layer to mask credentials unless the
 * operator explicitly expands the original. Expansion is per-field and resets
 * whenever the underlying value changes, so a reveal can never silently carry
 * over to a different Event or Evidence row.
 */

import { useEffect, useMemo, useState } from 'react'

import { maskCredentials, type MaskResult } from './text'

export interface MaskedText {
  /** What should be rendered right now. */
  shown: string
  masked: MaskResult
  revealed: boolean
  hasSecrets: boolean
  reveal: () => void
  hide: () => void
}

export function useMaskedText(value: string): MaskedText {
  const masked = useMemo(() => maskCredentials(value), [value])
  const [revealed, setRevealed] = useState(false)
  useEffect(() => {
    setRevealed(false)
  }, [value])
  return {
    shown: revealed ? value : masked.text,
    masked,
    revealed,
    hasSecrets: masked.masked.length > 0,
    reveal: () => setRevealed(true),
    hide: () => setRevealed(false),
  }
}

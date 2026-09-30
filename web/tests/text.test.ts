import { describe, expect, it } from 'vitest'

import {
  clip,
  formatTimestamp,
  hasCredentialShape,
  isMachineEvent,
  maskCredentials,
  payloadText,
  safeHref,
  stringifyJson,
} from '../src/lib/text'

describe('maskCredentials', () => {
  it('masks high-confidence credential shapes', () => {
    const input = [
      'Authorization: Bearer abcdefghijklmnop',
      'openai sk-ABCDEFGHIJKLMNOPQRSTUV',
      'jwt eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxIn0.abcd',
      'password = hunter2hunter2',
    ].join('\n')
    const { text, masked } = maskCredentials(input)
    expect(text).not.toContain('abcdefghijklmnop')
    expect(text).not.toContain('sk-ABCDEFGHIJKLMNOPQRSTUV')
    expect(text).not.toContain('hunter2hunter2')
    expect(text).toContain('«已脱敏:bearer»')
    expect(masked.map((item) => item.label)).toContain('openai-key')
  })

  it('leaves ordinary prose and Core ids alone', () => {
    const input = 'evt_01M3RCYJNQ13M3PQGAEXXKBASR at 2026-09-30T05:35:12.055048Z'
    const { text, masked } = maskCredentials(input)
    expect(text).toBe(input)
    expect(masked).toHaveLength(0)
    expect(hasCredentialShape(input)).toBe(false)
  })

  it('flags credential-looking text via hasCredentialShape', () => {
    expect(hasCredentialShape('token=abcdefghijklmnop')).toBe(true)
    expect(hasCredentialShape('nothing secret here')).toBe(false)
  })
})

describe('safeHref', () => {
  it('allows in-app, http(s) and mailto links', () => {
    expect(safeHref('#/event/evt_1')).toBe('#/event/evt_1')
    expect(safeHref('https://example.invalid/x')).toBe('https://example.invalid/x')
    expect(safeHref('mailto:someone@example.invalid')).toBe('mailto:someone@example.invalid')
  })

  it('refuses script, data, vbscript and protocol-relative URLs', () => {
    expect(safeHref('javascript:alert(1)')).toBeNull()
    expect(safeHref('JavaScript:alert(1)')).toBeNull()
    expect(safeHref('data:text/html,<script>1</script>')).toBeNull()
    expect(safeHref('vbscript:msgbox(1)')).toBeNull()
    expect(safeHref('//evil.invalid/x')).toBeNull()
    expect(safeHref('file:///etc/passwd')).toBeNull()
    expect(safeHref(null)).toBeNull()
    expect(safeHref('')).toBeNull()
  })
})

describe('event helpers', () => {
  it('separates machine events from human utterances', () => {
    expect(isMachineEvent('tool.result')).toBe(true)
    expect(isMachineEvent('step.transitioned')).toBe(true)
    expect(isMachineEvent('user.prompt')).toBe(false)
    expect(isMachineEvent('assistant.message')).toBe(false)
  })

  it('reads payload.text only when it is a string', () => {
    expect(payloadText({ text: 'hello' })).toBe('hello')
    expect(payloadText({ text: 42 })).toBeNull()
    expect(payloadText(null)).toBeNull()
    expect(payloadText('plain')).toBeNull()
  })
})

describe('formatting', () => {
  it('clips long values and says how much was hidden', () => {
    expect(clip('abcdef', 3)).toBe('abc…(+3)')
    expect(clip('abc', 3)).toBe('abc')
  })

  it('keeps an unparsable timestamp visible rather than printing NaN', () => {
    expect(formatTimestamp('not-a-time')).toBe('not-a-time')
    expect(formatTimestamp(null)).toBe('—')
  })

  it('stringifies structures for the JSON viewer', () => {
    expect(stringifyJson({ a: 1 })).toContain('"a": 1')
  })
})

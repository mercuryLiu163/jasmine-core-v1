/**
 * Public identifier helpers (P6-02 milestone B).
 *
 * Core's id schema (docs/adr/0001-public-id-schema-and-package-boundary.md, and
 * `jasmine_core/ids.py`) fixes every id to `<prefix>_<26 Crockford base32
 * chars>` and says of `evt_` specifically: *"An `evt` body is opaque: clients
 * may derive it deterministically for idempotent retries."*
 *
 * Authority writes (propose/approve/supersede/retire) do **not** accept an
 * `idempotency_key` — Core validates their fields exactly and would reject it.
 * Their replay key is the client-supplied `event_id`: re-sending the same
 * `event_id` with the same body returns the stored rule with `replayed: true`,
 * and re-sending it with a different body is refused as `event_id_conflict`.
 *
 * So the UI must mint an `evt_` id itself, and it must be a *valid* one, or
 * Core answers `invalid_request` before it ever reaches the replay check.
 */

/** Crockford base32, exactly as `jasmine_core/ids.py` orders it. */
const CROCKFORD = '0123456789ABCDEFGHJKMNPQRSTVWXYZ'
const EVT_BODY_CHARS = 26

/**
 * Return a fresh `evt_` id shaped exactly like Core's.
 *
 * The body is opaque to Core, so this fills the 26 characters with unbiased
 * random bytes rather than imitating Core's timestamp encoding — the schema
 * only requires the shape, and claiming a creation time the browser did not
 * measure would be a lie.
 */
export function newEventId(): string {
  const globalCrypto = globalThis.crypto
  const bytes = new Uint8Array(EVT_BODY_CHARS)
  if (globalCrypto && typeof globalCrypto.getRandomValues === 'function') {
    globalCrypto.getRandomValues(bytes)
  } else {
    for (let index = 0; index < bytes.length; index += 1) {
      bytes[index] = Math.floor(Math.random() * 256)
    }
  }
  let body = ''
  for (const byte of bytes) body += CROCKFORD[byte & 0x1f]
  return `evt_${body}`
}

/** True when `value` is a well-formed `evt_` id, so a draft can be validated. */
export function isEventId(value: string): boolean {
  return new RegExp(`^evt_[${CROCKFORD}]{${EVT_BODY_CHARS}}$`).test(value)
}

/**
 * True when `value` is a well-formed id for `prefix` (ADR 0001: `<prefix>_` + 26
 * Crockford base32 characters).
 *
 * The proxy's read allowlist spells ids the same way, so a malformed id never reaches
 * Core. That makes client-side validation necessary rather than decorative: without it a
 * typo is refused by the *proxy*, and the P6-03a views would report "P3 is not merged"
 * for what is actually a bad id — a wrong explanation is worse than none.
 */
export function isCoreId(value: string, prefix: string): boolean {
  return new RegExp(`^${prefix}_[${CROCKFORD}]{26}$`).test(value)
}

/**
 * Types for `serve.mjs`.
 *
 * The static host is plain ESM JavaScript so that running the dashboard never
 * needs a build step; these declarations exist so the TypeScript tests can import
 * it without an implicit `any` and so the classifier stays honest on both hosts.
 */

import type { IncomingMessage, Server, ServerResponse } from 'node:http'

export type TargetKind = 'api' | 'static' | 'bad'

export interface ClassifiedTarget {
  kind: TargetKind
  pathname: string
  reason?: string
}

export interface ParsedArgs {
  root: string
  port: number
  host: string
}

export interface Allowlist {
  methods: Set<string>
  paths: RegExp[]
  /** The live read list before the P3 opt-in is applied. */
  baseline_paths: RegExp[]
  /** Real P3 paths, forwarded only when the operator states the Core has P3. */
  p3_pending_paths: RegExp[]
  p3_reads_enabled: boolean
  /** Real P4 memory paths, forwarded only when the operator states the Core has P4. */
  p4_pending_paths: RegExp[]
  p4_reads_enabled: boolean
  /** Real P7-01 Validation read paths, forwarded only when the operator states the Core has them. */
  p7_pending_paths: RegExp[]
  p7_reads_enabled: boolean
  write_methods?: string[]
  /** The milestone B write list before the P4 write opt-in is applied. */
  baseline_write_paths: string[]
  /** Milestone B writes, plus the P4 writes when the operator states the Core has them. */
  write_paths?: string[]
  /** Real P4 memory write paths, forwarded only when the operator states the Core has them. */
  p4_write_paths: RegExp[]
  p4_writes_enabled: boolean
}

export const P3_READS_ENV: string
export const P4_READS_ENV: string
export const P4_WRITES_ENV: string
export const P7_READS_ENV: string
export function p3ReadsEnabled(env?: Record<string, string | undefined>): boolean
export function p4ReadsEnabled(env?: Record<string, string | undefined>): boolean
export function p4WritesEnabled(env?: Record<string, string | undefined>): boolean
export function p7ReadsEnabled(env?: Record<string, string | undefined>): boolean
export function parseArgs(argv: string[]): ParsedArgs
export function loadAllowlist(
  directory?: string,
  options?: {
    enableP3Reads?: boolean
    enableP4Reads?: boolean
    enableP4Writes?: boolean
    enableP7Reads?: boolean
  },
): Allowlist
export function resolveCoreOrigin(raw?: string): URL
export function isAllowed(allowlist: Allowlist, method: string | undefined, pathname: string): boolean
export function classifyRequestTarget(rawUrl: string | undefined): ClassifiedTarget
/** The correlation id a refusal this hop generates carries (mirrors `core-proxy.ts`). */
export function proxyRequestId(req: IncomingMessage | undefined): string
export function proxy(
  req: IncomingMessage,
  res: ServerResponse,
  origin: URL,
  allowlist: Allowlist,
): void
export function serveStatic(req: IncomingMessage, res: ServerResponse, root: string): void
export function createDashboardServer(options: {
  root: string
  origin: URL
  allowlist?: Allowlist
}): Server

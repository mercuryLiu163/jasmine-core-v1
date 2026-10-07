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
  write_methods?: string[]
  write_paths?: string[]
}

export function parseArgs(argv: string[]): ParsedArgs
export function loadAllowlist(directory?: string): Allowlist
export function resolveCoreOrigin(raw?: string): URL
export function isAllowed(allowlist: Allowlist, method: string | undefined, pathname: string): boolean
export function classifyRequestTarget(rawUrl: string | undefined): ClassifiedTarget
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

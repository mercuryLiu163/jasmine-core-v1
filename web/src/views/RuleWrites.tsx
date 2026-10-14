/**
 * Rule write panels (P6-02 milestone B).
 *
 * These are the Authority writes: `propose`, `approve`, `supersede`, `retire`.
 * Their Core contract differs from the interpretation/review writes in three
 * ways that the UI must not paper over, because Core validates fields exactly
 * and refuses anything extra:
 *
 * 1. **No `reason`.** `AuthorityStore.propose`/`transition` call `_fields` with
 *    an exact allow-list, so a `reason` field is answered with
 *    `invalid_request: unknown fields`. These panels use `reasonMode="none"`
 *    and simply do not send it.
 * 2. **No `idempotency_key`.** Replay is keyed on a client-minted `event_id`
 *    (`AuthorityStore._replay`): same id + same body returns the stored rule
 *    with `replayed: true`; same id + different body is `event_id_conflict`.
 *    The hook therefore runs with `keyKind: 'event_id'`.
 * 3. **Creation takes no `expected_revision`** — Core raises
 *    `UnexpectedExpectedRevision` if you send one — while `approve`, `retire`
 *    and `supersede` require it. Only the transition panels show it.
 *
 * Everything else follows the shared rules: the basis is the revision Core
 * returned, the host comes from `GET /v1/hosts`, a conflict keeps the draft,
 * and a refusal never gets a better credential.
 */

import { useEffect, useId, useState } from 'react'

import type { Rule } from '../api/types'
import { useSession } from '../app/session'
import { useResource } from '../app/useResource'
import { useWriteAction } from '../app/useWriteAction'
import { WritePanel, parseJsonField } from '../components/WritePanel'
import { ErrorPanel } from '../components/States'
import { isEventId } from '../lib/ids'

/** Mirrors `authority.RULE_KEY`; a bad key is refused by Core before any write. */
const RULE_KEY_RE = /^[a-z][a-z0-9_.-]{0,127}$/
/**
 * Core's public id shape (`ids.ID_RE`: `<prefix>_` + 26 Crockford base32 chars), per
 * prefix. A prefix-agnostic `[a-z]{3}_` let a `tsk_` id pass as a `project_id` and Core
 * answered 404, while the message claimed to check a prefix it did not — so the check
 * would have been both wrong and reassuring.
 */
const idShape = (prefix: string): RegExp =>
  new RegExp(`^${prefix}_[0-9ABCDEFGHJKMNPQRSTVWXYZ]{26}$`)

/**
 * Mirrors `authority._path` for `matcher.path_prefix`: an absolute, canonical POSIX
 * path. `PurePosixPath(value) == value` is what Core checks last, which is why a
 * trailing slash and dot segments are refused.
 */
function pathPrefixError(value: string): string | null {
  if (!value.startsWith('/')) return 'matcher.path_prefix 必须是绝对路径(以 / 开头)'
  if (value.includes('//')) return 'matcher.path_prefix 不能包含 //'
  if (value.includes('\\') || value.includes('\u0000')) {
    return 'matcher.path_prefix 不能包含反斜杠或 NUL'
  }
  if (value.split('/').some((part) => part === '.' || part === '..')) {
    return 'matcher.path_prefix 不能包含 . 或 .. 片段'
  }
  if (value !== '/' && value.endsWith('/')) return 'matcher.path_prefix 不能以 / 结尾'
  return null
}
const KINDS = ['RULE', 'DECISION', 'ACCEPTANCE'] as const
const SEVERITIES = ['HARD', 'NORMAL'] as const
const ENFORCEMENTS = ['CONTEXT', 'DENY', 'CONFIRM', 'VERIFY'] as const
const MATCHER_KEYS = ['tool', 'tool_category', 'action', 'path_prefix'] as const
/** Mirrors `authority.TOOL_CATEGORIES` (ADR 0018). */
const TOOL_CATEGORIES = ['shell', 'file.read', 'file.write', 'file.edit', 'web', 'mcp', 'other'] as const

type Kind = (typeof KINDS)[number]
type Enforcement = (typeof ENFORCEMENTS)[number]

/** The scope block as a form value, kept as text so a wrong id is reported. */
interface ScopeDraft {
  kind: 'global' | 'project' | 'task'
  projectId: string
  taskId: string
}

/**
 * Validate the scope exactly as `AuthorityStore._scope` does for the parts the
 * browser can know: shape and mutual consistency. Whether the project/task
 * exists is Core's answer, not something this view fabricates.
 */
function scopeError(draft: ScopeDraft): string | null {
  if (draft.kind === 'global') {
    if (draft.projectId.trim() || draft.taskId.trim()) {
      return 'global scope 不能同时指定 project_id/task_id(Core 会拒绝)'
    }
    return null
  }
  if (!idShape('prj').test(draft.projectId.trim())) {
    return 'project scope 需要 Core 形状的 project_id(prj_ + 26 位 Crockford base32)'
  }
  if (draft.kind === 'project') {
    if (draft.taskId.trim()) return 'project scope 不能同时指定 task_id'
    return null
  }
  if (!idShape('tsk').test(draft.taskId.trim())) {
    return 'task scope 需要 Core 形状的 task_id(tsk_ + 26 位 Crockford base32)'
  }
  return null
}

function scopeBody(draft: ScopeDraft): Record<string, unknown> {
  return {
    kind: draft.kind,
    project_id: draft.kind === 'global' ? null : draft.projectId.trim(),
    task_id: draft.kind === 'task' ? draft.taskId.trim() : null,
  }
}

/** The content fields Core's `_content` validates, shared by propose/supersede. */
interface ContentDraft {
  kind: Kind
  severity: string
  enforcement: Enforcement
  content: string
  matcher: string
  verification: string
}

const EMPTY_CONTENT: ContentDraft = {
  kind: 'RULE',
  severity: 'NORMAL',
  enforcement: 'CONTEXT',
  content: '',
  matcher: '{}',
  verification: '',
}

function contentFrom(rule: Rule): ContentDraft {
  return {
    kind: (KINDS as readonly string[]).includes(rule.kind) ? (rule.kind as Kind) : 'RULE',
    severity: (SEVERITIES as readonly string[]).includes(rule.severity) ? rule.severity : 'NORMAL',
    enforcement: (ENFORCEMENTS as readonly string[]).includes(rule.enforcement)
      ? (rule.enforcement as Enforcement)
      : 'CONTEXT',
    content: rule.content,
    matcher: JSON.stringify(rule.matcher ?? {}, null, 2),
    verification: rule.verification_requirements
      ? JSON.stringify(rule.verification_requirements, null, 2)
      : '',
  }
}

/**
 * Mirror `authority._content`: these checks explain a refusal before the round
 * trip instead of hiding it. Core remains the authority; a value that passes
 * here can still be refused (e.g. an unknown project, or a rule_key that
 * already exists in the scope).
 */
function contentError(draft: ContentDraft, parsedMatcher: { value?: unknown; error?: string }): string | null {
  if (!draft.content.trim()) return 'content 不能为空'
  if (draft.content.length > 4096) return 'content 最多 4096 个字符'
  if (parsedMatcher.error !== undefined) return parsedMatcher.error
  const matcher = parsedMatcher.value
  if (typeof matcher !== 'object' || matcher === null || Array.isArray(matcher)) {
    return 'matcher 必须是对象(Core 的 matcher 只接受 tool/tool_category/action/path_prefix)'
  }
  for (const key of Object.keys(matcher)) {
    if (!(MATCHER_KEYS as readonly string[]).includes(key)) {
      return `matcher 不认识的键 ${key};只允许 ${MATCHER_KEYS.join('/')}`
    }
    const entry = (matcher as Record<string, unknown>)[key]
    if (typeof entry !== 'string') return `matcher.${key} 必须是文本`
    if (entry === '') return `matcher.${key} 不能为空(Core 要求非空文本)`
    if (entry.length > 512) return `matcher.${key} 最多 512 个字符`
    if (key === 'path_prefix') {
      const problem = pathPrefixError(entry)
      if (problem !== null) return problem
    }
    if (key === 'tool_category' && !(TOOL_CATEGORIES as readonly string[]).includes(entry)) {
      return `matcher.tool_category 必须是 ${TOOL_CATEGORIES.join('/')} 之一`
    }
  }
  if ((draft.enforcement === 'DENY' || draft.enforcement === 'CONFIRM') && Object.keys(matcher).length === 0) {
    return 'DENY/CONFIRM 需要确定性的 matcher,不能为空'
  }
  if ((draft.enforcement === 'DENY' || draft.enforcement === 'CONFIRM') && draft.kind !== 'RULE') {
    return 'DENY/CONFIRM 只适用于 kind=RULE'
  }
  return null
}

function verificationError(raw: string, draft: ContentDraft): string | null {
  if (raw.trim() === '') return null
  if (draft.kind !== 'ACCEPTANCE' && draft.enforcement !== 'VERIFY') {
    return 'verification_requirements 需要 kind=ACCEPTANCE 或 enforcement=VERIFY'
  }
  let parsed: unknown
  try {
    parsed = JSON.parse(raw)
  } catch (error) {
    return `verification_requirements 不是合法 JSON: ${error instanceof Error ? error.message : String(error)}`
  }
  // Core's `_requirement_set` requires `{requirements: [...]}`, non-empty, with a
  // unique non-empty `key` per member. The `kind`/`required_result` enums are left to
  // Core: duplicating them here would drift the moment Core grows one, and the panel
  // already says Core is the authority.
  if (typeof parsed !== 'object' || parsed === null || Array.isArray(parsed)) {
    return 'verification_requirements 必须是对象 {requirements: [...]}'
  }
  // Core's `_requirement_set` allows exactly `{requirements}`; an extra top-level key
  // is `invalid_request`, not a harmless addition.
  const unknownTop = Object.keys(parsed as Record<string, unknown>).filter((key) => key !== 'requirements')
  if (unknownTop.length > 0) {
    return `verification_requirements 不认识的键: ${unknownTop.join('、')}(Core 只接受 requirements)`
  }
  const requirements = (parsed as { requirements?: unknown }).requirements
  if (!Array.isArray(requirements) || requirements.length === 0) {
    return 'verification_requirements.requirements 必须是非空数组'
  }
  const keys = new Set<string>()
  for (const item of requirements) {
    if (typeof item !== 'object' || item === null || Array.isArray(item)) {
      return '每个 requirement 必须是对象'
    }
    // Mirrors Core's per-member allow-list (`evidence._requirement_set`).
    const allowed = ['key', 'kind', 'required_result', 'tool_name', 'command_sha256', 'artifact_sha256']
    const unknown = Object.keys(item as Record<string, unknown>).filter((k) => !allowed.includes(k))
    if (unknown.length > 0) {
      return `requirement 不认识的键: ${unknown.join('、')}(Core 只接受 ${allowed.join('/')})`
    }
    const key = (item as { key?: unknown }).key
    if (typeof key !== 'string' || key === '' || key.length > 128) {
      return '每个 requirement 需要非空且不超过 128 字符的 key'
    }
    if (keys.has(key)) return `requirement key 重复: ${key}`
    keys.add(key)
  }
  return null
}

/** The shared content editor for propose and supersede. */
function ContentFields({
  draft,
  onChange,
}: {
  draft: ContentDraft
  onChange: (next: ContentDraft) => void
}): JSX.Element {
  const kindId = useId()
  const severityId = useId()
  const enforcementId = useId()
  const contentId = useId()
  const matcherId = useId()
  const verificationId = useId()
  return (
    <>
      <div className="filters">
        <label htmlFor={kindId}>
          kind
          <select
            id={kindId}
            value={draft.kind}
            onChange={(event) => onChange({ ...draft, kind: event.target.value as Kind })}
          >
            {KINDS.map((kind) => (
              <option key={kind} value={kind}>
                {kind}
              </option>
            ))}
          </select>
        </label>
        <label htmlFor={severityId}>
          severity
          <select
            id={severityId}
            value={draft.severity}
            onChange={(event) => onChange({ ...draft, severity: event.target.value })}
          >
            {SEVERITIES.map((severity) => (
              <option key={severity} value={severity}>
                {severity}
              </option>
            ))}
          </select>
        </label>
        <label htmlFor={enforcementId}>
          enforcement
          <select
            id={enforcementId}
            value={draft.enforcement}
            onChange={(event) =>
              onChange({ ...draft, enforcement: event.target.value as Enforcement })
            }
          >
            {ENFORCEMENTS.map((enforcement) => (
              <option key={enforcement} value={enforcement}>
                {enforcement}
              </option>
            ))}
          </select>
        </label>
      </div>
      <label className="write-field" htmlFor={contentId}>
        content(Core 必填,≤4096 字符)
        <textarea
          id={contentId}
          value={draft.content}
          onChange={(event) => onChange({ ...draft, content: event.target.value })}
          rows={3}
        />
      </label>
      <label className="write-field" htmlFor={matcherId}>
        matcher(JSON 对象;键只允许 tool / tool_category / action / path_prefix;tool_category 取 shell、file.read、file.write、file.edit、web、mcp、other,与具体 Agent 无关)
        <textarea
          id={matcherId}
          value={draft.matcher}
          onChange={(event) => onChange({ ...draft, matcher: event.target.value })}
          rows={3}
          spellCheck={false}
        />
      </label>
      <label className="write-field" htmlFor={verificationId}>
        verification_requirements(JSON,可空;仅 ACCEPTANCE 或 VERIFY 可用)
        <textarea
          id={verificationId}
          value={draft.verification}
          onChange={(event) => onChange({ ...draft, verification: event.target.value })}
          rows={3}
          spellCheck={false}
        />
      </label>
    </>
  )
}

/** An origin Event picker backed by real Events in the scope being viewed. */
function OriginField({
  label,
  value,
  onChange,
  candidates,
}: {
  label: string
  value: string
  onChange: (next: string) => void
  candidates: ReadonlyArray<{ event_id: string; event_type: string; actor_kind: string }>
}): JSX.Element {
  const selectId = useId()
  const inputId = useId()
  return (
    <div className="filters">
      <label htmlFor={selectId}>
        候选 origin Event(按当前 scope 过滤)
        <select
          id={selectId}
          value={candidates.some((event) => event.event_id === value) ? value : ''}
          onChange={(event) => onChange(event.target.value)}
        >
          <option value="">— 选择或直接填写 —</option>
          {candidates.map((event) => (
            <option key={event.event_id} value={event.event_id}>
              {event.event_type} · {event.actor_kind} · {event.event_id}
            </option>
          ))}
        </select>
      </label>
      <label htmlFor={inputId}>
        {label}
        <input
          id={inputId}
          value={value}
          onChange={(event) => onChange(event.target.value)}
          placeholder="evt_…"
          autoComplete="off"
        />
      </label>
    </div>
  )
}

export function RuleWrites({
  rule,
  projectId,
  taskId,
  onReadback,
}: {
  /** The Rule read by ID, when one is open; null means only propose is offered. */
  rule: Rule | null
  projectId: string | null
  taskId: string | null
  onReadback: () => void
}): JSX.Element {
  const { endpoints } = useSession()
  const propose = useWriteAction((body, signal) => endpoints.proposeRule(body, signal), {
    keyKind: 'event_id',
  })
  // `targetKey` is the Rule these hooks write to. This component stays mounted while
  // the operator moves between Rules, so without it the retire hook would carry Rule
  // A's `event_id` to Rule B and Core would refuse it as `event_id_conflict`.
  const targetKey = rule?.rule_id ?? ''
  const approve = useWriteAction(
    (body, signal) => endpoints.approveRule(rule?.rule_id ?? '', body, signal),
    { keyKind: 'event_id', targetKey },
  )
  const supersede = useWriteAction(
    (body, signal) => endpoints.supersedeRule(rule?.rule_id ?? '', body, signal),
    { keyKind: 'event_id', targetKey },
  )
  const retire = useWriteAction(
    (body, signal) => endpoints.retireRule(rule?.rule_id ?? '', body, signal),
    { keyKind: 'event_id', targetKey },
  )

  const [ruleKey, setRuleKey] = useState('')
  const [proposeOrigin, setProposeOrigin] = useState('')
  const [scope, setScope] = useState<ScopeDraft>({
    kind: taskId ? 'task' : projectId ? 'project' : 'global',
    projectId: projectId ?? '',
    taskId: taskId ?? '',
  })

  // `RuleWrites` stays mounted while the operator moves between projects and tasks,
  // so the propose scope has to follow the route rather than keep the first one.
  useEffect(() => {
    setScope({
      kind: taskId ? 'task' : projectId ? 'project' : 'global',
      projectId: projectId ?? '',
      taskId: taskId ?? '',
    })
    setProposeOrigin('')
  }, [projectId, taskId])
  const [proposeContent, setProposeContent] = useState<ContentDraft>(EMPTY_CONTENT)

  // `AuthorityStore._origin` requires the origin Event's scope to equal the Rule's,
  // so each panel's candidate list follows *its own* scope: offering an Event Core
  // will refuse is offering a guaranteed 400. Propose follows the scope being
  // proposed for; Supersede follows the Rule's own locked scope.
  const scopeQuery = (kind: string, project: string, task: string): { project_id?: string; task_id?: string } => {
    if (kind === 'global') return {}
    const trimmed = project.trim()
    if (kind === 'task' && task.trim()) return { project_id: trimmed, task_id: task.trim() }
    return trimmed ? { project_id: trimmed } : {}
  }
  const candidateQuery = scopeQuery(scope.kind, scope.projectId, scope.taskId)
  // Propose's candidates.
  const events = useResource(
    (signal) => endpoints.listEvents({ ...candidateQuery, limit: 50 }, signal),
    // The query is derived from the scope draft, so it is the dependency.
    [candidateQuery.project_id, candidateQuery.task_id],
  )
  // Supersede's candidates are fetched for the *Rule's* scope. Filtering the
  // propose-scope list instead left the dropdown empty whenever the two scopes
  // differed, which looks like "Core has no Events" rather than "wrong scope".
  const ruleScopeQuery = scopeQuery(
    rule?.scope.kind ?? 'global',
    rule?.scope.project_id ?? '',
    rule?.scope.task_id ?? '',
  )
  const ruleEvents = useResource(
    rule === null
      ? null
      : (signal) => endpoints.listEvents({ ...ruleScopeQuery, limit: 50 }, signal),
    [rule?.rule_id, ruleScopeQuery.project_id, ruleScopeQuery.task_id],
  )
  const inScope = (
    event: { project_id: string | null; task_id: string | null },
    kind: string,
    project: string,
    task: string,
  ): boolean => {
    if (kind === 'global') return event.project_id === null && event.task_id === null
    if (kind === 'project') return event.project_id === project.trim() && event.task_id === null
    return event.project_id === project.trim() && event.task_id === task.trim()
  }
  const candidates = (events.data?.events ?? []).filter((event) =>
    inScope(event, scope.kind, scope.projectId, scope.taskId),
  )
  // The Supersede panel's own candidates, from the Rule's scope rather than the
  // propose form's.
  const ruleScopeCandidates = (ruleEvents.data?.events ?? []).filter((event) =>
    rule ? inScope(event, rule.scope.kind, rule.scope.project_id ?? '', rule.scope.task_id ?? '') : false,
  )

  // Supersede keeps its own draft, seeded from the Rule Core returned, so editing
  // the replacement cannot silently change the proposal form's content. The seed
  // is keyed on the Rule's identity *and* revision: refetching the same revision
  // must not discard the operator's edits.
  const [supersedeContent, setSupersedeContent] = useState<ContentDraft>(EMPTY_CONTENT)
  const [supersedeOrigin, setSupersedeOrigin] = useState('')
  const seedKey = rule ? `${rule.rule_id}:${rule.revision}` : null
  useEffect(() => {
    if (rule) setSupersedeContent(contentFrom(rule))
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [seedKey])

  const proposeMatcher = parseJsonField(proposeContent.matcher, 'matcher')
  const supersedeMatcher = parseJsonField(supersedeContent.matcher, 'matcher')

  const ruleKeyError = RULE_KEY_RE.test(ruleKey.trim())
    ? null
    : 'rule_key 必须是小写稳定键(^[a-z][a-z0-9_.-]{0,127}$)'
  const proposeOriginError = isEventId(proposeOrigin.trim())
    ? null
    : 'origin_event_id 必须是 evt_ 开头的 Event id'
  const proposeError =
    ruleKeyError ??
    proposeOriginError ??
    scopeError(scope) ??
    contentError(proposeContent, proposeMatcher) ??
    verificationError(proposeContent.verification, proposeContent)

  const supersedeOriginError = isEventId(supersedeOrigin.trim())
    ? null
    : 'supersede 必须有 origin_event_id(evt_ 开头),且该 Event 的 scope 要和 Rule 一致'
  const supersedeError =
    supersedeOriginError ??
    contentError(supersedeContent, supersedeMatcher) ??
    verificationError(supersedeContent.verification, supersedeContent)

  const ruleKeyId = useId()
  const scopeKindId = useId()
  const scopeProjectId = useId()
  const scopeTaskId = useId()

  const transitionBasis = rule
    ? `rule ${rule.rule_id} · ${rule.rule_key} · status ${rule.status} · revision ${rule.revision}`
    : '尚未读取任何 Rule'

  /** The content keys every supersede/propose body shares. */
  const contentKeys = (draft: ContentDraft, matcher: { value?: unknown }) => ({
    kind: draft.kind,
    severity: draft.severity,
    enforcement: draft.enforcement,
    content: draft.content,
    matcher: matcher.value,
    ...(draft.verification.trim() === ''
      ? {}
      : { verification_requirements: JSON.parse(draft.verification) }),
  })

  return (
    <>
      <WritePanel
        title="Propose(提出新的 Rule)"
        basis={`新 Rule · scope ${scope.kind}${
          scope.kind === 'global' ? '' : ` · ${scope.projectId || '—'}`
        }${scope.kind === 'task' ? ` · ${scope.taskId || '—'}` : ''}`}
        action={propose}
        submitLabel="提交提案"
        ready={proposeError === null}
        reasonMode="none"
        onSucceeded={onReadback}
        fields={
          <>
            <p className="muted">
              提案产生 <code>rule.proposed</code> 事件,新 Rule 进入 <strong>PROPOSED</strong>;
              它<strong>不会</strong>立刻生效,必须再被 Approve。创建<strong>不接受</strong>
              <code> expected_revision</code>(Core 会返回 <code>unexpected_expected_revision</code>)。
            </p>
            <div className="filters">
              <label htmlFor={ruleKeyId}>
                rule_key(小写稳定键)
                <input
                  id={ruleKeyId}
                  value={ruleKey}
                  onChange={(event) => setRuleKey(event.target.value)}
                  placeholder="ui.review.required"
                  autoComplete="off"
                />
              </label>
              <label htmlFor={scopeKindId}>
                scope.kind
                <select
                  id={scopeKindId}
                  value={scope.kind}
                  onChange={(event) =>
                    setScope({ ...scope, kind: event.target.value as ScopeDraft['kind'] })
                  }
                >
                  <option value="global">global</option>
                  <option value="project">project</option>
                  <option value="task">task</option>
                </select>
              </label>
              {scope.kind !== 'global' ? (
                <label htmlFor={scopeProjectId}>
                  scope.project_id
                  <input
                    id={scopeProjectId}
                    value={scope.projectId}
                    onChange={(event) => setScope({ ...scope, projectId: event.target.value })}
                    placeholder="prj_…"
                    autoComplete="off"
                  />
                </label>
              ) : null}
              {scope.kind === 'task' ? (
                <label htmlFor={scopeTaskId}>
                  scope.task_id
                  <input
                    id={scopeTaskId}
                    value={scope.taskId}
                    onChange={(event) => setScope({ ...scope, taskId: event.target.value })}
                    placeholder="tsk_…"
                    autoComplete="off"
                  />
                </label>
              ) : null}
            </div>

            <OriginField
              label="origin_event_id(Core 校验存在性 + scope 一致)"
              value={proposeOrigin}
              onChange={setProposeOrigin}
              candidates={candidates}
            />
            {events.error ? (
              <ErrorPanel error={events.error} what="origin Event 候选" onRetry={events.reload} />
            ) : null}

            <ContentFields draft={proposeContent} onChange={setProposeContent} />
            {proposeError ? (
              <p className="state state--error" role="alert">
                {proposeError}
              </p>
            ) : null}
          </>
        }
        build={({ hostId }) =>
          proposeError !== null
            ? proposeError
            : {
                rule_key: ruleKey.trim(),
                scope: scopeBody(scope),
                origin_event_id: proposeOrigin.trim(),
                host_id: hostId,
                ...contentKeys(proposeContent, proposeMatcher),
              }
        }
      />

      {rule === null ? (
        <div className="panel" data-testid="rule-transition-unavailable">
          <p className="state state--meta">
            Approve / Supersede / Retire 需要一个已读取的 Rule:它们都要回传 Core 当前的
            <code> revision</code>,而 <code>GET /v1/rules/active</code> 只列出 ACTIVE Rule,
            所以请先用上面的 “按 ID 读取单个 Rule” 打开目标。
          </p>
        </div>
      ) : (
        <>
          <WritePanel
            title="Approve(让 PROPOSED Rule 生效)"
            basis={transitionBasis}
            action={approve}
            submitLabel="提交批准"
            ready={rule.status === 'PROPOSED'}
            reasonMode="none"
            onSucceeded={onReadback}
            fields={
              <p className="muted">
                只有 <strong>PROPOSED</strong> 的 Rule 可以被批准(当前 status{' '}
                <code>{rule.status}</code>)。批准写入 <code>rule.approved</code>,状态变为 ACTIVE,
                revision {rule.revision} → {rule.revision + 1}。
              </p>
            }
            build={({ hostId }) => ({
              expected_revision: rule.revision,
              host_id: hostId,
            })}
          />

          <WritePanel
            title="Supersede(用新版本替换 ACTIVE Rule)"
            basis={transitionBasis}
            action={supersede}
            submitLabel="提交替换"
            ready={rule.status === 'ACTIVE' && supersedeError === null}
            reasonMode="none"
            onSucceeded={onReadback}
            fields={
              <>
                <p className="muted">
                  只有 <strong>ACTIVE</strong> 的 Rule 可以被替换(当前 status{' '}
                  <code>{rule.status}</code>)。Core <strong>不支持跨 scope 替换</strong>:scope 已锁定为
                  这条 Rule 自己的 scope(<code>{rule.scope.kind}</code>
                  {rule.scope.project_id ? ` · ${rule.scope.project_id}` : ''}
                  {rule.scope.task_id ? ` · ${rule.scope.task_id}` : ''}
                  ),新版本写入 version {rule.version + 1},revision {rule.revision} →{' '}
                  {rule.revision + 1}。
                </p>
                <OriginField
                  label="origin_event_id(supersede 必填,scope 须与 Rule 一致)"
                  value={supersedeOrigin}
                  onChange={setSupersedeOrigin}
                  candidates={ruleScopeCandidates}
                />
                <ContentFields draft={supersedeContent} onChange={setSupersedeContent} />
                {supersedeError ? (
                  <p className="state state--error" role="alert">
                    {supersedeError}
                  </p>
                ) : null}
              </>
            }
            build={({ hostId }) =>
              supersedeError !== null
                ? supersedeError
                : {
                    expected_revision: rule.revision,
                    host_id: hostId,
                    scope: {
                      kind: rule.scope.kind,
                      project_id: rule.scope.project_id,
                      task_id: rule.scope.task_id,
                    },
                    origin_event_id: supersedeOrigin.trim(),
                    ...contentKeys(supersedeContent, supersedeMatcher),
                  }
            }
          />

          <WritePanel
            title="Retire(停用 ACTIVE Rule)"
            basis={transitionBasis}
            action={retire}
            submitLabel="提交停用"
            ready={rule.status === 'ACTIVE'}
            reasonMode="none"
            onSucceeded={onReadback}
            fields={
              <p className="muted">
                只有 <strong>ACTIVE</strong> 的 Rule 可以被停用(当前 status <code>{rule.status}</code>)。
                停用写入 <code>rule.retired</code>,状态变为 RETIRED;它不删除历史版本,也不回滚已经发生的
                Truth。
              </p>
            }
            build={({ hostId }) => ({
              expected_revision: rule.revision,
              host_id: hostId,
            })}
          />
        </>
      )}
    </>
  )
}

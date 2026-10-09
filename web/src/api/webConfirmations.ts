import type { CoreClient } from './client'
import type { AcceptanceCriteria, Evidence, RawEvent } from './types'

export const WEB_CONFIRMATION_STATEMENT = '我确认已审阅此 Task/Step 的验收条件和适用规则，并对该材料绑定的当前版本作出人工确认。'
export interface ConfirmationIdentity {
  actor_id: string; actor_kind: string; key_id: string; home_host_id: string | null
  scopes: string[]; capabilities: { web_confirmation_v1: boolean }; trust_model: string
}
export interface ConfirmationPrepareBody {
  event_id: string; task_id: string; step_id: string | null
  expected_task_revision: number; expected_step_revision: number | null
  target_status: 'VERIFIED' | 'ACCEPTED'
  confirmed_rule_id: string | null; confirmed_rule_version: number | null
}
export interface ConfirmationSubmitBody {
  event_id: string; preparation_id: string; review_sha256: string; affirmed: true; note: string
}
export interface ConfirmationReview {
  contract_version: 'web_confirmation_v1'
  actor: { actor_id: string; key_id: string; host_id: string; trust_model: string }
  project_id: string
  task: { task_id: string; title: string; status: string; revision: number; acceptance_criteria: AcceptanceCriteria }
  step: { step_id: string; title: string; status: string; revision: number; acceptance_criteria: AcceptanceCriteria } | null
  task_steps: { step_id: string; status: string; revision: number }[]
  applicable_rules: { rule_id: string; version: number; scope_kind: string; project_id: string | null; task_id: string | null; kind: string; severity: string; enforcement: string; content: string; matcher: unknown; verification_requirements: unknown; activation_event_id: string | null }[]
  confirmed_rule: { rule_id: string; version: number } | null
  workspace: { fingerprint_sha256: string; algorithm: string; coverage: unknown; complete: boolean }
  configuration_sha256: string; target_status: 'VERIFIED' | 'ACCEPTED'; statement: string
  evidence_sufficiency: 'NOT_EVALUATED'
}
export interface ConfirmationPreparation {
  preparation_id: string; contract_version: 'web_confirmation_v1'; review_document: ConfirmationReview
  review_sha256: string; created_at: string; expires_at: string; sampled_at: string; replayed?: boolean
}
export interface ConfirmationReceipt {
  preparation_id: string; contract_version: 'web_confirmation_v1'; review_sha256: string
  source_event: RawEvent; event: RawEvent; evidence: Evidence; replayed: boolean
}
export interface ConfirmationStatus {
  preparation: ConfirmationPreparation
  consumption: { command_event_id: string; source_event_id: string; evidence_id: string; receipt: ConfirmationReceipt } | null
  availability: 'OPEN' | 'CONSUMED' | 'EXPIRED' | 'CLOCK_INVALID'; server_time: string
}
/** Kept in tab memory across route changes; never persisted. No bearer is stored here. */
export interface PendingConfirmation {
  id: string; keyId: string; taskId: string; stepId: string | null
  kind: 'prepare' | 'submit'; body: ConfirmationPrepareBody | ConfirmationSubmitBody
}
export function confirmationEndpoints(client: CoreClient) {
  return {
    confirmationIdentity: (signal?: AbortSignal) => client.get<ConfirmationIdentity>('/auth/me', { signal }),
    prepareConfirmation: (body: ConfirmationPrepareBody, signal?: AbortSignal) => client.post<ConfirmationPreparation>('/evidence/confirmations/prepare', body, { signal }),
    submitConfirmation: (body: ConfirmationSubmitBody, signal?: AbortSignal) => client.post<ConfirmationReceipt>('/evidence/confirmations/submit', body, { signal }),
    confirmationStatus: (preparationId: string, signal?: AbortSignal) => client.get<ConfirmationStatus>(`/evidence/confirmations/${encodeURIComponent(preparationId)}`, { signal }),
  }
}

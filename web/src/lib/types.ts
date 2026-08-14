// Shape of records flowing from FastAPI to the browser.
// Kept narrow and explicit — every field that's optional is marked.

export type ServiceName = "heartbeat" | "telegram" | "web";
export type MemoryType = "user" | "feedback" | "project" | "skill" | "reference";

export interface MemoryEntry {
  filename: string;
  name: string;
  description: string;
  type: MemoryType | string;
  mtime: number;
  /** Outbound [[wikilink]] slugs parsed from the entry body. */
  links?: string[];
}

export interface LogFile {
  rel: string;
  date: string;
  size_kb: number;
  mtime: number;
}

export interface StatusEntry {
  state: "live" | "idle" | "stale" | "unknown";
  last_seen: number | null;
  age_s: number | null;
}

export type StatusMap = Record<string, StatusEntry>;

export interface WebConfig {
  auth_required: boolean;
}

export interface PersistedChatMessage {
  id: string;
  role: "user" | "assistant";
  content: string;
}

export interface Chapter {
  id: string;
  title: string;
  opened_at: string | null;
  closed_at: string | null;
  turns: number;
}

export type TaskStatus = "active" | "completed" | "cancelled";
export type Recurrence = "none" | "daily" | "weekly";

export interface TaskRun {
  ts: string;
  status: "success" | "failure";
  result: string;
  duration_s?: number;
  input_tokens?: number;
  output_tokens?: number;
  cached_tokens?: number;
  calls?: number;
  cost_cents?: number;
}

export interface Task {
  id: string;
  title: string;
  description: string;
  status: TaskStatus;
  due_at: string | null;
  recurrence: Recurrence;
  notify: boolean;
  /** "user" = explicitly requested; "inferred" = a commitment the agent
   *  noticed and recorded to follow up on proactively. Older tasks omit it. */
  source?: "user" | "inferred";
  created_at: string;
  updated_at: string;
  completed_at: string | null;
  last_result: string;
  last_runs: TaskRun[];
}

export interface TasksMeta {
  recurrence_options: Recurrence[];
}

export interface Skill {
  name: string;
  description: string;
  call_count: number;
  success_count: number;
  failure_count: number;
  last_used: string | null;
  last_status: "success" | "failure" | null;
  recent_calls?: string[]; // ISO timestamps in last 24h
  uses: number | null;              // from rate_skill() in skill_*.md
  consecutive_failures: number | null; // from rate_skill() in skill_*.md
}

// Mirrors transports/web_api.stats_today response shape. Used by the
// AutonomyConsole to surface today's budget burn alongside the tuning
// controls — the operator can see at a glance whether the agent is on
// track or about to exhaust the $5/month envelope.
export interface AgentBudgetStats {
  since: string;
  events: number;
  unique_tools: number;
  tasks_fired: number;
  memory_writes: number;
  memory_forgets: number;
  input_tokens: number;
  output_tokens: number;
  cached_tokens: number;
  cost_cents: number;
  budget_cents: number;
}

export type EvalTrend = "improving" | "steady" | "degrading" | "insufficient_data";
export type EvalContractKind = "states" | "requires_tools" | "none";

export interface EvalModelSlice {
  runs: number;
  compliance_rate: number | null;
  avg_violations: number | null;
  avg_guard_fires: number | null;
  reply_blocks: number;
  avg_cost_cents: number | null;
}

export interface EvalScorecard {
  contract_kind: EvalContractKind;
  runs: number;
  compliance_rate: number | null;
  avg_violations: number | null;
  avg_guard_fires: number | null;
  reply_blocks: number;
  avg_cost_cents: number | null;
  trend: EvalTrend;
  /** Keyed by model_id ("unknown" = predates model-tracking). One entry
   *  per model this skill has ever run under — lets a model swap show up
   *  as two comparable slices instead of one blended average. */
  by_model: Record<string, EvalModelSlice>;
}

export type EvalScorecards = Record<string, EvalScorecard>;

export interface AgentControls {
  mode: "plan" | "build";
  max_steps: number;
  dry_run: boolean;
  prefer_free_models: boolean;
  allowed_tools: string[];
  blocked_tools: string[];
  /** Kill switch — heartbeat refuses all ticks while true. */
  paused: boolean;
}

/** A skill the agent authored, awaiting operator review before it can
 *  go live. Created via propose_skill; resolved via the proposals API. */
export interface Proposal {
  id: string;
  kind: "new_skill" | "skill_edit" | "memory_delete";
  skill_name: string;
  body: string;
  rationale: string;
  source: string;
  task_spec: Record<string, unknown> | null;
  status: "pending" | "approved" | "rejected";
  created_at: string;
  resolved_at: string | null;
  resolution_note: string;
}

export interface ContainmentStatus {
  docker_proxy: boolean;
  ssrf_guard: boolean;
  daily_budget_cents: number;
  max_steps: number;
  paused: boolean;
  mode: "plan" | "build";
  delivery_gate: boolean;
  blocked_recent: number;
}

export interface AgentReplayTool {
  name: string;
  args: string;
  result: string;
  status: "pending" | "success" | "failure" | "blocked";
  started_at?: string;
  ended_at?: string;
}

export interface AgentReplayModel {
  ts: string;
  model: string;
  host: string;
  input_tokens: number;
  output_tokens: number;
  cached_tokens: number;
  cost_cents: number;
  request: string;
}

export interface AgentReplayGuard {
  ts: string;
  event: string;
  name: string;
  text: string;
  result: string;
}

export interface AgentReplayTurn {
  id: string;
  started_at: string | null;
  ended_at?: string;
  service: string;
  user: string;
  assistant: string;
  models: AgentReplayModel[];
  tools: AgentReplayTool[];
  guards: AgentReplayGuard[];
  input_tokens: number;
  output_tokens: number;
  cached_tokens: number;
  cost_cents: number;
}

/** Event kinds emitted into _events.jsonl by every Homunculus service. */
export type EventKind =
  | "user_message"
  | "assistant_reply"
  | "tool_call"
  | "tool_result"
  | "llm_call"
  | "output_guard"
  | "self_correction"
  | "tool_blocked"
  | "agent_controls_updated"
  | "memory_write"
  | "memory_forget";

export interface FeedEvent {
  ts: string;
  service: ServiceName | string;
  event: EventKind | string;
  // Event-specific fields — all optional, depend on `event`.
  text?: string;
  name?: string;
  args?: string;
  result?: string;
  model?: string;
  host?: string;
  request?: string;
  input_tokens?: number;
  output_tokens?: number;
  cached_tokens?: number;
}

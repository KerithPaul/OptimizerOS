export type ProjectMode =
  | "AUDIT_ONLY"
  | "SUGGEST_ONLY"
  | "APPLY_LOCALLY"
  | "COMMIT"
  | "CREATE_PR";

export const PROJECT_MODES: ProjectMode[] = [
  "AUDIT_ONLY",
  "SUGGEST_ONLY",
  "APPLY_LOCALLY",
  "COMMIT",
  "CREATE_PR",
];

export type JobStatus = "queued" | "running" | "succeeded" | "failed" | "cancelled";

export type UserRole = "admin" | "member";
export type UserStatus = "active" | "suspended";

export interface User {
  id: number;
  email: string;
  name: string;
  role: UserRole;
  status: UserStatus;
  /** Max projects this account may create; null = unlimited (admins). */
  project_limit: number | null;
  created_at: string;
}

export interface Project {
  id: number;
  name: string;
  mode: ProjectMode;
  created_by: number;
  created_at: string;
}

export interface Job {
  id: number;
  project_id: number;
  type: string;
  status: JobStatus;
  progress_json: Record<string, unknown> | null;
  error: string | null;
  cancel_requested: boolean;
  created_at: string;
  started_at: string | null;
  finished_at: string | null;
}

export interface JobEvent {
  stage?: string;
  percent?: number;
  message?: string;
  job_status: JobStatus;
  error?: string;
}

export type CloneStatus = "pending" | "cloning" | "cloned" | "clone_failed";

export interface Repository {
  id: number;
  project_id: number;
  url: string;
  default_branch: string;
  cloned_commit_hash: string | null;
  clone_status: CloneStatus;
  last_indexed_commit: string | null;
  architecture_profile: Record<string, unknown> | null;
  created_at: string;
  indexing_state: string | null;
  framework: string | null;
  has_clone_token: boolean;
}

export interface CodeFile {
  path: string;
  language: string | null;
}

export interface CodeSymbol {
  name: string;
  kind: string;
  file_path: string | null;
  start_line: number | null;
  end_line: number | null;
  role: string | null;
}

export interface CodeRoute {
  path: string;
  file_path: string | null;
}

export interface CodeNeighbor {
  name: string;
  kind: string;
  file_path: string | null;
  start_line: number | null;
  end_line: number | null;
  relation?: string | null;
}

export type ModificationLevel = "none" | "low" | "medium" | "high";

export interface CapabilityReport {
  platform: string;
  source_access: boolean;
  content_access: boolean;
  metadata_access: boolean;
  theme_source: boolean;
  seo_modification: ModificationLevel;
  aeo_modification: ModificationLevel;
  geo_modification: ModificationLevel;
  theme_modification: ModificationLevel;
  automatic_rollback: ModificationLevel;
  snapshot: boolean;
  ast_access?: boolean;
  git_history?: boolean;
  code_modification?: ModificationLevel;
  pull_request?: ModificationLevel;
}

export interface ProjectCapabilities {
  report: CapabilityReport | null;
  allowed_modes: ProjectMode[];
}

export type CrawlRunStatus = "pending" | "running" | "succeeded" | "failed" | "partial";

export interface CrawlRun {
  id: number;
  website_id: number;
  project_id: number;
  status: CrawlRunStatus;
  cap_reason: string | null;
  error: string | null;
  page_count: number | null;
  stats_json: Record<string, unknown> | null;
  created_at: string;
  started_at: string | null;
  finished_at: string | null;
}

export interface Website {
  id: number;
  project_id: number;
  url: string;
  platform: string;
  created_at: string;
  capabilities: CapabilityReport;
  auth_type: string;
  latest_crawl: CrawlRun | null;
}

export interface WebsitePageSummary {
  id: number;
  website_id: number;
  crawl_run_id: number | null;
  url: string;
  status_code: number | null;
  title: string | null;
  canonical: string | null;
  fetch_ms: number | null;
  word_count: number | null;
}

export interface WebsitePageDetail extends WebsitePageSummary {
  model_json: Record<string, unknown> | null;
  lighthouse: Record<string, unknown> | null;
}

export type AnalysisRunStatus = "pending" | "running" | "succeeded" | "failed" | "partial";

export interface ScoreEvidenceRef {
  finding_id: string;
  affected_resource: string;
  excerpt: string;
}

export interface ScoreSignal {
  rule: string;
  severity: RuleSeverity;
  deduction: number;
  finding_count: number;
  affected_resources: string[];
  evidence: ScoreEvidenceRef[];
}

export interface Score {
  key: string;
  label: string;
  value: number;
  max_value: number;
  signals: ScoreSignal[];
}

export const SCORE_KEYS = [
  "technical_seo_health",
  "content_aeo_readiness",
  "ai_search_geo_readiness",
] as const;

export interface AnalysisRun {
  id: number;
  project_id: number;
  status: AnalysisRunStatus;
  inputs_json: Record<string, unknown> | null;
  gaps_json: { capability: string; detail: string }[] | null;
  scores_json: Record<string, Score> | null;
  error: string | null;
  created_at: string;
  started_at: string | null;
  finished_at: string | null;
}

export type RuleCategory = "technical_seo" | "content_seo" | "aeo" | "geo" | "agent_accessibility";
export type RuleSeverity = "low" | "medium" | "high" | "critical";
export type RuleConfidence = "low" | "medium" | "high";
export type FindingStatus =
  | "OPEN"
  | "PLANNED"
  | "IN_PROGRESS"
  | "VALIDATED"
  | "REJECTED"
  | "FIXED"
  | "ROLLED_BACK";

export interface EvidenceRow {
  source: string;
  excerpt: string;
  selector?: string | null;
  value?: unknown;
  confidence: "direct" | "derived" | "heuristic";
  source_authority?: string | null;
}

export interface PriorityFactors {
  impact: number;
  confidence: number;
  reach: number;
  actionability: number;
  risk: number;
  reach_input: "impressions" | "affected_page_count" | string;
}

export interface Finding {
  id: number;
  project_id: number;
  analysis_run_id: number;
  finding_id: string;
  observation: string;
  problem: string;
  evidence: EvidenceRow[];
  source: string;
  source_url: string;
  source_authority: string;
  rule: string;
  rule_version: number;
  category: RuleCategory;
  severity: RuleSeverity;
  confidence: RuleConfidence;
  affected_resource: string;
  affected_url: string | null;
  affected_code_entity: string | null;
  expected_mechanism: string;
  recommended_action: string;
  recommendation: string;
  actionability: string;
  risk: string;
  will_validate: string;
  change_worked: string;
  rollback: string;
  impressions: number | null;
  reach_input: "impressions" | "affected_page_count" | string | null;
  status: FindingStatus;
  created_at: string;
  priority: number;
  priority_factors: PriorityFactors;
}

export interface FindingFilters {
  run_id?: number;
  category?: RuleCategory;
  severity?: RuleSeverity;
  confidence?: RuleConfidence;
  status?: FindingStatus;
  url?: string;
  rule?: string;
}

export type AgentType = "research" | "seo" | "aeo" | "geo" | "code" | "reviewer" | "cms";
export type AgentRunStatus = "running" | "succeeded" | "partial" | "failed" | "cancelled";

export interface IntentObjective {
  objective: string;
  scope: string;
  allowed_actions: string[];
  mode: string;
}

export interface Intervention {
  finding_id: string;
  hypothesis: string;
  intervention: string;
  expected_mechanism: string;
  risk: string;
}

export interface AgentRun {
  id: number;
  project_id: number;
  job_id: number | null;
  agent_type: AgentType;
  status: AgentRunStatus;
  request_text: string | null;
  objective_json: IntentObjective | Record<string, unknown> | null;
  result_json: Intervention[] | Record<string, unknown> | null;
  iterations_used: number;
  tool_calls_used: number;
  tokens_used: number;
  files_modified: number;
  stopped_reason: string | null;
  error: string | null;
  created_at: string;
  started_at: string | null;
  finished_at: string | null;
}

export interface AgentRunJob {
  job_id: number;
}

// Phase 7 — Code Agent + Sandbox + Validation

export interface ChangeJob {
  job_id: number;
  dry_run: boolean;
}

export interface ChangePlan {
  finding_id: string;
  target_files: string[];
  target_symbols: string[];
  reuse_notes: string;
  expected_diff_summary: string;
  required_tests: string[];
  required_validation: string[];
}

export interface ExecutionStep {
  order: number;
  action: string;
  detail: string;
}

export interface ExecutionPlan {
  finding_id: string;
  steps: ExecutionStep[];
  dependencies: string[];
  sandbox_operations: string[];
}

export interface ValidationPlan {
  finding_id: string;
  tests: string[];
  build: boolean;
  lint: boolean;
  browser_checks: string[];
  seo_checks: string[];
  aeo_checks: string[];
  geo_checks: string[];
  regression_checks: string[];
}

export interface DiffPreviewFile {
  file_path: string;
  change_summary: string;
  lines_added: number;
  lines_removed: number;
  unified_diff: string;
  before?: string;
  after?: string;
  file_status?: "missing" | "empty" | "present" | string;
}

export interface DiffPreview {
  notes?: string;
  files?: DiffPreviewFile[];
}

export interface ScopeViolationOut {
  reason: string;
  detail: string;
}

export type ValidationCheckType =
  | "scope"
  | "content"
  | "lint"
  | "typecheck"
  | "build"
  | "unit_test"
  | "integration_test"
  | "browser"
  | "seo"
  | "aeo"
  | "geo"
  | "agent_accessibility"
  | "regression";
export type ValidationCheckStatus = "passed" | "failed" | "skipped" | "not_applicable";
export type ValidationRunStatus = "pending" | "running" | "passed" | "failed" | "partial";

export interface ValidationResult {
  id: number;
  check_type: ValidationCheckType;
  status: ValidationCheckStatus;
  detail: string | null;
  duration_ms: number | null;
}

export interface ValidationRun {
  id: number;
  status: ValidationRunStatus;
  affected_urls_json: string[] | null;
  gaps_json: string[] | null;
  error: string | null;
  created_at: string;
  finished_at: string | null;
  results: ValidationResult[];
}

export interface ReviewVerdict {
  finding_id: string;
  approved: boolean;
  reasons: string[];
  regressions_detected: string[];
}

export interface ChangeSummary {
  agent_run_id: number;
  job_id: number | null;
  finding_id: string;
  status: AgentRunStatus;
  dry_run: boolean | null;
  stopped_reason: string | null;
  approved: boolean | null;
  created_at: string;
  finished_at: string | null;
}

export interface ChangeFinding {
  observation: string;
  evidence: EvidenceRow[];
  risk: string;
  recommended_action: string;
  affected_resource: string;
  affected_url: string | null;
  affected_code_entity: string | null;
  expected_mechanism: string;
  status: string;
}

export interface WorkspaceFileStatus {
  path: string;
  status: "missing" | "empty" | "present" | string;
}

export interface ChangeDetail {
  agent_run_id: number;
  job_id: number | null;
  finding_id: string;
  status: AgentRunStatus;
  error: string | null;
  dry_run: boolean | null;
  stopped_reason: string | null;
  violation: ScopeViolationOut | null;
  change_plan: ChangePlan | null;
  execution_plan: ExecutionPlan | null;
  validation_plan: ValidationPlan | null;
  diff_preview: DiffPreview | null;
  validation_run: ValidationRun | null;
  reviewer_verdict: ReviewVerdict | null;
  approved: boolean | null;
  finding_status: string;
  finding: ChangeFinding | null;
  validation_skipped: boolean | null;
  reviewer_skipped?: boolean | null;
  workspace_files: WorkspaceFileStatus[] | null;
  platform_notes?: string[] | null;
  repair_attempts?: number | null;
}

// Phase 8 — Change Management + Semantic Rollback

export type ChangeSetStatus = "applied" | "partially_rolled_back" | "rolled_back";
export type ChangePlatform = "git";
export type ChangeTransactionStatus = "applied" | "rolled_back";
export type ChangeItemKind =
  | "file"
  | "function"
  | "class"
  | "component"
  | "paragraph"
  | "other";
export type ChangeItemStatus = "applied" | "rolled_back";
export type RollbackTargetType = "change_set" | "change_transaction" | "change_item";
export type RollbackOperationStatus =
  | "pending_confirmation"
  | "applied"
  | "failed"
  | "cancelled";

export interface ChangeItem {
  id: number;
  kind: ChangeItemKind;
  symbol_name: string | null;
  start_line: number | null;
  end_line: number | null;
  status: ChangeItemStatus;
  created_at: string;
  rolled_back_at: string | null;
}

export interface ChangeTransaction {
  id: number;
  finding_id: string;
  platform: ChangePlatform;
  resource: string;
  field: string;
  hash_before: string;
  hash_after: string;
  reason: string;
  validation_status: ValidationRunStatus;
  rollback_method: string;
  status: ChangeTransactionStatus;
  created_at: string;
  rolled_back_at: string | null;
  items: ChangeItem[];
}

export interface ChangeSetSummary {
  id: number;
  objective: string;
  finding_ids_json: string[];
  affected_resources_json: string[];
  status: ChangeSetStatus;
  created_at: string;
  applied_at: string | null;
}

export interface ChangeSetDetail {
  id: number;
  objective: string;
  description: string | null;
  finding_ids_json: string[];
  evidence_json: EvidenceRow[] | null;
  affected_resources_json: string[];
  risk: string;
  status: ChangeSetStatus;
  git_commit_ref: string | null;
  pull_request_ref: string | null;
  rollback_info_json: Record<string, unknown> | null;
  baseline_metrics_json: Record<string, unknown> | null;
  treatment_metrics_json: Record<string, unknown> | null;
  created_at: string;
  applied_at: string | null;
  transactions: ChangeTransaction[];
}

export interface RollbackTargetRequest {
  change_set_id?: number;
  change_transaction_id?: number;
  change_item_id?: number;
  symbol_name?: string;
  category?: string;
  confirmed?: boolean;
}

export interface RollbackOperation {
  id: number;
  target_type: RollbackTargetType;
  change_set_id: number | null;
  change_transaction_id: number | null;
  change_item_id: number | null;
  requested_target: string;
  method: string;
  confidence: number;
  confidence_reasons_json: string[];
  requires_confirmation: boolean;
  status: RollbackOperationStatus;
  result_detail: string | null;
  created_at: string;
  resolved_at: string | null;
}

// Phase 9 — GitHub

export type GithubCommitStatus = "created" | "pushed" | "failed";
export type GithubPullRequestStatus = "open" | "merged" | "closed" | "failed";
export type GithubCiStatus =
  | "pending"
  | "success"
  | "failure"
  | "error"
  | "unknown"
  | "unavailable";

export interface GitHubConnection {
  connected: boolean;
  platform: string;
  auth_type: string;
  has_token: boolean;
  oauth_configured: boolean;
  oauth_redirect_uri: string;
  login: string | null;
  selected_repo: string | null;
  capabilities: CapabilityReport | null;
}

export interface GitHubRepo {
  full_name: string;
  private: boolean;
  html_url: string | null;
  clone_url: string | null;
  default_branch: string;
  push: boolean;
}

export interface GitHubRepoList {
  login: string | null;
  page: number;
  per_page: number;
  has_more: boolean;
  items: GitHubRepo[];
}

export interface GithubCommit {
  id: number;
  change_set_id: number;
  sha: string | null;
  branch: string;
  message: string;
  files_json: string[];
  status: GithubCommitStatus;
  error: string | null;
  created_at: string;
}

export interface GithubPullRequest {
  id: number;
  change_set_id: number;
  commit_id: number | null;
  number: number | null;
  html_url: string | null;
  title: string;
  body: string;
  head_branch: string;
  base_branch: string;
  status: GithubPullRequestStatus;
  ci_status: GithubCiStatus;
  ci_detail_json: Record<string, unknown> | null;
  error: string | null;
  created_at: string;
  updated_at: string | null;
}

export interface GitHubProject {
  mode: ProjectMode;
  connection: GitHubConnection;
  commits: GithubCommit[];
  pull_requests: GithubPullRequest[];
}

export interface GitHubPublish {
  change_set_id: number;
  commit: GithubCommit;
  pull_request: GithubPullRequest | null;
  error: string | null;
  pull_request_skipped_reason?: string | null;
}

export interface OmittedFinding {
  finding_id: string;
  status: string;
  rule: string;
}

export interface WordPressConnection {
  connected: boolean;
  platform: string;
  auth_type: string;
  has_credentials: boolean;
  website_id: number | null;
  url: string | null;
  seo_plugin: string | null;
  capabilities: CapabilityReport | null;
}

export interface WordPressPage {
  url: string;
  title: string | null;
  meta_description: string | null;
  canonical: string | null;
  rest_base: string | null;
  wordpress_id: number | null;
}

export interface WordPressRevision {
  id: number;
  parent: number | null;
  date: string | null;
  title: string | null;
  author: number | null;
}

export interface WordPressSnapshot {
  id: number;
  project_id: number;
  reason: string;
  snapshot_path: string;
  files_json: string[] | null;
  created_at: string;
}

export interface WordPressProject {
  connection: WordPressConnection;
  pages: WordPressPage[];
  revisions: WordPressRevision[];
}

export type SearchConsoleDimension = "page" | "query" | "page_query" | "date" | "device" | "country";

export interface SearchConsoleConnection {
  connected: boolean;
  platform: string | null;
  auth_type: string | null;
  has_stored_credentials: boolean;
  oauth_configured: boolean;
  oauth_redirect_uri: string;
  property_url: string | null;
  properties: string[];
  status: string;
  display: string;
}

export interface SearchConsoleRow {
  id: number;
  dimension: SearchConsoleDimension;
  query: string | null;
  page: string | null;
  impressions: number;
  clicks: number;
  ctr: number;
  position: number;
  start_date: string;
  end_date: string;
  fetched_at: string;
}

export interface SearchConsoleProject {
  connection: SearchConsoleConnection;
  rows: SearchConsoleRow[];
}

export type KeywordSuggestionSource = "gsc";

export interface KeywordSuggestion {
  query: string;
  source: KeywordSuggestionSource;
  impressions: number;
  clicks: number;
  ctr: number;
  position: number;
  start_date: string;
  end_date: string;
  search_console_row_id: number | null;
  matched_filters: string[];
  reason: string;
}

export interface KeywordSuggestionFilters {
  dimension: "query";
  match: "any";
  rank: "impressions_desc";
  position_gte: number;
  ctr_lt: number;
}

export interface KeywordSuggestions {
  source: KeywordSuggestionSource;
  connection: SearchConsoleConnection;
  analysis_run_id: number | null;
  start_date: string | null;
  end_date: string | null;
  query_row_count: number;
  suggestion_count: number;
  filters: KeywordSuggestionFilters;
  note: string;
  suggestions: KeywordSuggestion[];
}

export type ExperimentType =
  | "title"
  | "faq"
  | "content_restructure"
  | "schema"
  | "internal_links";

export type ExperimentStatus =
  | "hypothesis"
  | "baseline"
  | "change"
  | "validation"
  | "post_change_measurement"
  | "result";

export interface Experiment {
  id: number;
  project_id: number;
  change_set_id: number | null;
  experiment_type: ExperimentType;
  hypothesis: string;
  change_json: Record<string, unknown> | null;
  baseline_metrics: Record<string, unknown> | null;
  treatment_metrics: Record<string, unknown> | null;
  result: Record<string, unknown> | null;
  confidence: number | null;
  status: ExperimentStatus;
  created_at: string;
  baseline_recorded_at: string | null;
  treatment_recorded_at: string | null;
  finished_at: string | null;
  causation: string;
  causation_note: string;
}

// ---------------------------------------------------------------------------
// Guided fix workflow (inspect -> snapshot original -> apply -> compare).
// Additive types only; existing flows keep their shapes.
// ---------------------------------------------------------------------------

export type InspectedFileStatus = "present" | "missing" | "empty" | "unsafe";

export interface InspectedFile {
  path: string;
  status: InspectedFileStatus;
  content: string;
  locator_hits: string[];
}

export interface CodeInspection {
  finding_id: string;
  repository_attached: boolean;
  workspace_ready: boolean;
  files: InspectedFile[];
  locators: string[];
  detected_issue: string;
  recommended_action: string;
  applies: boolean;
  applies_reason: string;
  actionability: string;
}

export interface FileOriginalSnapshot {
  snapshot_id: number;
  file_path: string;
  created: boolean;
  original_content: string | null;
  commit_hash: string | null;
  captured_at: string | null;
}

export interface FileBeforeAfter {
  finding_id: string;
  file_path: string;
  snapshot_id: number;
  before: string;
  after: string;
  changed: boolean;
  error: string | null;
  captured_at: string | null;
}

export interface FileChangeSummaryEntry {
  file_path: string;
  snapshot_id: number;
  changed: boolean;
  lines_added: number;
  lines_removed: number;
  error: string | null;
}

export interface FileChangesSummary {
  finding_id: string;
  files: FileChangeSummaryEntry[];
}

export type SiteReportStatus = "running" | "succeeded" | "partial" | "failed";
export type SiteReportEmailStatus = "unavailable" | "sent" | "failed";

export interface SiteReportMetricCell {
  key?: string;
  status?: string;
  value?: number | null;
  detail?: string | null;
  source?: string | null;
}

export interface SiteReportDailyPoint {
  date: string;
  impressions: number;
  clicks: number;
  ctr: number;
  position: number;
}

export interface SiteReportTotalsBlock {
  impressions?: number;
  clicks?: number;
  ctr?: number;
  position?: number;
}

export interface SiteReportPeriodComparison {
  key: string;
  label: string;
  status: string;
  detail?: string;
  length?: number;
  current_start?: string;
  current_end?: string;
  current?: SiteReportTotalsBlock;
  previous?: SiteReportTotalsBlock;
  delta?: SiteReportTotalsBlock;
}

export interface SiteReportQueryRow {
  query?: string;
  page?: string;
  label?: string;
  impressions?: number;
  clicks?: number;
  ctr?: number;
  position?: number;
  reason?: string;
  movement?: string;
  delta_impressions?: number | null;
  delta_clicks?: number | null;
  delta_ctr?: number | null;
  delta_position?: number | null;
}

export interface SiteReportDocument {
  website_url?: string | null;
  search_console?: string | null;
  causation_note?: string | null;
  period?: { start_date?: string; end_date?: string } | null;
  totals?: {
    impressions?: SiteReportMetricCell;
    clicks?: SiteReportMetricCell;
    ctr?: SiteReportMetricCell;
    position?: SiteReportMetricCell;
  } | null;
  onsite?: Record<string, SiteReportMetricCell>;
  deltas?: Record<string, unknown> | null;
  period_comparisons?: SiteReportPeriodComparison[];
  daily_series?: SiteReportDailyPoint[];
  devices?: SiteReportQueryRow[];
  countries?: SiteReportQueryRow[];
  scores?: Record<string, { label?: string; value?: number; max_value?: number }>;
  top_pages?: SiteReportQueryRow[];
  top_queries?: SiteReportQueryRow[];
  keywords?: SiteReportQueryRow[];
  findings?: Array<{
    severity?: string;
    rule?: string;
    affected_url?: string;
    problem?: string;
    impressions?: number | null;
  }>;
  finding_count?: number;
  open_finding_count?: number;
  findings_by_severity?: Record<string, number>;
  indexability?: {
    crawled_pages?: number | null;
    sitemap_url_count?: number | null;
    crawl_status?: string | null;
    note?: string | null;
  };
  gaps?: { capability?: string; detail?: string }[];
}

export interface SiteReportSummary {
  id: number;
  project_id: number;
  job_id: number | null;
  analysis_run_id: number | null;
  crawl_run_id: number | null;
  status: SiteReportStatus;
  email_status: SiteReportEmailStatus;
  email_detail: string | null;
  created_at: string;
  finished_at: string | null;
  website_url: string | null;
  search_console: string | null;
  finding_count: number | null;
  open_finding_count: number | null;
  period: { start_date?: string; end_date?: string } | null;
  totals: {
    impressions?: SiteReportMetricCell;
    clicks?: SiteReportMetricCell;
    ctr?: SiteReportMetricCell;
    position?: SiteReportMetricCell;
  } | null;
  causation: string | null;
}

export interface SiteReport extends SiteReportSummary {
  gaps_json: { capability?: string; detail?: string }[] | null;
  metrics_json: Record<string, unknown> | null;
  deltas_json: Record<string, unknown> | null;
  document_json: Record<string, unknown> | null;
  html: string | null;
}

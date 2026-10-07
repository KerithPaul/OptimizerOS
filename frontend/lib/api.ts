import { setCachedUser } from "./authStore";
import type {
  AgentRun,
  AgentRunJob,
  AnalysisRun,
  ChangeDetail,
  ChangeJob,
  ChangeSetDetail,
  ChangeSetSummary,
  ChangeSummary,
  CodeFile,
  CodeInspection,
  FileBeforeAfter,
  FileChangesSummary,
  FileOriginalSnapshot,
  CodeNeighbor,
  CodeRoute,
  CodeSymbol,
  Experiment,
  ExperimentType,
  Finding,
  FindingFilters,
  GitHubConnection,
  GitHubProject,
  GitHubPublish,
  GitHubRepoList,
  GithubPullRequest,
  Job,
  KeywordSuggestions,
  Project,
  ProjectCapabilities,
  ProjectMode,
  Repository,
  RollbackOperation,
  RollbackTargetRequest,
  SearchConsoleConnection,
  SearchConsoleProject,
  SiteReport,
  SiteReportSummary,
  User,
  Website,
  WebsitePageDetail,
  WebsitePageSummary,
  WordPressConnection,
  WordPressProject,
  WordPressRevision,
  WordPressSnapshot,
} from "./types";

const API_BASE_URL = process.env.NEXT_PUBLIC_API_BASE_URL ?? "http://localhost:6000/api/v1";

export class ApiError extends Error {
  status: number;

  constructor(status: number, message: string) {
    super(message);
    this.status = status;
  }
}

export async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(`${API_BASE_URL}${path}`, {
    ...init,
    credentials: "include",
    headers: {
      "Content-Type": "application/json",
      ...init?.headers,
    },
  });

  if (!res.ok) {
    let message = res.statusText;
    try {
      const body = await res.json();
      message = body.detail ?? message;
    } catch {
      // response had no JSON body
    }
    throw new ApiError(res.status, message);
  }

  if (res.status === 204) {
    return undefined as T;
  }
  return res.json() as Promise<T>;
}

export function login(email: string, password: string): Promise<User> {
  return request<User>("/auth/login", {
    method: "POST",
    body: JSON.stringify({ email, password }),
  }).then((user) => {
    // Publish so every useCurrentUser subscriber (header avatar included)
    // updates instantly — no refresh needed.
    setCachedUser(user);
    return user;
  });
}

export function logout(): Promise<{ status: string }> {
  return request<{ status: string }>("/auth/logout", { method: "POST" }).then((result) => {
    setCachedUser(null);
    return result;
  });
}

export function getCurrentUser(): Promise<User> {
  return request<User>("/auth/me");
}

export function listProjects(): Promise<Project[]> {
  return request<Project[]>("/projects");
}

export function createProject(name: string, mode: ProjectMode): Promise<Project> {
  return request<Project>("/projects", {
    method: "POST",
    body: JSON.stringify({ name, mode }),
  });
}

export function getProject(id: number): Promise<Project> {
  return request<Project>(`/projects/${id}`);
}

export function deleteProject(id: number): Promise<void> {
  return request<void>(`/projects/${id}`, { method: "DELETE" });
}

export function createJob(projectId: number, type: string): Promise<Job> {
  return request<Job>("/jobs", {
    method: "POST",
    body: JSON.stringify({ project_id: projectId, type }),
  });
}

export function jobEventsUrl(jobId: number): string {
  return `${API_BASE_URL}/jobs/${jobId}/events`;
}

export function cancelJob(jobId: number): Promise<Job> {
  return request<Job>(`/jobs/${jobId}/cancel`, { method: "POST" });
}

export function getJob(jobId: number): Promise<Job> {
  return request<Job>(`/jobs/${jobId}`);
}

/** Returns null when no repository is attached yet (no 404 noise in the console). */
export function getRepository(projectId: number): Promise<Repository | null> {
  return request<Repository | null>(`/projects/${projectId}/repository?optional=true`);
}

export function attachRepository(
  projectId: number,
  url: string,
  defaultBranch = "main",
  cloneToken?: string,
): Promise<Repository> {
  return request<Repository>(`/projects/${projectId}/repository`, {
    method: "POST",
    body: JSON.stringify({
      url,
      default_branch: defaultBranch,
      clone_token: cloneToken || undefined,
    }),
  });
}

export function updateRepositoryToken(
  projectId: number,
  cloneToken: string,
): Promise<Repository> {
  return request<Repository>(`/projects/${projectId}/repository/token`, {
    method: "PATCH",
    body: JSON.stringify({ clone_token: cloneToken || null }),
  });
}

export function indexRepository(projectId: number): Promise<Job> {
  return request<Job>(`/projects/${projectId}/repository/index`, { method: "POST" });
}

export function listRepositoryFiles(projectId: number): Promise<CodeFile[]> {
  return request<CodeFile[]>(`/projects/${projectId}/repository/files`);
}

export function listRepositorySymbols(projectId: number): Promise<CodeSymbol[]> {
  return request<CodeSymbol[]>(`/projects/${projectId}/repository/symbols`);
}

export function listRepositoryRoutes(projectId: number): Promise<CodeRoute[]> {
  return request<CodeRoute[]>(`/projects/${projectId}/repository/routes`);
}

export function listRepositoryNeighbors(
  projectId: number,
  name: string,
  relation: string,
): Promise<CodeNeighbor[]> {
  const params = new URLSearchParams({ name, relation });
  return request<CodeNeighbor[]>(`/projects/${projectId}/repository/neighbors?${params}`);
}

/** Returns null when no website is attached yet (no 404 noise in the console). */
export function getWebsite(projectId: number): Promise<Website | null> {
  return request<Website | null>(`/projects/${projectId}/website?optional=true`);
}

export function attachWebsite(
  projectId: number,
  url: string,
  platform = "url_only",
): Promise<Website> {
  return request<Website>(`/projects/${projectId}/website`, {
    method: "POST",
    body: JSON.stringify({ url, platform }),
  });
}

export function startWebsiteCrawl(projectId: number): Promise<Job> {
  return request<Job>(`/projects/${projectId}/website/crawl`, { method: "POST" });
}

export function listWebsitePages(projectId: number): Promise<WebsitePageSummary[]> {
  return request<WebsitePageSummary[]>(`/projects/${projectId}/website/pages`);
}

export function getWebsitePage(projectId: number, pageId: number): Promise<WebsitePageDetail> {
  return request<WebsitePageDetail>(`/projects/${projectId}/website/pages/${pageId}`);
}

export function getProjectCapabilities(projectId: number): Promise<ProjectCapabilities> {
  return request<ProjectCapabilities>(`/projects/${projectId}/capabilities`);
}

export function updateProjectMode(projectId: number, mode: ProjectMode): Promise<Project> {
  return request<Project>(`/projects/${projectId}/mode`, {
    method: "PATCH",
    body: JSON.stringify({ mode }),
  });
}

export function startAudit(projectId: number): Promise<Job> {
  return createJob(projectId, "audit");
}

export function listAnalysisRuns(projectId: number): Promise<AnalysisRun[]> {
  return request<AnalysisRun[]>(`/projects/${projectId}/analysis-runs`);
}

/** Returns null when no audit has run yet (no 404 noise in the console). */
export function getLatestAnalysisRun(projectId: number): Promise<AnalysisRun | null> {
  return request<AnalysisRun | null>(`/projects/${projectId}/analysis-runs/latest?optional=true`);
}

export function listFindings(projectId: number, filters: FindingFilters = {}): Promise<Finding[]> {
  const params = new URLSearchParams();
  for (const [key, value] of Object.entries(filters)) {
    if (value !== undefined && value !== "") params.set(key, String(value));
  }
  const qs = params.toString();
  return request<Finding[]>(`/projects/${projectId}/findings${qs ? `?${qs}` : ""}`);
}

export function getFinding(projectId: number, findingId: number): Promise<Finding> {
  return request<Finding>(`/projects/${projectId}/findings/${findingId}`);
}

export function startAgentRun(projectId: number, requestText: string): Promise<AgentRunJob> {
  return request<AgentRunJob>(`/projects/${projectId}/agents/run`, {
    method: "POST",
    body: JSON.stringify({ request_text: requestText }),
  });
}

export function listAgentRuns(projectId: number): Promise<AgentRun[]> {
  return request<AgentRun[]>(`/projects/${projectId}/agents/runs`);
}

export function getAgentRunsForJob(projectId: number, jobId: number): Promise<AgentRun[]> {
  return request<AgentRun[]>(`/projects/${projectId}/agents/runs/by-job/${jobId}`);
}

export function getAgentRun(projectId: number, runId: number): Promise<AgentRun> {
  return request<AgentRun>(`/projects/${projectId}/agents/runs/${runId}`);
}

export function applyChange(
  projectId: number,
  findingId: string,
  sourceAgentRunId: number,
): Promise<ChangeJob> {
  return request<ChangeJob>(`/projects/${projectId}/changes/apply`, {
    method: "POST",
    body: JSON.stringify({ finding_id: findingId, source_agent_run_id: sourceAgentRunId }),
  });
}

export function listChanges(projectId: number): Promise<ChangeSummary[]> {
  return request<ChangeSummary[]>(`/projects/${projectId}/changes`);
}

export function getChangeDetail(projectId: number, findingId: string): Promise<ChangeDetail> {
  return request<ChangeDetail>(`/projects/${projectId}/changes/${encodeURIComponent(findingId)}`);
}

export function inspectChangeCode(
  projectId: number,
  findingId: string,
): Promise<CodeInspection> {
  return request<CodeInspection>(
    `/projects/${projectId}/changes/${encodeURIComponent(findingId)}/inspect`,
  );
}

export function snapshotOriginalFile(
  projectId: number,
  findingId: string,
  filePath: string,
): Promise<FileOriginalSnapshot> {
  return request<FileOriginalSnapshot>(
    `/projects/${projectId}/changes/${encodeURIComponent(findingId)}/snapshot-file`,
    {
      method: "POST",
      body: JSON.stringify({ file_path: filePath }),
    },
  );
}

export function getFileBeforeAfter(
  projectId: number,
  findingId: string,
  filePath: string,
): Promise<FileBeforeAfter> {
  const params = new URLSearchParams({ file_path: filePath });
  return request<FileBeforeAfter>(
    `/projects/${projectId}/changes/${encodeURIComponent(findingId)}/file-change?${params.toString()}`,
  );
}

export function getFileChangesSummary(
  projectId: number,
  findingId: string,
): Promise<FileChangesSummary> {
  return request<FileChangesSummary>(
    `/projects/${projectId}/changes/${encodeURIComponent(findingId)}/changes-summary`,
  );
}

export function listChangeSets(projectId: number): Promise<ChangeSetSummary[]> {
  return request<ChangeSetSummary[]>(`/projects/${projectId}/change-sets`);
}

export function getChangeSet(projectId: number, changeSetId: number): Promise<ChangeSetDetail> {
  return request<ChangeSetDetail>(`/projects/${projectId}/change-sets/${changeSetId}`);
}

export function requestRollback(
  projectId: number,
  target: RollbackTargetRequest,
): Promise<RollbackOperation> {
  return request<RollbackOperation>(`/projects/${projectId}/rollback`, {
    method: "POST",
    body: JSON.stringify(target),
  });
}

export function getRollbackOperation(
  projectId: number,
  operationId: number,
): Promise<RollbackOperation> {
  return request<RollbackOperation>(`/projects/${projectId}/rollback/${operationId}`);
}

export function getGitHub(projectId: number): Promise<GitHubProject> {
  return request<GitHubProject>(`/projects/${projectId}/github`);
}

export function githubOAuthUrl(projectId: number): string {
  return `${API_BASE_URL}/projects/${projectId}/github/oauth/start`;
}

export function listGitHubRepositories(
  projectId: number,
  page = 1,
): Promise<GitHubRepoList> {
  return request<GitHubRepoList>(`/projects/${projectId}/github/repositories?page=${page}`);
}

export function connectGitHub(projectId: number, pat: string): Promise<GitHubConnection> {
  return request<GitHubConnection>(`/projects/${projectId}/github/connection`, {
    method: "PUT",
    body: JSON.stringify({ pat }),
  });
}

export function selectGitHubRepository(
  projectId: number,
  fullName: string,
): Promise<GitHubConnection> {
  return request<GitHubConnection>(`/projects/${projectId}/github/connection`, {
    method: "PUT",
    body: JSON.stringify({ full_name: fullName }),
  });
}

export function disconnectGitHub(projectId: number): Promise<GitHubConnection> {
  return request<GitHubConnection>(`/projects/${projectId}/github/connection`, {
    method: "DELETE",
  });
}

export function refreshPullRequestCi(
  projectId: number,
  pullRequestId: number,
): Promise<GithubPullRequest> {
  return request<GithubPullRequest>(
    `/projects/${projectId}/github/pull-requests/${pullRequestId}/refresh-ci`,
    { method: "POST" },
  );
}

export function publishChangeSet(
  projectId: number,
  changeSetId: number,
): Promise<GitHubPublish> {
  return request<GitHubPublish>(
    `/projects/${projectId}/github/change-sets/${changeSetId}/publish`,
    { method: "POST" },
  );
}

export function getWordPress(projectId: number): Promise<WordPressProject> {
  return request<WordPressProject>(`/projects/${projectId}/wordpress`);
}

export function connectWordPress(
  projectId: number,
  url: string,
  username: string,
  applicationPassword: string,
): Promise<WordPressConnection> {
  return request<WordPressConnection>(`/projects/${projectId}/wordpress/connection`, {
    method: "PUT",
    body: JSON.stringify({
      url,
      username,
      application_password: applicationPassword,
    }),
  });
}

export function disconnectWordPress(projectId: number): Promise<WordPressConnection> {
  return request<WordPressConnection>(`/projects/${projectId}/wordpress/connection`, {
    method: "DELETE",
  });
}

export function listWordPressRevisions(
  projectId: number,
  url: string,
): Promise<WordPressRevision[]> {
  return request<WordPressRevision[]>(
    `/projects/${projectId}/wordpress/pages/revisions?url=${encodeURIComponent(url)}`,
  );
}

export function listWordPressSnapshots(projectId: number): Promise<WordPressSnapshot[]> {
  return request<WordPressSnapshot[]>(`/projects/${projectId}/wordpress/snapshots`);
}

export function getSearchConsole(projectId: number): Promise<SearchConsoleProject> {
  return request<SearchConsoleProject>(`/projects/${projectId}/search-console`);
}

export function getKeywordSuggestions(projectId: number): Promise<KeywordSuggestions> {
  return request<KeywordSuggestions>(`/projects/${projectId}/keyword-suggestions`);
}

export function searchConsoleOAuthUrl(projectId: number): string {
  return `${API_BASE_URL}/projects/${projectId}/search-console/oauth/start`;
}

export function connectSearchConsole(
  projectId: number,
  propertyUrl: string,
): Promise<SearchConsoleConnection> {
  return request<SearchConsoleConnection>(`/projects/${projectId}/search-console/connection`, {
    method: "PUT",
    body: JSON.stringify({ property_url: propertyUrl }),
  });
}

export function disconnectSearchConsole(projectId: number): Promise<SearchConsoleConnection> {
  return request<SearchConsoleConnection>(`/projects/${projectId}/search-console/connection`, {
    method: "DELETE",
  });
}

export function listExperiments(projectId: number): Promise<Experiment[]> {
  return request<Experiment[]>(`/projects/${projectId}/experiments`);
}

export function createExperiment(
  projectId: number,
  payload: {
    hypothesis: string;
    experiment_type: ExperimentType;
    change_set_id?: number;
    finding_ids?: string[];
  },
): Promise<Experiment> {
  return request<Experiment>(`/projects/${projectId}/experiments`, {
    method: "POST",
    body: JSON.stringify(payload),
  });
}

export function getExperiment(projectId: number, experimentId: number): Promise<Experiment> {
  return request<Experiment>(`/projects/${projectId}/experiments/${experimentId}`);
}

export function measureExperimentTreatment(
  projectId: number,
  experimentId: number,
): Promise<Experiment> {
  return request<Experiment>(
    `/projects/${projectId}/experiments/${experimentId}/measure-treatment`,
    { method: "POST" },
  );
}

export function startSiteReport(projectId: number): Promise<Job> {
  return request<Job>(`/projects/${projectId}/site-reports`, { method: "POST" });
}

export function listSiteReports(projectId: number): Promise<SiteReportSummary[]> {
  return request<SiteReportSummary[]>(`/projects/${projectId}/site-reports`);
}

export function getSiteReport(projectId: number, reportId: number): Promise<SiteReport> {
  return request<SiteReport>(`/projects/${projectId}/site-reports/${reportId}`);
}

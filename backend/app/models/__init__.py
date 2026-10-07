"""Import every model so Base.metadata is fully populated for Alembic autogenerate."""

from app.models.agent import (
    AgentMessage,
    AgentMessageRole,
    AgentRun,
    AgentRunStatus,
    AgentType,
)
from app.models.audit_log import AuditLog
from app.models.change import (
    ChangeItem,
    ChangeItemKind,
    ChangeItemStatus,
    ChangePlatform,
    ChangeSet,
    ChangeSetStatus,
    ChangeTransaction,
    ChangeTransactionStatus,
    RollbackOperation,
    RollbackOperationStatus,
    RollbackTargetType,
    Snapshot,
    SnapshotReason,
    ValidationCheckStatus,
    ValidationCheckType,
    ValidationResult,
    ValidationRun,
    ValidationRunStatus,
)
from app.models.github import (
    CiStatus,
    CommitStatus,
    GithubCommit,
    GithubPullRequest,
    PullRequestState,
)
from app.models.finding import (
    AnalysisRun,
    AnalysisRunStatus,
    Finding,
    FindingStatus,
)
from app.models.job import Job, JobStatus
from app.models.knowledge import (
    OptimizationRule,
    OptimizationSource,
    RuleCategory,
    RuleConfidence,
    RuleSeverity,
)
from app.models.project import Project, ProjectMode
from app.models.report import (
    MetricSnapshot,
    SiteReport,
    SiteReportEmailStatus,
    SiteReportStatus,
)
from app.models.repository import CloneStatus, Repository
from app.models.search import (
    Experiment,
    ExperimentStatus,
    ExperimentType,
    SearchConsoleDimension,
    SearchConsoleRow,
)
from app.models.user import User
from app.models.website import (
    CrawlRun,
    CrawlRunStatus,
    PlatformConnection,
    Website,
    WebsitePage,
)
from app.models.workspace import Workspace

__all__ = [
    "AgentMessage",
    "AgentMessageRole",
    "AgentRun",
    "AgentRunStatus",
    "AgentType",
    "AnalysisRun",
    "AnalysisRunStatus",
    "AuditLog",
    "ChangeItem",
    "ChangeItemKind",
    "ChangeItemStatus",
    "ChangePlatform",
    "ChangeSet",
    "ChangeSetStatus",
    "ChangeTransaction",
    "ChangeTransactionStatus",
    "CiStatus",
    "CloneStatus",
    "CommitStatus",
    "Experiment",
    "ExperimentStatus",
    "ExperimentType",
    "GithubCommit",
    "GithubPullRequest",
    "CrawlRun",
    "CrawlRunStatus",
    "Finding",
    "FindingStatus",
    "Job",
    "JobStatus",
    "MetricSnapshot",
    "OptimizationRule",
    "OptimizationSource",
    "PlatformConnection",
    "PullRequestState",
    "Project",
    "ProjectMode",
    "Repository",
    "RollbackOperation",
    "RollbackOperationStatus",
    "RollbackTargetType",
    "RuleCategory",
    "RuleConfidence",
    "RuleSeverity",
    "SearchConsoleDimension",
    "SearchConsoleRow",
    "SiteReport",
    "SiteReportEmailStatus",
    "SiteReportStatus",
    "Snapshot",
    "SnapshotReason",
    "User",
    "ValidationCheckStatus",
    "ValidationCheckType",
    "ValidationResult",
    "ValidationRun",
    "ValidationRunStatus",
    "Website",
    "WebsitePage",
    "Workspace",
]

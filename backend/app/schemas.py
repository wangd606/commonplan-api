from datetime import date, datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, EmailStr, Field, field_validator


class UserUpdate(BaseModel):
    email: EmailStr | None = None
    name: str | None = Field(default=None, min_length=1, max_length=255)


class UserRead(BaseModel):
    id: int
    email: EmailStr
    name: str
    auth_issuer: str | None
    auth_subject: str | None
    avatar_url: str | None
    is_deleted: bool
    created_at: datetime
    updated_at: datetime

    model_config = ConfigDict(from_attributes=True)


class IdentityUserRead(BaseModel):
    id: str
    email: EmailStr
    name: str
    avatar_url: str | None
    is_active: bool
    created_at: datetime
    updated_at: datetime


class AuthTokenResponse(BaseModel):
    access_token: str
    token_type: str = "Bearer"
    expires_in: int
    user: IdentityUserRead


class InternalAuthTokenResponse(AuthTokenResponse):
    refresh_token: str


class RegisterRequest(BaseModel):
    email: EmailStr
    name: str = Field(min_length=1, max_length=255)
    password: str = Field(min_length=8, max_length=128)

    @field_validator("name")
    @classmethod
    def normalize_name(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("Name cannot be empty")
        return normalized


class LoginRequest(BaseModel):
    email: EmailStr
    password: str = Field(min_length=1, max_length=128)


class WorkspaceCreate(BaseModel):
    name: str = Field(min_length=1, max_length=255)
    slug: str = Field(pattern=r"^[a-z0-9]+(?:-[a-z0-9]+)*$", min_length=2, max_length=80)
    description: str | None = Field(default=None, max_length=4000)


class WorkspaceUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=255)
    description: str | None = Field(default=None, max_length=4000)


class WorkspaceRead(BaseModel):
    id: str
    slug: str
    name: str
    description: str | None
    my_role: str
    created_at: datetime
    updated_at: datetime


class WorkspaceSettingsUpdate(BaseModel):
    allow_member_invites: bool | None = None
    default_timezone: str | None = Field(default=None, min_length=1, max_length=64)
    domain_policy: str | None = None


class WorkspaceSettingsRead(BaseModel):
    workspace_id: str
    allow_member_invites: bool
    default_timezone: str
    domain_policy: str
    updated_by_user_id: int | None
    created_at: datetime
    updated_at: datetime
    model_config = ConfigDict(from_attributes=True)


class AuditEventRead(BaseModel):
    id: str
    workspace_id: str
    actor_user_id: int | None
    action: str
    target_type: str
    target_id: str | None
    details: dict
    created_at: datetime
    model_config = ConfigDict(from_attributes=True)


class TeamCreate(BaseModel):
    name: str = Field(min_length=1, max_length=255)
    issue_prefix: str = Field(pattern=r"^[A-Za-z][A-Za-z0-9]{1,11}$")
    description: str | None = Field(default=None, max_length=4000)


class TeamUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=255)
    description: str | None = Field(default=None, max_length=4000)


class TeamRead(BaseModel):
    id: str
    workspace_id: str
    name: str
    description: str | None
    issue_prefix: str
    my_role: str
    created_at: datetime
    updated_at: datetime


class MembershipWrite(BaseModel):
    role: str


class MemberRead(BaseModel):
    user_id: int
    name: str
    email: EmailStr
    role: str
    status: str | None = None


class InvitationCreate(BaseModel):
    email: EmailStr
    role: str = "member"
    team_id: str | None = None


class InvitationRead(BaseModel):
    id: str
    workspace_id: str
    email: EmailStr
    role: str
    team_id: str | None
    expires_at: datetime
    invite_token: str


class InvitationAccept(BaseModel):
    token: str = Field(min_length=20, max_length=256)


class TeamOverviewRead(BaseModel):
    team_id: str
    project_count: int = 0
    open_issue_count: int = 0
    current_cycle: dict | None = None
    recent_issues: list[dict] = Field(default_factory=list)


class WorkflowStateRead(BaseModel):
    id: str
    team_id: str
    name: str
    category: str
    position: int
    is_default: bool
    model_config = ConfigDict(from_attributes=True)


class CycleCreate(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    starts_on: date
    ends_on: date


class CycleRead(BaseModel):
    id: str
    team_id: str
    name: str
    starts_on: date
    ends_on: date
    completed_at: datetime | None
    created_at: datetime
    model_config = ConfigDict(from_attributes=True)


class CycleSettingsUpdate(BaseModel):
    enabled: bool | None = None
    duration_weeks: int | None = Field(default=None, ge=1, le=8)
    upcoming_cycle_count: int | None = Field(default=None, ge=1, le=15)
    next_cycle_starts_on: date | None = None
    rollover_incomplete: bool | None = None


class CycleSettingsRead(BaseModel):
    team_id: str
    enabled: bool
    duration_weeks: int
    upcoming_cycle_count: int
    next_cycle_starts_on: date | None
    rollover_incomplete: bool
    updated_by_user_id: int | None
    created_at: datetime
    updated_at: datetime
    model_config = ConfigDict(from_attributes=True)


class LabelCreate(BaseModel):
    name: str = Field(min_length=1, max_length=80)
    color: str = Field(pattern=r"^#[0-9A-Fa-f]{6}$")


class LabelRead(BaseModel):
    id: str
    team_id: str
    name: str
    color: str
    model_config = ConfigDict(from_attributes=True)


class IssueCreate(BaseModel):
    title: str = Field(min_length=1, max_length=500)
    description: str | None = Field(default=None, max_length=50000)
    workflow_state_id: str | None = None
    priority: int = Field(default=0, ge=0, le=4)
    assignee_user_id: int | None = None
    cycle_id: str | None = None
    project_id: str | None = None
    milestone_id: str | None = None
    due_date: date | None = None
    label_ids: list[str] = Field(default_factory=list, max_length=20)


class IssueUpdate(BaseModel):
    version: int = Field(ge=1)
    title: str | None = Field(default=None, min_length=1, max_length=500)
    description: str | None = Field(default=None, max_length=50000)
    workflow_state_id: str | None = None
    priority: int | None = Field(default=None, ge=0, le=4)
    assignee_user_id: int | None = None
    cycle_id: str | None = None
    project_id: str | None = None
    milestone_id: str | None = None
    due_date: date | None = None
    label_ids: list[str] | None = Field(default=None, max_length=20)


class IssueRead(BaseModel):
    id: str
    workspace_id: str
    team_id: str
    number: int
    key: str
    title: str
    description: str | None
    workflow_state_id: str
    workflow_state_name: str
    workflow_category: str
    priority: int
    creator_user_id: int
    assignee_user_id: int | None
    cycle_id: str | None
    project_id: str | None
    milestone_id: str | None
    parent_issue_id: str | None
    due_date: date | None
    version: int
    labels: list[LabelRead]
    created_at: datetime
    updated_at: datetime


class IssueCommentCreate(BaseModel):
    body: str = Field(min_length=1, max_length=20000)
    mentioned_user_ids: list[int] = Field(default_factory=list, max_length=100)

    @field_validator("body")
    @classmethod
    def normalize_body(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("Comment cannot be empty")
        return normalized

    @field_validator("mentioned_user_ids")
    @classmethod
    def unique_mentions(cls, value: list[int]) -> list[int]:
        return list(dict.fromkeys(value))


class IssueCommentPatch(IssueCommentCreate):
    pass


class IssueWatcherRead(BaseModel):
    user_id: int
    name: str
    reason: str
    created_at: datetime


class IssueCollaborationRead(BaseModel):
    watching: bool
    watchers: list[IssueWatcherRead]
    sub_issues: list[IssueRead]


class IssueRelationTypeCreate(BaseModel):
    key: str = Field(min_length=2, max_length=64, pattern=r"^[a-z][a-z0-9_]*$")
    forward_label: str = Field(min_length=2, max_length=120)
    inverse_label: str = Field(min_length=2, max_length=120)
    category: str = Field(default="custom", pattern=r"^(dependency|custom)$")
    symmetric: bool = False
    allow_cycles: bool = True


class IssueRelationTypeRead(BaseModel):
    id: str
    workspace_id: str
    key: str
    forward_label: str
    inverse_label: str
    category: str
    is_system: bool
    symmetric: bool
    allow_cycles: bool
    created_at: datetime
    model_config = ConfigDict(from_attributes=True)


class IssueRelationCreate(BaseModel):
    relation_type_id: str
    target_issue_key: str = Field(min_length=2, max_length=32)
    direction: str = Field(default="outgoing", pattern=r"^(outgoing|incoming)$")


class IssueRelationRead(BaseModel):
    id: str
    relation_type_id: str
    type_key: str
    direction: str
    label: str
    related_issue: IssueRead
    created_by_user_id: int
    created_at: datetime


class IssueRelationSummaryRead(BaseModel):
    issue_key: str
    relation_id: str
    type_key: str
    category: str
    direction: str
    label: str
    related_issue_key: str


class SavedViewCreate(BaseModel):
    name: str = Field(min_length=1, max_length=255)
    team_id: str | None = None
    visibility: str = "private"
    filter_spec: dict = Field(default_factory=dict)
    group_by: str | None = None
    sort_by: str = "updated_at"
    sort_direction: str = "desc"


class SavedViewPatch(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=255)
    visibility: str | None = None
    filter_spec: dict | None = None
    group_by: str | None = None
    sort_by: str | None = None
    sort_direction: str | None = None


class SavedViewRead(BaseModel):
    id: str
    workspace_id: str
    team_id: str | None
    owner_user_id: int
    name: str
    visibility: str
    filter_version: int
    filter_spec: dict
    group_by: str | None
    sort_by: str
    sort_direction: str
    created_at: datetime
    updated_at: datetime
    model_config = ConfigDict(from_attributes=True)


class NotificationRead(BaseModel):
    id: str
    workspace_id: str
    issue_id: str | None
    issue_event_id: str | None
    actor_user_id: int | None
    kind: str
    payload: dict
    created_at: datetime
    read_at: datetime | None
    model_config = ConfigDict(from_attributes=True)


class InboxRead(BaseModel):
    unread_count: int
    notifications: list[NotificationRead]


class GitHubPullRequestRead(BaseModel):
    id: str
    github_repo_id: int
    github_repo_full_name: str
    pr_number: int
    title: str
    html_url: str
    state: str
    is_draft: bool
    merged_at: datetime | None
    github_updated_at: datetime
    model_config = ConfigDict(from_attributes=True)


class GitHubDeliveryRead(BaseModel):
    delivery_id: str
    github_repo_id: int | None
    event_type: str
    action: str | None
    status: str
    received_at: datetime
    processed_at: datetime | None
    error_code: str | None
    model_config = ConfigDict(from_attributes=True)


class GitHubIntegrationHealthRead(BaseModel):
    configured: bool
    owner_login: str | None
    owner_id: int | None
    hook_id: int | None
    event: str
    webhook_path: str
    last_delivery: GitHubDeliveryRead | None
    delivery_counts: dict[str, int]


class IssueActivityRead(BaseModel):
    id: str
    kind: str
    actor_user_id: int | None
    actor_name: str | None
    body: str | None = None
    event_type: str | None = None
    changes: dict = Field(default_factory=dict)
    created_at: datetime
    edited_at: datetime | None = None


class ProjectCreate(BaseModel):
    name: str = Field(min_length=1, max_length=255)
    slug: str | None = Field(default=None, pattern=r"^[a-z0-9]+(?:-[a-z0-9]+)*$", max_length=80)
    summary: str | None = Field(default=None, max_length=500)
    description: str | None = Field(default=None, max_length=50000)
    status: str = "planned"
    lead_user_id: int | None = None
    target_date: date | None = None


class ProjectPatch(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=255)
    summary: str | None = Field(default=None, max_length=500)
    description: str | None = Field(default=None, max_length=50000)
    status: str | None = None
    lead_user_id: int | None = None
    target_date: date | None = None


class ProjectObjectiveCreate(BaseModel):
    kind: str
    body: str = Field(min_length=1, max_length=10000)
    position: int = Field(default=0, ge=0)


class ProjectObjectivePatch(BaseModel):
    body: str | None = Field(default=None, min_length=1, max_length=10000)
    position: int | None = Field(default=None, ge=0)
    is_met: bool | None = None


class ProjectObjectiveRead(BaseModel):
    id: str
    project_id: str
    kind: str
    body: str
    position: int
    is_met: bool
    created_at: datetime
    updated_at: datetime
    model_config = ConfigDict(from_attributes=True)


class ProjectMilestoneCreate(BaseModel):
    name: str = Field(min_length=1, max_length=255)
    description: str | None = Field(default=None, max_length=10000)
    status: str = "planned"
    target_date: date | None = None
    position: int = Field(default=0, ge=0)


class ProjectMilestonePatch(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=255)
    description: str | None = Field(default=None, max_length=10000)
    status: str | None = None
    target_date: date | None = None
    position: int | None = Field(default=None, ge=0)


class ProjectMilestoneRead(BaseModel):
    id: str
    project_id: str
    name: str
    description: str | None
    status: str
    target_date: date | None
    position: int
    issue_count: int = 0
    completed_issue_count: int = 0
    created_at: datetime
    updated_at: datetime


class ProjectStatusUpdateCreate(BaseModel):
    body: str = Field(min_length=1, max_length=20000)
    health: str | None = None


class ProjectStatusUpdateRead(BaseModel):
    id: str
    project_id: str
    author_user_id: int
    author_name: str | None
    body: str
    health: str | None
    created_at: datetime
    edited_at: datetime | None


class ProjectRead(BaseModel):
    id: str
    team_id: str
    name: str
    slug: str
    summary: str | None
    description: str | None
    status: str
    lead_user_id: int | None
    target_date: date | None
    issue_count: int
    completed_issue_count: int
    progress_percent: int
    objectives: list[ProjectObjectiveRead] = Field(default_factory=list)
    milestones: list[ProjectMilestoneRead] = Field(default_factory=list)
    updates: list[ProjectStatusUpdateRead] = Field(default_factory=list)
    linked_issues: list[IssueRead] = Field(default_factory=list)
    created_at: datetime
    updated_at: datetime


class AttachmentInitiate(BaseModel):
    filename: str = Field(min_length=1, max_length=255)
    content_type: str = Field(min_length=1, max_length=255)
    byte_size: int = Field(ge=1)
    sha256: str | None = Field(default=None, min_length=64, max_length=64)


class AttachmentRead(BaseModel):
    id: str
    workspace_id: str
    uploaded_by_user_id: int | None
    original_filename: str
    content_type: str
    byte_size: int
    sha256: str | None
    upload_status: str
    scan_status: str
    created_at: datetime
    ready_at: datetime | None
    model_config = ConfigDict(from_attributes=True)


class AttachmentInitiated(BaseModel):
    attachment: AttachmentRead
    upload_url: str
    upload_method: str = "PUT"
    upload_headers: dict[str, str]


class AttachmentDownload(BaseModel):
    download_url: str
    expires_in_seconds: int


class AttachmentIngestionRead(BaseModel):
    id: str | None = None
    file_asset_id: str
    status: str
    parser_name: str | None = None
    parser_version: str | None = None
    chunker_version: str | None = None
    embedding_provider: str | None = None
    embedding_model: str | None = None
    embedding_dimensions: int | None = None
    attempts: int = 0
    error_code: str | None = None
    error_message: str | None = None
    page_count: int = 0
    chunk_count: int = 0
    started_at: datetime | None = None
    completed_at: datetime | None = None


class TeamSummaryRead(BaseModel):
    scope: dict
    filters: dict
    headline_metrics: dict
    status_distribution: list[dict]
    priority_distribution: list[dict]
    assignee_distribution: list[dict]
    cycle_progress: dict | None
    project_distribution: list[dict]
    trend: dict
    attention_issues: dict[str, list[dict]]
    recent_activity: list[dict]
    matching_issues: list[dict]


class RetrievalSearchRequest(BaseModel):
    query: str = Field(min_length=1, max_length=1000)
    mode: Literal["vector", "lexical", "both"] = "both"
    team_ids: list[str] | None = Field(default=None, max_length=50)
    project_id: str | None = None
    issue_id: str | None = None
    limit: int = Field(default=10, ge=1, le=50)


class RetrievalHitRead(BaseModel):
    node_id: str
    source_id: str
    source_type: str
    content: str
    method: Literal["vector", "lexical"]
    score: float
    workspace_id: str
    team_id: str
    project_id: str | None
    issue_id: str | None
    heading_path: str | None
    page_from: int | None
    page_to: int | None
    metadata: dict
    citation_target: str
    model_config = ConfigDict(from_attributes=True)


class RetrievalScopeRead(BaseModel):
    workspace_id: str
    team_ids: list[str]
    project_id: str | None
    issue_id: str | None


class RetrievalSearchResponse(BaseModel):
    scope: RetrievalScopeRead
    embedding_profile_key: str | None
    vector_results: list[RetrievalHitRead]
    lexical_results: list[RetrievalHitRead]


class HybridRetrievalSearchRequest(BaseModel):
    query: str = Field(min_length=1, max_length=1000)
    team_ids: list[str] | None = Field(default=None, max_length=50)
    project_id: str | None = None
    issue_id: str | None = None
    limit: int = Field(default=10, ge=1, le=30)
    candidate_limit: int = Field(default=40, ge=10, le=100)
    neighbor_window: int = Field(default=1, ge=0, le=3)
    include_parent: bool = True


class RetrievalContextRead(BaseModel):
    passage_id: str
    relation: Literal["parent_page", "previous", "next"]
    content: str
    ordinal: int | None
    heading_path: str | None
    page_from: int | None
    page_to: int | None


class HybridRetrievalHitRead(BaseModel):
    node_id: str
    source_id: str
    source_type: str
    content: str
    workspace_id: str
    team_id: str
    project_id: str | None
    issue_id: str | None
    heading_path: str | None
    page_from: int | None
    page_to: int | None
    metadata: dict
    citation_target: str
    rrf_score: float
    rerank_score: float
    final_score: float
    vector_rank: int | None
    lexical_rank: int | None
    vector_score: float | None
    lexical_score: float | None
    context: list[RetrievalContextRead]


class RetrievalPipelineRead(BaseModel):
    orchestrator: str = "langgraph-retrieval-v1"
    fusion: str
    reranker: str
    candidate_limit: int
    neighbor_window: int
    include_parent: bool
    timings_ms: dict[str, float]


class HybridRetrievalSearchResponse(BaseModel):
    scope: RetrievalScopeRead
    embedding_profile_key: str | None
    vector_results: list[RetrievalHitRead]
    lexical_results: list[RetrievalHitRead]
    fused_results: list[HybridRetrievalHitRead]
    pipeline: RetrievalPipelineRead


class GroundedAnswerRequest(HybridRetrievalSearchRequest):
    max_citations: int = Field(default=6, ge=1, le=12)


class GroundedClaimRead(BaseModel):
    text: str
    citation_ids: list[str]


class AnswerCitationRead(BaseModel):
    id: str
    target: str
    source_id: str
    source_type: str
    excerpt: str
    heading_path: str | None
    page_from: int | None
    page_to: int | None


class AnswerGenerationRead(BaseModel):
    provider: str
    model: str
    status: Literal["answered", "evidence_only", "insufficient_evidence"]
    grounding_status: Literal["citation_validated", "agent_validated", "not_generated"]
    grounded: bool
    insufficient_evidence: bool
    timings_ms: dict[str, float]


class AgenticStepRead(BaseModel):
    node: str
    query: str
    attempt: int
    decision: str
    detail: str | None = None


class AgenticUsageRead(BaseModel):
    prompt_tokens: int
    completion_tokens: int
    total_tokens: int


class AgenticRunRead(BaseModel):
    strategy: str
    final_query: str
    rewrites: int
    answer_retries: int
    model_calls: int
    usage: AgenticUsageRead
    steps: list[AgenticStepRead]


class GroundedAnswerResponse(BaseModel):
    query: str
    answer: str | None
    claims: list[GroundedClaimRead]
    citations: list[AnswerCitationRead]
    generation: AnswerGenerationRead
    retrieval: HybridRetrievalSearchResponse
    agent: AgenticRunRead | None = None

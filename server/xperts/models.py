from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

try:
    from server.workflow_native.schemas import NativeWorkflowDefinition, ValidationIssue
except ModuleNotFoundError:
    from workflow_native.schemas import NativeWorkflowDefinition, ValidationIssue

try:
    from server.prompts.models import PromptProfileBinding, ResolvedPromptProfile
except ModuleNotFoundError:
    from prompts.models import PromptProfileBinding, ResolvedPromptProfile


XpertStatus = Literal["draft", "published", "archived"]


class XpertAgentConfig(BaseModel):
    max_concurrency: int = Field(default=4, ge=1, le=100)
    recursion_limit: int = Field(default=1000, ge=100, le=10_000)


class XpertOpeningFeature(BaseModel):
    enabled: bool = False
    message: str = Field(default="", max_length=4_000)
    questions: list[str] = Field(default_factory=list, max_length=8)


class XpertGeneratedQuestionsFeature(BaseModel):
    enabled: bool = False
    model_id: str = Field(default="", max_length=300)
    count: int = Field(default=3, ge=1, le=6)


class XpertConversationTitleFeature(BaseModel):
    enabled: bool = False
    model_id: str = Field(default="", max_length=300)


class XpertConversationSummaryFeature(BaseModel):
    enabled: bool = False
    model_id: str = Field(default="", max_length=300)
    max_context_chars: int = Field(default=48_000, ge=8_000, le=200_000)
    trigger_ratio: float = Field(default=0.75, ge=0.5, le=0.95)
    keep_recent_messages: int = Field(default=8, ge=2, le=30)
    max_summary_chars: int = Field(default=4_000, ge=500, le=12_000)


class XpertMemoryReplyFeature(BaseModel):
    enabled: bool = False
    min_confidence: float = Field(default=0.92, ge=0.8, le=1.0)


class XpertFileUploadFeature(BaseModel):
    enabled: bool = True
    max_files_per_run: int = Field(default=5, ge=1, le=5)
    allowed_extensions: list[str] = Field(
        default_factory=lambda: [".txt", ".md", ".markdown", ".pdf"],
        min_length=1,
        max_length=12,
    )


class XpertSpeechFeature(BaseModel):
    enabled: bool = False
    model_id: str = Field(default="", max_length=300)
    voice: str = Field(default="alloy", max_length=80)
    max_text_chars: int = Field(default=4_000, ge=100, le=10_000)


class XpertTranscriptionFeature(BaseModel):
    enabled: bool = False
    model_id: str = Field(default="", max_length=300)


class XpertFeatureConfig(BaseModel):
    opening: XpertOpeningFeature = Field(default_factory=XpertOpeningFeature)
    generated_questions: XpertGeneratedQuestionsFeature = Field(
        default_factory=XpertGeneratedQuestionsFeature
    )
    conversation_title: XpertConversationTitleFeature = Field(
        default_factory=XpertConversationTitleFeature
    )
    conversation_summary: XpertConversationSummaryFeature = Field(
        default_factory=XpertConversationSummaryFeature
    )
    memory_reply: XpertMemoryReplyFeature = Field(
        default_factory=XpertMemoryReplyFeature
    )
    file_upload: XpertFileUploadFeature = Field(default_factory=XpertFileUploadFeature)
    text_to_speech: XpertSpeechFeature = Field(default_factory=XpertSpeechFeature)
    speech_to_text: XpertTranscriptionFeature = Field(
        default_factory=XpertTranscriptionFeature
    )


class XpertDraft(BaseModel):
    workflow: NativeWorkflowDefinition
    input_variable: str = Field(default="user_input", min_length=1, max_length=128)
    history_variable: str = Field(
        default="conversation_history",
        min_length=1,
        max_length=128,
    )
    output_variable: str = Field(default="agent_output", min_length=1, max_length=128)
    agent_config: XpertAgentConfig = Field(default_factory=XpertAgentConfig)
    features: XpertFeatureConfig = Field(default_factory=XpertFeatureConfig)
    prompt_profiles: list[PromptProfileBinding] = Field(
        default_factory=list,
        max_length=20,
    )


class XpertVersion(BaseModel):
    version: int = Field(ge=1)
    draft_revision: int = Field(ge=1)
    workflow: NativeWorkflowDefinition
    input_variable: str
    history_variable: str
    output_variable: str
    # Legacy versions intentionally keep the old unlimited runtime behavior.
    agent_config: XpertAgentConfig | None = None
    # Legacy versions preserve their existing chat behavior.
    features: XpertFeatureConfig | None = None
    prompt_profiles: list[ResolvedPromptProfile] = Field(default_factory=list)
    release_notes: str = ""
    checksum: str
    published_at: float


class XpertDefinition(BaseModel):
    id: str
    slug: str
    name: str
    description: str = ""
    tags: list[str] = Field(default_factory=list)
    starters: list[str] = Field(default_factory=list)
    status: XpertStatus = "draft"
    draft_revision: int = Field(default=1, ge=1)
    published_version: int | None = None
    draft: XpertDraft
    versions: list[XpertVersion] = Field(default_factory=list)
    created_at: float
    updated_at: float


class XpertSummary(BaseModel):
    id: str
    slug: str
    name: str
    description: str
    tags: list[str]
    starters: list[str]
    status: XpertStatus
    draft_revision: int
    published_version: int | None
    version_count: int
    created_at: float
    updated_at: float


class XpertValidationResult(BaseModel):
    valid: bool
    issues: list[ValidationIssue] = Field(default_factory=list)
    order: list[str] = Field(default_factory=list)
    node_count: int
    edge_count: int


class XpertConversationMessage(BaseModel):
    role: Literal["user", "assistant"]
    content: str = Field(min_length=1, max_length=20_000)


class XpertRunRequest(BaseModel):
    message: str = Field(min_length=1, max_length=20_000)
    messages: list[XpertConversationMessage] = Field(default_factory=list, max_length=20)
    version: int | None = Field(default=None, ge=1)
    conversation_id: str | None = Field(default=None, max_length=200)
    file_asset_ids: list[str] = Field(default_factory=list, max_length=5)


class XpertSpeechRequest(BaseModel):
    text: str = Field(min_length=1, max_length=10_000)
    version: int | None = Field(default=None, ge=1)

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from .contracts import FilePurpose


FILE_OUTPUT_CAPABILITIES_VERSION = "modelmirror-file-output-capabilities-v1"

FileOutputStatus = Literal[
    "queued",
    "running",
    "completed",
    "failed",
    "cancel_requested",
    "cancelled",
    "interrupted",
    "deleting",
    "deleted",
    "expired",
]
FileOutputPreviewKind = Literal[
    "text", "document", "image", "audio", "video", "none"
]
FileOutputAction = Literal["preview", "download", "reuse", "save_rag", "delete"]
FileOutputHandling = Literal["native", "extract"]


class FileOutputFormatCapability(BaseModel):
    model_config = ConfigDict(frozen=True)

    format_id: str = Field(pattern=r"^[a-z][a-z0-9_]*$")
    media_types: tuple[str, ...] = Field(min_length=1)
    preview_kind: FileOutputPreviewKind
    actions: tuple[FileOutputAction, ...] = Field(min_length=1)
    generation_kind: Literal["text", "document", "workbook", "presentation", "captured"]
    interaction_status: Literal["ready", "planned", "disabled"]
    status_reason: str | None = Field(default=None, max_length=500)

    @model_validator(mode="after")
    def require_unready_reason(self) -> "FileOutputFormatCapability":
        if self.interaction_status != "ready" and not self.status_reason:
            raise ValueError("Unready output formats require status_reason.")
        return self


class FileOutputLimits(BaseModel):
    model_config = ConfigDict(frozen=True)

    max_files_per_turn: int = Field(ge=1, le=5)
    max_bytes_per_file: int = Field(gt=0)
    max_total_bytes_per_turn: int = Field(gt=0)
    max_spec_bytes: int = Field(gt=0)
    max_spec_chars: int = Field(gt=0)
    hard_ttl_seconds: int = Field(gt=0)


class FileOutputCapabilitiesResponse(BaseModel):
    model_config = ConfigDict(frozen=True)

    version: Literal["modelmirror-file-output-capabilities-v1"] = (
        FILE_OUTPUT_CAPABILITIES_VERSION
    )
    registry_version: str
    requested_purpose: FilePurpose
    requested_model_id: str | None = Field(default=None, max_length=256)
    model_specific: bool = False
    interaction_status: Literal["ready", "planned", "disabled"]
    status_reason: str | None = Field(default=None, max_length=500)
    limits: FileOutputLimits
    formats: tuple[FileOutputFormatCapability, ...]


class FileOutputResponse(BaseModel):
    model_config = ConfigDict(frozen=True)

    output_id: str
    asset_id: str | None = None
    purpose: FilePurpose
    scope_id: str
    producer_kind: str
    display_name: str
    format: str
    media_type: str
    byte_size: int = Field(ge=0)
    preview_kind: FileOutputPreviewKind
    status: FileOutputStatus
    expires_at: str | None = None
    warnings: tuple[str, ...] = ()
    error_code: str | None = None
    source_run_id: str | None = None
    source_message_id: str | None = None
    source_node_id: str | None = None
    created_at: str
    updated_at: str


class FileOutputListResponse(BaseModel):
    model_config = ConfigDict(frozen=True)

    items: tuple[FileOutputResponse, ...]
    total: int = Field(ge=0)


class FileOutputPreviewResponse(BaseModel):
    model_config = ConfigDict(frozen=True)

    output_id: str
    preview_kind: Literal["text", "document", "none"]
    text: str | None = None
    document: dict[str, object] | None = None
    truncated: bool = False
    warnings: tuple[str, ...] = ()


class FileOutputReuseConfirmRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    handling: FileOutputHandling
    target_id: str = Field(min_length=1, max_length=256)
    gateway: Literal["default"] = "default"

    @field_validator("target_id")
    @classmethod
    def clean_target(cls, value: str) -> str:
        return value.strip()


class FileOutputReuseConfirmResponse(BaseModel):
    model_config = ConfigDict(frozen=True)

    output_id: str
    asset_id: str
    handling: FileOutputHandling
    target_id: str
    confirmation_revision: int = Field(ge=1)
    output_confirmation_revision: int = Field(ge=1)
    expires_at: str
    confirmed_at: str

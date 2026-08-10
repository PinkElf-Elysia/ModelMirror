from __future__ import annotations

from enum import Enum
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


FILE_CAPABILITIES_VERSION = "modelmirror-file-capabilities-v2"


class FileFamily(str, Enum):
    DOCUMENT = "document"
    IMAGE = "image"
    AUDIO = "audio"
    VIDEO = "video"
    DATASET = "dataset"


class FilePurpose(str, Enum):
    CHAT = "chat"
    RAG = "rag"
    DATAX = "datax"
    AGENT = "agent"
    WORKFLOW = "workflow"


class FileInputKind(str, Enum):
    DOCUMENT = "document"
    IMAGE = "image"
    AUDIO = "audio"
    VIDEO = "video"
    DATA_SOURCE = "data_source"
    IMAGE_REFERENCE = "image_reference"
    AUDIO_GENERATION_IMAGE = "audio_generation_image"
    VIDEO_GENERATION_FRAME = "video_generation_frame"
    VIDEO_GENERATION_REFERENCE = "video_generation_reference"
    VISUAL_ANALYSIS = "visual_analysis"


class FileTransport(str, Enum):
    DATA_URL = "data_url"
    MULTIPART = "multipart"


class FileRetention(str, Enum):
    REQUEST = "request"
    TEMPORARY = "temporary"
    PERSISTENT = "persistent"


class FileSizeMeasure(str, Enum):
    BINARY = "binary"
    ENCODED_PAYLOAD = "encoded_payload"


class FileSupportLevel(str, Enum):
    NATIVE = "native"
    CONVERTED = "converted"
    SPECIALIZED = "specialized"
    UNSUPPORTED = "unsupported"


class FileInteractionStatus(str, Enum):
    READY = "ready"
    PLANNED = "planned"
    DISABLED = "disabled"


class FileHandling(str, Enum):
    NATIVE = "native"
    EXTRACT = "extract"


class FileAnalysisMode(str, Enum):
    VISION = "vision"
    PROVIDER_OCR = "provider_ocr"


FILE_ANALYSIS_CANARY_VERIFIED_MODES = frozenset(
    {FileAnalysisMode.VISION, FileAnalysisMode.PROVIDER_OCR}
)


def file_analysis_mode_canary_verified(mode: FileAnalysisMode) -> bool:
    """Release only modes whose separately authorized real canary passed."""

    return mode in FILE_ANALYSIS_CANARY_VERIFIED_MODES


class FileAnalysisOption(BaseModel):
    model_config = ConfigDict(frozen=True)

    mode: FileAnalysisMode
    format_ids: tuple[str, ...] = Field(min_length=1)
    provider: str = Field(min_length=1, max_length=80)
    paid: bool = False
    max_pages: int = Field(ge=1, le=20)
    max_prompt_chars: int = Field(ge=1, le=2_000)
    requires_explicit_target: bool = True
    interaction_status: FileInteractionStatus
    status_reason: str | None = Field(default=None, max_length=500)

    @field_validator("format_ids", mode="before")
    @classmethod
    def normalize_format_ids(cls, value: object) -> tuple[str, ...]:
        if isinstance(value, (str, bytes)):
            raise ValueError("Format IDs must be a collection.")
        items = tuple(str(item or "").strip().lower() for item in value)  # type: ignore[arg-type]
        if not all(items) or len(items) != len(set(items)):
            raise ValueError("Format IDs must be non-empty and unique.")
        return tuple(sorted(items))

    @model_validator(mode="after")
    def validate_status_reason(self) -> "FileAnalysisOption":
        if (
            self.interaction_status != FileInteractionStatus.READY
            and not str(self.status_reason or "").strip()
        ):
            raise ValueError("Unready analysis options require status_reason.")
        return self


class FileHandlingOption(BaseModel):
    model_config = ConfigDict(frozen=True)

    handling: FileHandling
    format_ids: tuple[str, ...] = Field(min_length=1)
    support_level: FileSupportLevel
    interaction_status: FileInteractionStatus
    status_reason: str | None = Field(default=None, max_length=500)

    @field_validator("format_ids", mode="before")
    @classmethod
    def normalize_format_ids(cls, value: object) -> tuple[str, ...]:
        if isinstance(value, (str, bytes)):
            raise ValueError("Format IDs must be a collection.")
        items = tuple(str(item or "").strip().lower() for item in value)  # type: ignore[arg-type]
        if not all(items) or len(items) != len(set(items)):
            raise ValueError("Format IDs must be non-empty and unique.")
        return tuple(sorted(items))

    @model_validator(mode="after")
    def validate_status_reason(self) -> "FileHandlingOption":
        if (
            self.interaction_status != FileInteractionStatus.READY
            and not str(self.status_reason or "").strip()
        ):
            raise ValueError("Unready handling options require status_reason.")
        return self


def _validate_support_contract(
    *,
    support_level: FileSupportLevel,
    interaction_status: FileInteractionStatus,
    parser_id: str | None,
    ui_entrypoint: str | None,
    status_reason: str | None,
) -> None:
    if (
        support_level == FileSupportLevel.UNSUPPORTED
        and interaction_status != FileInteractionStatus.DISABLED
    ):
        raise ValueError("Unsupported file inputs must be disabled.")
    if interaction_status == FileInteractionStatus.READY:
        if not str(parser_id or "").strip() or not str(ui_entrypoint or "").strip():
            raise ValueError(
                "Ready file inputs require both parser_id and ui_entrypoint."
            )
        return
    if not str(status_reason or "").strip():
        raise ValueError("Planned and disabled file inputs require status_reason.")


def _extension(value: object) -> str:
    clean = str(value or "").strip().lower()
    if not clean or "/" in clean or "\\" in clean:
        raise ValueError("Extensions must be non-empty names without paths.")
    return clean if clean.startswith(".") else f".{clean}"


def _media_type(value: object) -> str:
    clean = str(value or "").split(";", 1)[0].strip().lower()
    if not clean or "/" not in clean:
        raise ValueError("Media types must use the type/subtype form.")
    return clean


class FileFormatCapability(BaseModel):
    model_config = ConfigDict(frozen=True)

    format_id: str = Field(pattern=r"^[a-z][a-z0-9_]*$")
    family: FileFamily
    extensions: tuple[str, ...] = Field(min_length=1)
    media_types: tuple[str, ...] = Field(min_length=1)
    parser_id: str | None = Field(default=None, max_length=160)
    interaction_status: FileInteractionStatus = FileInteractionStatus.READY
    status_reason: str | None = Field(default=None, max_length=500)

    @field_validator("extensions", mode="before")
    @classmethod
    def normalize_extensions(cls, value: object) -> tuple[str, ...]:
        if isinstance(value, (str, bytes)):
            raise ValueError("Extensions must be a collection.")
        items = tuple(_extension(item) for item in value)  # type: ignore[arg-type]
        if len(items) != len(set(items)):
            raise ValueError("Extensions must be unique.")
        return tuple(sorted(items))

    @field_validator("media_types", mode="before")
    @classmethod
    def normalize_media_types(cls, value: object) -> tuple[str, ...]:
        if isinstance(value, (str, bytes)):
            raise ValueError("Media types must be a collection.")
        items = tuple(_media_type(item) for item in value)  # type: ignore[arg-type]
        if len(items) != len(set(items)):
            raise ValueError("Media types must be unique.")
        return tuple(sorted(items))

    @model_validator(mode="after")
    def validate_status_reason(self) -> "FileFormatCapability":
        if (
            self.interaction_status != FileInteractionStatus.READY
            and not str(self.status_reason or "").strip()
        ):
            raise ValueError("Unready file formats require status_reason.")
        return self


class FileInputPolicy(BaseModel):
    model_config = ConfigDict(frozen=True)

    purpose: FilePurpose
    input_kind: FileInputKind
    format_ids: tuple[str, ...] = Field(min_length=1)
    max_bytes_per_file: int = Field(gt=0)
    max_files_per_request: int | None = Field(default=1, ge=1)
    max_total_bytes_per_request: int | None = Field(default=None, gt=0)
    size_measure: FileSizeMeasure = FileSizeMeasure.BINARY
    transport: FileTransport = FileTransport.MULTIPART
    retention: FileRetention = FileRetention.REQUEST
    support_level: FileSupportLevel
    interaction_status: FileInteractionStatus
    parser_id: str | None = Field(
        default=None,
        pattern=r"^[a-z][a-z0-9_.-]*$",
        max_length=128,
    )
    ui_entrypoint: str | None = Field(default=None, pattern=r"^/", max_length=256)
    status_reason: str | None = Field(default=None, max_length=500)

    @field_validator("format_ids", mode="before")
    @classmethod
    def normalize_format_ids(cls, value: object) -> tuple[str, ...]:
        if isinstance(value, (str, bytes)):
            raise ValueError("Format IDs must be a collection.")
        items = tuple(str(item or "").strip().lower() for item in value)  # type: ignore[arg-type]
        if not all(items) or len(items) != len(set(items)):
            raise ValueError("Format IDs must be non-empty and unique.")
        return tuple(sorted(items))

    @model_validator(mode="after")
    def validate_limits(self) -> "FileInputPolicy":
        if (
            self.max_total_bytes_per_request is not None
            and self.max_total_bytes_per_request < self.max_bytes_per_file
        ):
            raise ValueError("The total limit cannot be smaller than the per-file limit.")
        _validate_support_contract(
            support_level=self.support_level,
            interaction_status=self.interaction_status,
            parser_id=self.parser_id,
            ui_entrypoint=self.ui_entrypoint,
            status_reason=self.status_reason,
        )
        return self


class FileInputCapability(BaseModel):
    model_config = ConfigDict(frozen=True)

    purpose: FilePurpose
    input_kind: FileInputKind
    families: tuple[FileFamily, ...] = Field(min_length=1)
    max_bytes_per_file: int = Field(gt=0)
    max_files_per_request: int | None = Field(default=1, ge=1)
    max_total_bytes_per_request: int | None = Field(default=None, gt=0)
    size_measure: FileSizeMeasure
    transport: FileTransport
    retention: FileRetention
    support_level: FileSupportLevel
    interaction_status: FileInteractionStatus
    parser_id: str | None = Field(
        default=None,
        pattern=r"^[a-z][a-z0-9_.-]*$",
        max_length=128,
    )
    ui_entrypoint: str | None = Field(default=None, pattern=r"^/", max_length=256)
    status_reason: str | None = Field(default=None, max_length=500)
    handling_options: tuple[FileHandlingOption, ...] = ()
    analysis_options: tuple[FileAnalysisOption, ...] = ()
    formats: tuple[FileFormatCapability, ...] = Field(min_length=1)

    @model_validator(mode="after")
    def validate_support(self) -> "FileInputCapability":
        _validate_support_contract(
            support_level=self.support_level,
            interaction_status=self.interaction_status,
            parser_id=self.parser_id,
            ui_entrypoint=self.ui_entrypoint,
            status_reason=self.status_reason,
        )
        return self


class FileCapabilitiesResponse(BaseModel):
    model_config = ConfigDict(frozen=True)

    version: Literal["modelmirror-file-capabilities-v2"] = FILE_CAPABILITIES_VERSION
    registry_version: str
    requested_purpose: FilePurpose | None = None
    requested_model_id: str | None = Field(default=None, max_length=256)
    model_specific: bool = False
    capabilities: tuple[FileInputCapability, ...]


class FileAssetResponse(BaseModel):
    """Tenant-safe asset metadata; private hashes and storage keys stay internal."""

    model_config = ConfigDict(frozen=True)

    asset_id: str
    purpose: FilePurpose
    scope_id: str
    display_name: str
    format: str
    media_type: str
    byte_size: int = Field(ge=0)
    status: Literal[
        "validating",
        "processing",
        "ready",
        "failed",
        "expired",
        "deleting",
        "deleted",
    ]
    expires_at: str | None = None
    created_at: str
    updated_at: str


class FileAssetListResponse(BaseModel):
    """Public assets visible inside one authorized purpose and scope."""

    model_config = ConfigDict(frozen=True)

    items: tuple[FileAssetResponse, ...]
    total: int = Field(ge=0)

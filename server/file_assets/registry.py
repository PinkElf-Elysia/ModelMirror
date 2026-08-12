from __future__ import annotations

import os
from pathlib import PurePath
from types import MappingProxyType
from typing import Iterable, Mapping

from .contracts import (
    FileCapabilitiesResponse,
    FileAnalysisMode,
    FileAnalysisOption,
    FileFamily,
    FileFormatCapability,
    FileHandling,
    FileHandlingOption,
    FileInputCapability,
    FileInputKind,
    FileInputPolicy,
    FileInteractionStatus,
    FilePurpose,
    FileRetention,
    FileSizeMeasure,
    FileSupportLevel,
    FileTransport,
    file_analysis_mode_canary_verified,
)


FILE_FORMAT_REGISTRY_VERSION = "modelmirror-file-formats-v5"
MIB = 1024 * 1024

_CHAT_RUNTIME_GATES: dict[FileInputKind, tuple[str, bool, str]] = {
    FileInputKind.DOCUMENT: (
        "CHAT_FILE_INPUT_ENABLED",
        False,
        "Chat 文件输入当前未启用。",
    ),
    FileInputKind.IMAGE: (
        "MULTIMODAL_IMAGE_ANALYSIS_ENABLED",
        True,
        "Chat 图片分析当前未启用。",
    ),
    FileInputKind.IMAGE_REFERENCE: (
        "MULTIMODAL_IMAGE_GENERATION_ENABLED",
        True,
        "图片生成参考图当前未启用。",
    ),
    FileInputKind.AUDIO: (
        "MULTIMODAL_CHAT_AUDIO_ENABLED",
        False,
        "Chat 音频附件当前未启用。",
    ),
    FileInputKind.VIDEO: (
        "MULTIMODAL_CHAT_VIDEO_ENABLED",
        False,
        "Chat 视频附件当前未启用。",
    ),
    FileInputKind.AUDIO_GENERATION_IMAGE: (
        "MULTIMODAL_AUDIO_GENERATION_ENABLED",
        False,
        "音频生成图片提示当前未启用。",
    ),
    FileInputKind.VIDEO_GENERATION_FRAME: (
        "MULTIMODAL_VIDEO_GENERATION_ENABLED",
        False,
        "视频生成首尾帧当前未启用。",
    ),
    FileInputKind.VIDEO_GENERATION_REFERENCE: (
        "MULTIMODAL_VIDEO_GENERATION_ENABLED",
        False,
        "视频生成参考图当前未启用。",
    ),
    FileInputKind.VISUAL_ANALYSIS: (
        "CHAT_ONE_SHOT_VISION_ENABLED",
        False,
        "Chat 一次性视觉/OCR 当前未启用。",
    ),
}

_PURPOSE_RUNTIME_GATES: dict[FilePurpose, tuple[str, bool, str]] = {
    FilePurpose.WORKFLOW: (
        "WORKFLOW_FILE_ASSETS_ENABLED",
        False,
        "Workflow 文件资产变量当前未启用。",
    ),
}


class FileFormatRegistry:
    """Immutable registry of canonical formats and surface-specific limits."""

    def __init__(
        self,
        formats: Iterable[FileFormatCapability],
        policies: Iterable[FileInputPolicy],
        *,
        version: str = FILE_FORMAT_REGISTRY_VERSION,
    ) -> None:
        format_items = tuple(formats)
        format_map = {item.format_id: item for item in format_items}
        if len(format_map) != len(format_items):
            raise ValueError("File format IDs must be unique.")

        policy_items = tuple(policies)
        policy_map = {(item.purpose, item.input_kind): item for item in policy_items}
        if len(policy_map) != len(policy_items):
            raise ValueError("Purpose and input-kind pairs must be unique.")

        for policy in policy_items:
            missing = set(policy.format_ids) - set(format_map)
            if missing:
                raise ValueError(
                    f"Unknown formats for {policy.purpose.value}/{policy.input_kind.value}: "
                    f"{sorted(missing)}"
                )
            extensions = [
                extension
                for format_id in policy.format_ids
                for extension in format_map[format_id].extensions
            ]
            if len(extensions) != len(set(extensions)):
                raise ValueError(
                    f"Ambiguous extensions for {policy.purpose.value}/"
                    f"{policy.input_kind.value}."
                )

        clean_version = str(version or "").strip().lower()
        if not clean_version:
            raise ValueError("A registry version is required.")
        self.version = clean_version
        self._formats: Mapping[str, FileFormatCapability] = MappingProxyType(format_map)
        self._policies: Mapping[
            tuple[FilePurpose, FileInputKind], FileInputPolicy
        ] = MappingProxyType(policy_map)

    def formats_for(self, policy: FileInputPolicy) -> tuple[FileFormatCapability, ...]:
        return tuple(self._formats[item] for item in policy.format_ids)

    def policies_for(
        self, purpose: FilePurpose | None = None
    ) -> tuple[FileInputPolicy, ...]:
        policies = (
            item
            for item in self._policies.values()
            if purpose is None or item.purpose == purpose
        )
        return tuple(sorted(policies, key=lambda item: (item.purpose.value, item.input_kind.value)))

    def by_extension(
        self,
        purpose: FilePurpose,
        input_kind: FileInputKind,
        filename_or_extension: str,
        *,
        ready_only: bool = True,
    ) -> FileFormatCapability | None:
        policy = self._policies.get((purpose, input_kind))
        extension = self._extension(filename_or_extension)
        if policy is None or not extension:
            return None
        return next(
            (
                item
                for item in self.formats_for(policy)
                if extension in item.extensions
                and (
                    not ready_only
                    or item.interaction_status == FileInteractionStatus.READY
                )
            ),
            None,
        )

    def extensions_for(
        self,
        purpose: FilePurpose | str,
        input_kind: FileInputKind | str,
        *,
        ready_only: bool = True,
    ) -> tuple[str, ...]:
        """Return a sorted allow-list and fail closed for unknown or unready inputs."""

        try:
            key = (
                purpose if isinstance(purpose, FilePurpose) else FilePurpose(purpose),
                input_kind
                if isinstance(input_kind, FileInputKind)
                else FileInputKind(input_kind),
            )
        except ValueError:
            return ()
        policy = self._policies.get(key)
        if policy is None:
            return ()
        interaction_status, _status_reason = self._effective_interaction(policy)
        if ready_only and interaction_status != FileInteractionStatus.READY:
            return ()
        return tuple(
            sorted(
                {
                    extension
                    for item in self.formats_for(policy)
                    if (
                        not ready_only
                        or item.interaction_status == FileInteractionStatus.READY
                    )
                    for extension in item.extensions
                }
            )
        )

    def capabilities_response(
        self,
        *,
        purpose: FilePurpose | None = None,
        model_id: str | None = None,
        verified_native_pdf: bool | None = None,
    ) -> FileCapabilitiesResponse:
        capabilities = []
        for policy in self.policies_for(purpose):
            formats = self.formats_for(policy)
            interaction_status, status_reason = self._effective_interaction(policy)
            capabilities.append(
                FileInputCapability(
                    purpose=policy.purpose,
                    input_kind=policy.input_kind,
                    families=tuple(
                        sorted({item.family for item in formats}, key=lambda item: item.value)
                    ),
                    max_bytes_per_file=policy.max_bytes_per_file,
                    max_files_per_request=policy.max_files_per_request,
                    max_total_bytes_per_request=policy.max_total_bytes_per_request,
                    size_measure=policy.size_measure,
                    transport=policy.transport,
                    retention=policy.retention,
                    support_level=policy.support_level,
                    interaction_status=interaction_status,
                    parser_id=policy.parser_id,
                    ui_entrypoint=policy.ui_entrypoint,
                    status_reason=status_reason,
                    handling_options=self._handling_options(
                        policy,
                        interaction_status=interaction_status,
                        verified_native_pdf=(
                            verified_native_pdf
                            if model_id is not None
                            else None
                        ),
                    ),
                    analysis_options=self._analysis_options(
                        policy,
                        interaction_status=interaction_status,
                    ),
                    formats=formats,
                )
            )
        return FileCapabilitiesResponse(
            registry_version=self.version,
            requested_purpose=purpose,
            requested_model_id=model_id,
            model_specific=(
                model_id is not None and verified_native_pdf is not None
            ),
            capabilities=tuple(capabilities),
        )

    def _handling_options(
        self,
        policy: FileInputPolicy,
        *,
        interaction_status: FileInteractionStatus,
        verified_native_pdf: bool | None,
    ) -> tuple[FileHandlingOption, ...]:
        if not (
            policy.purpose == FilePurpose.CHAT
            and policy.input_kind == FileInputKind.DOCUMENT
            and interaction_status == FileInteractionStatus.READY
        ):
            return ()
        ready_format_ids = tuple(
            format_id
            for format_id in policy.format_ids
            if (format_item := self._formats.get(format_id)) is not None
            and format_item.interaction_status == FileInteractionStatus.READY
        )
        options = [
            FileHandlingOption(
                handling=FileHandling.EXTRACT,
                format_ids=ready_format_ids,
                support_level=FileSupportLevel.CONVERTED,
                interaction_status=FileInteractionStatus.READY,
            )
        ]
        if verified_native_pdf is True:
            options.append(
                FileHandlingOption(
                    handling=FileHandling.NATIVE,
                    format_ids=("pdf",),
                    support_level=FileSupportLevel.NATIVE,
                    interaction_status=FileInteractionStatus.READY,
                )
            )
        return tuple(options)

    def _analysis_options(
        self,
        policy: FileInputPolicy,
        *,
        interaction_status: FileInteractionStatus,
    ) -> tuple[FileAnalysisOption, ...]:
        if not (
            policy.purpose == FilePurpose.CHAT
            and policy.input_kind == FileInputKind.VISUAL_ANALYSIS
        ):
            return ()
        vision_enabled = self._env_enabled("CHAT_ONE_SHOT_VISION_ENABLED", False)
        ocr_enabled = self._env_enabled("CHAT_OPENROUTER_OCR_ENABLED", False)
        vision_verified = file_analysis_mode_canary_verified(FileAnalysisMode.VISION)
        ocr_verified = file_analysis_mode_canary_verified(
            FileAnalysisMode.PROVIDER_OCR
        )
        vision_ready = (
            interaction_status == FileInteractionStatus.READY
            and vision_enabled
            and vision_verified
        )
        ocr_ready = (
            interaction_status == FileInteractionStatus.READY
            and ocr_enabled
            and ocr_verified
        )
        return (
            FileAnalysisOption(
                mode=FileAnalysisMode.VISION,
                format_ids=("jpeg", "pdf", "png", "webp"),
                provider="explicit_openai_compatible_vlm",
                paid=False,
                max_pages=20,
                max_prompt_chars=2_000,
                interaction_status=(
                    FileInteractionStatus.READY
                    if vision_ready
                    else (
                        FileInteractionStatus.PLANNED
                        if vision_enabled and not vision_verified
                        else FileInteractionStatus.DISABLED
                    )
                ),
                status_reason=(
                    None
                    if vision_ready
                    else (
                        "Chat 一次性视觉理解真实金丝雀尚未通过。"
                        if vision_enabled and not vision_verified
                        else "Chat 一次性视觉理解当前未启用。"
                    )
                ),
            ),
            FileAnalysisOption(
                mode=FileAnalysisMode.PROVIDER_OCR,
                format_ids=("pdf",),
                provider="openrouter_mistral_ocr",
                paid=True,
                max_pages=20,
                max_prompt_chars=2_000,
                interaction_status=(
                    FileInteractionStatus.READY
                    if ocr_ready
                    else (
                        FileInteractionStatus.PLANNED
                        if ocr_enabled and not ocr_verified
                        else FileInteractionStatus.DISABLED
                    )
                ),
                status_reason=(
                    None
                    if ocr_ready
                    else (
                        "OpenRouter 供应商 OCR 修复后真实金丝雀尚未通过。"
                        if ocr_enabled and not ocr_verified
                        else "OpenRouter 供应商 OCR 当前未启用。"
                    )
                ),
            ),
        )

    def _effective_interaction(
        self,
        policy: FileInputPolicy,
    ) -> tuple[FileInteractionStatus, str | None]:
        if (
            policy.purpose == FilePurpose.CHAT
            and policy.input_kind == FileInputKind.VISUAL_ANALYSIS
        ):
            vision_enabled = self._env_enabled(
                "CHAT_ONE_SHOT_VISION_ENABLED", False
            )
            ocr_enabled = self._env_enabled(
                "CHAT_OPENROUTER_OCR_ENABLED", False
            )
            if not (vision_enabled or ocr_enabled):
                return (
                    FileInteractionStatus.DISABLED,
                    "Chat 一次性视觉理解与供应商 OCR 当前均未启用。",
                )
            if not (
                vision_enabled
                and file_analysis_mode_canary_verified(FileAnalysisMode.VISION)
            ) and not (
                ocr_enabled
                and file_analysis_mode_canary_verified(
                    FileAnalysisMode.PROVIDER_OCR
                )
            ):
                return (
                    FileInteractionStatus.PLANNED,
                    "已启用的分析方式尚未通过对应的真实金丝雀。",
                )
            return policy.interaction_status, policy.status_reason
        gate = (
            _CHAT_RUNTIME_GATES.get(policy.input_kind)
            if policy.purpose == FilePurpose.CHAT
            else None
        )
        if gate is not None and not self._env_enabled(gate[0], gate[1]):
            return FileInteractionStatus.DISABLED, gate[2]
        purpose_gate = _PURPOSE_RUNTIME_GATES.get(policy.purpose)
        if purpose_gate is not None and not self._env_enabled(
            purpose_gate[0], purpose_gate[1]
        ):
            return FileInteractionStatus.DISABLED, purpose_gate[2]
        if (
            policy.input_kind
            in {FileInputKind.DOCUMENT, FileInputKind.VISUAL_ANALYSIS}
            and policy.purpose in {FilePurpose.CHAT, FilePurpose.WORKFLOW}
            and os.getenv("FILE_ASSET_STORE_MODE", "legacy").strip().lower()
            not in {"shadow", "native"}
        ):
            return (
                FileInteractionStatus.DISABLED,
                "统一文件资产服务当前未启用，请先在设置中启用文件能力。",
            )
        return policy.interaction_status, policy.status_reason

    @staticmethod
    def _env_enabled(name: str, default: bool) -> bool:
        fallback = "true" if default else "false"
        return os.getenv(name, fallback).strip().lower() in {
            "1",
            "true",
            "yes",
            "on",
        }

    @staticmethod
    def _extension(filename_or_extension: str) -> str:
        clean = str(filename_or_extension or "").strip().lower()
        if clean.startswith(".") and clean.count(".") == 1:
            return clean
        return PurePath(clean).suffix


def _fmt(
    format_id: str,
    family: FileFamily,
    extensions: tuple[str, ...],
    media_types: tuple[str, ...],
    *,
    parser_id: str | None = None,
    interaction_status: FileInteractionStatus = FileInteractionStatus.READY,
    status_reason: str | None = None,
) -> FileFormatCapability:
    return FileFormatCapability(
        format_id=format_id,
        family=family,
        extensions=extensions,
        media_types=media_types,
        parser_id=parser_id,
        interaction_status=interaction_status,
        status_reason=status_reason,
    )


_FORMATS = (
    _fmt("plain_text", FileFamily.DOCUMENT, ("txt",), ("text/plain",)),
    _fmt("markdown", FileFamily.DOCUMENT, ("md", "markdown"), ("text/markdown", "text/plain")),
    _fmt("pdf", FileFamily.DOCUMENT, ("pdf",), ("application/pdf",)),
    _fmt("jpeg", FileFamily.IMAGE, ("jpg", "jpeg"), ("image/jpeg", "image/jpg")),
    _fmt("png", FileFamily.IMAGE, ("png",), ("image/png",)),
    _fmt("gif", FileFamily.IMAGE, ("gif",), ("image/gif",)),
    _fmt("webp", FileFamily.IMAGE, ("webp",), ("image/webp",)),
    _fmt("csv", FileFamily.DATASET, ("csv",), ("text/csv",)),
    _fmt(
        "tsv",
        FileFamily.DATASET,
        ("tsv",),
        ("text/tab-separated-values", "text/tsv", "text/plain"),
    ),
    _fmt("json", FileFamily.DOCUMENT, ("json",), ("application/json", "text/json")),
    _fmt(
        "jsonl",
        FileFamily.DOCUMENT,
        ("jsonl", "ndjson"),
        ("application/x-ndjson", "application/jsonl", "application/json", "text/plain"),
    ),
    _fmt(
        "yaml",
        FileFamily.DOCUMENT,
        ("yaml", "yml"),
        ("application/yaml", "application/x-yaml", "text/yaml", "text/x-yaml", "text/plain"),
    ),
    _fmt("xml", FileFamily.DOCUMENT, ("xml",), ("application/xml", "text/xml")),
    _fmt("html", FileFamily.DOCUMENT, ("html", "htm"), ("text/html",)),
    _fmt("srt", FileFamily.DOCUMENT, ("srt",), ("application/x-subrip", "text/srt", "text/plain")),
    _fmt("vtt", FileFamily.DOCUMENT, ("vtt",), ("text/vtt", "text/plain")),
    _fmt(
        "source_code",
        FileFamily.DOCUMENT,
        (
            "py", "js", "jsx", "ts", "tsx", "java", "go", "rs", "c", "h",
            "cpp", "hpp", "cs", "php", "rb", "sh", "ps1", "sql", "css", "scss",
        ),
        (
            "text/plain", "text/x-python", "text/javascript", "application/javascript",
            "text/typescript", "application/typescript", "text/css", "text/x-java-source",
            "text/x-c", "text/x-c++src", "text/x-csharp", "application/sql",
            "text/x-shellscript", "application/x-powershell",
        ),
    ),
    _fmt(
        "configuration",
        FileFamily.DOCUMENT,
        ("toml", "ini", "cfg", "conf"),
        ("text/plain", "application/toml", "text/x-toml", "text/x-ini"),
    ),
    _fmt("log", FileFamily.DOCUMENT, ("log",), ("text/plain",)),
    _fmt(
        "docx",
        FileFamily.DOCUMENT,
        ("docx",),
        (
            "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        ),
        parser_id="office-parser-mcp.extract_office_document",
    ),
    _fmt(
        "pptx",
        FileFamily.DOCUMENT,
        ("pptx",),
        (
            "application/vnd.openxmlformats-officedocument.presentationml.presentation",
        ),
        parser_id="office-parser-mcp.extract_office_document",
    ),
    _fmt(
        "xlsx",
        FileFamily.DATASET,
        ("xlsx",),
        ("application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",),
    ),
    _fmt("parquet", FileFamily.DATASET, ("parquet",), ("application/vnd.apache.parquet",)),
    _fmt("wav", FileFamily.AUDIO, ("wav",), ("audio/wav", "audio/x-wav")),
    _fmt("mp3", FileFamily.AUDIO, ("mp3",), ("audio/mpeg", "audio/mp3")),
    _fmt("flac", FileFamily.AUDIO, ("flac",), ("audio/flac", "audio/x-flac")),
    _fmt("m4a", FileFamily.AUDIO, ("m4a",), ("audio/mp4", "audio/x-m4a", "video/mp4")),
    _fmt("ogg", FileFamily.AUDIO, ("ogg",), ("application/ogg", "audio/ogg")),
    _fmt("audio_webm", FileFamily.AUDIO, ("webm",), ("audio/webm", "video/webm")),
    _fmt("aac", FileFamily.AUDIO, ("aac",), ("audio/aac", "audio/x-aac")),
    _fmt("mp4", FileFamily.VIDEO, ("mp4",), ("video/mp4",)),
    _fmt("mpeg", FileFamily.VIDEO, ("mpeg", "mpg"), ("video/mpeg",)),
    _fmt("mov", FileFamily.VIDEO, ("mov",), ("video/mov", "video/quicktime")),
    _fmt("video_webm", FileFamily.VIDEO, ("webm",), ("video/webm",)),
)

_DOCUMENTS = (
    "plain_text",
    "markdown",
    "pdf",
    "csv",
    "tsv",
    "json",
    "jsonl",
    "yaml",
    "xml",
    "html",
    "srt",
    "vtt",
    "source_code",
    "configuration",
    "log",
)
_OFFICE_DOCUMENTS = ("docx", "pptx")
_SEMANTIC_DOCUMENTS = (*_DOCUMENTS, "xlsx", *_OFFICE_DOCUMENTS)
_IMAGES = ("jpeg", "png", "webp")
_AUDIO = ("wav", "mp3", "flac", "m4a", "ogg", "audio_webm", "aac")
_VIDEO = ("mp4", "mpeg", "mov", "video_webm")


def _policy(
    purpose: FilePurpose,
    input_kind: FileInputKind,
    format_ids: tuple[str, ...],
    max_mib: int,
    support_level: FileSupportLevel,
    interaction_status: FileInteractionStatus,
    parser_id: str | None,
    ui_entrypoint: str | None,
    status_reason: str | None = None,
    **kwargs: object,
) -> FileInputPolicy:
    return FileInputPolicy(
        purpose=purpose,
        input_kind=input_kind,
        format_ids=format_ids,
        max_bytes_per_file=max_mib * MIB,
        support_level=support_level,
        interaction_status=interaction_status,
        parser_id=parser_id,
        ui_entrypoint=ui_entrypoint,
        status_reason=status_reason,
        **kwargs,
    )


_POLICIES = (
    _policy(
        FilePurpose.CHAT,
        FileInputKind.DOCUMENT,
        _SEMANTIC_DOCUMENTS,
        10,
        FileSupportLevel.CONVERTED,
        FileInteractionStatus.READY,
        "chat.local_document_parser",
        "/chat/:modelId",
        retention=FileRetention.TEMPORARY,
        max_files_per_request=5,
        max_total_bytes_per_request=25 * MIB,
    ),
    _policy(
        FilePurpose.CHAT,
        FileInputKind.VISUAL_ANALYSIS,
        ("pdf", "jpeg", "png", "webp"),
        10,
        FileSupportLevel.SPECIALIZED,
        FileInteractionStatus.READY,
        "chat.one_shot_visual_analysis",
        "/chat/:modelId",
        retention=FileRetention.TEMPORARY,
        max_files_per_request=1,
    ),
    _policy(
        FilePurpose.CHAT,
        FileInputKind.IMAGE,
        ("jpeg", "png", "gif", "webp"),
        5,
        FileSupportLevel.NATIVE,
        FileInteractionStatus.READY,
        "chat.image_data_url",
        "/chat/:modelId",
        max_files_per_request=None,
        size_measure=FileSizeMeasure.ENCODED_PAYLOAD,
        transport=FileTransport.DATA_URL,
    ),
    _policy(
        FilePurpose.CHAT, FileInputKind.AUDIO, _AUDIO, 25,
        FileSupportLevel.CONVERTED, FileInteractionStatus.READY,
        "multimodal.chat_audio_attachment", "/chat/:modelId",
        retention=FileRetention.TEMPORARY,
    ),
    _policy(
        FilePurpose.CHAT, FileInputKind.VIDEO, _VIDEO, 20,
        FileSupportLevel.SPECIALIZED, FileInteractionStatus.READY,
        "multimodal.chat_video_attachment", "/chat/:modelId",
        retention=FileRetention.TEMPORARY,
    ),
    _policy(
        FilePurpose.CHAT, FileInputKind.IMAGE_REFERENCE, _IMAGES, 10,
        FileSupportLevel.SPECIALIZED, FileInteractionStatus.READY,
        "multimodal.image_generation_reference", "/chat/:modelId",
        max_files_per_request=10,
    ),
    _policy(
        FilePurpose.CHAT, FileInputKind.AUDIO_GENERATION_IMAGE, _IMAGES, 10,
        FileSupportLevel.SPECIALIZED, FileInteractionStatus.READY,
        "multimodal.audio_generation_image", "/chat/:modelId",
    ),
    _policy(
        FilePurpose.CHAT, FileInputKind.VIDEO_GENERATION_FRAME, _IMAGES, 10,
        FileSupportLevel.SPECIALIZED, FileInteractionStatus.READY,
        "multimodal.video_generation_frame", "/chat/:modelId",
        max_files_per_request=2,
    ),
    _policy(
        FilePurpose.CHAT,
        FileInputKind.VIDEO_GENERATION_REFERENCE,
        _IMAGES,
        10,
        FileSupportLevel.SPECIALIZED,
        FileInteractionStatus.READY,
        "multimodal.video_generation_reference",
        "/chat/:modelId",
        max_files_per_request=3,
        max_total_bytes_per_request=30 * MIB,
    ),
    _policy(
        FilePurpose.RAG,
        FileInputKind.DOCUMENT,
        _SEMANTIC_DOCUMENTS,
        10,
        FileSupportLevel.CONVERTED,
        FileInteractionStatus.READY,
        "rag.document_parser",
        "/rag",
        retention=FileRetention.PERSISTENT,
    ),
    _policy(
        FilePurpose.RAG,
        FileInputKind.IMAGE,
        _IMAGES,
        10,
        FileSupportLevel.SPECIALIZED,
        FileInteractionStatus.READY,
        "rag.vision_processor",
        "/rag",
        retention=FileRetention.PERSISTENT,
    ),
    _policy(
        FilePurpose.DATAX,
        FileInputKind.DATA_SOURCE,
        ("csv", "xlsx", "parquet"),
        50,
        FileSupportLevel.SPECIALIZED,
        FileInteractionStatus.READY,
        "datax.source_importer",
        "/datax",
        retention=FileRetention.PERSISTENT,
    ),
    _policy(
        FilePurpose.AGENT,
        FileInputKind.DOCUMENT,
        _DOCUMENTS,
        10,
        FileSupportLevel.CONVERTED,
        FileInteractionStatus.READY,
        "xperts.context_file_parser",
        "/agents",
        retention=FileRetention.PERSISTENT,
    ),
    _policy(
        FilePurpose.WORKFLOW,
        FileInputKind.DOCUMENT,
        _SEMANTIC_DOCUMENTS,
        10,
        FileSupportLevel.CONVERTED,
        FileInteractionStatus.READY,
        "workflow.document_extractor",
        "/workflow",
        retention=FileRetention.PERSISTENT,
    ),
    _policy(
        FilePurpose.WORKFLOW,
        FileInputKind.VISUAL_ANALYSIS,
        ("pdf", "jpeg", "png", "webp"),
        10,
        FileSupportLevel.SPECIALIZED,
        FileInteractionStatus.READY,
        "workflow.vision_understanding",
        "/workflow",
        retention=FileRetention.PERSISTENT,
        max_files_per_request=1,
    ),
)

_DEFAULT_REGISTRY = FileFormatRegistry(_FORMATS, _POLICIES)


def get_file_format_registry() -> FileFormatRegistry:
    return _DEFAULT_REGISTRY

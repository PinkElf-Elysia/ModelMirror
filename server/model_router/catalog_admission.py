"""Request-local, read-only Managed admission projection for Catalog.

Operation names group evidence, not execution protocols. A qualified Xpert or
Batch binding cannot make default Chat qualified: every fact keeps its entry
and shape. No service snapshot is retained across requests.
"""
from __future__ import annotations

from .chat_control import ProviderChatControlService
from .schemas import ManagedEntryReadiness
from .service import ModelRouterService
from .workload_control import ProviderWorkloadControlService


SHAPE_OPERATION = {
    "chat_text": "chat",
    "chat_tools": "chat",
    "chat_file_output": "chat",
    "chat_text_unary": "chat",
    "chat_json_object": "chat",
    "fusion_native": "chat",
    "embedding_vectors": "embed",
    "rerank_documents": "rerank",
    "openrouter_batch_chat": "chat",
    "openrouter_batch_embeddings": "embed",
    "chat_image_stream": "analyze_image",
    "chat_document_stream": "analyze_document",
    "vision_json_unary": "analyze_image",
    "image_generation": "generate_image",
    "audio_transcription": "transcribe",
    "audio_speech": "synthesize_speech",
    "chat_audio_input": "analyze_audio",
    "chat_audio_output": "chat",
    "audio_generation_stream": "generate_audio",
    "video_analysis_unary": "analyze_video",
    "chat_video_stream": "analyze_video",
    "video_generation_async": "generate_video",
    "realtime_voice_session": "realtime_voice",
}

# Only exact user entry mappings can suppress an old specialized snapshot.
# Other entry qualifications must not stand in for these dedicated workspaces.
SPECIALIZED_ENTRY = {
    "generate_image": ("image_generation", "image_generation"),
    "transcribe": ("multimodal_transcription", "audio_transcription"),
    "synthesize_speech": ("multimodal_speech", "audio_speech"),
    "generate_audio": ("audio_generation", "audio_generation_stream"),
    "analyze_audio": ("chat_audio_input", "chat_audio_input"),
    "realtime_voice": ("realtime_voice", "realtime_voice_session"),
    "analyze_video": ("multimodal_video_analysis", "video_analysis_unary"),
    "generate_video": ("video_generation", "video_generation_async"),
}


class CatalogAdmissionSnapshot:
    def __init__(self, router: ModelRouterService) -> None:
        self.chat = ProviderChatControlService(router)
        self.chat_policy = self.chat.peek_policy()
        self.workload = ProviderWorkloadControlService(router)
        self.policies = {item.entry_id: item for item in self.workload.policies().policies}

    def entries(self, connection_id: str, model_id: str, operation: str) -> list[ManagedEntryReadiness]:
        result = []
        if operation == "chat":
            intercepted, gate_reason = self.chat.route_admission(self.chat_policy, model_id)
            for capability in ("chat_text", "chat_tools", "chat_file_output"):
                if connection_id not in self.chat.route_candidates(self.chat_policy, capability):
                    continue
                certification, reason = self.chat.route_qualification(
                    self.chat_policy, connection_id=connection_id, model_id=model_id,
                    capability=capability,
                )
                available = intercepted and gate_reason is None and certification is not None
                result.append(ManagedEntryReadiness(
                    entry_id="chat_default", execution_shape=capability,
                    available=available, reason_code=gate_reason or reason,
                ))
        for policy in self.policies.values():
            for binding in policy.bindings:
                if (binding.connection_id != connection_id or binding.model_id != model_id
                        or SHAPE_OPERATION.get(binding.execution_shape) != operation):
                    continue
                admitted, reason = self.workload.admission_binding(policy, model_id, binding.execution_shape)
                result.append(ManagedEntryReadiness(
                    entry_id=policy.entry_id, execution_shape=binding.execution_shape,
                    available=admitted is not None, reason_code=reason,
                ))
        return result

    def blocks_legacy_snapshot(self, model_id: str, operation: str, *, specialized: bool) -> str | None:
        if not specialized and operation == "chat":
            _, blocks, reason = self.chat.managed_readiness(self.chat_policy, model_id, "chat_text")
            return reason if blocks else None
        if specialized and operation in SPECIALIZED_ENTRY:
            entry, shape = SPECIALIZED_ENTRY[operation]
            policy = self.policies[entry]
            if policy.feature_enabled and policy.configured_status != "legacy":
                binding, reason = self.workload.admission_binding(policy, model_id, shape)
                return reason if binding is None else None
        return None

    def revision_material(self) -> dict:
        # Only the hash leaves the service. Include derived validity so expiry,
        # feature flags and gate degradation invalidate pagination as well.
        return {
            "chat": self.chat_policy.model_dump(mode="json"),
            "required_allowed": self.chat.required_runtime_allowed(self.chat_policy),
            "workload": [policy.model_dump(mode="json") for policy in self.policies.values()],
        }

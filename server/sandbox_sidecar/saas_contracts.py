"""Fixed executable contracts for the Wave 6 stateful SaaS adapters.

Only this private sidecar module knows provider hosts, credential slots and
tool effects.  The catalog may display localized metadata, but it cannot add
hosts, headers, tools or executable configuration at runtime.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Final


MAX_ARGUMENT_BYTES: Final = 256 * 1024
MAX_OUTPUT_BYTES: Final = 256 * 1024
IDEMPOTENCY_KEY_PATTERN: Final = re.compile(r"^mcpidem_[0-9a-f]{32}$")


@dataclass(frozen=True, slots=True)
class SaaSToolContract:
    effect: str


@dataclass(frozen=True, slots=True)
class SaaSAdapterContract:
    host: str
    credential_fields: tuple[str, ...]
    setting_fields: tuple[str, ...]
    tools: dict[str, SaaSToolContract]
    minimum_interval_seconds: float

    @property
    def read_tools(self) -> frozenset[str]:
        return frozenset(name for name, policy in self.tools.items() if policy.effect == "read")

    @property
    def write_tools(self) -> frozenset[str]:
        return frozenset(
            name for name, policy in self.tools.items() if policy.effect == "state-write"
        )


def _tools(*, read: tuple[str, ...], write: tuple[str, ...]) -> dict[str, SaaSToolContract]:
    return {
        **{name: SaaSToolContract("read") for name in read},
        **{name: SaaSToolContract("state-write") for name in write},
    }


SAAS_ADAPTERS: Final[dict[str, SaaSAdapterContract]] = {
    "airtable-mcp": SaaSAdapterContract(
        host="api.airtable.com",
        credential_fields=("personal_access_token",),
        setting_fields=("base_id",),
        tools=_tools(
            read=("list_tables", "list_records", "get_record"),
            write=("create_record", "update_record"),
        ),
        # Airtable documents 5 requests/second per base.  Leave headroom.
        minimum_interval_seconds=0.25,
    ),
    "asana-mcp": SaaSAdapterContract(
        host="app.asana.com",
        credential_fields=("personal_access_token",),
        setting_fields=("workspace_gid", "project_gid"),
        tools=_tools(
            read=("list_projects", "list_tasks", "get_task"),
            write=("create_task", "update_task", "add_comment"),
        ),
        # Free domains receive 150 requests/minute.  Cap at 120/minute.
        minimum_interval_seconds=0.5,
    ),
    "gitlab-mcp": SaaSAdapterContract(
        host="gitlab.com",
        credential_fields=("personal_access_token",),
        setting_fields=("project_id",),
        tools=_tools(
            read=(
                "list_issues",
                "get_issue",
                "list_merge_requests",
                "get_merge_request",
                "get_repository_file",
            ),
            write=("create_issue", "update_issue", "add_issue_note"),
        ),
        # GitLab.com currently allows much more, but five/second is sufficient.
        minimum_interval_seconds=0.2,
    ),
    "notion-mcp-server": SaaSAdapterContract(
        host="api.notion.com",
        credential_fields=("integration_token",),
        setting_fields=("data_source_id",),
        tools=_tools(
            read=("query_data_source", "retrieve_page"),
            write=("create_page", "update_page_properties"),
        ),
        # Notion documents an average of three requests/second per integration.
        minimum_interval_seconds=0.4,
    ),
}


# Filled from offline tools/list discovery.  A mismatch blocks image release.
SAAS_SCHEMA_SHA256: Final[dict[str, str]] = {
    "airtable-mcp": "5fce8249d6fcfa6b57f17d6c4d996c0c1ac5b8299584547923c2f18b14ca86c4",
    "asana-mcp": "c935b8d982352d5e8379fe32a84986a4dd45c7d8635a6bb95838d26680f37d86",
    "gitlab-mcp": "c5525a94bbe3dd3c4f83381f6138375243000102d31b28360641acc3cddb6dd9",
    "notion-mcp-server": "4c5a2edc829d7d8823dd6e2270d52c6bc8bc67f9a4cba55b6edc59be4a70da80",
}


def _credential(value: object) -> str:
    if not isinstance(value, str) or not value or len(value) > 20_000:
        raise ValueError("invalid_credential")
    if any(character in value for character in ("\x00", "\r", "\n")):
        raise ValueError("invalid_credential")
    return value


def _opaque_gid(value: object, *, name: str) -> str:
    clean = str(value or "").strip()
    if not re.fullmatch(r"[1-9][0-9]{0,31}", clean):
        raise ValueError(f"invalid_{name}")
    return clean


def validate_configuration(
    adapter_id: str,
    configuration: object,
) -> tuple[SaaSAdapterContract, dict[str, str], dict[str, str]]:
    """Return a normalized fixed configuration or fail closed."""

    contract = SAAS_ADAPTERS.get(adapter_id)
    if contract is None:
        raise ValueError("mcp_adapter_denied")
    if not isinstance(configuration, dict):
        raise ValueError("invalid_configuration")
    raw_credentials = configuration.get("credentials")
    raw_settings = configuration.get("settings")
    if not isinstance(raw_credentials, dict) or not isinstance(raw_settings, dict):
        raise ValueError("invalid_configuration")
    if set(raw_credentials) != set(contract.credential_fields):
        raise ValueError("configuration_contract_mismatch")
    if set(raw_settings) != set(contract.setting_fields):
        raise ValueError("configuration_contract_mismatch")

    credentials = {name: _credential(raw_credentials.get(name)) for name in contract.credential_fields}
    settings: dict[str, str]
    if adapter_id == "airtable-mcp":
        base_id = str(raw_settings.get("base_id") or "").strip()
        if not re.fullmatch(r"app[A-Za-z0-9]{14}", base_id):
            raise ValueError("invalid_base_id")
        settings = {"base_id": base_id}
    elif adapter_id == "asana-mcp":
        settings = {
            "workspace_gid": _opaque_gid(raw_settings.get("workspace_gid"), name="workspace_gid"),
            "project_gid": _opaque_gid(raw_settings.get("project_gid"), name="project_gid"),
        }
    elif adapter_id == "gitlab-mcp":
        settings = {"project_id": _opaque_gid(raw_settings.get("project_id"), name="project_id")}
    else:
        data_source_id = str(raw_settings.get("data_source_id") or "").strip().replace("-", "")
        if not re.fullmatch(r"[0-9a-fA-F]{32}", data_source_id):
            raise ValueError("invalid_data_source_id")
        settings = {"data_source_id": data_source_id.lower()}
    return contract, credentials, settings


def validate_idempotency_key(value: object) -> str:
    clean = str(value or "").strip()
    if not IDEMPOTENCY_KEY_PATTERN.fullmatch(clean):
        raise ValueError("invalid_idempotency_key")
    return clean

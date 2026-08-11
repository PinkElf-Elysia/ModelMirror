"""Private executable contracts for Wave 4 Token adapters.

This module is copied only into the isolated runtime.  Public catalog responses
never expose commands, environment variable names, or service endpoints.
"""

from __future__ import annotations

import re
from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class TokenAdapterContract:
    command: tuple[str, ...]
    tools: frozenset[str]
    credential_environment: tuple[tuple[str, str], ...]
    setting_environment: tuple[tuple[str, str], ...] = ()
    allowed_hosts: frozenset[str] = frozenset()
    builtin: bool = False


BIN = "/opt/modelmirror/node_modules/.bin"


TOKEN_ADAPTERS: dict[str, TokenAdapterContract] = {
    "agentql-mcp": TokenAdapterContract(
        (f"{BIN}/agentql-mcp",), frozenset({"extract-web-data"}),
        (("api_key", "AGENTQL_API_KEY"),),
        allowed_hosts=frozenset({"api.agentql.com"}),
    ),
    "brave-search-mcp": TokenAdapterContract(
        (f"{BIN}/mcp-server-brave-search",),
        frozenset({"brave_web_search", "brave_local_search"}),
        (("api_key", "BRAVE_API_KEY"),),
        allowed_hosts=frozenset({"api.search.brave.com"}),
    ),
    "brave-brave-search-mcp-server": TokenAdapterContract(
        (
            "/opt/modelmirror/brave/node_modules/.bin/brave-search-mcp-server",
            "--transport",
            "stdio",
            "--enabled-tools",
            "brave_web_search",
            "brave_local_search",
        ),
        frozenset({"brave_web_search", "brave_local_search"}),
        (("api_key", "BRAVE_API_KEY"),),
        allowed_hosts=frozenset({"api.search.brave.com"}),
    ),
    "blazickjp-arxiv-mcp-server": TokenAdapterContract(
        (
            "python",
            "-m",
            "sandbox_sidecar.token_builtin",
            "blazickjp-arxiv-mcp-server",
        ),
        frozenset({"search_papers", "get_abstract"}),
        (),
        allowed_hosts=frozenset({"export.arxiv.org"}),
        builtin=True,
    ),
    "exa-mcp": TokenAdapterContract(
        (f"{BIN}/exa-mcp-server",),
        frozenset({"web_search_exa", "web_fetch_exa"}),
        (("api_key", "EXA_API_KEY"),),
        allowed_hosts=frozenset({"api.exa.ai"}),
    ),
    "firecrawl-mcp": TokenAdapterContract(
        (f"{BIN}/firecrawl-mcp",),
        frozenset({"firecrawl_search", "firecrawl_scrape", "firecrawl_map"}),
        (("api_key", "FIRECRAWL_API_KEY"),),
        allowed_hosts=frozenset({"api.firecrawl.dev"}),
    ),
    "fatwang2-search1api-mcp": TokenAdapterContract(
        (
            "python",
            "-m",
            "sandbox_sidecar.token_builtin",
            "fatwang2-search1api-mcp",
        ),
        frozenset({"search", "news", "trending"}),
        (("api_key", "SEARCH1API_KEY"),),
        allowed_hosts=frozenset({"api.search1api.com"}),
        builtin=True,
    ),
    "perplexity-mcp": TokenAdapterContract(
        (f"{BIN}/perplexity-mcp",),
        frozenset({"perplexity_search", "perplexity_ask"}),
        (("api_key", "PERPLEXITY_API_KEY"),),
        allowed_hosts=frozenset({"api.perplexity.ai"}),
    ),
    "tavily-mcp": TokenAdapterContract(
        (f"{BIN}/tavily-mcp",),
        frozenset({"tavily_search", "tavily_extract", "tavily_map"}),
        (("api_key", "TAVILY_API_KEY"),),
        allowed_hosts=frozenset({"api.tavily.com"}),
    ),
    "axiom-mcp": TokenAdapterContract(
        ("python", "-m", "sandbox_sidecar.token_builtin", "axiom-mcp"),
        frozenset({"queryApl", "listDatasets", "getDatasetSchema", "getSavedQueries", "getMonitors", "getMonitorsHistory"}),
        (("api_token", "AXIOM_TOKEN"),),
        (("organization_id", "AXIOM_ORG_ID"),),
        frozenset({"api.axiom.co"}), True,
    ),
    "figma-context-mcp": TokenAdapterContract(
        (f"{BIN}/figma-developer-mcp", "--stdio"),
        frozenset({"get_figma_data"}),
        (("api_token", "FIGMA_API_KEY"),),
        allowed_hosts=frozenset({"api.figma.com"}),
    ),
    "google-maps-mcp": TokenAdapterContract(
        (f"{BIN}/mcp-server-google-maps",),
        frozenset({"maps_geocode", "maps_reverse_geocode", "maps_search_places", "maps_distance_matrix", "maps_elevation", "maps_directions"}),
        (("api_key", "GOOGLE_MAPS_API_KEY"),),
        allowed_hosts=frozenset({"maps.googleapis.com"}),
    ),
    "grafana-mcp": TokenAdapterContract(
        ("python", "-m", "sandbox_sidecar.token_builtin", "grafana-mcp"),
        frozenset({"search_dashboards", "get_dashboard_by_uid", "list_datasources", "list_alert_rules"}),
        (("service_token", "GRAFANA_SERVICE_TOKEN"),),
        (("stack_slug", "GRAFANA_STACK_SLUG"),),
        builtin=True,
    ),
    "graphlit-mcp": TokenAdapterContract(
        (f"{BIN}/graphlit-mcp-server",),
        frozenset({"queryProjectUsage", "askGraphlit", "retrieveSources", "queryContents", "queryCollections", "queryFeeds", "queryConversations", "webMap", "webSearch"}),
        (("api_token", "GRAPHLIT_JWT_SECRET"),),
        (("organization_id", "GRAPHLIT_ORGANIZATION_ID"), ("environment_id", "GRAPHLIT_ENVIRONMENT_ID")),
        frozenset({"graphlit-api.azurewebsites.net"}),
    ),
    "kagi-mcp": TokenAdapterContract(
        ("python", "-m", "sandbox_sidecar.token_builtin", "kagi-mcp"),
        frozenset({"kagi_search"}),
        (("api_token", "KAGI_API_TOKEN"),),
        allowed_hosts=frozenset({"kagi.com"}), builtin=True,
    ),
    "kagisearch-kagimcp": TokenAdapterContract(
        (
            "python",
            "-m",
            "sandbox_sidecar.token_builtin",
            "kagisearch-kagimcp",
        ),
        frozenset({"kagi_search_fetch", "kagi_extract"}),
        (("api_key", "KAGI_API_KEY"),),
        allowed_hosts=frozenset({"kagi.com"}),
        builtin=True,
    ),
    "livetennisapi-livetennisapi-mcp": TokenAdapterContract(
        (
            "python",
            "-m",
            "sandbox_sidecar.token_builtin",
            "livetennisapi-livetennisapi-mcp",
        ),
        frozenset(
            {
                "get_live_matches",
                "get_upcoming_matches",
                "get_match_score",
                "search_players",
                "get_player",
                "get_fixtures",
                "search_tournaments",
                "get_tournament",
            }
        ),
        (("api_key", "LIVE_TENNIS_API_KEY"),),
        allowed_hosts=frozenset({"api.livetennisapi.com"}),
        builtin=True,
    ),
    "pinecone-assistant-mcp": TokenAdapterContract(
        ("python", "-m", "sandbox_sidecar.token_builtin", "pinecone-assistant-mcp"),
        frozenset({"assistant_chat"}),
        (("api_key", "PINECONE_API_KEY"),),
        (("assistant_host", "PINECONE_ASSISTANT_HOST"), ("assistant_name", "PINECONE_ASSISTANT_NAME")),
        builtin=True,
    ),
    "shodan-mcp": TokenAdapterContract(
        (f"{BIN}/mcp-shodan",),
        frozenset({"ip_lookup", "shodan_search", "cve_lookup", "dns_lookup", "reverse_dns_lookup", "cpe_lookup", "cves_by_product"}),
        (("api_key", "SHODAN_API_KEY"),),
        allowed_hosts=frozenset({"api.shodan.io", "cvedb.shodan.io"}),
    ),
    "virustotal-mcp": TokenAdapterContract(
        (f"{BIN}/mcp-virustotal",),
        frozenset({"get_url_report", "get_url_relationship", "get_file_report", "get_file_relationship", "get_ip_report", "get_ip_relationship", "get_domain_report", "get_domain_relationship", "search_vt", "get_file_behaviour_summary", "get_collection"}),
        (("api_key", "VIRUSTOTAL_API_KEY"),),
        allowed_hosts=frozenset({"www.virustotal.com"}),
    ),
    "terraform-mcp": TokenAdapterContract(
        ("python", "-m", "sandbox_sidecar.token_builtin", "terraform-mcp"),
        frozenset(
            {
                "get_latest_provider_version",
                "get_provider_capabilities",
                "get_provider_details",
                "search_modules",
                "get_module_details",
                "get_latest_module_version",
            }
        ),
        (),
        allowed_hosts=frozenset({"registry.terraform.io"}),
        builtin=True,
    ),
    "cablate-mcp-google-map": TokenAdapterContract(
        (
            "python",
            "-m",
            "sandbox_sidecar.token_builtin",
            "cablate-mcp-google-map",
        ),
        frozenset({"maps_search_places", "maps_place_details"}),
        (("api_key", "GOOGLE_MAPS_API_KEY"),),
        allowed_hosts=frozenset({"places.googleapis.com"}),
        builtin=True,
    ),
    "vectorize-io-vectorize-mcp-server": TokenAdapterContract(
        (
            "python",
            "-m",
            "sandbox_sidecar.token_builtin",
            "vectorize-io-vectorize-mcp-server",
        ),
        frozenset({"retrieve"}),
        (("api_token", "VECTORIZE_TOKEN"),),
        (
            ("organization_id", "VECTORIZE_ORG_ID"),
            ("pipeline_id", "VECTORIZE_PIPELINE_ID"),
        ),
        frozenset({"api.vectorize.io"}),
        True,
    ),
    "comet-ml-opik-mcp": TokenAdapterContract(
        (
            "python",
            "-m",
            "sandbox_sidecar.token_builtin",
            "comet-ml-opik-mcp",
        ),
        frozenset({"list", "read"}),
        (("api_key", "OPIK_API_KEY"),),
        (("workspace", "OPIK_WORKSPACE"),),
        frozenset({"www.comet.com"}),
        True,
    ),
    "keboola-keboola-mcp-server": TokenAdapterContract(
        (
            "python",
            "-m",
            "sandbox_sidecar.token_builtin",
            "keboola-keboola-mcp-server",
        ),
        frozenset({"get_project_info", "get_buckets", "get_tables"}),
        (("storage_token", "KEBOOLA_STORAGE_TOKEN"),),
        allowed_hosts=frozenset({"connection.keboola.com"}),
        builtin=True,
    ),
}


STAGED_TOKEN_ADAPTERS = frozenset(
    {
        "cablate-mcp-google-map",
        "vectorize-io-vectorize-mcp-server",
        "comet-ml-opik-mcp",
        "keboola-keboola-mcp-server",
    }
)


TOKEN_SCHEMA_SHA256: dict[str, str] = {
    "agentql-mcp": "6582600f94ae76b2ba7814bae19143fb9d6ce2354dcf322b0b7dcaf6fb52d8c1",
    "axiom-mcp": "bb46324da16281e84700d9be6ac7b480e40b9495b0347d01a8385d5b2a5b1df0",
    "brave-search-mcp": "b9506f13f40a0d736150abdbc517ae0f38e3973d86ead03038237584048db6f5",
    "brave-brave-search-mcp-server": "a3091c78dcd4311a815b1659997bba78ffcbf619c8c35671a5a4bd8e6d0f744f",
    "blazickjp-arxiv-mcp-server": "e123a5f4aee83b481b60d11beb3138844f71b10d5fc37bf44f3c5b849399931e",
    "exa-mcp": "9bc6a8a1af9848243f3fd846d000bf034634507db0497d43ea2939d738e255ed",
    "figma-context-mcp": "0614400b753e60083d995b0c7b1a1ef07b87087ed7bf1111aec9e539b67822a5",
    "firecrawl-mcp": "90dda8f14c61acd692c726bd661f20a458e754d1e7c0b552761eca5c2f5e8f97",
    "fatwang2-search1api-mcp": "b6b6c1475f2a655ccd5091bf0e1ccd5243f12ebe70bf82345bacfbdc202c4a9f",
    "google-maps-mcp": "e4da6fe8fe538ca6c2a65cd1b43a58aa8e32f48bd26e6c8536a67605888bcaf0",
    "grafana-mcp": "5af6771c84821bf721206acc2308ac3176c7898537cef3077421a77ac526f3ca",
    "graphlit-mcp": "11ded310a9c4ae7bed48fc64b823e5e42dcca8750b28b5c00bf7e9d1e06dd420",
    "kagi-mcp": "65f5e06ec75c3610c29d031f6c27ac0f71dc4937eda1ce0ebef961141e0e05ec",
    "kagisearch-kagimcp": "116408255d86f1327da05d0e7a05bfa621c250dc43d3a7cf64c8e4c3ad013fc1",
    "livetennisapi-livetennisapi-mcp": "7d97a80289ace9ba92fc78ac15e0f6cc41caffb35420b51d3f0782542c524a49",
    "perplexity-mcp": "04cf72631909fea7c8523ac3c72b69192673a83934a82a72ca19966d2f682acd",
    "pinecone-assistant-mcp": "ac7d3777df219b30af55ced9643a1ce705c5cf7bf484212ba3e7bffd6fb93e2c",
    "shodan-mcp": "5fefb2af3c61d61dc3c5679db0bbdaf095de5fd2fbbee5d1ffc5401bb6cd001e",
    "tavily-mcp": "01ca28e4482a06c12ef88bf26e1ddb96e2aae83ec65c5234365a8555854d7710",
    "virustotal-mcp": "c66291ebfcfb5ccf9cd23608cbfca9760031f3215271448249959927f843c234",
    "terraform-mcp": "73a2b116bcaa257dbf158d1ab8a778d067dac2d969db7dff160372d1617e3445",
    "cablate-mcp-google-map": "186785bce37ec786aa86bfa2b3fdfeb6918633eb309e0000de8d291d7a7650a6",
    "vectorize-io-vectorize-mcp-server": "b04acf174a49c2c123805ce96ea1d220604e80d5dd56c448f03e494572ada993",
    "comet-ml-opik-mcp": "084588762fe49f9cc6be8c82e4e1b6a4eb2fc361cbf9156b792465a49d7d50b9",
    "keboola-keboola-mcp-server": "fc72f9c337b51f7ae45c6bb566256e6a7e163f98635df6bc406f15d11f027f3c",
}


def validate_configuration(
    adapter_id: str,
    configuration: object,
) -> tuple[TokenAdapterContract, dict[str, str], dict[str, str]]:
    contract = TOKEN_ADAPTERS.get(adapter_id)
    if contract is None:
        raise ValueError("mcp_adapter_denied")
    if not isinstance(configuration, dict):
        raise ValueError("invalid_configuration")
    raw_credentials = configuration.get("credentials")
    raw_settings = configuration.get("settings")
    if not isinstance(raw_credentials, dict) or not isinstance(raw_settings, dict):
        raise ValueError("invalid_configuration")
    credential_keys = {item[0] for item in contract.credential_environment}
    setting_keys = {item[0] for item in contract.setting_environment}
    if set(raw_credentials) != credential_keys or set(raw_settings) != setting_keys:
        raise ValueError("configuration_contract_mismatch")
    credentials: dict[str, str] = {}
    for key in credential_keys:
        value = raw_credentials.get(key)
        if not isinstance(value, str) or not value or len(value) > 20_000:
            raise ValueError("invalid_credential")
        credentials[key] = value
    settings: dict[str, str] = {}
    for key in setting_keys:
        value = raw_settings.get(key)
        if not isinstance(value, str) or not value or len(value) > 253:
            raise ValueError("invalid_setting")
        if "://" in value or "/" in value or "@" in value:
            raise ValueError("invalid_setting")
        settings[key] = value
    for key in (
        "organization_id",
        "environment_id",
        "assistant_name",
        "pipeline_id",
    ):
        if key in settings and not re.fullmatch(r"[A-Za-z0-9_-]{1,120}", settings[key]):
            raise ValueError("invalid_setting")
    if "workspace" in settings and not re.fullmatch(
        r"[A-Za-z0-9_.-]{1,120}", settings["workspace"]
    ):
        raise ValueError("invalid_setting")
    if "stack_slug" in settings and not re.fullmatch(
        r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?", settings["stack_slug"]
    ):
        raise ValueError("invalid_setting")
    if "assistant_host" in settings:
        host = settings["assistant_host"].lower().rstrip(".")
        if not host.endswith(".pinecone.io") or host == "pinecone.io":
            raise ValueError("invalid_setting")
        settings["assistant_host"] = host
    return contract, credentials, settings

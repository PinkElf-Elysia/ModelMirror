"""Contract and isolated runtime smoke for Wave 16-17 public adapters."""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import os
import socket
import sys
import threading
import time
from pathlib import Path
from typing import TYPE_CHECKING, Any
from urllib.parse import urlsplit

if TYPE_CHECKING:
    from mcp.client.session import ClientSession
    from mcp.client.stdio import StdioServerParameters

from .public_mcp import ADAPTER_TOOL_NAMES, BUILDERS, PUBLIC_SCHEMA_SHA256


def _load_mcp_stdio() -> tuple[Any, Any, Any]:
    """Load the official SDK only inside isolated runtime smoke paths."""

    from mcp.client.session import ClientSession
    from mcp.client.stdio import StdioServerParameters, stdio_client

    return ClientSession, StdioServerParameters, stdio_client


WAVE16A_ADAPTERS = frozenset(
    {
        "nickclyde-duckduckgo-mcp-server",
        "jpisnice-shadcn-ui-mcp-server",
        "docker-hub-mcp",
    }
)
WAVE16B_ADAPTERS = frozenset(
    {
        "genomoncology-biomcp",
        "safedep-vet",
    }
)
WAVE17A_ADAPTERS = frozenset(
    {
        "aas-ee-open-websearch",
        "mnemox-ai-idea-reality-mcp",
        "idosal-git-mcp",
    }
)
WAVE25A_ADAPTERS = frozenset(
    {
        "coinpaprika-dexpaprika-mcp",
        "pab1it0-chess-mcp",
        "yuna0x0-anilist-mcp",
    }
)
WAVE25B_ADAPTERS = frozenset(
    {
        "karanb192-reddit-mcp-buddy",
        "rishijatia-fantasy-pl-mcp",
    }
)
WAVE29_ADAPTERS = frozenset({"takashiishida-arxiv-latex-mcp"})
PUBLIC_EXPANSION_ADAPTERS = (
    WAVE16A_ADAPTERS
    | WAVE16B_ADAPTERS
    | WAVE17A_ADAPTERS
    | WAVE25A_ADAPTERS
    | WAVE25B_ADAPTERS
    | WAVE29_ADAPTERS
)
BLOCKED_TOOL_PROBES = {
    "nickclyde-duckduckgo-mcp-server": "fetch_content",
    "jpisnice-shadcn-ui-mcp-server": "apply_theme",
    "docker-hub-mcp": "createRepository",
    "genomoncology-biomcp": "biomcp",
    "safedep-vet": "vet_query_execute_sql_query",
    "aas-ee-open-websearch": "fetchWebContent",
    "mnemox-ai-idea-reality-mcp": "producthunt_search",
    "idosal-git-mcp": "fetch_generic_url_content",
    "coinpaprika-dexpaprika-mcp": "submitFeedback",
    "pab1it0-chess-mcp": "download_player_games_pgn",
    "yuna0x0-anilist-mcp": "favourite_anime",
    "karanb192-reddit-mcp-buddy": "get_post_details",
    "rishijatia-fantasy-pl-mcp": "get_transfer_advice",
    "takashiishida-arxiv-latex-mcp": "save_paper_source",
}
TIMEOUT_TOOL_PROBES: dict[str, tuple[str, dict[str, Any]]] = {
    "nickclyde-duckduckgo-mcp-server": (
        "search",
        {"query": "ModelMirror timeout probe", "max_results": 1},
    ),
    "jpisnice-shadcn-ui-mcp-server": ("list_components", {}),
    "docker-hub-mcp": ("search", {"query": "python", "max_results": 1}),
    "genomoncology-biomcp": (
        "search",
        {"entity": "article", "query": "BRAF", "limit": 1},
    ),
    "safedep-vet": (
        "get_package_latest_version",
        {"purl": "pkg:npm/lodash"},
    ),
    "aas-ee-open-websearch": (
        "search",
        {"query": "ModelMirror timeout probe", "limit": 1, "engines": ["bing"]},
    ),
    "mnemox-ai-idea-reality-mcp": (
        "idea_check",
        {"idea_text": "ModelMirror timeout probe", "depth": "quick"},
    ),
    "idosal-git-mcp": (
        "fetch_repository_documentation",
        {"repository": "octocat/hello-world"},
    ),
    "coinpaprika-dexpaprika-mcp": ("getStats", {}),
    "pab1it0-chess-mcp": ("get_player_profile", {"username": "hikaru"}),
    "yuna0x0-anilist-mcp": ("get_genres", {}),
    "karanb192-reddit-mcp-buddy": (
        "browse_subreddit",
        {"subreddit": "python", "sort": "hot", "limit": 1},
    ),
    "rishijatia-fantasy-pl-mcp": (
        "search_fpl_players",
        {"query": "a", "max_results": 1},
    ),
    "takashiishida-arxiv-latex-mcp": (
        "get_paper_abstract",
        {"arxiv_id": "1706.03762"},
    ),
}
MAX_HANDSHAKE_BYTES = 4 * 1024


def _reviewed_digest(tools: list[Any]) -> str:
    reviewed = [
        {"name": tool.name, "inputSchema": tool.inputSchema}
        for tool in sorted(tools, key=lambda item: item.name)
    ]
    return hashlib.sha256(
        json.dumps(
            reviewed,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()


async def contract_only(adapter_ids: frozenset[str]) -> None:
    for adapter_id in sorted(adapter_ids):
        tools = await BUILDERS[adapter_id]().list_tools()
        names = {tool.name for tool in tools}
        digest = _reviewed_digest(tools)
        if names != set(ADAPTER_TOOL_NAMES[adapter_id]):
            raise RuntimeError("public_smoke_tool_contract_drift")
        if digest != PUBLIC_SCHEMA_SHA256[adapter_id]:
            raise RuntimeError("public_smoke_schema_contract_drift")
        print(
            f"adapter={adapter_id} contract_tools={len(names)} "
            f"schema_sha256={digest}",
            flush=True,
        )
    print("wave17_public_contract_smoke=ok", flush=True)


def _copy_stdin(sock: socket.socket) -> None:
    try:
        while True:
            chunk = os.read(sys.stdin.fileno(), 64 * 1024)
            if not chunk:
                break
            sock.sendall(chunk)
    except (BrokenPipeError, ConnectionError, OSError):
        pass
    finally:
        try:
            sock.shutdown(socket.SHUT_WR)
        except OSError:
            pass


def proxy(socket_path: Path, adapter_id: str) -> int:
    if not socket_path.is_absolute() or adapter_id not in PUBLIC_EXPANSION_ADAPTERS:
        return 64
    sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    try:
        sock.settimeout(10)
        sock.connect(str(socket_path))
        sock.sendall(
            json.dumps(
                {"action": "mcp_stdio", "adapter_id": adapter_id},
                separators=(",", ":"),
            ).encode("utf-8")
            + b"\n"
        )
        raw = sock.makefile("rb", buffering=0).readline(MAX_HANDSHAKE_BYTES + 1)
        if not raw or len(raw) > MAX_HANDSHAKE_BYTES:
            return 69
        response = json.loads(raw.decode("utf-8"))
        if not isinstance(response, dict) or response.get("ok") is not True:
            return 69
        sock.settimeout(None)
        thread = threading.Thread(target=_copy_stdin, args=(sock,), daemon=True)
        thread.start()
        while True:
            chunk = sock.recv(64 * 1024)
            if not chunk:
                break
            sys.stdout.buffer.write(chunk)
            sys.stdout.buffer.flush()
        return 0
    except (ConnectionError, OSError, ValueError, json.JSONDecodeError):
        return 69
    finally:
        sock.close()


def _decode_result(result: Any) -> Any:
    if result.isError:
        raise RuntimeError("public_smoke_tool_result_failed")
    structured = result.structuredContent
    if isinstance(structured, dict):
        return structured.get("result", structured)
    texts = [
        str(item.text)
        for item in result.content
        if getattr(item, "type", "") == "text"
    ]
    if not texts:
        raise RuntimeError("public_smoke_tool_result_missing")
    try:
        return json.loads("\n".join(texts))
    except json.JSONDecodeError as exc:
        raise RuntimeError("public_smoke_tool_result_invalid") from exc


async def _call(
    session: ClientSession,
    tool_name: str,
    arguments: dict[str, Any],
    *,
    timeout: float = 35.0,
) -> Any:
    result = await asyncio.wait_for(
        session.call_tool(tool_name, arguments),
        timeout=timeout,
    )
    return _decode_result(result)


def _parameters(socket_path: Path, adapter_id: str) -> StdioServerParameters:
    _, stdio_parameters_type, _ = _load_mcp_stdio()
    return stdio_parameters_type(
        command=sys.executable,
        args=[
            "-m",
            "sandbox_sidecar.smoke_public_adapters",
            "--proxy",
            str(socket_path),
            adapter_id,
        ],
        env={
            "PATH": os.environ.get("PATH", "/usr/local/bin:/usr/bin:/bin"),
            "PYTHONPATH": os.environ.get("PYTHONPATH", "/opt/modelmirror"),
            "PYTHONDONTWRITEBYTECODE": "1",
            "PYTHONNOUSERSITE": "1",
            "PYTHONUNBUFFERED": "1",
            "LANG": "C.UTF-8",
            "LC_ALL": "C.UTF-8",
            "TZ": "UTC",
        },
        cwd=Path("/tmp"),
    )


async def _blocked_tool_is_denied(
    session: ClientSession,
    adapter_id: str,
) -> bool:
    try:
        result = await session.call_tool(BLOCKED_TOOL_PROBES[adapter_id], {})
    except Exception:
        return True
    return bool(result.isError)


async def runtime_adapter(socket_path: Path, adapter_id: str) -> None:
    client_session_type, _, stdio_client_runtime = _load_mcp_stdio()
    async with stdio_client_runtime(_parameters(socket_path, adapter_id)) as streams:
        async with client_session_type(*streams) as session:
            await session.initialize()
            listed = await session.list_tools()
            names = {tool.name for tool in listed.tools}
            digest = _reviewed_digest(listed.tools)
            if names != set(ADAPTER_TOOL_NAMES[adapter_id]):
                raise RuntimeError("public_runtime_tool_contract_drift")
            if digest != PUBLIC_SCHEMA_SHA256[adapter_id]:
                raise RuntimeError("public_runtime_schema_contract_drift")
            if not await _blocked_tool_is_denied(session, adapter_id):
                raise RuntimeError("public_runtime_blocked_tool_callable")

            if adapter_id == "nickclyde-duckduckgo-mcp-server":
                first = await _call(
                    session,
                    "search",
                    {
                        "query": "Model Context Protocol",
                        "max_results": 3,
                        "region": "us-en",
                    },
                )
                started = time.monotonic()
                second = await _call(
                    session,
                    "search",
                    {"query": "ModelMirror MCP", "max_results": 1},
                )
                throttle_seconds = time.monotonic() - started
                results = first.get("results") if isinstance(first, dict) else None
                if not isinstance(results, list) or not results:
                    raise RuntimeError("duckduckgo_runtime_results_empty")
                result_url = str(results[0].get("url") or "")
                result_host = str(urlsplit(result_url).hostname or "")
                if not result_host or throttle_seconds < 1.5:
                    raise RuntimeError("duckduckgo_runtime_contract_invalid")
                print("input.duckduckgo.query=Model Context Protocol")
                print(f"result.duckduckgo.count={len(results)}")
                print(f"result.duckduckgo.first_host={result_host[:253]}")
                print(f"result.duckduckgo.rate_probe_count={second.get('count', 0)}")
                print(f"result.duckduckgo.rate_wait_ms={int(throttle_seconds * 1000)}")
            elif adapter_id == "jpisnice-shadcn-ui-mcp-server":
                listed_components = await _call(session, "list_components", {})
                metadata = await _call(
                    session,
                    "get_component_metadata",
                    {"componentName": "button"},
                )
                components = listed_components.get("components")
                if (
                    not isinstance(components, list)
                    or "button" not in components
                    or metadata.get("name") != "button"
                    or metadata.get("commit")
                    != "d14b6e69a91f0fc99e31a7adb26a48d661df9911"
                ):
                    raise RuntimeError("shadcn_runtime_contract_invalid")
                print("input.shadcn.component=button")
                print(f"result.shadcn.total={listed_components.get('total', 0)}")
                print(f"result.shadcn.sha={str(metadata.get('sha') or '')[:40]}")
                print(f"result.shadcn.commit={metadata.get('commit')}")
            elif adapter_id == "docker-hub-mcp":
                search = await _call(
                    session,
                    "search",
                    {"query": "python", "max_results": 3},
                )
                repository = await _call(
                    session,
                    "getRepositoryInfo",
                    {"namespace": "library", "repository": "python"},
                )
                tags = await _call(
                    session,
                    "listRepositoryTags",
                    {
                        "namespace": "library",
                        "repository": "python",
                        "page": 1,
                        "page_size": 3,
                    },
                )
                if (
                    not isinstance(search.get("results"), list)
                    or repository.get("namespace") != "library"
                    or repository.get("name") != "python"
                    or not isinstance(tags.get("tags"), list)
                    or not tags["tags"]
                ):
                    raise RuntimeError("docker_hub_runtime_contract_invalid")
                print("input.docker_hub.repository=library/python")
                print(f"result.docker_hub.search_count={search.get('count', 0)}")
                print(f"result.docker_hub.pull_count={repository.get('pull_count', 0)}")
                print(f"result.docker_hub.first_tag={str(tags['tags'][0].get('name') or '')[:128]}")
            elif adapter_id == "genomoncology-biomcp":
                articles = await _call(
                    session,
                    "search",
                    {"entity": "article", "query": "BRAF melanoma", "limit": 3},
                )
                trial = await _call(
                    session,
                    "get",
                    {"entity": "trial", "id": "NCT02576665", "sections": ["summary", "status"]},
                )
                if (
                    not isinstance(articles.get("results"), list)
                    or not articles["results"]
                    or trial.get("entity") != "trial"
                    or trial.get("record", {}).get("id") != "NCT02576665"
                ):
                    raise RuntimeError("biomcp_runtime_contract_invalid")
                print("input.biomcp.article_query=BRAF melanoma")
                print(f"result.biomcp.article_count={articles.get('count', 0)}")
                print(f"result.biomcp.trial_id={trial['record']['id']}")
            elif adapter_id == "safedep-vet":
                purl = "pkg:npm/lodash@4.17.20"
                latest = await _call(
                    session,
                    "get_package_latest_version",
                    {"purl": "pkg:npm/lodash"},
                )
                versions = await _call(
                    session,
                    "get_package_available_versions",
                    {"purl": "pkg:npm/lodash", "max_results": 5},
                )
                vulnerabilities = await _call(
                    session,
                    "get_package_version_vulnerabilities",
                    {"purl": purl},
                )
                popularity = await _call(
                    session,
                    "get_package_version_popularity",
                    {"purl": purl},
                )
                licenses = await _call(
                    session,
                    "get_package_version_license_info",
                    {"purl": purl},
                )
                malware = await _call(
                    session,
                    "get_package_version_malware_report",
                    {"purl": "pkg:npm/safedep-test-pkg@1.0.0"},
                )
                if (
                    not latest.get("version")
                    or not isinstance(versions.get("versions"), list)
                    or not isinstance(vulnerabilities.get("vulnerabilities"), list)
                    or not isinstance(popularity.get("projects"), list)
                    or not isinstance(licenses.get("licenses"), list)
                    or malware.get("is_malware") is not True
                ):
                    raise RuntimeError("safedep_runtime_contract_invalid")
                print("input.safedep.purl=pkg:npm/lodash@4.17.20")
                print(f"result.safedep.latest={latest.get('version')}")
                print(f"result.safedep.version_count={versions.get('count', 0)}")
                print(f"result.safedep.vulnerability_count={vulnerabilities.get('count', 0)}")
                print(f"result.safedep.malware_marker={str(malware.get('is_malware')).lower()}")
            elif adapter_id == "aas-ee-open-websearch":
                search = await _call(
                    session,
                    "search",
                    {
                        "query": "Model Context Protocol",
                        "limit": 4,
                        "engines": ["bing", "duckduckgo"],
                    },
                )
                results = search.get("results")
                engines = {
                    str(item.get("engine") or "")
                    for item in (results or [])
                    if isinstance(item, dict)
                }
                if (
                    not isinstance(results, list)
                    or not results
                    or engines != {"bing", "duckduckgo"}
                    or search.get("mode") != "request-only"
                ):
                    raise RuntimeError("open_websearch_runtime_contract_invalid")
                print("input.open_websearch.query=Model Context Protocol")
                print(f"result.open_websearch.count={search.get('count', 0)}")
                print(f"result.open_websearch.engines={','.join(sorted(engines))}")
            elif adapter_id == "mnemox-ai-idea-reality-mcp":
                research = await _call(
                    session,
                    "idea_check",
                    {"idea_text": "Secure MCP catalog research", "depth": "quick"},
                )
                sources = research.get("sources_used")
                if (
                    sources != ["github", "hacker_news"]
                    or not isinstance(research.get("results"), dict)
                    or int(research.get("similar_result_count") or 0) < 1
                ):
                    raise RuntimeError("idea_reality_runtime_contract_invalid")
                print("input.idea_reality.depth=quick")
                print(f"result.idea_reality.sources={','.join(sources)}")
                print(f"result.idea_reality.similar_count={research.get('similar_result_count', 0)}")
            elif adapter_id == "idosal-git-mcp":
                repository = "octocat/hello-world"
                docs = await _call(
                    session,
                    "fetch_repository_documentation",
                    {"repository": repository},
                )
                doc_search = await _call(
                    session,
                    "search_repository_documentation",
                    {"repository": repository, "query": "Hello", "limit": 5},
                )
                code_search = await _call(
                    session,
                    "search_repository_code",
                    {"repository": repository, "query": "readme", "page": 1},
                )
                if (
                    docs.get("repository") != repository
                    or not docs.get("content")
                    or not isinstance(doc_search.get("results"), list)
                    or not isinstance(code_search.get("results"), list)
                    or not code_search["results"]
                ):
                    raise RuntimeError("gitmcp_runtime_contract_invalid")
                print(f"input.gitmcp.repository={repository}")
                print(f"result.gitmcp.readme_path={docs.get('path')}")
                print(f"result.gitmcp.docs_count={doc_search.get('count', 0)}")
                print(f"result.gitmcp.code_count={code_search.get('count', 0)}")
            elif adapter_id == "coinpaprika-dexpaprika-mcp":
                networks = await _call(session, "getNetworks", {})
                stats = await _call(session, "getStats", {})
                search = await _call(
                    session,
                    "search",
                    {"query": "bitcoin", "max_results": 2},
                )
                if (
                    not isinstance(networks.get("networks"), list)
                    or not networks["networks"]
                    or int(stats.get("chains") or 0) < 1
                    or not isinstance(search.get("tokens"), list)
                    or not search["tokens"]
                ):
                    raise RuntimeError("dexpaprika_runtime_contract_invalid")
                print("input.dexpaprika.query=bitcoin")
                print(f"result.dexpaprika.network_count={networks.get('count', 0)}")
                print(f"result.dexpaprika.chain_count={stats.get('chains', 0)}")
                print(f"result.dexpaprika.token_count={search.get('token_count', 0)}")
            elif adapter_id == "pab1it0-chess-mcp":
                profile = await _call(
                    session, "get_player_profile", {"username": "hikaru"}
                )
                stats = await _call(
                    session, "get_player_stats", {"username": "hikaru"}
                )
                if (
                    profile.get("username") != "hikaru"
                    or not isinstance(stats.get("statistics"), dict)
                ):
                    raise RuntimeError("chess_runtime_contract_invalid")
                print("input.chess.username=hikaru")
                print(f"result.chess.player_id={profile.get('player_id', 0)}")
                print(f"result.chess.rating_modes={len(stats.get('statistics', {}))}")
            elif adapter_id == "yuna0x0-anilist-mcp":
                genres = await _call(session, "get_genres", {})
                search = await _call(
                    session,
                    "search_anime",
                    {"term": "Frieren", "page": 1, "amount": 2},
                )
                details = await _call(session, "get_anime", {"ids": 154587})
                if (
                    not isinstance(genres.get("genres"), list)
                    or not genres["genres"]
                    or not isinstance(search.get("results"), list)
                    or not search["results"]
                    or not isinstance(details.get("results"), list)
                    or not details["results"]
                    or details["results"][0].get("id") != 154587
                ):
                    raise RuntimeError("anilist_runtime_contract_invalid")
                print("input.anilist.term=Frieren")
                print(f"result.anilist.genre_count={genres.get('count', 0)}")
                print(f"result.anilist.search_count={search.get('count', 0)}")
                print(f"result.anilist.detail_id={details['results'][0].get('id')}")
            elif adapter_id == "karanb192-reddit-mcp-buddy":
                browse = await _call(
                    session,
                    "browse_subreddit",
                    {"subreddit": "python", "sort": "hot", "limit": 3},
                )
                search = await _call(
                    session,
                    "search_reddit",
                    {
                        "query": "model context protocol",
                        "sort": "relevance",
                        "time": "year",
                        "limit": 3,
                    },
                )
                if (
                    browse.get("data_source") != "reddit-public-atom"
                    or not isinstance(browse.get("posts"), list)
                    or not browse["posts"]
                    or search.get("data_source") != "reddit-public-atom"
                    or not isinstance(search.get("posts"), list)
                ):
                    raise RuntimeError("reddit_buddy_runtime_contract_invalid")
                print("input.reddit_buddy.subreddit=python")
                print(f"result.reddit_buddy.post_count={browse.get('count', 0)}")
                print(f"result.reddit_buddy.search_count={search.get('count', 0)}")
            elif adapter_id == "rishijatia-fantasy-pl-mcp":
                players = await _call(
                    session,
                    "search_fpl_players",
                    {"query": "a", "max_results": 3},
                )
                fixtures = await _call(
                    session,
                    "list_fpl_fixtures",
                    {"max_results": 3},
                )
                if (
                    not isinstance(players.get("players"), list)
                    or not players["players"]
                    or not isinstance(fixtures.get("fixtures"), list)
                ):
                    raise RuntimeError("fantasy_pl_runtime_contract_invalid")
                player_id = players["players"][0].get("id")
                details = await _call(
                    session,
                    "get_player_information",
                    {"player_id": player_id},
                )
                if details.get("player", {}).get("id") != player_id:
                    raise RuntimeError("fantasy_pl_runtime_contract_invalid")
                print("input.fantasy_pl.query=a")
                print(f"result.fantasy_pl.player_count={players.get('count', 0)}")
                print(f"result.fantasy_pl.fixture_count={fixtures.get('count', 0)}")
            elif adapter_id == "takashiishida-arxiv-latex-mcp":
                paper_id = "1706.03762"
                abstract = await _call(
                    session,
                    "get_paper_abstract",
                    {"arxiv_id": paper_id},
                )
                sections = await _call(
                    session,
                    "list_paper_sections",
                    {"arxiv_id": paper_id},
                )
                if (
                    abstract.get("arxiv_id") != paper_id
                    or not abstract.get("title")
                    or not abstract.get("abstract")
                    or not isinstance(sections.get("sections"), list)
                    or not sections["sections"]
                ):
                    raise RuntimeError("arxiv_latex_runtime_contract_invalid")
                first_path = str(sections["sections"][0].get("path") or "")
                section = await _call(
                    session,
                    "get_paper_section",
                    {"arxiv_id": paper_id, "section_path": first_path},
                )
                if section.get("path") != first_path or not section.get("latex"):
                    raise RuntimeError("arxiv_latex_runtime_contract_invalid")
                print(f"input.arxiv_latex.paper_id={paper_id}")
                print(f"result.arxiv_latex.section_count={sections.get('count', 0)}")
                print(f"result.arxiv_latex.first_section={first_path[:64]}")
            else:
                raise RuntimeError("public_runtime_adapter_unhandled")
            print(
                f"adapter={adapter_id} initialized=true tools={len(names)} "
                f"schema_sha256={digest} blocked_tool=denied",
                flush=True,
            )


async def cancellation_timeout_probe(socket_path: Path, adapter_id: str) -> None:
    tool_name, arguments = TIMEOUT_TOOL_PROBES[adapter_id]
    client_session_type, _, stdio_client_runtime = _load_mcp_stdio()
    async with stdio_client_runtime(_parameters(socket_path, adapter_id)) as streams:
        async with client_session_type(*streams) as session:
            await session.initialize()
            try:
                await asyncio.wait_for(
                    session.call_tool(tool_name, arguments),
                    timeout=0.001,
                )
            except asyncio.TimeoutError:
                print("timeout_probe=cancelled_and_session_closed", flush=True)
                return
    raise RuntimeError("public_runtime_timeout_probe_did_not_timeout")


async def runtime(socket_path: Path, adapter_ids: frozenset[str]) -> None:
    if not socket_path.is_absolute():
        raise RuntimeError("public_runtime_socket_must_be_absolute")
    for adapter_id in sorted(adapter_ids):
        await runtime_adapter(socket_path, adapter_id)
    await cancellation_timeout_probe(socket_path, sorted(adapter_ids)[0])
    print(
        "wave17_public_runtime_smoke=ok adapters="
        + ",".join(sorted(adapter_ids)),
        flush=True,
    )


def _adapter_ids(raw: str) -> frozenset[str]:
    requested = frozenset(item.strip() for item in raw.split(",") if item.strip())
    if not requested or not requested.issubset(PUBLIC_EXPANSION_ADAPTERS):
        raise RuntimeError("public_smoke_adapter_selection_invalid")
    return requested


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--contract-only", action="store_true")
    parser.add_argument("--socket")
    parser.add_argument(
        "--adapters",
        default=",".join(sorted(PUBLIC_EXPANSION_ADAPTERS)),
    )
    parser.add_argument("--proxy", nargs=2, metavar=("SOCKET", "ADAPTER"))
    args = parser.parse_args()
    if args.proxy:
        return proxy(Path(args.proxy[0]), args.proxy[1])
    adapter_ids = _adapter_ids(args.adapters)
    if args.contract_only:
        asyncio.run(contract_only(adapter_ids))
        return 0
    if not args.socket:
        parser.error("--socket is required for runtime smoke")
    asyncio.run(runtime(Path(args.socket), adapter_ids))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

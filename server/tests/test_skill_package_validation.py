from __future__ import annotations

from server.skills.package_validation import (
    MAX_FILE_BYTES,
    MAX_TOTAL_BYTES,
    VALIDATOR_VERSION,
    compute_package_digest,
    compute_skill_content_digest,
    scan_skill_package_credentials,
    validate_skill_package,
)


def _skill_markdown(
    *,
    name: str = "analyze-data",
    description: str = "Analyze tabular data and explain the results when users request a data review.",
    extra: str = "",
    body: str = "# Analyze data\n\nFollow the requested analysis workflow.",
) -> str:
    return (
        "---\n"
        f"name: {name}\n"
        f"description: {description}\n"
        f"{extra}"
        "---\n\n"
        f"{body}\n"
    )


def _codes(result) -> set[str]:
    return {issue.code for issue in result.issues}


def test_validates_complete_utf8_package_and_retains_optional_metadata() -> None:
    markdown = _skill_markdown(
        extra=(
            "license: MIT\n"
            "compatibility: ModelMirror private agents\n"
            "metadata:\n"
            "  category: analytics\n"
            "  stable: true\n"
            "allowed-tools:\n"
            "  - Read\n"
            "  - Grep\n"
        ),
        body=(
            "# Analyze data\n\n"
            "Read [the method](references/method.md), then run the helper when needed."
        ),
    )
    files = {
        "references/method.md": "# Method\n\nUse the deterministic checks.\n",
        "scripts/check.py": "def check(value: int) -> int:\n    return value + 1\n",
        "assets/schema.json": '{"type":"object","properties":{}}',
        "assets/config.yaml": "enabled: true\nlimits:\n  rows: 10\n",
        "scripts/check.js": "const rx = /[)]/g;\nexport function check(v) { return rx.test(v); }\n",
        "agents/openai.yaml": "interface:\n  display_name: Analyze data\n",
    }

    result = validate_skill_package(
        root_name="analyze-data", skill_markdown=markdown, files=files
    )

    assert result.valid is True
    assert result.validator_version == VALIDATOR_VERSION
    assert result.issues == ()
    assert result.package is not None
    assert result.package.name == "analyze-data"
    assert result.package.description.startswith("Analyze tabular data")
    assert result.package.license == "MIT"
    assert result.package.compatibility == "ModelMirror private agents"
    assert result.package.metadata == {"category": "analytics", "stable": True}
    assert result.package.allowed_tools == ("Read", "Grep")
    assert result.package.file_count == 1 + len(files)
    assert result.package.content_digest == result.content_digest
    assert len(result.package.content_digest) == 64


def test_digest_is_order_independent_and_sensitive_to_exact_utf8_bytes() -> None:
    first = compute_skill_content_digest(
        {"SKILL.md": "alpha\n", "references/a.md": "café\n"}
    )
    reordered = compute_skill_content_digest(
        {"references/a.md": "café\n", "SKILL.md": "alpha\n"}
    )
    crlf = compute_skill_content_digest(
        {"SKILL.md": b"alpha\r\n", "references/a.md": "café\n"}
    )

    assert first == reordered
    assert first != crlf
    assert first == compute_package_digest(
        "alpha\n", {"references/a.md": "café\n"}
    )


def test_rejects_duplicate_frontmatter_keys_including_nested_metadata() -> None:
    markdown = (
        "---\n"
        "name: analyze-data\n"
        "description: Analyze data when users ask for a review.\n"
        "metadata:\n"
        "  owner: first\n"
        "  owner: second\n"
        "---\n\n"
        "# Analyze data\n"
    )

    result = validate_skill_package(
        root_name="analyze-data", skill_markdown=markdown, files={}
    )

    assert result.valid is False
    assert "frontmatter_duplicate_key" in _codes(result)
    assert result.package is None


def test_rejects_unsafe_yaml_tags_and_non_mapping_frontmatter() -> None:
    tagged = validate_skill_package(
        root_name="analyze-data",
        skill_markdown=(
            "---\n"
            "name: analyze-data\n"
            "description: !!python/object/apply:os.system [echo unsafe]\n"
            "---\n\n# Analyze data\n"
        ),
        files={},
    )
    sequence = validate_skill_package(
        root_name="analyze-data",
        skill_markdown="---\n- name\n- description\n---\n\n# Analyze data\n",
        files={},
    )

    assert "frontmatter_invalid_yaml" in _codes(tagged)
    assert "frontmatter_type" in _codes(sequence)


def test_enforces_frontmatter_types_name_format_description_and_root_match() -> None:
    wrong_types = validate_skill_package(
        root_name="analyze-data",
        skill_markdown=(
            "---\n"
            "name: true\n"
            "description: [not, text]\n"
            "metadata:\n"
            "  released: 2026-08-06\n"
            "allowed-tools:\n"
            "  - Read\n"
            "  - 7\n"
            "---\n\n# Analyze data\n"
        ),
        files={},
    )
    mismatch = validate_skill_package(
        root_name="analyze-data",
        skill_markdown=_skill_markdown(name="review-data"),
        files={},
    )
    invalid_name = validate_skill_package(
        root_name="analyze-data",
        skill_markdown=_skill_markdown(name="Analyze Data"),
        files={},
    )

    assert {
        "frontmatter_name_type",
        "frontmatter_description_type",
        "frontmatter_metadata_type",
        "frontmatter_allowed_tools_type",
    } <= _codes(wrong_types)
    assert "skill_name_root_mismatch" in _codes(mismatch)
    assert "frontmatter_name_invalid" in _codes(invalid_name)


def test_rejects_unsafe_paths_case_collisions_and_agents_extras() -> None:
    files = {
        "../outside.txt": "no",
        "references\\windows.md": "no",
        "references/Guide.md": "# Guide\n",
        "references/guide.md": "# duplicate\n",
        "agents/custom.yaml": "enabled: true\n",
        "assets/a": "file",
        "assets/a/nested.txt": "nested",
    }

    result = validate_skill_package(
        root_name="analyze-data", skill_markdown=_skill_markdown(), files=files
    )

    assert result.valid is False
    assert {
        "file_path_unsafe",
        "file_path_case_collision",
        "agents_file_unsupported",
        "file_directory_conflict",
    } <= _codes(result)


def test_rejects_windows_reserved_trailing_and_oversized_paths() -> None:
    result = validate_skill_package(
        root_name="analyze-data",
        skill_markdown=_skill_markdown(),
        files={
            "assets/CON.txt": "reserved",
            "references/trailing./guide.md": "trailing dot",
            f"assets/{'数' * 100}.txt": "oversized component",
            f"references/{'a' * 80}/{'b' * 80}/{'c' * 80}.md": "long path",
        },
    )
    reserved_root = validate_skill_package(
        root_name="con",
        skill_markdown=_skill_markdown(name="con"),
        files={},
    )

    assert result.valid is False
    assert {
        "file_path_windows_unsafe",
        "file_path_too_long",
    } <= _codes(result)
    assert "root_name_windows_reserved" in _codes(reserved_root)


def test_rejects_invalid_utf8_and_enforces_file_and_package_limits() -> None:
    invalid_utf8 = validate_skill_package(
        root_name="analyze-data",
        skill_markdown=_skill_markdown(),
        files={"assets/data.txt": b"\xff\xfe"},
    )
    too_many = validate_skill_package(
        root_name="analyze-data",
        skill_markdown=_skill_markdown(),
        files={f"assets/file-{index}.txt": "x" for index in range(40)},
    )
    too_large_file = validate_skill_package(
        root_name="analyze-data",
        skill_markdown=_skill_markdown(),
        files={"assets/large.txt": "x" * (MAX_FILE_BYTES + 1)},
    )
    package_too_large = validate_skill_package(
        root_name="analyze-data",
        skill_markdown=_skill_markdown(),
        files={f"assets/chunk-{index}.txt": "x" * 900_000 for index in range(6)},
    )

    assert "file_invalid_utf8" in _codes(invalid_utf8)
    assert "package_file_count_exceeded" in _codes(too_many)
    assert "file_size_exceeded" in _codes(too_large_file)
    assert "package_size_exceeded" in _codes(package_too_large)
    assert MAX_TOTAL_BYTES < 6 * 900_000


def test_detects_missing_case_mismatched_and_escaping_local_references() -> None:
    markdown = _skill_markdown(
        body=(
            "# Analyze data\n\n"
            "Read [missing](references/missing.md), "
            "[wrong case](references/GUIDE.md), and "
            "[outside](../../outside.md).\n\n"
            "External [documentation](https://example.com/guide) is allowed."
        )
    )
    result = validate_skill_package(
        root_name="analyze-data",
        skill_markdown=markdown,
        files={"references/guide.md": "# Guide\n"},
    )

    assert {
        "local_reference_missing",
        "local_reference_case_mismatch",
        "local_reference_unsafe",
    } <= _codes(result)


def test_detects_missing_resource_referenced_as_an_inline_code_path() -> None:
    result = validate_skill_package(
        root_name="analyze-data",
        skill_markdown=_skill_markdown(
            body=(
                "# Analyze data\n\n"
                "Read `references/missing.md` and run "
                "`python scripts/analyze.py --mode safe`.\n"
                "Do not treat `https://example.com/references/external.md` as local."
            )
        ),
        files={},
    )

    assert result.valid is False
    assert "local_reference_missing" in _codes(result)
    missing_issues = [
        issue for issue in result.issues if issue.code == "local_reference_missing"
    ]
    assert len(missing_issues) == 2


def test_static_syntax_checks_do_not_execute_package_files(tmp_path) -> None:
    marker = tmp_path / "must-not-exist"
    files = {
        "scripts/broken.py": f"open({str(marker)!r}, 'w').write('ran')\ndef broken(:\n",
        "assets/broken.json": '{"missing": }',
        "assets/broken.yaml": "item: one\nitem: two\n",
        "scripts/broken.js": "export function broken() { return [1, 2; }\n",
    }

    result = validate_skill_package(
        root_name="analyze-data", skill_markdown=_skill_markdown(), files=files
    )

    assert result.valid is False
    assert {
        "python_syntax_invalid",
        "json_syntax_invalid",
        "yaml_syntax_invalid",
        "javascript_syntax_invalid",
    } <= _codes(result)
    assert marker.exists() is False


def test_javascript_check_rejects_obvious_missing_operand_without_scanning_strings() -> None:
    invalid = validate_skill_package(
        root_name="analyze-data",
        skill_markdown=_skill_markdown(),
        files={"scripts/broken.js": "const value = ;\n"},
    )
    valid = validate_skill_package(
        root_name="analyze-data",
        skill_markdown=_skill_markdown(),
        files={
            "scripts/example.js": (
                'const sample = "const value = ;";\n'
                "const pattern = /=;/;\n"
            )
        },
    )

    assert "javascript_syntax_invalid" in _codes(invalid)
    assert valid.valid is True


def test_rejects_yaml_aliases_before_recursive_metadata_traversal() -> None:
    result = validate_skill_package(
        root_name="analyze-data",
        skill_markdown=(
            "---\n"
            "name: analyze-data\n"
            "description: Analyze data when users ask for a review.\n"
            "metadata:\n"
            "  defaults: &defaults [one, two]\n"
            "  copied: *defaults\n"
            "---\n\n# Analyze data\n"
        ),
        files={},
    )

    assert result.valid is False
    assert "frontmatter_invalid_yaml" in _codes(result)


def test_deep_yaml_is_a_structured_validation_error_not_a_recursion_crash() -> None:
    nested_value = "[" * 2_000 + "value" + "]" * 2_000
    markdown = (
        "---\n"
        "name: analyze-data\n"
        f"description: {nested_value}\n"
        "---\n\n# Analyze data\n"
    )

    result = validate_skill_package(
        root_name="analyze-data",
        skill_markdown=markdown,
        files={"assets/deep.yaml": nested_value},
    )

    assert result.valid is False
    assert {"frontmatter_invalid_yaml", "yaml_syntax_invalid"} <= _codes(result)


def test_blocks_credentials_without_echoing_secret_or_returning_content() -> None:
    secret = "sk-abcdefghijklmnopqrstuvwxyz1234567890"
    result = validate_skill_package(
        root_name="analyze-data",
        skill_markdown=_skill_markdown(),
        files={
            "references/private.md": (
                f"token: {secret}\n"
                "password = real-production-password\n"
                "https://service-user:real-password@example.com/path\n"
                "-----BEGIN PRIVATE KEY-----\n"
            )
        },
    )

    assert result.valid is False
    assert result.package is None
    assert result.file_count == 2
    assert result.total_bytes > 0
    assert {
        "credential_token",
        "credential_assignment",
        "credential_url",
        "credential_private_key",
    } <= _codes(result)
    serialized = json_text = str(result.to_dict(include_package=True))
    assert secret not in serialized
    assert "real-production-password" not in json_text


def test_blocks_prefixed_secret_variables_and_tokens_hidden_in_paths() -> None:
    prefixed = validate_skill_package(
        root_name="analyze-data",
        skill_markdown=_skill_markdown(),
        files={
            "references/config.md": (
                'DIFY_API_KEY="dify-live-secret-abcdefghijklmnopqrstuvwxyz"\n'
            )
        },
    )
    path_secret = validate_skill_package(
        root_name="analyze-data",
        skill_markdown=_skill_markdown(),
        files={
            "assets/sk-abcdefghijklmnopqrstuvwxyz1234567890.json": "{invalid json"
        },
    )

    assert "credential_assignment" in _codes(prefixed)
    assert "credential_token" in _codes(path_secret)
    assert prefixed.package is None
    assert path_secret.package is None


def test_placeholders_are_not_mistaken_for_hard_coded_credentials() -> None:
    result = validate_skill_package(
        root_name="analyze-data",
        skill_markdown=_skill_markdown(),
        files={
            "references/config.md": (
                "api_key = ${ANALYTICS_API_KEY}\n"
                "password: YOUR-PASSWORD\n"
                "client_secret = <replace-me>\n"
            )
        },
    )

    assert result.valid is True
    assert not any(code.startswith("credential_") for code in _codes(result))


def test_credential_scanner_supports_partial_update_payloads_without_echo() -> None:
    secret = "github_pat_abcdefghijklmnopqrstuvwxyz1234567890"

    issues = scan_skill_package_credentials(
        files={"scripts/config.py": f"client_secret = '{secret}'\n"}
    )

    assert {issue.code for issue in issues} == {
        "credential_token",
        "credential_assignment",
    }
    assert secret not in str([issue.to_dict() for issue in issues])


def test_blocks_prefixed_secret_variables_and_credentials_in_paths_without_echo() -> None:
    path_secret = "sk-abcdefghijklmnopqrstuvwxyz1234567890"
    result = validate_skill_package(
        root_name="analyze-data",
        skill_markdown=_skill_markdown(),
        files={
            "scripts/config.py": 'DIFY_API_KEY = "dify-live-secret-123456789"\n',
            f"assets/{path_secret}.txt": "safe text\n",
        },
    )

    assert result.valid is False
    assert {"credential_assignment", "credential_token"} <= _codes(result)
    assert path_secret not in str(result.to_dict(include_package=True))


def test_unknown_frontmatter_field_is_warning_not_install_metadata() -> None:
    result = validate_skill_package(
        root_name="analyze-data",
        skill_markdown=_skill_markdown(extra="custom-field: ignored\n"),
        files={},
    )

    assert result.valid is True
    assert _codes(result) == {"frontmatter_field_unsupported"}
    assert result.issues[0].severity == "warning"
    assert result.package is not None
    assert "custom-field" not in result.package.to_dict()

from __future__ import annotations

import io
import zipfile

import pytest

from server.file_assets import validation as validation_module
from server.file_assets.contracts import (
    FileInputKind,
    FileInteractionStatus,
    FilePurpose,
)
from server.file_assets.document_parser import ParsedSection
from server.file_assets.registry import get_file_format_registry
from server.file_assets.validation import FileUploadValidator, FileValidationError


DOCX_MEDIA_TYPE = (
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
)
PPTX_MEDIA_TYPE = (
    "application/vnd.openxmlformats-officedocument.presentationml.presentation"
)
REL_NS = "http://schemas.openxmlformats.org/package/2006/relationships"
OFFICE_REL_NS = (
    "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
)


@pytest.fixture(autouse=True)
def _enable_chat_file_assets(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CHAT_FILE_INPUT_ENABLED", "true")
    monkeypatch.setenv("FILE_ASSET_STORE_MODE", "shadow")


def _relationships(*items: str) -> bytes:
    return (
        f'<?xml version="1.0" encoding="UTF-8"?>'
        f'<Relationships xmlns="{REL_NS}">{"".join(items)}</Relationships>'
    ).encode("utf-8")


def _relationship(
    relationship_id: str,
    relationship_type: str,
    target: str,
    *,
    external: bool = False,
) -> str:
    mode = ' TargetMode="External"' if external else ""
    return (
        f'<Relationship Id="{relationship_id}" Type="{relationship_type}" '
        f'Target="{target}"{mode}/>'
    )


def _content_types(format_id: str) -> bytes:
    if format_id == "docx":
        part = "/word/document.xml"
        content_type = (
            "application/vnd.openxmlformats-officedocument."
            "wordprocessingml.document.main+xml"
        )
    else:
        part = "/ppt/presentation.xml"
        content_type = (
            "application/vnd.openxmlformats-officedocument."
            "presentationml.presentation.main+xml"
        )
    return (
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
        '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
        '<Default Extension="xml" ContentType="application/xml"/>'
        f'<Override PartName="{part}" ContentType="{content_type}"/>'
        '</Types>'
    ).encode("utf-8")


def _office_package(
    format_id: str,
    *,
    hyperlink_target: str | None = "https://example.invalid/source",
) -> bytes:
    if format_id == "docx":
        main_part = "word/document.xml"
        main_xml = (
            '<?xml version="1.0" encoding="UTF-8"?>'
            '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main" '
            f'xmlns:r="{OFFICE_REL_NS}"><w:body>'
            '<w:p><w:pPr><w:pStyle w:val="Heading1"/></w:pPr><w:r><w:t>路线图</w:t></w:r></w:p>'
            '<w:p><w:hyperlink r:id="rLink"><w:r><w:t>来源</w:t></w:r></w:hyperlink></w:p>'
            '<w:tbl><w:tr><w:tc><w:p><w:r><w:t>表格值</w:t></w:r></w:p></w:tc></w:tr></w:tbl>'
            '</w:body></w:document>'
        ).encode("utf-8")
        rel_name = "word/_rels/document.xml.rels"
    else:
        main_part = "ppt/presentation.xml"
        main_xml = (
            '<?xml version="1.0" encoding="UTF-8"?>'
            '<p:presentation xmlns:p="http://schemas.openxmlformats.org/presentationml/2006/main" '
            f'xmlns:r="{OFFICE_REL_NS}"><p:sldIdLst>'
            '<p:sldId id="256" r:id="rId1"/>'
            '</p:sldIdLst></p:presentation>'
        ).encode("utf-8")
        rel_name = "ppt/_rels/presentation.xml.rels"

    root_relationship = _relationship(
        "rOffice",
        f"{OFFICE_REL_NS}/officeDocument",
        main_part,
    )
    members: dict[str, bytes] = {
        "[Content_Types].xml": _content_types(format_id),
        "_rels/.rels": _relationships(root_relationship),
        main_part: main_xml,
    }
    if format_id == "pptx":
        members["ppt/slides/slide1.xml"] = (
            '<?xml version="1.0" encoding="UTF-8"?>'
            '<p:sld xmlns:p="http://schemas.openxmlformats.org/presentationml/2006/main" '
            'xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main">'
            '<p:cSld><p:spTree><p:sp><p:txBody><a:p><a:r><a:t>标题</a:t></a:r></a:p>'
            '</p:txBody></p:sp></p:spTree></p:cSld></p:sld>'
        ).encode("utf-8")
        members["ppt/notesSlides/notesSlide1.xml"] = (
            '<?xml version="1.0" encoding="UTF-8"?>'
            '<p:notes xmlns:p="http://schemas.openxmlformats.org/presentationml/2006/main"/>'
        ).encode("utf-8")
        presentation_relationships = [
            _relationship(
                "rId1",
                f"{OFFICE_REL_NS}/slide",
                "slides/slide1.xml",
            )
        ]
        if hyperlink_target:
            presentation_relationships.append(
                _relationship(
                    "rLink",
                    f"{OFFICE_REL_NS}/hyperlink",
                    hyperlink_target,
                    external=True,
                )
            )
        members[rel_name] = _relationships(*presentation_relationships)
    elif hyperlink_target:
        members[rel_name] = _relationships(
            _relationship(
                "rLink",
                f"{OFFICE_REL_NS}/hyperlink",
                hyperlink_target,
                external=True,
            )
        )

    output = io.BytesIO()
    with zipfile.ZipFile(output, "w", zipfile.ZIP_DEFLATED) as archive:
        for name, content in members.items():
            archive.writestr(name, content)
    return output.getvalue()


def _rewrite_package(
    content: bytes,
    *,
    replacements: dict[str, bytes] | None = None,
    additions: dict[str, bytes] | None = None,
) -> bytes:
    output = io.BytesIO()
    replaced = set()
    with zipfile.ZipFile(io.BytesIO(content)) as source, zipfile.ZipFile(
        output, "w", zipfile.ZIP_DEFLATED
    ) as target:
        for entry in source.infolist():
            value = source.read(entry.filename)
            if replacements and entry.filename in replacements:
                value = replacements[entry.filename]
                replaced.add(entry.filename)
            target.writestr(entry, value)
        if additions:
            for name, value in additions.items():
                target.writestr(name, value)
    assert not replacements or replaced == set(replacements)
    return output.getvalue()


def _preflight(content: bytes, format_id: str) -> None:
    FileUploadValidator()._validate_office_ooxml(  # noqa: SLF001 - security unit boundary
        io.BytesIO(content),
        format_id=format_id,
    )


def test_office_formats_are_ready_for_chat_rag_and_workflow_only(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    registry = get_file_format_registry()
    assert registry.version == "modelmirror-file-formats-v5"

    monkeypatch.setenv("WORKFLOW_FILE_ASSETS_ENABLED", "true")
    for purpose in (FilePurpose.CHAT, FilePurpose.RAG, FilePurpose.WORKFLOW):
        policy = next(
            item
            for item in registry.policies_for(purpose)
            if item.input_kind == FileInputKind.DOCUMENT
        )
        formats = {item.format_id: item for item in registry.formats_for(policy)}
        for format_id in ("docx", "pptx"):
            assert formats[format_id].interaction_status == FileInteractionStatus.READY
            assert formats[format_id].status_reason is None
            assert (
                formats[format_id].parser_id
                == "office-parser-mcp.extract_office_document"
            )
        ready_extensions = set(registry.extensions_for(purpose, FileInputKind.DOCUMENT))
        assert {".docx", ".pptx"} <= ready_extensions
        catalog_extensions = set(
            registry.extensions_for(
                purpose,
                FileInputKind.DOCUMENT,
                ready_only=False,
            )
        )
        assert {".docx", ".pptx"} <= catalog_extensions

    agent_policy = next(
        item
        for item in registry.policies_for(FilePurpose.AGENT)
        if item.input_kind == FileInputKind.DOCUMENT
    )
    assert {"docx", "pptx"}.isdisjoint(agent_policy.format_ids)


@pytest.mark.parametrize(
    ("format_id", "media_type"),
    (("docx", DOCX_MEDIA_TYPE), ("pptx", PPTX_MEDIA_TYPE)),
)
def test_ready_office_formats_pass_light_preflight_before_bridge_invocation(
    format_id: str,
    media_type: str,
) -> None:
    content = _office_package(format_id)
    validator = FileUploadValidator()
    validated = validator.validate_stream(
        io.BytesIO(content),
        purpose=FilePurpose.CHAT,
        input_kind=FileInputKind.DOCUMENT,
        filename=f"golden.{format_id}",
        declared_media_type=media_type,
    )
    assert validated.format_id == format_id


def test_pptx_preflight_allows_inert_printer_settings_binary() -> None:
    content = _rewrite_package(
        _office_package("pptx"),
        additions={"ppt/printerSettings/printerSettings1.bin": b"printer settings"},
    )
    validated = FileUploadValidator().validate_stream(
        io.BytesIO(content),
        purpose=FilePurpose.CHAT,
        input_kind=FileInputKind.DOCUMENT,
        filename="golden.pptx",
        declared_media_type=PPTX_MEDIA_TYPE,
    )
    assert validated.format_id == "pptx"


@pytest.mark.parametrize("format_id", ("docx", "pptx"))
def test_ooxml_preflight_never_reads_or_crc_checks_members(
    format_id: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    content = _office_package(format_id)
    real_zip_file = zipfile.ZipFile

    class DirectoryOnlyZipFile(real_zip_file):
        def open(self, *args, **kwargs):  # type: ignore[no-untyped-def]
            del args, kwargs
            raise AssertionError("API preflight must not decompress members")

        def read(self, *args, **kwargs):  # type: ignore[no-untyped-def]
            del args, kwargs
            raise AssertionError("API preflight must not read members")

        def testzip(self):  # type: ignore[no-untyped-def]
            raise AssertionError("API preflight must not run CRC scans")

    monkeypatch.setattr(validation_module.zipfile, "ZipFile", DirectoryOnlyZipFile)
    _preflight(content, format_id)


@pytest.mark.parametrize("format_id", ("docx", "pptx"))
def test_ooxml_preflight_defers_relationship_xml_to_sidecar(format_id: str) -> None:
    content = _office_package(format_id, hyperlink_target=None)
    relationship_name = (
        "word/_rels/document.xml.rels"
        if format_id == "docx"
        else "ppt/_rels/presentation.xml.rels"
    )
    external_image = _relationships(
        _relationship(
            "rExternal",
            f"{OFFICE_REL_NS}/image",
            "https://example.invalid/pixel.png",
            external=True,
        )
    )
    if format_id == "pptx":
        external_image = _relationships(
            _relationship("rId1", f"{OFFICE_REL_NS}/slide", "slides/slide1.xml"),
            _relationship(
                "rExternal",
                f"{OFFICE_REL_NS}/image",
                "https://example.invalid/pixel.png",
                external=True,
            ),
        )
    content = _rewrite_package(
        content,
        replacements={relationship_name: external_image}
        if relationship_name in _package_names(content)
        else None,
        additions={relationship_name: external_image}
        if relationship_name not in _package_names(content)
        else None,
    )
    # Central-directory checks intentionally accept this package. The
    # network-free sidecar deep validator rejects the external image before
    # any document library is invoked.
    _preflight(content, format_id)


def _package_names(content: bytes) -> set[str]:
    with zipfile.ZipFile(io.BytesIO(content)) as archive:
        return set(archive.namelist())


@pytest.mark.parametrize("format_id", ("docx", "pptx"))
@pytest.mark.parametrize(
    "member_name",
    (
        "word/vbaProject.bin",
        "word/activeX/activeX1.xml",
        "word/embeddings/oleObject1.bin",
    ),
)
def test_ooxml_preflight_rejects_macros_activex_and_ole(
    format_id: str,
    member_name: str,
) -> None:
    if format_id == "pptx":
        member_name = member_name.replace("word/", "ppt/")
    content = _rewrite_package(
        _office_package(format_id),
        additions={member_name: b"not-executed"},
    )
    with pytest.raises(FileValidationError) as captured:
        _preflight(content, format_id)
    assert captured.value.error_code == f"unsupported_{format_id}_feature"


@pytest.mark.parametrize("format_id", ("docx", "pptx"))
def test_ooxml_preflight_rejects_broken_zip_and_missing_required_part(
    format_id: str,
) -> None:
    with pytest.raises(FileValidationError) as captured:
        _preflight(b"PK\x03\x04broken", format_id)
    assert captured.value.error_code == f"invalid_{format_id}"

    output = io.BytesIO()
    with zipfile.ZipFile(output, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("[Content_Types].xml", _content_types(format_id))
    with pytest.raises(FileValidationError) as captured:
        _preflight(output.getvalue(), format_id)
    assert captured.value.error_code == f"invalid_{format_id}"


@pytest.mark.parametrize("format_id", ("docx", "pptx"))
def test_ooxml_preflight_defers_active_xml_to_sidecar(format_id: str) -> None:
    content = _office_package(format_id)
    main_part = "word/document.xml" if format_id == "docx" else "ppt/presentation.xml"
    with zipfile.ZipFile(io.BytesIO(content)) as archive:
        active_xml = archive.read(main_part).replace(
            b"</",
            b"<field>INCLUDEPICTURE https://example.invalid/pixel.png</field></",
            1,
        )
    active = _rewrite_package(content, replacements={main_part: active_xml})
    _preflight(active, format_id)


def test_parsed_section_contract_has_slide_source_without_office_dependency() -> None:
    section = ParsedSection(text="标题", slide=3, heading_path=("季度回顾",))
    assert section.model_dump()["slide"] == 3

"""Read-compatibility fixtures only; never a runtime path to build legacy data."""
from __future__ import annotations


def mark_version_as_legacy_parser(service, version_id: str) -> None:
    """Represent a pre-4C stored version in the test's temporary metadata.

    Build guards cannot create such a version anymore. Downgrade just the test
    snapshot after its offline build; leave the indices and active pointer alone.
    """
    with service._metadata_lock:
        metadata = service._read_metadata_unlocked()
        version = metadata["pipeline_versions"][version_id]
        profiles = [version["processor_profile"], version["config_snapshot"]["stages"]["stage_processor"]]
        for profile in profiles:
            profile.pop("parser_contract_version", None)
            profile.pop("processing_receipt_version", None)
        version.pop("content_index_contract", None)
        version["config_snapshot"].pop("content_index_contract", None)
        service._write_metadata_unlocked(metadata)

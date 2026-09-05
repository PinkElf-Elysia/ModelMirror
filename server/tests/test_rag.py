from __future__ import annotations

from server.tests.test_rag_integration import (
    client,
    pipeline_runtime,
    test_create_upload_pipeline_query_and_cleanup,
    test_empty_knowledge_base_query_returns_hint,
    test_query_after_delete_returns_404,
    test_unsupported_file_type_returns_400,
)

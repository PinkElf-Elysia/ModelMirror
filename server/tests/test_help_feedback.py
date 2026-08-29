"""帮助中心文章反馈 Store 测试。"""

import pytest

from server.help_feedback_store import DuplicateFeedbackError, HelpFeedbackStore


def test_add_and_stats(tmp_path):
    store = HelpFeedbackStore(storage_dir=tmp_path)
    store.add_feedback(
        slug="start-with-a-model",
        article_version="cc49136c",
        value="helpful",
        anonymous_id="u1",
    )
    store.add_feedback(
        slug="start-with-a-model",
        article_version="cc49136c",
        value="not-helpful",
        anonymous_id="u2",
    )
    stats = store.stats(slug="start-with-a-model")
    assert stats["total"] == 2
    assert stats["helpful"] == 1


def test_duplicate_same_article_rejected(tmp_path):
    store = HelpFeedbackStore(storage_dir=tmp_path)
    store.add_feedback(
        slug="a", article_version="v1", value="helpful", anonymous_id="u1"
    )
    with pytest.raises(DuplicateFeedbackError):
        store.add_feedback(
            slug="a", article_version="v1", value="not-helpful", anonymous_id="u1"
        )


def test_same_user_different_article_allowed(tmp_path):
    store = HelpFeedbackStore(storage_dir=tmp_path)
    store.add_feedback(
        slug="a", article_version="v1", value="helpful", anonymous_id="u1"
    )
    store.add_feedback(
        slug="b", article_version="v1", value="not-helpful", anonymous_id="u1"
    )
    assert store.stats()["total"] == 2


def test_stats_all_and_empty(tmp_path):
    store = HelpFeedbackStore(storage_dir=tmp_path)
    assert store.stats()["total"] == 0
    assert store.stats(slug="nonexistent")["total"] == 0

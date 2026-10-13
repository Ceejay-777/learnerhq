import pytest
from unittest.mock import MagicMock, patch

from apps.ai.services import _validate_shape, _is_retryable_error
from apps.ai.exceptions import ProviderError
from apps.ai.prompts import REVIEW_SCHEMA, TOPIC_CONTENT_SCHEMA


class TestValidateShape:
    def test_passes_valid_data(self):
        data = {
            "accuracy": {"pass": True, "note": "OK"},
            "audience_fit": {"pass": True, "note": "OK"},
            "relevance": {"pass": True, "note": "OK"},
            "completeness": {"pass": True, "note": "OK"},
            "overall_passed": True,
        }
        _validate_shape(data, REVIEW_SCHEMA)  # should not raise

    def test_fails_on_missing_key(self):
        data = {
            "accuracy": {"pass": True, "note": "OK"},
            # missing audience_fit, relevance, completeness, overall_passed
        }
        from apps.learning.exceptions import GenerationError
        with pytest.raises(GenerationError, match="Missing required key"):
            _validate_shape(data, REVIEW_SCHEMA)

    def test_fails_on_wrong_type_string(self):
        data = {
            "accuracy": {"pass": "yes", "note": "OK"},  # pass should be bool
            "audience_fit": {"pass": True, "note": "OK"},
            "relevance": {"pass": True, "note": "OK"},
            "completeness": {"pass": True, "note": "OK"},
            "overall_passed": True,
        }
        from apps.learning.exceptions import GenerationError
        with pytest.raises(GenerationError, match="expected boolean"):
            _validate_shape(data, REVIEW_SCHEMA)

    def test_fails_on_non_object_top_level(self):
        from apps.learning.exceptions import GenerationError
        with pytest.raises(GenerationError, match="Expected object"):
            _validate_shape("not a dict", REVIEW_SCHEMA)

    def test_validates_nested_objects(self):
        data = {
            "summary": "A summary",
            "resource_links": [
                {"title": "T", "url": "http://x.com"},  # missing 'type' key
            ],
        }
        from apps.learning.exceptions import GenerationError
        with pytest.raises(GenerationError, match="Missing required key"):
            _validate_shape(data, TOPIC_CONTENT_SCHEMA)

    def test_topic_content_valid_data(self):
        data = {
            "summary": "A detailed summary about quantum physics.",
            "resource_links": [
                {"title": "Wikipedia", "url": "https://en.wikipedia.org/wiki/QM", "type": "article"},
                {"title": "Video", "url": "https://youtube.com/watch?v=xyz", "type": "video"},
            ],
        }
        _validate_shape(data, TOPIC_CONTENT_SCHEMA)  # should not raise

    def test_topic_content_missing_summary(self):
        data = {
            "resource_links": [
                {"title": "A", "url": "http://a.com", "type": "article"},
                {"title": "B", "url": "http://b.com", "type": "video"},
            ],
        }
        from apps.learning.exceptions import GenerationError
        with pytest.raises(GenerationError, match="Missing required key: summary"):
            _validate_shape(data, TOPIC_CONTENT_SCHEMA)


class TestIsRetryableError:
    def test_provider_error_is_not_retryable(self):
        exc = ProviderError("test", provider="groq", recoverable=True)
        # ProviderError is not a known SDK exception class
        assert _is_retryable_error(exc) is False

    def test_non_retryable_returns_false(self):
        assert _is_retryable_error(ValueError("nope")) is False


class TestCallProvider:
    """Test the provider dispatch function."""

    @patch("apps.ai.services.call_gemini_native")
    def test_gemini_routes_to_native_sdk(self, mock_gemini):
        from apps.ai.services import _call_provider
        mock_gemini.return_value = {"result": "ok"}
        result = _call_provider("gemini", "gemini-3.8-flash", [{"role": "user", "content": "hi"}], {})
        mock_gemini.assert_called_once()
        assert result == {"result": "ok"}

    @patch("apps.ai.services._call_openai_compatible")
    def test_groq_routes_to_openai_compatible(self, mock_openai):
        from apps.ai.services import _call_provider
        mock_openai.return_value = {"result": "ok"}
        result = _call_provider("groq", "openai/gpt-oss-120b", [{"role": "user", "content": "hi"}], {})
        mock_openai.assert_called_once()
        assert result == {"result": "ok"}

    def test_unknown_provider_raises(self):
        from apps.ai.services import _call_provider
        with pytest.raises(ValueError, match="Unknown provider"):
            _call_provider("unknown", "model", [], {})


class TestReviewContent:
    """Test the review_content public API."""

    @patch("apps.ai.services._call_with_fallback")
    def test_returns_result_on_success(self, mock_fallback):
        from apps.ai.services import review_content
        mock_fallback.return_value = {
            "accuracy": {"pass": True, "note": "OK"},
            "audience_fit": {"pass": True, "note": "OK"},
            "relevance": {"pass": True, "note": "OK"},
            "completeness": {"pass": True, "note": "OK"},
            "overall_passed": True,
        }
        result = review_content("Some content")
        assert result["overall_passed"] is True

    @patch("apps.ai.services._call_with_fallback")
    def test_flags_for_manual_review_on_provider_failure(self, mock_fallback):
        from apps.ai.services import review_content
        mock_fallback.side_effect = ProviderError("All failed", provider="groq")
        result = review_content("Some content")
        assert result["overall_passed"] is False
        assert "all providers failed" in result["accuracy"]["note"].lower()

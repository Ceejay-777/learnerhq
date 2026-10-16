import pytest
from unittest.mock import MagicMock, patch

from apps.ai.services import _validate_shape, _is_retryable_error
from apps.ai.exceptions import ProviderError
from apps.ai.prompts import REVIEW_SCHEMA, TOPIC_CONTENT_SCHEMA
from apps.ai.providers import PROVIDERS


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


class TestGenerateContent:
    """generate_content must honor the caller's output_schema."""

    ROADMAP_LIKE = {
        "type": "object",
        "properties": {"topics": {"type": "array", "items": {"type": "object"}}},
        "required": ["topics"],
    }

    @patch("apps.ai.services._call_with_fallback")
    def test_passes_caller_schema_to_provider(self, mock_fallback):
        from apps.ai.services import generate_content
        mock_fallback.return_value = {"topics": []}
        generate_content("p", output_schema=self.ROADMAP_LIKE)
        _, _, schema, _ = mock_fallback.call_args.args
        assert schema is self.ROADMAP_LIKE

    @patch("apps.ai.services._call_with_fallback")
    def test_validates_against_caller_schema(self, mock_fallback):
        from apps.ai.services import generate_content
        from apps.learning.exceptions import GenerationError
        # A topic-content-shaped response is wrong for a roadmap schema.
        mock_fallback.return_value = {"summary": "s", "resource_links": []}
        with pytest.raises(GenerationError):
            generate_content("p", output_schema=self.ROADMAP_LIKE)

    @patch("apps.ai.services._call_with_fallback")
    def test_defaults_to_topic_content_schema(self, mock_fallback):
        from apps.ai.prompts import TOPIC_CONTENT_SCHEMA
        from apps.ai.services import generate_content
        mock_fallback.return_value = {"summary": "s", "resource_links": []}
        generate_content("p")
        assert mock_fallback.call_args.args[2] is TOPIC_CONTENT_SCHEMA


class TestActionChains:
    """The chains and per-action settings come from docs/groq-model-test-report.md."""

    EXPECTED = {
        "roadmap": [("openai/gpt-oss-120b", "medium"), ("qwen/qwen3.8-27b", None), ("openai/gpt-oss-20b", "medium")],
        "topic": [("openai/gpt-oss-120b", "medium"), ("qwen/qwen3.8-27b", None), ("openai/gpt-oss-20b", "medium")],
        "review": [("qwen/qwen3.8-27b", "default"), ("openai/gpt-oss-120b", "medium"), ("openai/gpt-oss-20b", "medium")],
        "quiz_normal": [("openai/gpt-oss-120b", "low"), ("openai/gpt-oss-20b", "low"), ("qwen/qwen3.8-27b", None)],
        "quiz_advanced": [("openai/gpt-oss-120b", "medium"), ("openai/gpt-oss-20b", "medium"), ("qwen/qwen3.8-27b", None)],
        "standardize": [("openai/gpt-oss-20b", "low"), ("openai/gpt-oss-120b", "low")],
        "resolve": [("openai/gpt-oss-20b", "low"), ("openai/gpt-oss-120b", "low")],
    }

    def test_chains_match_the_agreed_table(self):
        from apps.ai.services import _ACTION_CHAINS
        assert set(_ACTION_CHAINS) == set(self.EXPECTED)
        for action, expected in self.EXPECTED.items():
            assert [(s.model, s.effort) for s in _ACTION_CHAINS[action]] == expected
            assert all(s.provider == "groq" for s in _ACTION_CHAINS[action])

    def test_every_action_has_a_token_cap(self):
        from apps.ai.services import _ACTION_CHAINS, _MAX_COMPLETION_TOKENS
        assert set(_MAX_COMPLETION_TOKENS) == set(_ACTION_CHAINS)
        assert all(v >= 4096 for v in _MAX_COMPLETION_TOKENS.values())
        for action in ("roadmap", "topic", "quiz_normal", "quiz_advanced"):
            assert _MAX_COMPLETION_TOKENS[action] == 8192


class TestFallbackDispatch:
    MSGS = [{"role": "user", "content": "hi"}]

    @patch("apps.ai.services._call_provider")
    def test_passes_effort_and_token_cap_for_each_step(self, mock_call):
        from apps.ai.services import _call_with_fallback
        mock_call.return_value = {"ok": True}
        _call_with_fallback("quiz_normal", self.MSGS, {}, "Quiz")
        args, kwargs = mock_call.call_args
        assert args[1] == "openai/gpt-oss-120b"
        assert kwargs == {"effort": "low", "max_tokens": 8192}

    @patch("apps.ai.services._call_provider")
    def test_falls_through_the_chain_in_order(self, mock_call):
        from apps.ai.services import _call_with_fallback
        mock_call.side_effect = [RuntimeError("503"), RuntimeError("429"), {"ok": True}]
        result = _call_with_fallback("roadmap", self.MSGS, {}, "Roadmap")
        assert result == {"ok": True}
        used = [c.args[1] for c in mock_call.call_args_list]
        assert used == ["openai/gpt-oss-120b", "qwen/qwen3.8-27b", "openai/gpt-oss-20b"]
        # The third step is gpt-oss-20b at medium effort.
        assert mock_call.call_args_list[2].kwargs["effort"] == "medium"
        # Qwen has no effort override for roadmaps.
        assert mock_call.call_args_list[1].kwargs["effort"] is None

    @patch("apps.ai.services._call_provider", side_effect=RuntimeError("down"))
    def test_raises_provider_error_when_every_step_fails(self, _mock):
        from apps.ai.services import _call_with_fallback
        with pytest.raises(ProviderError, match="All providers failed for review"):
            _call_with_fallback("review", self.MSGS, {}, "Review")

    def test_unknown_action_is_rejected(self):
        from apps.ai.services import _call_with_fallback
        with pytest.raises(ValueError, match="Unknown AI action"):
            _call_with_fallback("nope", self.MSGS, {}, "X")


class TestGroqCallParameters:
    @staticmethod
    def _fake_client(body, captured):
        class Resp:
            def raise_for_status(self):
                pass

            def json(self):
                return body

        class Client:
            def post(self, path, json):
                captured["payload"] = json
                return Resp()

        return Client()

    @patch("apps.ai.services.get_client")
    def test_sends_reasoning_effort_and_max_completion_tokens(self, mock_client):
        from apps.ai.services import _call_openai_compatible
        captured = {}
        mock_client.return_value = self._fake_client(
            {"choices": [{"finish_reason": "stop", "message": {"content": "{\"a\": 1}"}}]}, captured
        )
        out = _call_openai_compatible(
            PROVIDERS["groq"], "openai/gpt-oss-120b", [], {"type": "json_schema"},
            reasoning_effort="low", max_completion_tokens=8192,
        )
        assert out == {"a": 1}
        assert captured["payload"]["reasoning_effort"] == "low"
        assert captured["payload"]["max_completion_tokens"] == 8192

    @patch("apps.ai.services.get_client")
    def test_omits_optional_parameters_when_unset(self, mock_client):
        from apps.ai.services import _call_openai_compatible
        captured = {}
        mock_client.return_value = self._fake_client(
            {"choices": [{"finish_reason": "stop", "message": {"content": "{}"}}]}, captured
        )
        _call_openai_compatible(PROVIDERS["groq"], "qwen/qwen3.8-27b", [])
        assert "reasoning_effort" not in captured["payload"]
        assert "max_completion_tokens" not in captured["payload"]

    @patch("apps.ai.services.get_client")
    def test_truncated_response_is_an_error_not_partial_json(self, mock_client):
        from apps.ai.services import _call_openai_compatible
        mock_client.return_value = self._fake_client(
            {"choices": [{"finish_reason": "length", "message": {"content": "{\"a\": [1, 2"}}]}, {}
        )
        with pytest.raises(ProviderError, match="truncated"):
            _call_openai_compatible(PROVIDERS["groq"], "openai/gpt-oss-20b", [], max_completion_tokens=2048)

    @patch("apps.ai.services._call_openai_compatible")
    def test_truncation_moves_the_chain_to_the_next_model(self, mock_openai):
        from apps.ai.services import _call_with_fallback
        mock_openai.side_effect = [ProviderError("truncated", provider="groq"), {"ok": True}]
        assert _call_with_fallback("quiz_normal", [], {}, "Quiz") == {"ok": True}
        assert [c.args[1] for c in mock_openai.call_args_list] == ["openai/gpt-oss-120b", "openai/gpt-oss-20b"]


class TestGenerateContentAction:
    @patch("apps.ai.services._call_with_fallback")
    def test_action_selects_the_chain(self, mock_fallback):
        from apps.ai.services import generate_content
        mock_fallback.return_value = {"topics": []}
        schema = {"type": "object", "properties": {"topics": {"type": "array"}}, "required": ["topics"]}
        generate_content("p", output_schema=schema, action="roadmap")
        assert mock_fallback.call_args.args[0] == "roadmap"

    @patch("apps.ai.services._call_with_fallback")
    def test_defaults_to_topic_action(self, mock_fallback):
        from apps.ai.services import generate_content
        mock_fallback.return_value = {"summary": "s", "resource_links": []}
        generate_content("p")
        assert mock_fallback.call_args.args[0] == "topic"

    @patch("apps.ai.services._call_with_fallback")
    def test_standardize_uses_its_own_chain(self, mock_fallback):
        from apps.ai.services import standardize_subject_name
        mock_fallback.return_value = {"standardized_name": "X", "is_specific": True}
        standardize_subject_name("x")
        assert mock_fallback.call_args.args[0] == "standardize"

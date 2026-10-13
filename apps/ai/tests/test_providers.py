import pytest
from unittest.mock import MagicMock, patch

from apps.ai.providers import (
    PROVIDERS,
    ProviderConfig,
    build_groq_response_format,
    build_gemini_response_format,
    reset_verification,
    verify_provider_models,
)
from apps.ai.exceptions import ProviderError


class TestProviderConfig:
    def test_providers_dict_has_expected_keys(self):
        assert "groq" in PROVIDERS
        assert "gemini" in PROVIDERS
        assert "opencode_zen" not in PROVIDERS

    def test_gemini_config_has_generate_models(self):
        gemini = PROVIDERS["gemini"]
        assert "gemini-3.8-flash" in gemini.generate_models
        assert "gemini-3.7-flash" in gemini.generate_models
        assert "gemini-3.5-flash-lite" in gemini.generate_models

    def test_groq_config_has_review_models(self):
        groq = PROVIDERS["groq"]
        assert "openai/gpt-oss-120b" in groq.review_models
        assert "openai/gpt-oss-20b" in groq.review_models


class TestResponseFormatBuilders:
    def test_groq_format_has_best_effort_schema(self):
        schema = {"type": "object", "properties": {"x": {"type": "string"}}}
        result = build_groq_response_format(schema, "TestSchema")
        assert result["type"] == "json_schema"
        assert result["json_schema"]["strict"] is False
        assert result["json_schema"]["name"] == "TestSchema"
        assert result["json_schema"]["schema"] == schema

    def test_gemini_format_returns_none(self):
        result = build_gemini_response_format()
        assert result is None


class TestVerifyProviderModels:
    def setup_method(self):
        reset_verification()

    def test_gemini_skips_verification(self):
        """Gemini uses native SDK — models validated at call time."""
        config = PROVIDERS["gemini"]
        result = verify_provider_models(config)
        assert "gemini-3.8-flash" in result

    def test_verify_caches_result(self):
        import httpx as _httpx

        mock_config = ProviderConfig(
            name="test_provider",
            base_url="http://test.com/v1",
            api_key_env="TEST_API_KEY",
            generate_models=["model-a"],
            review_models=["model-b"],
        )

        mock_resp = MagicMock()
        mock_resp.json.return_value = {
            "data": [{"id": "model-a"}, {"id": "model-b"}]
        }
        mock_resp.raise_for_status = MagicMock()

        with patch("httpx.get", return_value=mock_resp):
            with patch.dict("os.environ", {"TEST_API_KEY": "test-key"}):
                result1 = verify_provider_models(mock_config)
                result2 = verify_provider_models(mock_config)

        assert result1 == result2
        assert result1 == ["model-a", "model-b"]

    def test_verify_raises_on_missing_model(self):
        mock_config = ProviderConfig(
            name="test_provider",
            base_url="http://test.com/v1",
            api_key_env="TEST_API_KEY",
            generate_models=["nonexistent-model"],
            review_models=[],
        )

        mock_resp = MagicMock()
        mock_resp.json.return_value = {
            "data": [{"id": "other-model"}]
        }
        mock_resp.raise_for_status = MagicMock()

        with patch("httpx.get", return_value=mock_resp):
            with patch.dict("os.environ", {"TEST_API_KEY": "test-key"}):
                with pytest.raises(ProviderError, match="models not available"):
                    verify_provider_models(mock_config)


class TestMessageBuilders:
    def test_build_generation_messages_with_schema(self):
        from apps.ai.prompts import build_generation_messages
        messages = build_generation_messages(
            prompt="Generate content",
            system_instruction="Be educational",
            output_schema={"type": "object", "properties": {"x": {"type": "string"}}},
        )
        assert len(messages) == 2
        assert messages[0]["role"] == "system"
        assert "Be educational" in messages[0]["content"]
        assert "Respond with valid JSON" in messages[1]["content"]

    def test_build_review_messages(self):
        from apps.ai.prompts import build_review_messages
        messages = build_review_messages(
            content="Some content",
            criteria={"topic_title": "AI", "subject_name": "CS"},
        )
        assert len(messages) == 2
        assert "AI" in messages[1]["content"]
        assert "CS" in messages[1]["content"]

    def test_build_resolve_messages(self):
        from apps.ai.prompts import build_resolve_messages
        messages = build_resolve_messages(
            input_text="machine learning",
            candidates=[{"id": 1, "score": 0.9}],
        )
        assert len(messages) == 2
        assert "machine learning" in messages[1]["content"]

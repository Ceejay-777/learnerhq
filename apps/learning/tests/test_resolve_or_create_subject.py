from unittest.mock import patch
import pytest
from apps.ai.exceptions import ProviderError
from apps.learning.models import Subject
from apps.learning.services import resolve_or_create_subject, GenerationError


@pytest.mark.django_db
class TestResolveOrCreateExactMatch:
    def test_exact_name_match_returns_existing(self):
        subject = Subject.objects.create(name="World War II")
        result = resolve_or_create_subject("World War II")
        assert result.action == "resolve"
        assert result.subject.id == subject.id

    def test_exact_name_match_case_insensitive(self):
        subject = Subject.objects.create(name="World War II")
        result = resolve_or_create_subject("WORLD war ii")
        assert result.action == "resolve"
        assert result.subject.id == subject.id


@pytest.mark.django_db
class TestResolveOrCreateViaSimilarity:

    @patch("apps.learning.services._find_similar_subjects")
    @patch("apps.learning.services.generate_embedding")
    @patch("apps.learning.services.rank_or_resolve")
    def test_existing_subject_via_similarity(self, mock_resolve, mock_embed, mock_find):
        mock_embed.return_value = [0.1] * 3072
        mock_find.return_value = [{"id": "1", "name": "World War II", "similarity_score": 0.95}]
        existing = Subject.objects.create(name="World War II")
        mock_resolve.return_value = {
            "action": "resolve",
            "subject_id": str(existing.id),
            "standardized_name": "World War II",
        }
        result = resolve_or_create_subject("WW2")
        assert result.action == "resolve"
        assert result.subject.id == existing.id

    @patch("apps.learning.services._find_similar_subjects")
    @patch("apps.learning.services.generate_embedding")
    @patch("apps.learning.services.rank_or_resolve")
    def test_no_match_creates_new_subject(self, mock_resolve, mock_embed, mock_find):
        mock_embed.return_value = [0.1] * 3072
        mock_find.return_value = [{"id": "1", "name": "History", "similarity_score": 0.5}]
        Subject.objects.create(name="History")
        mock_resolve.return_value = {
            "action": "create",
            "standardized_name": "Pacific Theater WW2",
        }
        result = resolve_or_create_subject("Pacific Theater")
        assert result.action == "resolve"
        assert result.subject.name == "Pacific Theater WW2"
        assert Subject.objects.filter(name="Pacific Theater WW2").exists()

    @patch("apps.learning.services._find_similar_subjects")
    @patch("apps.learning.services.generate_embedding")
    @patch("apps.learning.services.rank_or_resolve")
    def test_overly_broad_input_returns_narrow(self, mock_resolve, mock_embed, mock_find):
        mock_embed.return_value = [0.1] * 3072
        mock_find.return_value = [{"id": "1", "name": "World War II", "similarity_score": 0.4}]
        Subject.objects.create(name="World War II")
        mock_resolve.return_value = {
            "action": "narrow",
            "suggestion": "Which aspect of History? Try: WW2, French Revolution",
        }
        result = resolve_or_create_subject("History")
        assert result.action == "narrow"
        assert "WW2" in result.suggestion
        assert Subject.objects.count() == 1

    @patch("apps.learning.services._find_similar_subjects")
    @patch("apps.learning.services.generate_embedding")
    @patch("apps.learning.services.rank_or_resolve")
    def test_unknown_action_raises_error(self, mock_resolve, mock_embed, mock_find):
        mock_embed.return_value = [0.1] * 3072
        mock_find.return_value = [{"id": "1", "name": "World War II", "similarity_score": 0.5}]
        Subject.objects.create(name="World War II")
        mock_resolve.return_value = {"action": "unknown"}
        with pytest.raises(GenerationError):
            resolve_or_create_subject("Something")

    @patch("apps.learning.services._compute_and_store_embedding")
    @patch("apps.learning.services._find_similar_subjects")
    @patch("apps.learning.services.generate_embedding")
    @patch("apps.learning.services.rank_or_resolve")
    def test_create_action_rolls_back_on_embedding_failure(
        self, mock_resolve, mock_embed, mock_find, mock_compute
    ):
        mock_embed.return_value = [0.1] * 3072
        mock_find.return_value = [{"id": "1", "name": "History", "similarity_score": 0.3}]
        Subject.objects.create(name="History")
        mock_resolve.return_value = {"action": "create", "standardized_name": "World War II"}
        mock_compute.side_effect = ProviderError("API down", provider="gemini")
        with pytest.raises(ProviderError):
            resolve_or_create_subject("WW2")
        assert not Subject.objects.filter(name="World War II").exists()


@pytest.mark.django_db
class TestResolveOrCreateEmptyDatabase:

    @patch("apps.learning.services._compute_and_store_embedding")
    @patch("apps.learning.services.standardize_subject_name")
    @patch("apps.learning.services.generate_embedding")
    def test_creates_with_standardized_name(self, mock_embed, mock_std_name, mock_compute):
        mock_embed.return_value = [0.1] * 3072
        mock_std_name.return_value = {
            "standardized_name": "World War II",
            "is_specific": True,
        }
        result = resolve_or_create_subject("ww2")
        assert result.action == "resolve"
        assert result.subject.name == "World War II"
        assert Subject.objects.filter(name="World War II").exists()

    @patch("apps.learning.services.standardize_subject_name")
    @patch("apps.learning.services.generate_embedding")
    def test_narrows_broad_input(self, mock_embed, mock_canon_name):
        mock_embed.return_value = [0.1] * 3072
        mock_canon_name.return_value = {
            "standardized_name": "History",
            "is_specific": False,
            "suggestion": "Try: World War II, French Revolution, or Ancient Rome",
        }
        result = resolve_or_create_subject("History")
        assert result.action == "narrow"
        assert "World War II" in result.suggestion
        assert not Subject.objects.exists()

    @patch("apps.learning.services._compute_and_store_embedding")
    @patch("apps.learning.services.standardize_subject_name")
    @patch("apps.learning.services.generate_embedding")
    def test_rolls_back_on_embedding_failure(
        self, mock_embed, mock_canon_name, mock_compute
    ):
        mock_embed.return_value = [0.1] * 3072
        mock_canon_name.return_value = {
            "standardized_name": "World War II",
            "is_specific": True,
        }
        mock_compute.side_effect = ProviderError("API down", provider="gemini")
        with pytest.raises(ProviderError):
            resolve_or_create_subject("ww2")
        assert not Subject.objects.filter(name="World War II").exists()

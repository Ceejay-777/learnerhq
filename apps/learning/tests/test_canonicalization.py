from unittest.mock import patch
import pytest
from apps.learning.models import Subject
from apps.learning.services import canonicalize_subject, GenerationError


@pytest.mark.django_db
class TestCanonicalizeExactMatch:
    def test_exact_name_match_returns_existing(self):
        subject = Subject.objects.create(name="World War II")
        result = canonicalize_subject("World War II")
        assert result.action == "resolve"
        assert result.subject.id == subject.id

    def test_exact_name_match_case_insensitive(self):
        subject = Subject.objects.create(name="World War II")
        result = canonicalize_subject("WORLD war ii")
        assert result.action == "resolve"
        assert result.subject.id == subject.id


@pytest.mark.django_db
class TestCanonicalizeViaSimilarity:

    @patch("apps.learning.services.generate_embedding")
    @patch("apps.learning.services.rank_or_resolve")
    def test_existing_subject_via_similarity(self, mock_resolve, mock_embed):
        mock_embed.return_value = [0.1] * 768
        existing = Subject.objects.create(name="World War II")
        mock_resolve.return_value = {
            "action": "resolve",
            "subject_id": str(existing.id),
            "canonical_name": "World War II",
        }
        result = canonicalize_subject("WW2")
        assert result.action == "resolve"
        assert result.subject.id == existing.id

    @patch("apps.learning.services.generate_embedding")
    @patch("apps.learning.services.rank_or_resolve")
    def test_no_match_creates_new_subject(self, mock_resolve, mock_embed):
        mock_embed.return_value = [0.1] * 768
        mock_resolve.return_value = {
            "action": "create",
            "canonical_name": "Pacific Theater WW2",
        }
        result = canonicalize_subject("Pacific Theater")
        assert result.action == "resolve"
        assert result.subject.name == "Pacific Theater WW2"
        assert Subject.objects.filter(name="Pacific Theater WW2").exists()

    @patch("apps.learning.services.generate_embedding")
    @patch("apps.learning.services.rank_or_resolve")
    def test_overly_broad_input_returns_narrow(self, mock_resolve, mock_embed):
        mock_embed.return_value = [0.1] * 768
        Subject.objects.create(name="World War II")
        mock_resolve.return_value = {
            "action": "narrow",
            "suggestion": "Which aspect of History? Try: WW2, French Revolution",
        }
        result = canonicalize_subject("History")
        assert result.action == "narrow"
        assert "WW2" in result.suggestion
        assert Subject.objects.count() == 1

    @patch("apps.learning.services.generate_embedding")
    @patch("apps.learning.services.rank_or_resolve")
    def test_unknown_action_raises_error(self, mock_resolve, mock_embed):
        mock_embed.return_value = [0.1] * 768
        Subject.objects.create(name="World War II")
        mock_resolve.return_value = {"action": "unknown"}
        with pytest.raises(GenerationError):
            canonicalize_subject("Something")

    @patch("apps.learning.services.generate_embedding")
    @patch("apps.learning.services.rank_or_resolve")
    def test_empty_database_creates_new(self, mock_resolve, mock_embed):
        mock_embed.return_value = [0.1] * 768
        mock_resolve.return_value = {
            "action": "create",
            "canonical_name": "World War II",
        }
        result = canonicalize_subject("World War II")
        assert result.action == "resolve"
        assert result.subject.name == "World War II"

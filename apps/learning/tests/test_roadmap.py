from unittest.mock import patch
import pytest
from apps.learning.models import Subject, Topic
from apps.learning.services import generate_roadmap, GenerationError


@pytest.mark.django_db
class TestGenerateRoadmap:
    VALID = {
        "topics": [
            {"title": f"Topic {i}", "level": (i // 7) + 1, "description": "desc"}
            for i in range(1, 21)
        ],
    }

    def _subject(self):
        return Subject.objects.create(name="World War II")

    @patch("apps.learning.services.generate_content")
    def test_generates_20_topics(self, mock_generate):
        mock_generate.return_value = self.VALID
        topics = generate_roadmap(self._subject())
        assert len(topics) == 20
        for i, t in enumerate(topics):
            assert t.subject_id is not None
            assert t.order == i + 1
            assert t.level in (1, 2, 3)
            assert t.content_status == Topic.ContentStatus.NOT_GENERATED

    @patch("apps.learning.services.generate_content")
    def test_generates_25_topics(self, mock_generate):
        data = {
            "topics": [
                {"title": f"Topic {i}", "level": (i // 9) + 1, "description": "desc"}
                for i in range(1, 26)
            ],
        }
        mock_generate.return_value = data
        topics = generate_roadmap(self._subject())
        assert len(topics) == 25

    @patch("apps.learning.services.generate_content")
    def test_retries_on_failure_then_succeeds(self, mock_generate):
        mock_generate.side_effect = [{"topics": []}, self.VALID]
        topics = generate_roadmap(self._subject())
        assert len(topics) == 20
        assert mock_generate.call_count == 2

    @patch("apps.learning.services.generate_content")
    def test_repeated_failure_raises_error(self, mock_generate):
        mock_generate.return_value = {"topics": []}
        with pytest.raises(GenerationError):
            generate_roadmap(self._subject())
        assert mock_generate.call_count == 2

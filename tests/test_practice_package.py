import json
import os
import unittest
from unittest.mock import patch

os.environ.setdefault("GEMINI_API_KEY", "test-key")

from backend.app.services.ai_service import (
    generate_assessment_from_text,
    generate_practice_package,
)


def question(index: int, option_count: int = 4) -> dict:
    return {
        "topic": "Cells",
        "question_text": f"Question {index}",
        "options": [f"Option {option}" for option in range(option_count)],
        "correct_option_index": 0,
        "explanation": "Because the supplied context supports this answer.",
    }


class PracticePackageTests(unittest.TestCase):
    @staticmethod
    def response(questions: list[dict]) -> object:
        return type("Response", (), {"text": json.dumps({
            "topic": "Cells",
            "multiple_choice": questions,
            "flashcards": [],
            "matching_pairs": [],
        })})()

    @staticmethod
    def assessment_response(questions: list[dict]) -> object:
        return type("Response", (), {"text": json.dumps({
            "detected_errors": [],
            "topic": ["Cells"],
            "questions": questions,
        })})()

    @patch("backend.app.services.ai_service._generate_content_with_fallback")
    def test_practice_package_keeps_ten_valid_questions(self, mock_generate):
        mock_generate.return_value = self.response([question(index) for index in range(10)])

        package = generate_practice_package("Cells", "Cell study material")

        self.assertEqual(len(package.multiple_choice), 10)
        self.assertTrue(all(len(item.options) == 4 for item in package.multiple_choice))

    @patch("backend.app.services.ai_service._generate_content_with_fallback")
    def test_practice_package_accepts_fewer_valid_questions(self, mock_generate):
        mock_generate.return_value = self.response([question(index) for index in range(8)])

        package = generate_practice_package("Cells", "Limited cell study material")

        self.assertEqual(len(package.multiple_choice), 8)

    @patch("backend.app.services.ai_service._generate_content_with_fallback")
    def test_practice_package_filters_invalid_questions_and_empty_is_safe(self, mock_generate):
        questions = [question(1), question(2, option_count=3), question(3)]
        questions[2]["correct_option_index"] = 4
        mock_generate.return_value = self.response(questions)

        package = generate_practice_package("Cells", "Cell study material")

        self.assertEqual(len(package.multiple_choice), 1)
        mock_generate.return_value = self.response([])
        empty_package = generate_practice_package("Cells", "Insufficient material")
        self.assertEqual(empty_package.multiple_choice, [])

    @patch("backend.app.services.ai_service._generate_content_with_fallback")
    def test_diagnostic_generator_still_requests_four_questions(self, mock_generate):
        mock_generate.return_value = self.assessment_response([question(index) for index in range(4)])

        generate_assessment_from_text("Diagnostic study material")

        prompt = mock_generate.call_args.kwargs["contents"]
        self.assertIn("4-question diagnostic quiz", prompt)
        self.assertNotIn("10 targeted multiple-choice", prompt)


if __name__ == "__main__":
    unittest.main()
# LLM extraction & quiz generation logic
import os
import time
from google import genai
from google.genai import errors
from pydantic import BaseModel, Field
from typing import List
from dotenv import load_dotenv

load_dotenv()  # Load environment variables from .env file
client = genai.Client(api_key=os.getenv("GEMINI_API_KEY"))
MODEL_NAME = os.getenv("GEMINI_MODEL", "gemini-3.6-flash")

class NoteError(BaseModel):
    claimed_concept: str = Field(description="The incorrect statement or claim found in the notes.")
    correction: str = Field(description="The accurate explanation based on standard academic domain principles.")
    severity: str = Field(description="'Warning' or 'Critical Error'")

class Question(BaseModel):
    topic: str
    question_text: str
    options: List[str]
    correct_option_index: int

class Assessment(BaseModel):
    detected_errors: List[NoteError] = Field(
        default=[],
        description="Factual errors, flawed algorithmic complexities, or contradictions detected in the notes."
    )
    topic: List[str]
    questions: List[Question]


def _generate_content(prompt: str, response_schema: type[BaseModel]):
    for attempt in range(3):
        try:
            return client.models.generate_content(
                model=MODEL_NAME,
                contents=prompt,
                config={
                    "response_mime_type": "application/json",
                    "response_schema": response_schema,
                },
            )
        except errors.ServerError as error:
            if error.status_code != 503 or attempt == 2:
                raise
            time.sleep(2 ** attempt)

def generate_assessment_from_text(course_text: str, check_correctness: bool = False) -> Assessment:
    if check_correctness:
        prompt = (
            "You are an academic evaluator. Carefully review the provided lecture notes.\n"
            "1. Audit the text for factual inaccuracies, wrong algorithmic complexities, or contradictions. "
            "List any issues in 'detected_errors' with the student's statement and the correct explanation.\n"
            "2. Extract 4 core technical concepts and generate a 4-question diagnostic quiz (1 question per concept, 4 options each.)\n"
            "Ensure the quiz tests scientifically and academically correct facts even if the notes contain errors.\n\n"
            f"Notes:\n{course_text}"
        )
    else:
        prompt = (
            "Extract key technical concepts from this material and create a "
            "4-question diagnostic quiz (1 multiple-choice question per concept) with 4 options each:\n\n"
            f"Notes:\n{course_text}"
        )

    response = _generate_content(prompt, Assessment)
    return Assessment.model_validate_json(response.text)

# Function to practice a specific topic
def generate_practice_for_topic(topic: str, course_text: str) -> List[Question]:
    """Generates 2 targeted drill questions for a specific weak topic."""
    class PracticeQuestions(BaseModel):
        questions: List[Question]

    prompt = (
        f"Generate 2 targeted multiple-hcoice practice questions specifically focused on the topic: '{topic}'. "
        f"Use the following course material context if available:\n\n{course_text}"
    )
    response = _generate_content(prompt, PracticeQuestions)
    parsed = PracticeQuestions.model_validate_json(response.text)
    return parsed.questions
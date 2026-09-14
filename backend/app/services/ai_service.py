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
    claimed_concept: str = Field(description="The incorrect statement or claim found in the study material.")
    correction: str = Field(description="The accurate explanation based on reliable principles from the subject area.")
    severity: str = Field(description="'Warning' or 'Critical Error'")

class Question(BaseModel):
    topic: str
    question_text: str
    options: List[str]
    correct_option_index: int

class Assessment(BaseModel):
    detected_errors: List[NoteError] = Field(
        default=[],
        description="Factual inaccuracies, misconceptions, or contradictions detected in the study material."
    )
    topic: List[str]
    questions: List[Question]

# Flashcard Schema
class Flashcard(BaseModel):
    front: str = Field(description="Concept, question, or term")
    back: str = Field(description="Explanation, definition, example, or worked solution")

# Matching Pair Schema
class MatchingPair(BaseModel):
    term: str = Field(description="Term, concept, or question")
    definition: str = Field(description="Definition, explanation, example, or answer")

# Practice Drill Response Container
class TopicPracticePackage(BaseModel):
    topic: str
    multiple_choice: List[Question] = Field(description="3 to 5 targeted multiple choice questions")
    flashcards: List[Flashcard] = Field(description="4 to 6 key concept flashcards")
    matching_pairs: List[MatchingPair] = Field(description="4 distinct term-to-definition pairs")

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
            "You are an academic study assistant. Carefully review the provided study material.\n"
            "1. Check for factual inaccuracies, misconceptions, or contradictions. "
            "List any issues in 'detected_errors' with the student's statement and an accurate correction.\n"
            "2. Identify 4 important topics from the material and generate a 4-question diagnostic quiz "
            "(1 question per topic, 4 options each).\n"
            "Ensure each question and answer is accurate for the subject represented in the material, "
            "even if the material contains errors.\n\n"
            f"Study material:\n{course_text}"
        )
    else:
        prompt = (
            "Identify 4 important topics from this study material and create a "
            "4-question diagnostic quiz (1 multiple-choice question per topic) with 4 options each. "
            "Use terminology and question types appropriate to the subject:\n\n"
            f"Study material:\n{course_text}"
        )

    response = _generate_content(prompt, Assessment)
    return Assessment.model_validate_json(response.text)

def generate_practice_package(topic: str, course_text: str) -> TopicPracticePackage:
    prompt = f"""
    Context:
    \"\"\"{course_text}\"\"\"

    Target Weak Topic: '{topic}'

    Generate a complete targeted study package focused strictly on this weak topic:
    1. 4 targeted multiple-choice questions with 4 options each and a correct answer index.
    2. 5 flashcards covering important definitions, examples, distinctions, or worked solutions.
    3. 4 matching pairs linking distinct terms or concepts to concise explanations.
    """

    response = client.models.generate_content(
        model=MODEL_NAME,
        contents=prompt,
        config={
            "response_mime_type": "application/json",
            "response_schema": TopicPracticePackage,
        },
    )
    return TopicPracticePackage.model_validate_json(response.text)
# LLM extraction & quiz generation logic
import os
from google import genai
from pydantic import BaseModel
from typing import List
from dotenv import load_dotenv

load_dotenv()  # Load environment variables from .env file
client = genai.Client(api_key=os.getenv("GEMINI_API_KEY"))

class Question(BaseModel):
    topic: str
    question_text: str
    options: List[str]
    correct_option_index: int

class Assessment(BaseModel):
    topic: List[str]
    questions: List[Question]

def generate_assessment_from_text(course_text: str) -> Assessment:
    prompt = (
        "Extract key technical concepts from this material and create a "
        "4-question diagnostic quiz (1 multiple-choice question per concept) with 4 options each:\n\n"
        f"{course_text}"
    )

    response = client.models.generate_content(
        model='gemini-3.6-flash',
        contents=prompt,
        config={
            'response_mime_type': 'application/json',
            'response_schema': Assessment,
        },
    )
    return Assessment.model_validate_json(response.text)
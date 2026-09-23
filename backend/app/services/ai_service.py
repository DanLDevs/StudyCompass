# LLM extraction & quiz generation logic
import os
import re
import time
from tenacity import (
    retry,
    stop_after_attempt,
    wait_exponential,
    retry_if_exception,
)
from google import genai
from google.genai import errors
from pydantic import BaseModel, Field
from typing import List, Sequence
from dotenv import load_dotenv

load_dotenv()  # Load environment variables from .env file

api_key = os.getenv("GEMINI_API_KEY")
if not api_key:
    raise ValueError("GEMINI_API_KEY not found in environment variables. Please set it in your .env file.")

client = genai.Client(api_key=api_key)
PRIMARY_MODEL = "gemini-3.5-flash"
FALLBACK_MODEL = "gemini-3.5-flash-lite"
DEFAULT_MODEL = PRIMARY_MODEL  # Default model for all AI service functions

# Pydantic Schemas for AI Service Responses
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
    detected_errors: List[NoteError] = Field(default_factory=list)
    topic: List[str]
    questions: List[Question]

class ChunkAnalysis(BaseModel):
    topics: List[str]
    evidence: str
    detected_errors: List[NoteError] = Field(default_factory=list)

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

# Retry handling
def is_transient_error(exception: BaseException) -> bool:
    # Catches transient 429 rate limits, resource exhaustion, and 503 server dropouts.
    err_str = str(exception).upper()
    return isinstance(exception, (errors.APIError, errors.ClientError, errors.ServerError)) and (
        "429" in err_str  # Rate limit
        or "RESOURCE_EXHAUSTED" in err_str  # Resource exhausted
        or "503" in err_str  # Server unavailable
        or "UNAVAILABLE" in err_str  # Service unavailable
    )

@retry(
    retry=retry_if_exception(is_transient_error),
    wait=wait_exponential(multiplier=1.5, min=2, max=10), # Exponential backoff starting at 2s, max 10s
    stop=stop_after_attempt(2), # Retry up to 2 times
    reraise=True,
)

def _call_gemini_api(model: str, contents: str, config=None):
    # Executes the raw API request with a small retry window for transient blips.
    return client.models.generate_content(
        model=model,
        contents=contents,
        config=config,
    )

def _generate_content_with_fallback(model: str, contents: str, response_schema=None):
    # Attempt to generate content with the primary model, fallback to a lighter model if it fails.
    config = None
    if response_schema:
        config = {
            "response_mime_type": "application/json",
            "response_schema": response_schema,
        }

    # Determine fallback model if the primary model is unavailable
    fallback_candidate = (
        FALLBACK_MODEL if model != FALLBACK_MODEL else None
    )

    try:
        return _call_gemini_api(model=model, contents=contents, config=config)
    except Exception as primary_error:
        err_str = str(primary_error).upper()
        # Check if the failures are due to model unavailability or transient issues
        is_quota_error = "429" in err_str or "RESOURCE_EXHAUSTED" in err_str

        if is_quota_error and fallback_candidate:
            print(f"⚠️ [StudyCompass] Quota reached on {model}. Falling back to {fallback_candidate}.")
            try:
                # Call the fallback model
                return _call_gemini_api(model=fallback_candidate, contents=contents, config=config)
            except Exception as fallback_error:
                raise fallback_error from primary_error
        # If the error is not related to quota or if no fallback is available, re-raise the original error
        raise primary_error

CHUNK_TARGET_CHARS = 2600
CHUNK_OVERLAP_CHARS = 300
CHUNKING_VERSION = "semantic-v1"


def semantic_chunk_text(text: str) -> list[dict[str, int | str]]:
    """Split notes at paragraph and sentence boundaries with bounded overlap."""
    paragraphs = [part.strip() for part in re.split(r"\n\s*\n+", text) if part.strip()]
    chunks = []
    current = ""
    current_start = 0

    for paragraph in paragraphs:
        pieces = [paragraph]
        if len(paragraph) > CHUNK_TARGET_CHARS:
            pieces = [
                piece.strip()
                for piece in re.split(r"(?<=[.!?])\s+", paragraph)
                if piece.strip()
            ]

        for piece in pieces:
            candidate = f"{current}\n\n{piece}" if current else piece
            if current and len(candidate) > CHUNK_TARGET_CHARS:
                end = current_start + len(current)
                chunks.append({
                    "chunk_index": len(chunks),
                    "chunk_text": current,
                    "start_offset": current_start,
                    "end_offset": end,
                })
                overlap = current[-CHUNK_OVERLAP_CHARS:]
                current = f"{overlap}\n\n{piece}".strip()
                current_start = max(0, end - len(overlap))
            else:
                current = candidate

    if current:
        chunks.append({
            "chunk_index": len(chunks),
            "chunk_text": current,
            "start_offset": current_start,
            "end_offset": current_start + len(current),
        })

    return chunks


def generate_assessment_from_chunks(
    chunks: Sequence[dict[str, int | str]],
    check_correctness: bool = False,
    model_name: str = PRIMARY_MODEL,
) -> Assessment:
    """Analyze every semantic chunk, then synthesize one document-wide assessment."""
    analyses = []
    for chunk in chunks:
        correctness_instruction = (
            "Identify factual inaccuracies and list corrections. "
            if check_correctness else ""
        )
        prompt = (
            "You are analyzing one section of a larger study document. "
            "Extract the important concepts and concise evidence that should inform a document-wide quiz. "
            f"{correctness_instruction}Return no quiz questions.\n\n"
            f"Section {chunk['chunk_index'] + 1}:\n{chunk['chunk_text']}"
        )
        response = _generate_content_with_fallback(
            model=model_name,
            contents=prompt,
            response_schema=ChunkAnalysis,
        )
        analyses.append(ChunkAnalysis.model_validate_json(response.text))

    summaries = "\n\n".join(
        f"Section {index + 1}: Topics={analysis.topics}; "
        f"Evidence={analysis.evidence}; Errors={analysis.detected_errors}"
        for index, analysis in enumerate(analyses)
    )
    prompt = (
        "You are creating a document-wide diagnostic assessment from section analyses. "
        "Identify the 4 most important topics across all sections and create exactly 4 multiple-choice "
        "questions, one per topic, with 4 options each. Avoid duplicate topics and questions. "
        "Use only the supplied evidence. Include detected_errors only when correctness checking is enabled.\n\n"
        f"Correctness checking enabled: {check_correctness}\n{summaries}"
    )
    response = _generate_content_with_fallback(
        model=model_name,
        contents=prompt,
        response_schema=Assessment,
    )
    return Assessment.model_validate_json(response.text)

# Service Functions
def generate_assessment_from_text(course_text: str, check_correctness: bool = False, model_name: str = PRIMARY_MODEL) -> Assessment:
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

    response = _generate_content_with_fallback(model=model_name, contents=prompt, response_schema=Assessment)
    return Assessment.model_validate_json(response.text)

def generate_practice_package(topic: str, course_text: str, model_name: str = PRIMARY_MODEL) -> TopicPracticePackage:
    prompt = f"""
    Context:
    \"\"\"{course_text}\"\"\"

    Target Weak Topic: '{topic}'

    Generate a complete targeted study package focused strictly on this weak topic:
    1. 4 targeted multiple-choice questions with 4 options each and a correct answer index.
    2. 5 flashcards covering important definitions, examples, distinctions, or worked solutions.
    3. 4 matching pairs linking distinct terms or concepts to concise explanations.
    """

    response = _generate_content_with_fallback(
        model=model_name,
        contents=prompt,
        response_schema=TopicPracticePackage,
    )
    return TopicPracticePackage.model_validate_json(response.text)

def apply_note_corrections(course_text: str, detected_errors: list, model_name: str = PRIMARY_MODEL) -> str:
    # Rewrite the course text to correct any detected inaccuracies while preserving original working and structure.
    errors_summary = "\n".join([
        f"- Inaccuracy: '{e.claimed_concept}' | Correction: '{e.correction}'"
        for e in detected_errors
    ])

    prompt = f"""
    Original Study Notes:
    \"\"\"{course_text}\"\"\"

    Inaccuracies to Correct:
    {errors_summary}

    Task:
    Rewrite the original study notes to incorporate these corrections cleanly.
    Maintain the original formatting, bullet points, and tone.
    Do not add conversational fluff or meta-commentary; return only the revised notes.
    """

    response = _generate_content_with_fallback(
        model=model_name,
        contents=prompt,
        response_schema=None,
    )
    return response.text.strip()
import sys
import hashlib
import json
from pathlib import Path
import random

# Resolve project root ('Study Compass') from 'frontend/pages/'
ROOT_DIR = Path(__file__).resolve().parent.parent.parent
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

BACKEND_DIR = ROOT_DIR / "backend"
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

import streamlit as st
import time
import streamlit.components.v1 as components
import pandas as pd
from docx import Document
from pypdf import PdfReader
from pptx import Presentation

from backend.app.database import get_connection, init_db
from backend.app.services.ai_service import (
    CHUNKING_VERSION,
    NoteError,
    apply_note_corrections,
    generate_assessment_from_chunks,
    generate_practice_package,
    semantic_chunk_text,
)
from backend.app.services.cheat_sheet_service import (
    PDF_RENDER_VERSION,
    generate_cheat_sheet,
    render_markdown,
    render_pdf,
)
from backend.app.services.srs_service import calculate_sm2
from backend.app.services.recommendation_service import get_prioritized_topic

init_db()
conn = get_connection()

st.set_page_config(page_title="StudyCompass | Courses", layout="wide")


def extract_uploaded_text(uploaded_file) -> str:
    # Extract readable text from a supported study-material file.
    file_type = Path(uploaded_file.name).suffix.lower()

    if file_type == ".pdf":
        reader = PdfReader(uploaded_file)
        return "\n".join(
            page.extract_text() or ""
            for page in reader.pages
        )

    if file_type == ".docx":
        document = Document(uploaded_file)
        fragments = [paragraph.text for paragraph in document.paragraphs]
        fragments.extend(
            cell.text
            for table in document.tables
            for row in table.rows
            for cell in row.cells
        )
        return "\n".join(fragment for fragment in fragments if fragment.strip())

    if file_type == ".pptx":
        presentation = Presentation(uploaded_file)
        fragments = []
        for slide in presentation.slides:
            for shape in slide.shapes:
                if shape.has_text_frame:
                    fragments.append(shape.text)
                if shape.has_table:
                    fragments.extend(
                        cell.text
                        for row in shape.table.rows
                        for cell in row.cells
                    )
        return "\n".join(fragment for fragment in fragments if fragment.strip())

    if file_type == ".txt":
        return uploaded_file.read().decode("utf-8")

    raise ValueError("Unsupported file type. Upload a TXT, PDF, DOCX, or PPTX file.")


def get_topic_context(connection, document_id: int | None, topic: str, fallback_text: str) -> str:
    # Retrieve the most relevant ordered chunks for a practice topic.
    if not document_id:
        return fallback_text

    chunks = connection.execute(
        """
        SELECT chunk_index, chunk_text
        FROM document_chunks
        WHERE document_id = ?
        ORDER BY chunk_index
        """,
        (document_id,),
    ).fetchall()
    if not chunks:
        return fallback_text

    terms = {term for term in topic.lower().split() if len(term) > 2}
    ranked = sorted(
        chunks,
        key=lambda chunk: sum(
            chunk["chunk_text"].lower().count(term)
            for term in terms
        ),
        reverse=True,
    )
    selected_indices = {chunk["chunk_index"] for chunk in ranked[:4] if any(
        term in chunk["chunk_text"].lower() for term in terms
    )}
    if not selected_indices:
        selected_indices = {chunks[0]["chunk_index"]}

    selected = [chunk for chunk in chunks if chunk["chunk_index"] in selected_indices]
    return "\n\n".join(
        f"Section {chunk['chunk_index'] + 1}:\n{chunk['chunk_text']}"
        for chunk in selected
    )

# Session state setup
session_defaults = {
    "mastery": {},
    "assessment": None,
    "quiz_submitted": False,
    "course_text": "",
    "document_id": None,
    "practice_package": None,
    "cheat_sheet_markdown": None,
    "cheat_sheet_pdf": None,
    "card_flipped": False,
    "scrambled_defs": [],
    "current_course_id": None,
    "srs_queue": None,
    "srs_completed": False,
    "pending_assessment": None,
    "reviewing_corrections": False
}
for key, default in session_defaults.items():
    if key not in st.session_state:
        st.session_state[key] = default

st.title("📚 Courses Workspace")

# Ensure default model is set in session state
if "selected_model" not in st.session_state:
    st.session_state.selected_model = "gemini-3.5-flash"

# UI selector for LLM model in sidebar
with st.sidebar.expander("⚡ Model Selection", expanded=False):
    model_display_options = {
        "gemini-3.5-flash": "Gemini 3.5 Flash (Recommended)",
        "gemini-3.5-flash-lite": "Gemini 3.5 Flash-Lite (Fast & Cost-Efficient)"
    }

    selected_model = st.selectbox(
        "Active LLM Model",
        options=list(model_display_options.keys()),
        format_func=lambda k: model_display_options[k],
        index=0 if st.session_state.selected_model == "gemini-3.5-flash" else 1,
        help="Switch to Flash-Lite if you encounter 429 rate limits or need faster card generation."
    )
    st.session_state.selected_model = selected_model

# Course selector
courses = conn.execute("SELECT id, name FROM courses ORDER BY name").fetchall()
course_names = [c["name"] for c in courses]

selected_course_name = st.sidebar.selectbox("Select Active Course", ["+ Add New Course"] + course_names)

if selected_course_name == "+ Add New Course":
    new_course = st.sidebar.text_input("Enter New Course Name")
    if st.sidebar.button("Create Course") and new_course.strip():
        conn.execute("INSERT OR IGNORE INTO courses (name) VALUES (?)", (new_course.strip(),))
        conn.commit()
        st.rerun()
    active_course_id = None
else:
    active_course_id = next(c["id"] for c in courses if c["name"] == selected_course_name)

# Course switch detection and session state reset
if active_course_id != st.session_state.current_course_id:
    st.session_state.current_course_id = active_course_id
    st.session_state.assessment = None
    st.session_state.pending_assessment = None
    st.session_state.reviewing_corrections = False
    st.session_state.practice_package = None
    st.session_state.cheat_sheet_markdown = None
    st.session_state.cheat_sheet_pdf = None
    st.session_state.srs_queue = None
    st.session_state.srs_completed = None

    if active_course_id:
        latest_document = conn.execute(
            """
            SELECT id, extracted_text, processing_version
            FROM documents
            WHERE course_id = ?
            ORDER BY uploaded_at DESC, id DESC
            LIMIT 1
            """,
            (active_course_id,),
        ).fetchone()
        if latest_document:
            st.session_state.document_id = latest_document["id"]
            st.session_state.course_text = latest_document["extracted_text"]
            if latest_document["processing_version"] == "legacy":
                st.warning("This course has older truncated study material. Re-upload the original file for complete processing.")

        rows = conn.execute(
            "SELECT topic_name, mastery_score FROM topic_mastery WHERE course_id = ?", (active_course_id,)
        ).fetchall()
        st.session_state.mastery = {r["topic_name"]: r["mastery_score"] for r in rows}
        st.session_state.quiz_submitted = bool(st.session_state.mastery)
    else:
        st.session_state.document_id = None
        st.session_state.course_text = ""
        st.session_state.mastery = {}
        st.session_state.quiz_submitted = False

if not active_course_id:
    st.info("👈 Please select or create an active course in the sidebar to begin.")
    st.stop()

# Sidebar Course Deletion and Management
with st.sidebar.expander("⚙️ Manage Course"):
    # State tracking for confirmation
    delete_key = f"confirm_delete_course_{active_course_id}"
    if delete_key not in st.session_state:
        st.session_state[delete_key] = False
    if not st.session_state[delete_key]:
        if st.button("🗑️ Delete Course", type="secondary", use_container_width=True):
            st.session_state[delete_key] = True
            st.rerun()
    else:
        st.error(f"Permanently delete **{selected_course_name}** and all its data? This action cannot be undone.")
        col1, col2 = st.columns(2)
        with col1:
            if st.button("Yes, Delete", type="primary", use_container_width=True):
                # Delete from database (foreign keys cascade)
                conn.execute("DELETE FROM courses WHERE id = ?", (active_course_id,))
                conn.commit()
                # Reset session state and rerun
                st.session_state[delete_key] = False
                st.session_state.current_course_id = None
                st.session_state.mastery = {}
                st.session_state.assessment = None
                st.session_state.reviewing_corrections = False
                st.session_state.practice_package = None
                st.rerun()
        with col2:
            if st.button("Cancel", use_container_width=True):
                st.session_state[delete_key] = False
                st.rerun()

# Material Ingestion & Fact Checking
st.subheader("1. Upload Study Material")
uploaded_file = st.file_uploader(
    "Upload study material (.txt, .pdf, .docx, or .pptx)",
    type=["txt", "pdf", "docx", "pptx"],
)
check_errors = st.toggle("🔍 Fact-check notes for errors", value=True)

if uploaded_file and st.button("Analyze Notes & Prepare Assessment"):
    with st.spinner("Parsing notes and checking for misconceptions..."):
        try:
            text = extract_uploaded_text(uploaded_file)
        except Exception as error:
            st.error(f"Could not read {uploaded_file.name}: {error}")
            st.stop()

        if not text.strip():
            st.error(f"No readable text was found in {uploaded_file.name}.")
            st.stop()

        chunks = semantic_chunk_text(text)
        if not chunks:
            st.error(f"No semantic sections could be created from {uploaded_file.name}.")
            st.stop()

        st.session_state.course_text = text

        # Initial Document Save
        document_cursor = conn.execute(
            """
            INSERT INTO documents
                (course_id, filename, extracted_text, processing_version)
            VALUES (?, ?, ?, ?)
            """,
            (active_course_id, uploaded_file.name, st.session_state.course_text, CHUNKING_VERSION)
        )
        document_id = document_cursor.lastrowid
        conn.executemany(
            """
            INSERT INTO document_chunks
                (document_id, chunk_index, chunk_text, start_offset, end_offset, chunking_version)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            [
                (
                    document_id,
                    chunk["chunk_index"],
                    chunk["chunk_text"],
                    chunk["start_offset"],
                    chunk["end_offset"],
                    CHUNKING_VERSION,
                )
                for chunk in chunks
            ],
        )
        conn.commit()
        st.session_state.document_id = document_id

        try:
            # Generate assessment and error analysis
            assessment = generate_assessment_from_chunks(
                chunks,
                check_correctness=check_errors, 
                model_name=st.session_state.get("selected_model", "gemini-3.5-flash")
            )
        except Exception as e:
            e_str = str(e).upper()
            if "429" in e_str or "RESOURCE_EXHAUSTED" in e_str:
                st.error("⏳ Both Gemini 3.5 Flash and Flash-Lite have reached their rate limits. Please wait a few minutes and try again.")
            elif "503" in e_str or "UNAVAILABLE" in e_str:
                st.error("🛰️ **Gemini Cluster Busy (503):** Google's AI servers are momentarily congested. Click the button to try again.")
            else:
                st.error(f"⚠️ **AI Service Error: {e_str}")
            st.stop()

        for error in assessment.detected_errors:
            conn.execute(
                """
                INSERT INTO note_errors
                    (course_id, document_id, claimed_concept, correction, severity)
                VALUES (?, ?, ?, ?, ?)
                """,
                (
                    active_course_id,
                    document_id,
                    error.claimed_concept,
                    error.correction,
                    error.severity,
                ),
            )
        conn.commit()

        # Decision Gate: Check for misconceptions
        if check_errors and assessment.detected_errors:
            st.session_state.pending_assessment = assessment
            st.session_state.reviewing_corrections = True
            st.session_state.assessment = None
        else:
            # Clean pass: proceed imediately
            st.session_state.assessment = assessment
            st.session_state.pending_assessment = None
            st.session_state.reviewing_corrections = False

        st.session_state.quiz_submitted = False
        st.session_state.practice_package = None
        st.rerun()

# Interactive Decision Gate for Detected Misconceptions
if st.session_state.reviewing_corrections and st.session_state.pending_assessment:
    st.warning("⚠️ **Detected Misconceptions in Uploaded Notes:**")
    st.write("Gemini detected potential factual errors in your uploaded notes. How would you like to proceed?")

    # Display detected errors with corrections
    for e in st.session_state.pending_assessment.detected_errors:
        with st.container(border=True):
            st.markdown(f"**Found in Notes:** *\"{e.claimed_concept}\"*")
            st.markdown(f"💡 **Suggested Fix:** {e.correction} `[{e.severity}]`")

    gate_col1, gate_col2 = st.columns(2)

    with gate_col1:
        if st.button("✨ Apply Corrections & Update Notes", type="primary", use_container_width=True):
            with st.spinner("Updating notes and regenerating assessment..."):
                # Rewrite notes with corrections applied
                corrected_text = apply_note_corrections(
                    st.session_state.course_text,
                    st.session_state.pending_assessment.detected_errors,
                    model_name = st.session_state.selected_model
                )
                st.session_state.course_text = corrected_text

                # Update the database document with corrected text
                conn.execute("""
                    UPDATE documents
                    SET extracted_text = ?, processing_version = ?
                    WHERE id = ? AND course_id = ?
                """, (corrected_text, CHUNKING_VERSION, st.session_state.document_id, active_course_id))
                corrected_chunks = semantic_chunk_text(corrected_text)
                conn.execute(
                    "DELETE FROM document_chunks WHERE document_id = ?",
                    (st.session_state.document_id,),
                )
                conn.executemany(
                    """
                    INSERT INTO document_chunks
                        (document_id, chunk_index, chunk_text, start_offset, end_offset, chunking_version)
                    VALUES (?, ?, ?, ?, ?, ?)
                    """,
                    [
                        (
                            st.session_state.document_id,
                            chunk["chunk_index"],
                            chunk["chunk_text"],
                            chunk["start_offset"],
                            chunk["end_offset"],
                            CHUNKING_VERSION,
                        )
                        for chunk in corrected_chunks
                    ],
                )
                conn.commit()

                # Brief pause to stay clear of rate limits
                time.sleep(2)


                # Regenerate assessment based on accurate material
                st.session_state.assessment = generate_assessment_from_chunks(
                    corrected_chunks,
                    check_correctness=False,
                    model_name=st.session_state.selected_model
                )
                st.session_state.reviewing_corrections = False
                st.session_state.pending_assessment = None
                st.toast("Notes updated with correction!", icon="✅")
                st.rerun()

    with gate_col2:
        if st.button("Keep Notes As-Is & Proceed", type="secondary", use_container_width=True):
            st.session_state.assessment = st.session_state.pending_assessment
            st.session_state.reviewing_corrections = False
            st.session_state.pending_assessment = None
            st.rerun()  
        
# Diagnostic Assessment
if st.session_state.assessment and not st.session_state.quiz_submitted and not st.session_state.reviewing_corrections:
    st.header("2. Diagnostic Assessment")
    with st.form("quiz_form"):
        user_answers = {}
        for idx, q in enumerate(st.session_state.assessment.questions):
            st.write(f"**Q{idx + 1}: {q.question_text}** `[{q.topic}]`")
            user_answers[idx] = st.radio(
                f"Options for Q{idx + 1}", 
                q.options, 
                key=f"q{idx}",
                label_visibility="collapsed"
            )

        if st.form_submit_button("Submit Answers"):
            # Compute fresh mastery scores from answers
            topic_totals = {}
            topic_correct = {}

            for idx, q in enumerate(st.session_state.assessment.questions):
                selected_idx = q.options.index(user_answers[idx])
                is_correct = (selected_idx == q.correct_option_index)

                # Update topic totals and correct counts
                topic_totals[q.topic] = topic_totals.get(q.topic, 0) + 1
                topic_correct[q.topic] = topic_correct.get(q.topic, 0) + (1 if is_correct else 0)

            # Assign initial scores (0.25 baseline for incorrect, 0.90 for correct)
            updated_mastery = dict(st.session_state.mastery)
            for topic, total in topic_totals.items():
                ratio = topic_correct[topic] / total
                score = 0.90 if ratio == 1.0 else (0.50 if ratio > 0 else 0.25)
                updated_mastery[topic] = score

                # Persist mastery scores to the database
                conn.execute("""
                    INSERT INTO topic_mastery (course_id, topic_name, mastery_score)
                    VALUES (?, ?, ?)
                    ON CONFLICT(course_id, topic_name) DO UPDATE SET mastery_score = excluded.mastery_score
                    """, (active_course_id, topic, score)
                )

            conn.commit()
            st.session_state.mastery = updated_mastery
            st.session_state.quiz_submitted = True
            st.rerun()

# Mastery Overview & Recommendations
if st.session_state.quiz_submitted and st.session_state.mastery:
    st.header("3. Mastery & Priority Actions")
    col1, col2 = st.columns([2, 1])

    with col1:
        chart_data = {k: int(v * 100) for k, v in st.session_state.mastery.items()}
        df = pd.DataFrame(
            list(st.session_state.mastery.items()),
            columns=["Topic", "Mastery %"]
        ).set_index("Topic")
        st.bar_chart(df)

    with col2:
        exam = conn.execute(
            "SELECT exam_name, exam_date FROM exams WHERE course_id = ? ORDER BY exam_date ASC LIMIT 1", (active_course_id,)
        ).fetchone()
        exam_date_str = exam["exam_date"] if exam else None

        # Run recommendation engine with exam date and proximity check
        weakest_topic, urgency_mult = get_prioritized_topic(st.session_state.mastery, exam_date_str)
        weakest_score = int(st.session_state.mastery[weakest_topic] * 100)

        # Dynamic UI feedback based on 'Upcoming Exam?' check
        if exam and urgency_mult > 1.0:
            st.error(f"🚨 **High-Priority Target:** {weakest_topic}")
            st.caption(f"Targeting upcoming {exam['exam_name']} ({exam['exam_date']})*")
        else:
            st.error(f"**Focus Area:** {weakest_topic}")


        st.metric(label="Current Mastery", value=f"{weakest_score}%")
        if st.button(f"🎯 Launch Practice Hub for {weakest_topic}"):
            with st.spinner(f"Generating drills & flashcards for {weakest_topic}..."):
                st.session_state.practice_package = generate_practice_package(
                    weakest_topic, 
                    get_topic_context(
                        conn,
                        st.session_state.document_id,
                        weakest_topic,
                        st.session_state.course_text,
                    ),
                    model_name=st.session_state.selected_model
                )
                # Scramble definitions once upon generation
                defs = [p.definition for p in st.session_state.practice_package.matching_pairs]
                random.shuffle(defs)
                st.session_state.scrambled_defs = defs       
                st.session_state.srs_queue = None
                st.session_state.srs_completed = False     
                st.rerun()


    st.divider()
    st.subheader("4. Personalized Cheat Sheet")
    st.caption("Synthesizes your lowest-mastery topics and flagged misconceptions into one study guide.")
    if st.button("Generate Personalized Cheat Sheet", use_container_width=True):
        with st.spinner("Synthesizing your personalized cheat sheet..."):
            errors = conn.execute(
                """
                SELECT claimed_concept, correction, severity
                FROM note_errors
                WHERE course_id = ?
                ORDER BY created_at DESC, id DESC
                """,
                (active_course_id,),
            ).fetchall()
            chunks = conn.execute(
                """
                SELECT chunk_index, chunk_text
                FROM document_chunks
                WHERE document_id = ?
                ORDER BY chunk_index
                """,
                (st.session_state.document_id,),
            ).fetchall()
            error_models = [NoteError(**dict(error)) for error in errors]
            context = {
                "document_id": st.session_state.document_id,
                "mastery": st.session_state.mastery,
                "errors": [dict(error) for error in errors],
                    "pdf_render_version": PDF_RENDER_VERSION,
            }
            context_hash = hashlib.sha256(
                json.dumps(context, sort_keys=True, default=str).encode("utf-8")
            ).hexdigest()
            existing_guide = conn.execute(
                """
                SELECT markdown_content, pdf_content
                FROM study_guides
                WHERE course_id = ? AND document_id = ? AND context_hash = ?
                ORDER BY generated_at DESC, id DESC
                LIMIT 1
                """,
                (active_course_id, st.session_state.document_id, context_hash),
            ).fetchone()
            if existing_guide:
                markdown = existing_guide["markdown_content"]
                pdf = existing_guide["pdf_content"]
            else:
                cheat_sheet = generate_cheat_sheet(
                    selected_course_name,
                    st.session_state.mastery,
                    error_models,
                    [dict(chunk) for chunk in chunks],
                    st.session_state.selected_model,
                )
                markdown = render_markdown(cheat_sheet)
                pdf = render_pdf(cheat_sheet)
                conn.execute(
                    """
                    INSERT INTO study_guides
                        (course_id, document_id, context_hash, markdown_content, pdf_content)
                    VALUES (?, ?, ?, ?, ?)
                    """,
                    (active_course_id, st.session_state.document_id, context_hash, markdown, pdf),
                )
                conn.commit()
            st.session_state.cheat_sheet_markdown = markdown
            st.session_state.cheat_sheet_pdf = pdf

    if st.session_state.cheat_sheet_markdown:
        with st.container(border=True):
            st.markdown(st.session_state.cheat_sheet_markdown)
        download_col1, download_col2 = st.columns(2)
        with download_col1:
            st.download_button(
                "Download Markdown",
                data=st.session_state.cheat_sheet_markdown,
                file_name=f"{selected_course_name.lower().replace(' ', '-')}-cheat-sheet.md",
                mime="text/markdown",
                use_container_width=True,
            )
        with download_col2:
            if st.session_state.cheat_sheet_pdf:
                st.download_button(
                    "Download PDF",
                    data=st.session_state.cheat_sheet_pdf,
                    file_name=f"{selected_course_name.lower().replace(' ', '-')}-cheat-sheet.pdf",
                    mime="application/pdf",
                    use_container_width=True,
                )

# Practice Hub (MCQ, Flashcards, Matching)
if st.session_state.practice_package:
    pkg = st.session_state.practice_package
    st.divider()
    st.subheader(f"🛠️ Practice Hub: {pkg.topic}")

    tab_mcq, tab_cards, tab_matching = st.tabs([
        "📝 Target Quiz",
        "🎴 Flashcards",
        "🧩 Term Matching"
    ])

    with tab_mcq:
        with st.form("mcq_form"):
            answers = {}
            for idx, q in enumerate(pkg.multiple_choice):
                st.write(f"**Q{idx + 1}: {q.question_text}**")
                answers[idx] = st.radio(f"Options for Q{idx+1}", q.options, key=f"drill_q{idx}", label_visibility="collapsed")

            if st.form_submit_button("Submit Quiz & Update Mastery"):
                correct = sum(
                    1 for idx, q in enumerate(pkg.multiple_choice)
                    if q.options.index(answers[idx]) == q.correct_option_index
                )
                score = correct / len(pkg.multiple_choice)
                prev = st.session_state.mastery.get(pkg.topic, 0.5)
                new_score = round((prev * 0.4) + (score * 0.6), 2)

                # Persist the updated mastery score to the database
                conn.execute("""
                    INSERT INTO topic_mastery (course_id, topic_name, mastery_score)
                    VALUES (?, ?, ?)
                    ON CONFLICT(course_id, topic_name) DO UPDATE SET mastery_score = excluded.mastery_score
                """, (active_course_id, pkg.topic, new_score))
                conn.commit()

                st.session_state.mastery[pkg.topic] = new_score
                st.toast(f"Mastery on {pkg.topic} recalibrated to {int(new_score * 100)}%!", icon="📈")
                st.rerun()

    with tab_cards:
        cards = pkg.flashcards
        components.html("""
            <script>
            const doc = window.parent.document;
            doc.addEventListener('keydown', function(e) {
                // Ignore if user is typing in a real text input
                if (['INPUT', 'TEXTAREA'].includes(doc.activeElement.tagName)) return;
                
                let buttonText = null;
                if (e.code === 'Space') {
                    buttonText = 'Flip Card';
                } else if (e.key === '1') {
                    buttonText = '1 - Again';
                } else if (e.key === '2') {
                    buttonText = '2 - Hard';
                } else if (e.key === '3') {
                    buttonText = '3 - Good';
                } else if (e.key === '4') {
                    buttonText = '4 - Easy';
                }

                if (buttonText) {
                    const buttons = Array.from(doc.querySelectorAll('button'));
                    const target = buttons.find(b => b.innerText.includes(buttonText));
                    if (target) {
                        target.click();
                        e.preventDefault();
                    }
                }
            });
            </script>
        """, height=0, width=0)

        if st.session_state.srs_queue is None and not st.session_state.srs_completed:
            st.session_state.srs_queue = list(range(len(cards)))
            st.session_state.card_flipped = False

        if st.session_state.srs_completed or (st.session_state.srs_queue is not None and len(st.session_state.srs_queue) == 0):
            st.session_state.srs_completed = True
            st.balloons()
            st.success("🎉 You have reviewed all cards for this session!")
            if st.button("Restart Session"):
                st.session_state.srs_queue = list(range(len(cards)))
                st.session_state.card_flipped = False
                st.session_state.srs_completed = False
                st.rerun()
        else:
            card = cards[st.session_state.srs_queue[0]]
            st.caption(f"Remaining in queue: **{len(st.session_state.srs_queue)}**")

            with st.container(border=True):
                if not st.session_state.card_flipped:
                    st.markdown(f"### ❓ {card.front}")
                    st.caption("Press **Space** or click below to reveal answer.")
                    if st.button("🔄 Flip Card (Space)", use_container_width=True):
                        st.session_state.card_flipped = True
                        st.rerun()
                else:
                    st.markdown(f"### 💡 {card.back}")
                    st.caption(f"Term: {card.front}")
                    st.divider()

                    cols = st.columns(4)
                    def record(grade):
                        row = conn.execute(
                            "SELECT repetitions, ease_factor, interval FROM flashcards WHERE course_id = ? AND front = ?",
                            (active_course_id, card.front)
                        ).fetchone()
                        reps = row["repetitions"] if row else 0
                        ef = row["ease_factor"] if row else 2.5
                        interval = row["interval"] if row else 0

                        # Calculate next SM-2 interval 
                        new_reps, new_ef, new_interval, next_due = calculate_sm2(grade, reps, ef, interval)
    
                        # Persist to SQLite
                        conn.execute("""
                            INSERT INTO flashcards (course_id, topic_name, front, back, interval, repetitions, ease_factor, due_date)
                            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                            ON CONFLICT(course_id, front) DO UPDATE SET
                                interval = excluded.interval,
                                repetitions = excluded.repetitions,
                                ease_factor = excluded.ease_factor,
                                due_date = excluded.due_date
                        """, (active_course_id, pkg.topic, card.front, card.back, new_interval, new_reps, new_ef, next_due))
                        conn.commit()

                        # If "Again" (1), push card to the end of the current session queue
                        finished_idx = st.session_state.srs_queue.pop(0)
                        if grade == 1:
                            st.session_state.srs_queue.append(finished_idx)  # Re-add to end of queue
    
                        st.session_state.card_flipped = False
                        st.rerun()

                    for g_idx, label in enumerate(["1 - Again", "2 - Hard", "3 - Good", "4 - Easy"], start=1):
                        with cols[g_idx - 1]:
                            if st.button(label, use_container_width=True):
                                record(g_idx)

    # Term Matching
    with tab_matching:
        pairs = pkg.matching_pairs
        selected_matches = {}
        for p in pairs:
            selected_matches[p.term] = st.selectbox(f"**{p.term}**", ["Select a definition..."] + st.session_state.scrambled_defs, key=f"match_{p.term}")

        if st.button("Check Matches"):
            correct_count = sum(1 for p in pairs if selected_matches[p.term] == p.definition)
            if correct_count == len(pairs):
                st.success(f"🎉 Perfect score! You matched all terms correctly.")
            else:
                st.warning(f"You got {correct_count} out of {len(pairs)} correct.")
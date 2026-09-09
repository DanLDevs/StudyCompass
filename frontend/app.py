# Streamlit app
import sys
from pathlib import Path

# Add the project root ('Study Compass') to Python's sys.path
ROOT_DIR = Path(__file__).resolve().parent.parent
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

# Add the backend package root so imports work when Streamlit runs this file
# directly from the frontend directory.
BACKEND_DIR = ROOT_DIR / "backend"
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

import streamlit as st
import pandas as pd
from pypdf import PdfReader
from backend.app.database import get_connection, init_db
from backend.app.services.ai_service import generate_assessment_from_text, generate_practice_for_topic


init_db()
conn = get_connection()

st.set_page_config(page_title="StudyCompass", layout="wide")

# --- Session State Initialization ---
if "mastery" not in st.session_state:
    st.session_state.mastery = {} # Empty until quiz completion

if "assessment" not in st.session_state:
    st.session_state.assessment = None

if "quiz_submitted" not in st.session_state:
    st.session_state.quiz_submitted = False

if "course_text" not in st.session_state:
    st.session_state.course_text = ""  # Store uploaded course material text

if "practice_questions" not in st.session_state:
    st.session_state.practice_questions = None  # Store generated practice questions for a topic

if "practice_topic" not in st.session_state:
    st.session_state.practice_topic = ""
if "current_course_id" not in st.session_state:
    st.session_state.current_course_id = None  # Store the currently selected course ID

st.title("🧭 Study Compass")
st.caption("AI-Powered Adaptive Study Planner")

# Sidebar for Course Management
st.sidebar.title("📚 My Courses:")

# Fetch existing courses
courses = conn.execute("SELECT id, name FROM courses ORDER BY name").fetchall()
course_names = [c["name"] for c in courses]

selected_course_name = st.sidebar.selectbox("Select Active Course", ["+ Add New Course"] + course_names)

if selected_course_name == "+ Add New Course":
    new_course = st.sidebar.text_input("Course Name")
    if st.sidebar.button("Create Course") and new_course:
        conn.execute("INSERT OR IGNORE INTO courses (name) VALUES (?)", (new_course,))
        conn.commit()
        st.rerun()
    active_course_id = None
else:
    active_course_id = next(c["id"] for c in courses if c["name"] == selected_course_name)

# Switch course state and load persistent mastery if changed
if active_course_id != st.session_state.current_course_id:
    st.session_state.current_course_id = active_course_id
    st.session_state.assessment = None
    st.session_state.practice_questions = None

    if active_course_id:
        rows = conn.execute(
            "Select topic_name, mastery_score FROM topic_mastery WHERE course_id = ?",
            (active_course_id,)
        ).fetchall()
        st.session_state.mastery = {r["topic_name"]: r["mastery_score"] for r in rows}
        st.session_state.quiz_submitted = bool(st.session_state.mastery)
    else:
        st.session_state.mastery = {}
        st.session_state.quiz_submitted = False

# --- Section 1: Upload Course Material ---
st.subheader("1. Upload Lecture Notes")

if not active_course_id:
    st.info("👈 Please select or create an active course in the sidebar to begin.")
    st.stop()

uploaded_file = st.file_uploader("Upload course notes (.txt or .pdf)", type=["txt", "pdf"])

# Toggle button to verify note correctness
check_errors = st.toggle(
    "🔍 Fact-check notes for errors or misconceptions", 
    value=True,
    help="When enabled, Gemini verifies whether your uploaded notes contain errors and provides feedback."
)

if uploaded_file and st.button("Generate Diagnostic Quiz"):
    with st.spinner("Analyzing material and generating questions..."):
        text = ""
        if uploaded_file.name.endswith(".pdf"):
            reader = PdfReader(uploaded_file)
            for page in reader.pages:
                text += page.extract_text() or ""
        else:
            text = uploaded_file.read().decode("utf-8")

        st.session_state.course_text = text[:4000]  # Limit to first 4000 chars for LLM

        # Store the uploaded notes in the database for future reference
        conn.execute(
            "INSERT INTO documents (course_id, filename, extracted_text) VALUES (?, ?, ?)",
            (active_course_id, uploaded_file.name, st.session_state.course_text)
        )
        conn.commit()

        try:
            st.session_state.assessment = generate_assessment_from_text(
                st.session_state.course_text,
                check_correctness=check_errors)
        except Exception as error:
            st.error(f"The AI service is temporarily unavailable. Please try again shortly. ({error})")
            st.stop()
        st.session_state.mastery = {}
        st.session_state.quiz_submitted = False
        st.session_state.practice_questions = None
        st.rerun()  # Refresh the app to show the quiz

# -- Feedback Detection Popup ---
if st.session_state.assessment: 
    if st.session_state.assessment.detected_errors:
        st.warning("The AI detected potential factual errors or misconceptions in your uploaded notes:")
        for err in st.session_state.assessment.detected_errors:
            st.markdown(f"* **In Your Notes:** *\"{err.claimed_concept}\"*")
            st.markdown(f"  * **Correction:** {err.correction} `[{err.severity}]`")
    elif check_errors:
        st.success("✅ **Notes Verified:** No obvious factual errors or contradictions were detected.")

# --- Section 2: Diagnostic Assessment ---
if st.session_state.assessment and not st.session_state.quiz_submitted:
    st.header("2. Diagnostic Assessment")
    with st.form("quiz_form"):
        user_answers = {}
        for idx, q in enumerate(st.session_state.assessment.questions):
            st.write(f"**Q{idx + 1}: {q.question_text}** `[{q.topic}]`")
            user_answers[idx] = st.radio(
                f"Options for Q{idx + 1}", 
                q.options, 
                key=f"q{idx}"
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
            updated_mastery = {}
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

# --- Section 3: Mastery Dashboard & Recommendations ---
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
        weakest_topic = min(st.session_state.mastery, key=st.session_state.mastery.get)
        weakest_score = int(st.session_state.mastery[weakest_topic] * 100)

        st.error(f"**Focus Area:** {weakest_topic}")
        st.metric(label="Estimated Mastery", value=f"{weakest_score}%")
        st.write(f"⏱️**Recommended:** 30 minutes of targeted review and practice on **{weakest_topic}**.")

        if st.button(f"🎯 Start Practice on {weakest_topic}"):
            with st.spinner(f"Generating practice questions for {weakest_topic}..."):
                st.session_state.practice_topic = weakest_topic
                try:
                    st.session_state.practice_questions = generate_practice_for_topic(
                        weakest_topic, st.session_state.course_text
                    )
                except Exception as error:
                    st.error(
                        "The AI service is temporarily unavailable. "
                        f"Please try again shortly. ({error})"
                    )
                    st.stop()
                st.rerun()

# --- Section 4: Target Practice & Reassessment ---
if st.session_state.practice_questions:
    st.divider()
    st.subheader(f"4. Target Practice: {st.session_state.practice_topic}")

    with st.form("practice_form"):
        practice_answers = {}
        for p_idx, pq in enumerate(st.session_state.practice_questions):
            st.write(f"**Practice Q{p_idx + 1}: {pq.question_text}**")
            practice_answers[p_idx] = st.radio(
                f"Select option for Q{p_idx + 1}", 
                pq.options, 
                key=f"practice_q{p_idx}"
            )

        if st.form_submit_button("Submit Practice & Reassess"):
            correct_count = 0
            for p_idx, pq in enumerate(st.session_state.practice_questions):
                selected_idx = pq.options.index(practice_answers[p_idx])
                if selected_idx == pq.correct_option_index:
                    correct_count += 1

            # Reassess mastery for the practiced topic
            practice_score = correct_count / len(st.session_state.practice_questions)
            prev_mastery = st.session_state.mastery[st.session_state.practice_topic]
            new_mastery = round((prev_mastery * 0.4) + (practice_score * 0.6), 2)  # Weighted update

            # Persist the updated mastery score to the database
            conn.execute("""
                INSERT INTO topic_mastery (course_id, topic_name, mastery_score)
                VALUES (?, ?, ?)
                ON CONFLICT(course_id, topic_name) DO UPDATE SET mastery_score = excluded.mastery_score
                """, (active_course_id, st.session_state.practice_topic, new_mastery)
            )
            conn.commit()

            st.session_state.mastery[st.session_state.practice_topic] = new_mastery
            st.session_state.practice_questions = None  # Clear practice questions
            st.success(f"Mastery on {st.session_state.practice_topic} updated to {int(prev_mastery * 100)}% to {int(new_mastery * 100)}%!")
            st.rerun()
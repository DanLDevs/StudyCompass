# Streamlit app
import sys
from pathlib import Path
import random

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
import streamlit.components.v1 as components
import pandas as pd
from pypdf import PdfReader
from datetime import datetime, date
from backend.app.database import get_connection, init_db
from backend.app.services.ai_service import generate_assessment_from_text, generate_practice_package
from backend.app.services.srs_service import calculate_sm2
from backend.app.services.recommendation_service import get_prioritized_topic


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

if "practice_package" not in st.session_state:
    st.session_state.practice_package = None  # Store generated practice package for the topic

if "card_idx" not in st.session_state:
    st.session_state.card_idx = 0  # Index for flashcard navigation

if "card_flipped" not in st.session_state:
    st.session_state.card_flipped = False  # Flashcard flip state

if "scrambled_defs" not in st.session_state:
    st.session_state.scrambled_defs = []  # Store scrambled definitions for term matching

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

# Sidebar: Exam date tracker
st.sidebar.subheader("📅 Upcoming Exam Date")
current_exam = conn.execute(
    "SELECT exam_name, exam_date FROM exams WHERE course_id = ? ORDER BY exam_date ASC LIMIT 1", 
    (active_course_id,)
).fetchone()

if current_exam:
    exam_name = current_exam["exam_name"]
    exam_date = current_exam["exam_date"]
    days_left = (datetime.strptime(exam_date, "%Y-%m-%d").date() - date.today()).days

    if days_left >= 0:
        st.sidebar.info(f"🎯 **{exam_name}**\n\n📅 {exam_date} ({days_left} days left)")
    else:
        st.sidebar.warning(f"⚠️ **{exam_name}** was on {exam_date}")
else:
    with st.sidebar.expander("➕ Add Upcoming Exam"):
        exam_name_input = st.text_input("Exam Name", "Midterm Exam")
        exam_date_input = st.date_input("Exam Date")
        if st.button("Save Exam"):
            if active_course_id and exam_name_input and exam_date_input:
                conn.execute(
                    "INSERT INTO exams (course_id, exam_name, exam_date) VALUES (?, ?, ?)",
                    (active_course_id, exam_name_input, exam_date_input.strftime("%Y-%m-%d"))
                )
                conn.commit()
                st.sidebar.success(f"Saved: {exam_name_input} on {exam_date_input}")
                st.rerun()
            else:
                st.sidebar.error("Please select a course and provide both name and date.")

# Switch course state and load persistent mastery if changed
if active_course_id != st.session_state.current_course_id:
    st.session_state.current_course_id = active_course_id
    st.session_state.assessment = None
    st.session_state.practice_package = None

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
st.subheader("1. Upload Study Material")

if not active_course_id:
    st.info("👈 Please select or create an active course in the sidebar to begin.")
    st.stop()

uploaded_file = st.file_uploader("Upload study material (.txt or .pdf)", type=["txt", "pdf"])

# Toggle button to verify study material correctness
check_errors = st.toggle(
    "🔍 Check study material for errors or misconceptions", 
    value=True,
    help="When enabled, Gemini checks whether your uploaded study material contains errors and provides feedback."
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

        # Store the uploaded study material in the database for future reference
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
        st.warning("The AI detected potential factual errors or misconceptions in your uploaded study material:")
        for err in st.session_state.assessment.detected_errors:
            st.markdown(f"* **In Your Notes:** *\"{err.claimed_concept}\"*")
            st.markdown(f"  * **Correction:** {err.correction} `[{err.severity}]`")
    elif check_errors:
        st.success("✅ **Study Material Verified:** No obvious factual errors or contradictions were detected.")

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
        exam_date_str = current_exam["exam_date"] if current_exam else None

        # Run recommendation engine with exam date and proximity check
        weakest_topic, urgency_mult = get_prioritized_topic(st.session_state.mastery, exam_date_str)
        weakest_score = int(st.session_state.mastery[weakest_topic] * 100)

        # Dynamic UI feedback based on 'Upcoming Exam?' check
        if current_exam and urgency_mult > 1.0:
            st.error(f"🚨 **High-Priority Target:** {weakest_topic}")
            st.caption(f"⚡ *Boosted due to upcoming {current_exam['exam_name']} ({days_left} days remaining)*")
            recommended_mins = 45 if days_left <= 3 else 30
        else:
            st.error(f"**Focus Area:** {weakest_topic}")
            recommended_mins = 20


        st.metric(label="Current Mastery", value=f"{weakest_score}%")
        st.write(f"⏱️ **Recommended Study Time:** {recommended_mins} minutes for targeted practice on **{weakest_topic}**.")
        if st.button(f"🎯 Launch Practice Hub for {weakest_topic}"):
            with st.spinner(f"Generating drills & flashcards for {weakest_topic}..."):
                st.session_state.practice_package = generate_practice_package(
                    weakest_topic, st.session_state.course_text
                )
                st.session_state.card_idx = 0
                st.session_state.card_flipped = False

                # Scramble definitions once upon generation
                defs = [p.definition for p in st.session_state.practice_package.matching_pairs]
                random.shuffle(defs)
                st.session_state.scrambled_defs = defs            
                st.rerun()

# --- Section 4: Target Practice & Reassessment ---
if st.session_state.practice_package:
    pkg = st.session_state.practice_package
    st.divider()
    st.subheader(f"🛠️ Practice Hub: {pkg.topic}")

    tab_mcq, tab_cards, tab_matching = st.tabs([
        "📝 Target Quiz",
        "🎴 Flashcards",
        "🧩 Term Matching"
    ])

    # Targeted Multiple Choice Quiz Reassessment
    with tab_mcq:
        with st.form("mcq_practice_form"):
            answers = {}
            for idx, q in enumerate(pkg.multiple_choice):
                st.write(f"**Q{idx + 1}: {q.question_text}**")
                answers[idx] = st.radio(f"Options for Q{idx+1}", q.options, key=f"drill_q{idx}")

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

    def keyboard_shortcut_listener():
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

    # Anki-Style Flashcards with SM-2 Spaced Repetition
    with tab_cards:
        cards = pkg.flashcards
        keyboard_shortcut_listener()  # Enable keyboard shortcuts for flashcard navigation

        # Queue tracking in session state
        if "srs_queue" not in st.session_state or not st.session_state.srs_queue:
            st.session_state.srs_queue = list(range(len(cards)))
            st.session_state.card_flipped = False

        if not st.session_state.srs_queue:
            st.balloons()
            st.success("🎉 You have reviewed all cards for this session!")
            if st.button("Restart Review Session"):
                st.session_state.srs_queue = list(range(len(cards)))
                st.session_state.card_flipped = False
                st.rerun()
        else:
            current_card_idx = st.session_state.srs_queue[0]
            card = cards[current_card_idx]
        

        st.caption(f"Remaining in queue: **{len(st.session_state.srs_queue)}** | Shortcuts: [Space] Flip | [1] Again | [2] Hard | [3] Good | [4] Easy")

        # Flashcard Container
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

                # Anki buttons: 1 - Again, 2 - Hard, 3 - Good, 4 - Easy
                col1, col2, col3, col4 = st.columns(4)

                def record_grade(grade):
                    # Fetch current card stats from DB
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

                    # Anki queue behavior:
                    # If "Again" (1), push card to the end of the current session queue
                    finished_idx = st.session_state.srs_queue.pop(0)
                    if grade == 1:
                        st.session_state.srs_queue.append(finished_idx)  # Re-add to end of queue

                    st.session_state.card_flipped = False
                    st.rerun()

                with col1:
                    if st.button("1 - Again", use_container_width=True):
                        record_grade(1)
                with col2:
                    if st.button("2 - Hard", use_container_width=True):
                        record_grade(2)
                with col3:
                    if st.button("3 - Good", use_container_width=True):
                        record_grade(3)
                with col4:
                    if st.button("4 - Easy", use_container_width=True):
                        record_grade(4)

    # Term Matching
    with tab_matching:
        pairs = pkg.matching_pairs
        st.write("Match each term to its correct definition:")

        # Scramble definitions for the drop-down selector
        selected_matches = {}
        for p in pairs:
            selected_matches[p.term] = st.selectbox(
                f"**{p.term}**",
                options=["Select a definition..."] + st.session_state.scrambled_defs,
                key=f"match_{p.term}"
            )

        if st.button("Check Matches"):
            correct_matches = sum(1 for p in pairs if selected_matches[p.term] == p.definition)

            if correct_matches == len(pairs):
                st.success(f"🎉 Perfect Score! All {len(pairs)} matches are correct.")
            else:
                st.warning(f"You got {correct_matches} out of {len(pairs)} correct. Review your notes and try again!")
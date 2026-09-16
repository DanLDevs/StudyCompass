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

from backend.app.database import get_connection, init_db
from backend.app.services.ai_service import generate_assessment_from_text, generate_practice_package
from backend.app.services.srs_service import calculate_sm2
from backend.app.services.recommendation_service import get_prioritized_topic

init_db()
conn = get_connection()

st.set_page_config(page_title="StudyCompass | Courses", layout="wide")

# Session state setup
for key in ["mastery", "assessment", "quiz_submitted", "course_text", "practice_package", 
            "card_flipped", "scrambled_defs", "current_course_id", "srs_queue", "srs_completed"]:
    if key not in st.session_state:
        st.session_state[key] = {} if key == "mastery" else None

st.title("📚 Courses Workspace")

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

if active_course_id != st.session_state.current_course_id:
    st.session_state.current_course_id = active_course_id
    st.session_state.assessment = None
    st.session_state.practice_package = None
    st.session_state.srs_queue = None
    st.session_state.srs_completed = None

    if active_course_id:
        rows = conn.execute(
            "SELECT topic_name, mastery_score FROM topic_mastery WHERE course_id = ?", (active_course_id,)
        ).fetchall()
        st.session_state.mastery = {r["topic_name"]: r["mastery_score"] for r in rows}
        st.session_state.quiz_submitted = bool(st.session_state.mastery)
    else:
        st.session_state.mastery = {}
        st.session_state.quiz_submitted = False

if not active_course_id:
    st.info("👈 Please select or create an active course in the sidebar to begin.")
    st.stop()

# Material Ingestion & Fact Checking
st.subheader("1. Upload Study Material")
uploaded_file = st.file_uploader("Upload study material (.txt or .pdf)", type=["txt", "pdf"])
check_errors = st.toggle("🔍 Fact-check notes for errors", value=True)

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
    
            st.session_state.assessment = generate_assessment_from_text(
                st.session_state.course_text, check_correctness=check_errors)
            st.session_state.mastery = {}
            st.session_state.quiz_submitted = False
            st.session_state.practice_questions = None
            st.rerun()  # Refresh the app to show the quiz

# Verification Warnings
if st.session_state.assessment and st.session_state.assessment.detected_errors:
    st.warning("⚠️ **Misconceptions Detected in Source Material:")
    for err in st.session_state.assessment.detected_errors:
        st.markdown(f"* **In Your Notes:** *\"{err.claimed_concept}\"*")
        st.markdown(f"  * **Correction:** {err.correction} `[{err.severity}]`")

# Diagnostic Assessment
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
                    weakest_topic, st.session_state.course_text
                )
                # Scramble definitions once upon generation
                defs = [p.definition for p in st.session_state.practice_package.matching_pairs]
                random.shuffle(defs)
                st.session_state.scrambled_defs = defs       
                st.session_state.srs_queue = None
                st.session_state.srs_completed = False     
                st.rerun()

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
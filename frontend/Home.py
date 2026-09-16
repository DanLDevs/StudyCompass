import sys
from pathlib import Path
from datetime import datetime, date

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
from backend.app.database import get_connection, init_db
from backend.app.services.recommendation_service import get_prioritized_topic

init_db()
conn = get_connection()

st.set_page_config(page_title="StudyCompass | Dashboard", layout="wide")

st.title("🧭 Study Compass")
st.caption("Central Command & Daily Priorities")

courses = conn.execute("SELECT id, name FROM courses ORDER BY name").fetchall()

if not courses:
    st.info("👋 Welcome! You haven't added any courses yet. Head to the **Courses** page in the sidebar to get started.")
    st.stop()

# Section 1
st.subheader("📅 Urgent Exam Radar")
upcoming_exams = conn.execute("""
    SELECT e.exam_name, e.exam_date, c.name AS course_name
    FROM exams e
    JOIN courses c ON e.course_id = c.id
    ORDER BY e.exam_date ASC
""").fetchall()

if upcoming_exams:
    cols = st.columns(min(len(upcoming_exams), 3))
    for idx, exam in enumerate(upcoming_exams[:3]):  # Show only the next 3 exams
        days_left = (datetime.strptime(exam['exam_date'], "%Y-%m-%d").date() - date.today()).days
        with cols[idx]:
            with st.container(border=True):
                st.markdown(f"**{exam['course_name']}**")
                st.write(f"🎯 {exam['exam_name']}")
                if days_left <= 3:
                    st.error(f"⏰ **{days_left} days left! Critical crunch time!**")
                elif days_left <= 7:
                    st.warning(f"📅 **{days_left} days left**")
                else:
                    st.info(f"📅 **{days_left} days left**")
else:
    st.caption("No upcoming exams found. Add deadlines on the **Calendar** page.")

st.divider()

# Section 2
st.subheader("📊 Mastery Overview")

col1, col2 = st.columns([2, 1])

with col1:
    mastery_rows = conn.execute("""
        SELECT c.name AS course_name, tm.topic_name, tm.mastery_score
        FROM topic_mastery tm
        JOIN courses c ON tm.course_id = c.id
    """).fetchall()

    if mastery_rows:
        df = pd.DataFrame([dict(r) for r in mastery_rows])
        avg_mastery = df.groupby('course_name')['mastery_score'].mean().reset_index()
        avg_mastery["Mastery (%)"] = (avg_mastery["mastery_score"] * 100).astype(int)

        st.bar_chart(avg_mastery.set_index('course_name')['Mastery (%)'])
    else:
        st.info("No assessments completed yet. Take a diagnostic quiz on the **Courses** page to start tracking your mastery.")

with col2:
    st.markdown("### 🎯 Recommended Study Focus")

    # Identify the highest priority topic across the whole system
    priority_candidates = []
    for c in courses:
        c_mastery = conn.execute(
            "SELECT topic_name, mastery_score FROM topic_mastery WHERE course_id = ?",
            (c['id'],)
        ).fetchall()

        if c_mastery:
            m_dict = {r["topic_name"]: r["mastery_score"] for r in c_mastery}
            exam = conn.execute(
                "SELECT exam_date FROM exams WHERE course_id = ? ORDER BY exam_date ASC LIMIT 1", (c['id'],)
            ).fetchone()
            exam_date_str = exam["exam_date"] if exam else None

            top_topic, urgency = get_prioritized_topic(m_dict, exam_date_str)
            score = m_dict[top_topic]
            priority_score = (1.0 - score) * urgency
            priority_candidates.append((c["name"], top_topic, int(score * 100), priority_score))

    if priority_candidates:
        priority_candidates.sort(key=lambda x: x[3], reverse=True)
        top_course, top_topic, top_score, _ = priority_candidates[0]

        st.error(f"**Top Focus:** {top_topic}")
        st.write(f"Class: **{top_course}**")
        st.metric(label="Current Mastery", value=f"{top_score}%")
        st.write("👉 Navigate to **Courses** in the sidebar to review flashcards or run practice drills.")
    else:
        st.write("Complete an assessment in the **Courses** page to generate personalized recommendations.")
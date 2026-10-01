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
from backend.app.database import get_connection, init_db, get_active_exam_alerts
from backend.app.services.recommendation_service import get_prioritized_topic
from backend.app.services.srs_service import get_review_counts

init_db()
conn = get_connection()

st.set_page_config(page_title="StudyCompass | Dashboard", layout="wide")

st.title("🧭 Study Compass")
st.caption("Central Command & Daily Priorities")

courses = conn.execute("SELECT id, name FROM courses ORDER BY name").fetchall()

if not courses:
    st.info("👋 Welcome! You haven't added any courses yet. Head to the **Courses** page in the sidebar to get started.")
    st.stop()

st.subheader("🎴 Flashcard Review")
review_counts = get_review_counts(conn, course_id=None)

count_col1, count_col2, count_col3, count_col4 = st.columns(4)
count_col1.metric("Due cards", review_counts["due"])
count_col2.metric("New cards", review_counts["new"])
count_col3.metric("Scheduled later", review_counts["scheduled"])
count_col4.metric("Reviewed today", review_counts["reviewed_today"])

if review_counts["due"] or review_counts["new"]:
    st.page_link(
        "pages/1_Courses.py",
        label=f"Review {review_counts['due'] + review_counts['new']} cards",
        icon="🎴",
    )
else:
    st.success("No flashcards are due right now. Your next scheduled cards are still in the future.")

st.divider()

# Section 1
st.subheader("🔔 Upcoming Exam Alerts")
active_exams = get_active_exam_alerts(conn)

if not active_exams:
    st.info("🎉 No upcoming exams scheduled! All previous exams have passed.")
else:
    today = date.today()
    for exam_id, exam_name, exam_date_str, course_name in active_exams:
        exam_date = datetime.strptime(exam_date_str, "%Y-%m-%d").date()
        days_left = (exam_date - today).days

        if days_left == 0:
            badge = "🔥 **TODAY**"
            alert_box = st.error
        elif days_left <= 3:
            badge = f"⚠️ **In {days_left} days**"
            alert_box = st.warning
        else:
            badge = f"📅 **In {days_left} days**"
            alert_box = st.info

        alert_box(f"{badge}: **{course_name}** - *{exam_name}* ({exam_date_str})")

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
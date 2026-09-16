import sys
from pathlib import Path
from datetime import datetime, date

# Resolve project root ('Study Compass') from 'frontend/pages/'
ROOT_DIR = Path(__file__).resolve().parent.parent.parent
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

BACKEND_DIR = ROOT_DIR / "backend"
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

import streamlit as st
import pandas as pd
from backend.app.database import get_connection, init_db

init_db()
conn = get_connection()

st.set_page_config(page_title="StudyCompass | Calendar", layout="wide")

st.title("📅 Exam Deadlines & Calendar")

courses = conn.execute("SELECT id, name FROM courses ORDER BY name").fetchall()
if not courses:
    st.warning("Please create a course on the **Courses** page before adding exams.")
    st.stop()

col1, col2 = st.columns([1, 2])

# Column 1: Add New Exam
with col1:
    with st.container(border=True):
        st.subheader("➕ Schedule New Exam")
        course_map = {c["name"]: c["id"] for c in courses}
        target_course = st.selectbox("Course", list(course_map.keys()))
        exam_name = st.text_input("Exam Name (e.g., Midterm 1, Final Exam)" )
        exam_date = st.date_input("Exam Date:", min_value=date.today())

        if st.button("Add Exam"):
            if exam_name.strip():
                conn.execute(
                    "INSERT INTO exams (course_id, exam_name, exam_date) VALUES (?, ?, ?)",
                    (course_map[target_course], exam_name.strip(), str(exam_date)),
                )
                conn.commit()
                st.success(f"Added {exam_name} for {target_course} on {exam_date}.")
                st.rerun()
            else:
                st.error("Please enter an exam name.")

# Column 2: Display Scheduled Exams
with col2:
    st.subheader("📋 Scheduled Exams")
    exams = conn.execute("""
        SELECT e.id, c.name as course_name, e.exam_name, e.exam_date
        FROM exams e
        JOIN courses c ON e.course_id = c.id
        ORDER BY e.exam_date ASC
    """).fetchall()

    if exams:
        for e in exams:
            days = (datetime.strptime(e['exam_date'], "%Y-%m-%d").date() - date.today()).days

            with st.container(border=True):
                row_col1, row_col2, row_col3 = st.columns([3, 2, 1])

                with row_col1:
                    st.markdown(f"**{e['course_name']}**: {e['exam_name']}")
                    st.caption(f"📅 Exam Date: {e['exam_date']}")

                with row_col2:
                    if days <= 0:
                        st.error("🚨 Today / Overdue")
                    elif days <= 3:
                        st.error(f"⏰ {days} days left! Critical crunch time!")
                    elif days <= 7:
                        st.warning(f"⚠️ {days} days left")
                    else:
                        st.info(f"📅 {days} days left")

                with row_col3:
                    # Confirmation Popup for Deletion
                    with st.popover("🗑️"):
                        st.write(f"Delete **{e['exam_name']}**?")
                        if st.button("Confirm", key=f"delete_exam_{e['id']}", type="primary"):
                            conn.execute("DELETE FROM exams WHERE id = ?", (e['id'],))
                            conn.commit()
                            st.toast(f"Deleted {e['exam_name']}", icon="🗑️")
                            st.rerun()
    else:
        st.info("No exams currently scheduled. Use the form on the left to set your first target date.")
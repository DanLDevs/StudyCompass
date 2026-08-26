# Streamlit app
import streamlit as st
import pandas as pd
from pypdf import PdfReader
import sys
from pathlib import Path

# Add the project root ('Study Compass') to Python's sys.path
ROOT_DIR = Path(__file__).resolve().parent.parent
sys.path.append(str(ROOT_DIR))

# Import from backend
from backend.app.services.ai_service import generate_assessment_from_text

st.set_page_config(page_title="StudyCompass", layout="wide")

# --- Session State Initialization ---
if "mastery" not in st.session_state:
    st.session_state.mastery = {"Arrays": 0.91, "Trees": 0.72, "BFS": 0.55, "DFS": 0.42}
if "assessment" not in st.session_state:
    st.session_state.assessment = None

st.title("🧭 Study Compass")
st.caption("AI-Powered Adaptive Study Planner")

# --- Section 1: Upload Course Material ---
st.header("1. Upload Lecture Notes")
uploaded_file = st.file_uploader("Upload course notes (.txt or .pdf)", type=["txt", "pdf"])

if uploaded_file and st.button("Generate Diagnostic Quiz"):
    with st.spinner("Analyzing material and generating questions..."):
        text = ""
        if uploaded_file.name.endswith(".pdf"):
            reader = PdfReader(uploaded_file)
            for page in reader.pages:
                text += page.extract_text() or ""
        else:
            text = uploaded_file.read().decode("utf-8")

        st.session_state.assessment = generate_assessment_from_text(text[:4000])  # Limit to first 4000 chars for LLM

        # Initialize mastery levels for extracted topics if not already present
        for topic in st.session_state.assessment.topic:
            if topic not in st.session_state.mastery:
                st.session_state.mastery[topic] = 0.50

# --- Section 2: Diagnostic Assessment ---
if st.session_state.assessment:
    st.header("2. Diagnostic Assessment")
    with st.form("quiz_form"):
        user_answers = {}
        for idx, q in enumerate(st.session_state.assessment.questions):
            st.write(f"**Q{idx + 1}: {q.question_text}** `[{q.topic}]`")
            user_answers[idx] = st.radio(f"Options for Q{idx + 1}", options=q.options, key=f"q{idx}")

        submitted = st.form_submit_button("Submit Answers")
        if submitted:
            for idx, q in enumerate(st.session_state.assessment.questions):
                selected_idx = q.options.index(user_answers[idx])
                is_correct = (selected_idx == q.correct_option_index)

                # Update moving average mastery score
                current_score = st.session_state.mastery.get(q.topic, 0.50)
                new_score = 1.0 if is_correct else 0.0
                st.session_state.mastery[q.topic] = round((current_score * 0.4) + (new_score * 0.6), 2)
            st.success("Mastery levels updated!")

# --- Section 3: Mastery Dashboard & Recommendations ---
st.header("3. Mastery & Priority Actions")
col1, col2 = st.columns([2, 1])

with col1:
    df = pd.DataFrame(
        list(st.session_state.mastery.items()),
        columns=["Topic", "Mastery"]
    ).set_index("Topic")
    st.bar_chart(df)

with col2:
    if st.session_state.mastery:
        # Find the topic with the lowest mastery
        weakest_topic = min(st.session_state.mastery, key=st.session_state.mastery.get)
        weakest_score = int(st.session_state.mastery[weakest_topic] * 100)

        st.error(f"**Focus Area:** {weakest_topic}")
        st.metric(label="Current Mastery", value=f"{weakest_score}%")
        st.write(f"⏱️ **Recommended:** 30 minutes of targeted practice on **{weakest_topic}**.")
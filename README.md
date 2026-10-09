# StudyCompass

StudyCompass is a Streamlit study-planning app for organizing courses and exams, measuring topic mastery, and guiding practice with uploaded study materials. The local Streamlit app is configured and runnable. GitHub and Streamlit Community Cloud deployment are not complete.

## Current Features

- Course management and a calendar for exams and deadlines.
- TXT, PDF, DOCX, and PPTX uploads with semantic chunking.
- AI diagnostic assessment, topic mastery tracking, and study recommendations.
- Practice hub with multiple-choice questions, flashcards/SRS, term matching, and misconception correction.
- Cheat sheets with Markdown and PDF downloads.
- Direct dashboard flashcard review and persistent review-course state across reruns.
- Keyboard shortcuts intended for `Space`/`Enter` and `1`-`4`.

## Project Structure

```text
StudyCompass1/
├── backend/
│   └── app/
│       ├── database.py              # SQLite setup and persistence
│       └── services/                # AI, recommendations, SRS, and cheat sheets
├── frontend/
│   ├── Home.py                      # Streamlit entry point and dashboard
│   └── pages/
│       ├── 1_Courses.py
│       └── 2_Calendar.py
├── tests/
├── requirements.txt
└── README.md
```

## Requirements

- Python 3.10 or newer
- A Gemini API key for AI features
- PowerShell, Command Prompt, or an equivalent terminal

Dependencies are listed in `requirements.txt`.

## Local Installation and Configuration

From the project root, create and activate a virtual environment in Windows PowerShell:

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
```

If PowerShell blocks activation, run this once for your user account, then activate the environment again:

```powershell
Set-ExecutionPolicy -Scope CurrentUser RemoteSigned
```

Create `.env` in the project root, next to `requirements.txt`:

```env
GEMINI_API_KEY=your_gemini_api_key_here
```

The current code loads this value with `python-dotenv` and `os.getenv("GEMINI_API_KEY")`. In VS Code, select `.venv\Scripts\python.exe` with **Python: Select Interpreter**.

## Run Locally

With `.venv` activated:

```powershell
python -m streamlit run frontend/Home.py
```

Open the URL shown by Streamlit, usually `http://localhost:8501`. Press `Ctrl+C` in the terminal to stop the app.

For Command Prompt, activate with `.venv\Scripts\activate.bat` and use the same run command.

## Deployment Readiness

For Streamlit Community Cloud, the exact app entry point is:

```text
frontend/Home.py
```

The repository must first be pushed to GitHub. Cloud API-key secret handling still needs to be verified or updated: the current implementation reads `GEMINI_API_KEY` through dotenv and `os.getenv` and does not yet document or implement Streamlit Cloud secrets directly. SQLite data is stored in `study_compass.db`, so it will not be durable across hosted restarts or rebuilds. SQLite files are excluded from Git.

## Development and Testing

Compile the Python sources without running the app:

```powershell
python -m py_compile frontend/Home.py frontend/pages/1_Courses.py frontend/pages/2_Calendar.py backend/app/database.py
```

Run the test suite:

```powershell
python -m pytest
```

## Security Notes

- Never commit `.env`, API keys, or other credentials.
- Never commit the local `study_compass.db` file or other SQLite database files.
- `.gitignore` excludes `.env`, virtual environments, caches, and SQLite database files.

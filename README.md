# StudyCompass

StudyCompass is a Streamlit study-planning application that helps students organize courses, track exam deadlines, measure topic mastery, and focus their study time. It uses the Gemini API to analyze uploaded study materials and generate personalized assessments and practice activities.

## Features

- Create and manage courses.
- Add exams and deadlines through the Calendar page.
- Upload TXT, PDF, DOCX, and PPTX study materials.
- Analyze notes with semantic chunking and generate diagnostic quizzes.
- Track mastery by topic and receive priority study recommendations.
- Practice with targeted quizzes, flashcards, spaced repetition, and term matching.
- Detect possible misconceptions and optionally apply corrections to uploaded notes.
- Generate personalized cheat sheets containing low-mastery topics and flagged misconceptions.
- Download cheat sheets as Markdown or PDF files.

## Project Structure

```text
StudyCompass1/
├── backend/
│   └── app/
│       ├── database.py
│       └── services/
├── frontend/
│   ├── Home.py
│   └── pages/
│       ├── 1_Courses.py
│       └── 2_Calendar.py
├── requirements.txt
└── README.md
```

## Requirements

- Python 3.10 or newer
- A Gemini API key
- Windows PowerShell, Command Prompt, or an equivalent terminal

## Installation

Open a terminal in the project folder:

```powershell
cd "c:\Users\Daniel Lai\OneDrive - St. Louis Community College\Desktop\StudyCompass\StudyCompass1"
```

Create a virtual environment:

```powershell
python -m venv .venv
```

Activate it in PowerShell:

```powershell
.\.venv\Scripts\Activate.ps1
```

If PowerShell prevents activation, run this once for your user account:

```powershell
Set-ExecutionPolicy -Scope CurrentUser RemoteSigned
```

Then activate the environment again. A successful activation displays `(.venv)` in the terminal prompt.

Install the project dependencies:

```powershell
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
```

## Configure the Gemini API

Create a file named `.env` in the project root, next to `requirements.txt`, with this content:

```env
GEMINI_API_KEY=your_gemini_api_key_here
```

Replace the placeholder with your own key. Do not commit `.env` or share your API key publicly.

## Start the Application

With the virtual environment activated, run:

```powershell
python -m streamlit run frontend/Home.py
```

Streamlit will display a local URL, usually:

```text
http://localhost:8501
```

Open that URL in a browser. Use the sidebar to navigate between the dashboard, Courses, and Calendar pages.

To stop the application, return to the terminal and press `Ctrl+C`.

## VS Code Interpreter

In VS Code, press `Ctrl+Shift+P`, select **Python: Select Interpreter**, and choose:

```text
.venv\Scripts\python.exe
```

This ensures that VS Code, Streamlit, and the installed project packages use the same Python environment.

## Command Prompt Alternative

If you are using Command Prompt instead of PowerShell, activate the environment with:

```cmd
.venv\Scripts\activate.bat
```

Then start the application using the same Streamlit command:

```cmd
python -m streamlit run frontend/Home.py
```

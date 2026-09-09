import sqlite3
from pathlib import Path

DB_PATH = Path(__file__).resolve().parent.parent.parent.parent / "study_compass.db"

def get_connection():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn

def init_db():
    conn = get_connection()
    cursor = conn.cursor()

    # Courses Table
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS courses (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL UNIQUE,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );
    """)

    # Documents Table
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS documents (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            course_id INTEGER NOT NULL,
            filename TEXT NOT NULL,
            extracted_text TEXT NOT NULL,
            uploaded_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (course_id) REFERENCES courses (id) ON DELETE CASCADE
        );
    """)

    # Topic Mastery Table
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS topic_mastery (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            course_id INTEGER NOT NULL,
            topic_name TEXT NOT NULL,
            mastery_score REAL NOT NULL,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            UNIQUE (course_id, topic_name),
            FOREIGN KEY (course_id) REFERENCES courses (id) ON DELETE CASCADE
        );
    """)

    topic_mastery_columns = {
        row[1] for row in cursor.execute("PRAGMA table_info(topic_mastery)")
    }
    if "topic" in topic_mastery_columns and "topic_name" not in topic_mastery_columns:
        cursor.execute(
            "ALTER TABLE topic_mastery RENAME COLUMN topic TO topic_name"
        )

    cursor.execute("""
        CREATE UNIQUE INDEX IF NOT EXISTS idx_topic_mastery_course_topic
        ON topic_mastery (course_id, topic_name)
    """)

    conn.commit()
    conn.close()
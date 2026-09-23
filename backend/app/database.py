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
            processing_version TEXT NOT NULL DEFAULT 'legacy',
            uploaded_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (course_id) REFERENCES courses (id) ON DELETE CASCADE
        );
    """)

    document_columns = {
        row[1] for row in cursor.execute("PRAGMA table_info(documents)")
    }
    if "processing_version" not in document_columns:
        cursor.execute(
            "ALTER TABLE documents ADD COLUMN processing_version TEXT NOT NULL DEFAULT 'legacy'"
        )

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS document_chunks (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            document_id INTEGER NOT NULL,
            chunk_index INTEGER NOT NULL,
            chunk_text TEXT NOT NULL,
            start_offset INTEGER NOT NULL,
            end_offset INTEGER NOT NULL,
            chunking_version TEXT NOT NULL,
            UNIQUE(document_id, chunk_index),
            FOREIGN KEY (document_id) REFERENCES documents (id) ON DELETE CASCADE
        );
    """)

    cursor.execute("""
        CREATE INDEX IF NOT EXISTS idx_document_chunks_document_order
        ON document_chunks (document_id, chunk_index)
    """)

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS note_errors (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            course_id INTEGER NOT NULL,
            document_id INTEGER,
            claimed_concept TEXT NOT NULL,
            correction TEXT NOT NULL,
            severity TEXT NOT NULL,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (course_id) REFERENCES courses (id) ON DELETE CASCADE,
            FOREIGN KEY (document_id) REFERENCES documents (id) ON DELETE CASCADE
        );
    """)

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS study_guides (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            course_id INTEGER NOT NULL,
            document_id INTEGER,
            context_hash TEXT NOT NULL,
            markdown_content TEXT NOT NULL,
            pdf_content BLOB,
            generated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(course_id, document_id, context_hash),
            FOREIGN KEY (course_id) REFERENCES courses (id) ON DELETE CASCADE,
            FOREIGN KEY (document_id) REFERENCES documents (id) ON DELETE CASCADE
        );
    """)

    study_guide_columns = {
        row[1] for row in cursor.execute("PRAGMA table_info(study_guides)")
    }
    if "pdf_content" not in study_guide_columns:
        cursor.execute("ALTER TABLE study_guides ADD COLUMN pdf_content BLOB")

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

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS flashcards (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            course_id INTEGER NOT NULL,
            topic_name TEXT NOT NULL,
            front TEXT NOT NULL,
            back TEXT NOT NULL,
            interval INTEGER DEFAULT 0, -- in days
            repetitions INTEGER DEFAULT 0,
            ease_factor REAL DEFAULT 2.5,
            due_date TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(course_id, front),
            FOREIGN KEY (course_id) REFERENCES courses (id) ON DELETE CASCADE
        );
    """)

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS exams (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            course_id INTEGER NOT NULL,
            exam_name TEXT NOT NULL,
            exam_date DATE NOT NULL,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (course_id) REFERENCES courses (id) ON DELETE CASCADE
        );
    """)

    conn.commit()
    conn.close()
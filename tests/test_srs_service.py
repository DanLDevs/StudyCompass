import sqlite3
import unittest
from datetime import datetime
from types import SimpleNamespace

from backend.app.services.srs_service import (
    calculate_sm2,
    get_review_queue,
    upsert_flashcards,
)


class SrsServiceTests(unittest.TestCase):
    def setUp(self):
        self.connection = sqlite3.connect(":memory:")
        self.connection.row_factory = sqlite3.Row
        self.connection.executescript(
            """
            CREATE TABLE courses (
                id INTEGER PRIMARY KEY,
                name TEXT NOT NULL
            );
            CREATE TABLE flashcards (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                course_id INTEGER NOT NULL,
                topic_name TEXT NOT NULL,
                front TEXT NOT NULL,
                back TEXT NOT NULL,
                interval INTEGER DEFAULT 0,
                repetitions INTEGER DEFAULT 0,
                ease_factor REAL DEFAULT 2.5,
                due_date TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                last_reviewed_at TIMESTAMP,
                UNIQUE(course_id, front)
            );
            INSERT INTO courses (id, name) VALUES (1, 'Biology'), (2, 'History');
            """
        )

    def tearDown(self):
        self.connection.close()

    def test_new_card_ratings_are_deterministic(self):
        now = datetime(2026, 10, 1, 9, 0, 0)

        again = calculate_sm2(1, 0, 2.5, 0, now)
        good = calculate_sm2(3, 0, 2.5, 0, now)

        self.assertEqual(again[0], 0)
        self.assertEqual(again[2], 1)
        self.assertEqual(again[3], datetime(2026, 10, 2, 9, 0, 0))
        self.assertEqual(good[2], 2)
        self.assertEqual(good[3], datetime(2026, 10, 3, 9, 0, 0))

    def test_established_ratings_are_strictly_ordered(self):
        now = datetime(2026, 10, 1, 9, 0, 0)
        intervals = [
            calculate_sm2(grade, 4, 2.5, 10, now)[2]
            for grade in (2, 3, 4)
        ]

        self.assertLess(intervals[0], intervals[1])
        self.assertLess(intervals[1], intervals[2])

    def test_repeated_good_grows_and_again_resets(self):
        now = datetime(2026, 10, 1, 9, 0, 0)
        first = calculate_sm2(3, 2, 2.5, 6, now)
        second = calculate_sm2(3, first[0], first[1], first[2], now)
        reset = calculate_sm2(1, second[0], second[1], second[2], now)

        self.assertGreater(second[2], first[2])
        self.assertEqual(reset[0], 0)
        self.assertEqual(reset[2], 1)
        self.assertGreaterEqual(reset[1], 1.3)
        self.assertLess(reset[1], second[1])

    def test_invalid_scheduler_values_are_normalized(self):
        result = calculate_sm2(99, -4, float("nan"), -10, datetime(2026, 10, 1))

        self.assertEqual(result[0], 1)
        self.assertEqual(result[2], 4)
        self.assertGreaterEqual(result[1], 1.3)

    def test_review_queue_filters_due_cards_and_preserves_course_scope(self):
        self.connection.executemany(
            """
            INSERT INTO flashcards
                (course_id, topic_name, front, back, interval, repetitions,
                 ease_factor, due_date, last_reviewed_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            [
                (1, "Cells", "due", "answer", 2, 1, 2.5, "2026-10-01 08:00:00", "2026-09-30 08:00:00"),
                (1, "Cells", "future", "answer", 2, 1, 2.5, "2026-10-02 08:00:00", "2026-09-30 08:00:00"),
                (1, "Cells", "new", "answer", 0, 0, 2.5, "2026-10-01 08:00:00", None),
                (2, "Wars", "other course", "answer", 2, 1, 2.5, "2026-10-01 08:00:00", "2026-09-30 08:00:00"),
            ],
        )
        self.connection.commit()

        queue = get_review_queue(
            self.connection,
            course_id=1,
            review_limit=10,
            new_limit=10,
            now=datetime(2026, 10, 1, 9, 0, 0),
        )

        self.assertEqual([card["front"] for card in queue], ["due", "new"])
        self.assertEqual(queue[0]["course_name"], "Biology")

    def test_upsert_preserves_scheduling_history(self):
        self.connection.execute(
            """
            INSERT INTO flashcards
                (course_id, topic_name, front, back, interval, repetitions,
                 ease_factor, due_date, last_reviewed_at)
            VALUES (1, 'Old Topic', 'front', 'old answer', 12, 4, 2.1,
                    '2026-10-10 09:00:00', '2026-09-30 09:00:00')
            """
        )
        self.connection.commit()

        upsert_flashcards(
            self.connection,
            1,
            "New Topic",
            [SimpleNamespace(front="front", back="new answer")],
        )
        row = self.connection.execute(
            "SELECT * FROM flashcards WHERE course_id = 1 AND front = 'front'"
        ).fetchone()

        self.assertEqual(row["topic_name"], "New Topic")
        self.assertEqual(row["back"], "new answer")
        self.assertEqual(row["interval"], 12)
        self.assertEqual(row["repetitions"], 4)
        self.assertEqual(row["ease_factor"], 2.1)
        self.assertEqual(row["due_date"], "2026-10-10 09:00:00")
        self.assertEqual(row["last_reviewed_at"], "2026-09-30 09:00:00")


if __name__ == "__main__":
    unittest.main()

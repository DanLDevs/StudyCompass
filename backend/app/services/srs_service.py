from collections.abc import Sequence
from datetime import datetime, timedelta


CURRENT_LIMIT_MIGRATION_VERSION = 2


def normalize_review_limits(
    new_limit: int,
    review_limit: int,
    migration_version: int,
) -> tuple[int, int, int]:
    """Migrate only legacy zero limits while preserving user choices."""
    if migration_version < CURRENT_LIMIT_MIGRATION_VERSION:
        if new_limit == 0:
            new_limit = 10
        if review_limit == 0:
            review_limit = 10
        migration_version = CURRENT_LIMIT_MIGRATION_VERSION

    return new_limit, review_limit, migration_version


def _timestamp(value: datetime | None = None) -> str:
    return (value or datetime.now()).strftime("%Y-%m-%d %H:%M:%S")


def upsert_flashcards(connection, course_id: int, topic_name: str, cards: Sequence) -> None:
    """Persist generated cards without resetting existing scheduling history."""
    connection.executemany(
        """
        INSERT INTO flashcards (course_id, topic_name, front, back)
        VALUES (?, ?, ?, ?)
        ON CONFLICT(course_id, front) DO UPDATE SET
            topic_name = excluded.topic_name,
            back = excluded.back
        """,
        [
            (course_id, topic_name, card.front, card.back)
            for card in cards
        ],
    )
    connection.commit()


def get_review_queue(
    connection,
    course_id: int | None,
    review_limit: int,
    new_limit: int,
    now: datetime | None = None,
) -> list[dict]:
    """Return due reviewed cards first, followed by never-reviewed cards."""
    timestamp = _timestamp(now)
    course_filter = "" if course_id is None else "WHERE f.course_id = ?"
    course_params = [] if course_id is None else [course_id]

    due_rows = connection.execute(
        f"""
        SELECT f.*, c.name AS course_name
        FROM flashcards AS f
        JOIN courses AS c ON c.id = f.course_id
        {course_filter}{' AND' if course_filter else 'WHERE'}
            f.last_reviewed_at IS NOT NULL
            AND datetime(f.due_date) <= datetime(?)
        ORDER BY datetime(f.due_date) ASC, f.id ASC
        LIMIT ?
        """,
        [*course_params, timestamp, max(0, review_limit)],
    ).fetchall()

    new_course_filter = "" if course_id is None else "WHERE f.course_id = ?"
    new_rows = connection.execute(
        f"""
        SELECT f.*, c.name AS course_name
        FROM flashcards AS f
        JOIN courses AS c ON c.id = f.course_id
        {new_course_filter}{' AND' if new_course_filter else 'WHERE'}
            f.last_reviewed_at IS NULL
        ORDER BY f.id ASC
        LIMIT ?
        """,
        [*course_params, max(0, new_limit)],
    ).fetchall()

    return [dict(row) for row in [*due_rows, *new_rows]]


def get_review_counts(connection, course_id: int | None, now: datetime | None = None) -> dict[str, int]:
    """Return due, new, and today's reviewed-card counts for a scope."""
    timestamp = _timestamp(now)
    course_filter = "" if course_id is None else "WHERE course_id = ?"
    course_params = [] if course_id is None else [course_id]
    due_filter = "" if course_id is None else "AND course_id = ?"

    due = connection.execute(
        f"""
        SELECT COUNT(*) AS count FROM flashcards
        WHERE last_reviewed_at IS NOT NULL
          AND datetime(due_date) <= datetime(?) {due_filter}
        """,
        [timestamp, *course_params],
    ).fetchone()["count"]
    new = connection.execute(
        f"""
        SELECT COUNT(*) AS count FROM flashcards
        {course_filter}{' AND' if course_filter else 'WHERE'} last_reviewed_at IS NULL
        """,
        course_params,
    ).fetchone()["count"]
    reviewed = connection.execute(
        f"""
        SELECT COUNT(*) AS count FROM flashcards
        WHERE date(last_reviewed_at) = date(?) {due_filter}
        """,
        [timestamp, *course_params],
    ).fetchone()["count"]
    scheduled = connection.execute(
        f"""
        SELECT COUNT(*) AS count FROM flashcards
        WHERE last_reviewed_at IS NOT NULL
          AND datetime(due_date) > datetime(?) {due_filter}
        """,
        [timestamp, *course_params],
    ).fetchone()["count"]
    return {
        "due": due,
        "new": new,
        "scheduled": scheduled,
        "reviewed_today": reviewed,
    }

def calculate_sm2(grade: int, repetitions: int, ease_factor: float, interval: int):
    """
    grade: 1 (Again), 2 (Hard), 3 (Good), 4 (Easy)
    Returns: (new_reptitions, new_ease_factor, new_interval, next_due_date)
    """
    # Grade 1 (Again): Fail / Reset
    if grade == 1:
        new_reps = 0
        new_interval = 1
        new_ef = max(1.3, ease_factor - 0.2)  # Decrease EF but not below 1.3
    else:
        # Grade 2 (Hard), 3 (Good), 4 (Easy)
        if repetitions == 0:
            new_interval = 1
        elif repetitions == 1:
            new_interval = 3 if grade == 2 else 6
        else:
            multiplier = ease_factor
            if grade == 2: # Hard gives smaller boost
                multiplier = 1.2
            elif grade == 4: # Easy gives extra bonus
                multiplier = ease_factor * 1.3
            new_interval = int(round(interval * multiplier))

        new_reps = repetitions + 1

        # Standard SM-2 EF adjustment formula
        sm2_grade = grade + 1
        new_ef = ease_factor + (0.1 - (5 - sm2_grade) * (0.08 + (5 - sm2_grade) * 0.02))
        new_ef = max(1.3, new_ef)  # Ensure EF doesn't drop below 1.3

    next_due = datetime.now() + timedelta(days=new_interval)
    return new_reps, round(new_ef, 2), new_interval, next_due
from datetime import datetime, timedelta

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
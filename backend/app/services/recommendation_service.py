from datetime import datetime, date

def calculate_exam_urgency(exam_date_str: str) -> float:
    # Computes an urgency multiplier based on days remaining until the exam.
    if not exam_date_str:
        return 1.0  # Default urgency if no date is provided

    exam_date = datetime.strptime(exam_date_str, "%Y-%m-%d").date()
    days_left = (exam_date - date.today()).days

    if days_left <= 0:
        return 3.0 # Exam is today or overdue, highest urgency
    elif days_left <= 3:
        return 2.5 # Critical crunch period
    elif days_left <= 7: 
        return 1.8 # One week out
    elif days_left <= 14:
        return 1.3 # Moderate focus period
    return 1.0 # More than two weeks away, normal urgency

def get_prioritized_topic(mastery_dict: dict, exam_date_str: str = None) -> tuple[str, float]:
    # Evaluates (Upcoming exam?) decision diamond:
    # Balances low mastery with proximity boosting urgency for exam preparation.
    urgency = calculate_exam_urgency(exam_date_str)

    # Calculate a priority score for each topic based on mastery and urgency
    topic_priorities = {}
    for topic, score in mastery_dict.items():
        deficit = 1.0 - score  # Lower mastery means higher deficit
        topic_priorities[topic] = deficit * urgency

    highest_priority_topic = max(topic_priorities, key=topic_priorities.get)
    return highest_priority_topic, urgency
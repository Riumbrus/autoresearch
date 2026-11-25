"""Контроль арифметики модели на двух определениях изменения маржи."""
from pathlib import Path

from inspect_ai import Task, task
from inspect_ai.dataset import Sample
from inspect_ai.scorer import match
from inspect_ai.solver import generate


@task
def margin():
    table = (Path(__file__).parent / "fixtures" / "margin.csv").read_text(encoding="utf-8")
    cases = [
        ("при фактических весах выручки каждого периода", "8"),
        ("при фиксированных весах выручки первого периода", "-4"),
    ]
    return Task(
        dataset=[Sample(input=f"{table}\nКак изменилась маржа в п.п. {method}? "
                              "Ответь только целым числом, положительное без знака.", target=target)
                 for method, target in cases],
        solver=generate(),
        scorer=match(location="exact"),
    )

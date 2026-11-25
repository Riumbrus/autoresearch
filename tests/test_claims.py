"""Регрессионные проверки финальной публикации через CLI."""
import json
import hashlib
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest


SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "dashboard.py"


class PublicationTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.run_dir = Path(temp.name)
        self.published = self.run_dir / "state.json"
        self.state = dict(topic="Выкуп", phase="Синтез", status="Завершено",
                          rounds=dict(used=1, limit=1), assignments=[], conflicts=[],
                          questions=[dict(id="Q1", text="Что изменилось?", status="Обоснован")],
                          sources=[dict(id="S1", title="Выгрузка", url="", evidence="Строки 2–3")])
        self.claim = dict(question_id="Q1", source_ids=["S1"], status="Наблюдение",
                          text="Выкуп снизился", effect="−20 п.п.", evidence="820 и 620 из 1000",
                          limitation="Два месяца")
        (self.run_dir / "report.md").write_text("Результат проверки", encoding="utf-8")

    def publish(self, valid):
        (self.run_dir / "draft.json").write_text(json.dumps(self.state), encoding="utf-8")
        result = subprocess.run([sys.executable, str(SCRIPT), "publish", str(self.run_dir)],
                                capture_output=True)
        self.assertEqual(result.returncode == 0, valid, result.stderr)

    def test_missing_card_and_bad_references(self):
        self.state["schema_version"] = 1
        self.publish(False)
        self.state["claims"] = [self.claim]
        self.publish(True)
        previous = self.published.read_bytes()
        for key, value in [("question_id", "Q2"), ("source_ids", []), ("source_ids", ["S2"])]:
            with self.subTest(key=key, value=value):
                self.state["claims"] = [dict(self.claim, **{key: value})]
                self.publish(False)
                self.assertEqual(self.published.read_bytes(), previous)

    def test_gap_only(self):
        self.state["rounds"] = dict(used=5, limit=None)
        self.state["status"] = "Частично"
        self.state["questions"][0]["status"] = "Пробел"
        self.publish(False)
        for value in ["  ", None, 1]:
            self.state["questions"][0]["next_check"] = value
            self.publish(False)
        self.state["questions"][0]["next_check"] = "Нужны заказы и выкупы по категориям за оба месяца."
        self.publish(True)
        self.state["rounds"]["limit"] = 3
        self.publish(False)

    def test_legacy_snapshot(self):
        self.published.write_text(json.dumps(self.state), encoding="utf-8")
        self.publish(True)
        self.assertEqual(json.loads(self.published.read_text(encoding="utf-8"))["schema_version"], 1)

    def test_changed_calculation_input(self):
        source = self.run_dir / "data.csv"
        source.write_bytes(b"a,b\n1,2\n")
        self.state["claims"] = [self.claim]
        self.state["calculations"] = [dict(id="C1", inputs={source.name: hashlib.sha256(source.read_bytes()).hexdigest()},
                                           command="sum", parameters={}, result="3")]
        self.publish(True)
        previous = self.published.read_bytes()
        source.write_bytes(b"a,b\n2,2\n")
        self.publish(False)
        self.assertEqual(self.published.read_bytes(), previous)

    def test_deep_reviews(self):
        self.state.update(mode="deep", status="В работе", claims=[self.claim])
        self.publish(True)
        del self.state["mode"]
        self.state["status"] = "Завершено"
        self.publish(False)
        reviews = {role: dict(agent=role, verdict="Принято", result="Проверено по строкам 2–3")
                   for role in ("plan", "critique", "acceptance")}
        self.state["reviews"] = reviews
        self.publish(False)
        reviews["acceptance"]["blind_answer"] = "−20 п.п."
        plan = self.run_dir / "plan.md"
        plan.write_text("Проверить выкуп", encoding="utf-8")
        report = self.run_dir / "report.md"
        for role, review in reviews.items():
            artifact = plan if role == "plan" else report
            review["artifact_sha256"] = hashlib.sha256(artifact.read_bytes()).hexdigest()
        self.publish(True)
        for artifact in (plan, report):
            original = artifact.read_bytes()
            artifact.write_bytes(original + b"!")
            self.publish(False)
            artifact.write_bytes(original)
        reviews["critique"]["agent"] = "plan"
        self.publish(False)
        reviews["critique"].update(agent="другой", verdict="Открыто")
        self.publish(False)
        self.state["status"] = "Частично"
        self.publish(True)

    def test_subagents_count(self):
        self.state["claims"] = [self.claim]
        self.state["assignments"] = [
            dict(id="R1-A1", question_id="Q1", text="Участок 1", worker="agent-1", status="Сдан"),
            dict(id="R1-A2", question_id="Q1", text="Участок 2", worker="agent-2", status="Сдан"),
        ]
        self.publish(True)
        self.assertEqual(json.loads(self.published.read_text(encoding="utf-8"))["subagents_count"], 2)
        self.state["subagents_count"] = 1
        self.publish(False)
        self.state["subagents_count"] = 4
        self.publish(True)
        self.assertEqual(json.loads(self.published.read_text(encoding="utf-8"))["subagents_count"], 4)


if __name__ == "__main__":
    unittest.main()

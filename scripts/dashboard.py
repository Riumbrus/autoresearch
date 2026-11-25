"""Publish and serve a local research dashboard."""
import argparse
import hashlib
import json
import os
import tempfile
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

TEMPLATE = Path(__file__).resolve().parents[1] / "assets" / "dashboard.html"


def publish(run):
    state = json.loads((run / "draft.json").read_text(encoding="utf-8-sig"))
    for key in ("topic", "phase", "status"):
        if not isinstance(state[key], str) or not state[key].strip():
            raise ValueError(f"Expected nonempty text for {key}")
    used, limit = (state["rounds"][key] for key in ("used", "limit"))
    if type(used) is not int or used < 0:
        raise ValueError("Число начатых раундов должно быть целым и неотрицательным")
    if limit is not None and (type(limit) is not int or limit <= 0 or used > limit):
        raise ValueError("Числовой лимит должен быть положительным и покрывать начатые раунды")
    for group, fields in (
        ("questions", ("id", "text", "status")),
        ("assignments", ("id", "question_id", "text", "status", "worker")),
        ("sources", ("id", "title", "url", "evidence")),
        ("conflicts", ("id", "text", "status")),
    ):
        rows = state[group]
        if not isinstance(rows, list):
            raise ValueError(f"Expected a list for {group}")
        ids = set()
        for row in rows:
            if not all(isinstance(row[key], str) for key in fields):
                raise ValueError(f"Expected text fields in {group}")
            if not row["id"] or row["id"] in ids:
                raise ValueError(f"Missing or duplicate ID in {group}")
            ids.add(row["id"])

    question_ids = {question["id"] for question in state["questions"]}
    claims = state.get("claims", [])
    published = run / "state.json"
    version = 2
    if published.exists():
        previous = json.loads(published.read_text(encoding="utf-8-sig"))
        version = previous.get("schema_version", 1)
        if previous.get("mode") == "deep":
            state["mode"] = "deep"
    state["schema_version"] = version
    source_ids = {source["id"] for source in state["sources"]}
    covered = set()
    if not isinstance(claims, list):
        raise ValueError("claims должен быть списком")
    for claim in claims:
        fields = ("text", "effect", "evidence", "limitation")
        if not isinstance(claim, dict) or claim.get("status") not in {"Наблюдение", "Гипотеза"}:
            raise ValueError("Укажите статус доказательности вывода")
        if not all(isinstance(claim.get(k), str) and claim[k].strip() for k in fields):
            raise ValueError("Для вывода нужны формулировка, эффект, основание и ограничение")
        if version >= 2:
            refs = claim.get("source_ids")
            if claim.get("question_id") not in question_ids:
                raise ValueError("Укажите существующий question_id карточки")
            if not isinstance(refs, list) or not refs or any(not isinstance(ref, str) or ref not in source_ids for ref in refs):
                raise ValueError("Укажите существующие source_ids карточки")
            covered.add(claim["question_id"])
    if any(item["question_id"] not in question_ids for item in state["assignments"]):
        raise ValueError("Every assignment must reference a question")

    active_subagents = {
        item["worker"].strip() for item in state["assignments"] if item["worker"].strip()
    }
    reviews_raw = state.get("reviews")
    if isinstance(reviews_raw, dict):
        for review in reviews_raw.values():
            if isinstance(review, dict) and isinstance(review.get("agent"), str) and review["agent"].strip():
                active_subagents.add(review["agent"].strip())
    if "subagents_count" in state and state["subagents_count"] is not None:
        count = state["subagents_count"]
        if type(count) is not int or count < len(active_subagents):
            raise ValueError("Число запущенных субагентов должно быть целым и не меньше числа назначенных исполнителей")
    else:
        state["subagents_count"] = len(active_subagents)

    status = state["status"]
    if status not in {"В работе", "Завершено", "Частично"}:
        raise ValueError("Неизвестный статус прогона")
    state.pop("report_text", None)
    if status != "В работе":
        for calculation in state.get("calculations", []):
            inputs = calculation.get("inputs") if isinstance(calculation, dict) else None
            if not isinstance(inputs, dict) or not inputs:
                raise ValueError("У расчёта должны быть файловые зависимости")
            for name, expected in inputs.items():
                with (run / name).open("rb") as source:
                    digest = hashlib.sha256()
                    for chunk in iter(lambda: source.read(1024 * 1024), b""):
                        digest.update(chunk)
                if digest.hexdigest() != expected:
                    raise ValueError(f"Входные данные изменились: {name}. Повторите расчёт.")
        report = run / "report.md"
        report_bytes = report.read_bytes() if report.is_file() else b""
        state["report_text"] = report_bytes.decode("utf-8-sig")
        if not state["report_text"].strip():
            raise ValueError("Для завершения нужен непустой report.md")
        if version >= 2 and any(q["status"] == "Обоснован" and q["id"] not in covered for q in state["questions"]):
            raise ValueError("Для каждого обоснованного ответа нужна карточка со ссылками")
        if not question_ids or any(q["status"] not in {"Обоснован", "Пробел"} for q in state["questions"]):
            raise ValueError("У каждого вопроса должен быть итоговый статус")
        if version >= 2:
            for question in state["questions"]:
                if question["status"] == "Пробел":
                    next_check = question.get("next_check")
                    if not isinstance(next_check, str) or not next_check.strip():
                        raise ValueError(f'{question["id"]}: укажите следующий шаг или ограничение в next_check')
    if status == "Завершено":
        if state.get("mode") == "deep":
            reviews = state.get("reviews", {})
            plan_bytes = (run / "plan.md").read_bytes()
            if not plan_bytes.decode("utf-8-sig").strip():
                raise ValueError("Для проверки плана нужен непустой plan.md")
            plan_hash = hashlib.sha256(plan_bytes).hexdigest()
            report_hash = hashlib.sha256(report_bytes).hexdigest()
            agents = set()
            for role in ("plan", "critique", "acceptance"):
                review = reviews.get(role) if isinstance(reviews, dict) else None
                fields = ("agent", "result", "blind_answer") if role == "acceptance" else ("agent", "result")
                if not isinstance(review, dict) or review.get("verdict") != "Принято" or not all(
                    isinstance(review.get(key), str) and review[key].strip() for key in fields
                ):
                    raise ValueError(f"Для завершения нужна независимая проверка {role}")
                agent = review["agent"].strip()
                expected_hash = plan_hash if role == "plan" else report_hash
                if review.get("artifact_sha256") != expected_hash:
                    raise ValueError(f"Проверка {role} отсутствует для текущей редакции файла")
                if agent in agents:
                    raise ValueError("Назначьте разные агенты для трёх независимых проверок")
                agents.add(agent)
        if any(q["status"] == "Пробел" for q in state["questions"]):
            raise ValueError("Для вопросов с пробелом выберите статус «Частично»")
        if any(c["status"] != "Разобрано" for c in state["conflicts"]):
            raise ValueError("Укажите влияние каждого противоречия на вывод")

    with tempfile.NamedTemporaryFile(dir=run, delete=False, mode="w", encoding="utf-8") as temp:
        temporary_path = Path(temp.name)
        try:
            json.dump(state, temp, ensure_ascii=False, allow_nan=False, indent=2)
            temp.close()
            os.replace(temporary_path, run / "state.json")
        finally:
            temporary_path.unlink(missing_ok=True)


def serve(run, port):
    routes = {
        "/": (TEMPLATE, "text/html; charset=utf-8"),
        "/state.json": (run / "state.json", "application/json; charset=utf-8"),
    }

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            if self.headers.get("Host") != f"127.0.0.1:{self.server.server_port}":
                self.send_error(403)
                return
            if self.path not in routes:
                self.send_error(404)
                return
            path, content_type = routes[self.path]
            try:
                body = path.read_bytes()
            except OSError:
                self.send_error(503)
                return
            self.send_response(200)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("Content-Security-Policy", "default-src 'none'; connect-src 'self'; style-src 'unsafe-inline'; script-src 'unsafe-inline'; frame-ancestors 'none'; base-uri 'none'")
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *_args):
            pass

    with ThreadingHTTPServer(("127.0.0.1", port), Handler) as server:
        print(f"http://127.0.0.1:{server.server_port}/", flush=True)
        try:
            server.serve_forever()
        except KeyboardInterrupt:
            pass


def main():
    parser = argparse.ArgumentParser()
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("publish").add_argument("run", type=Path)
    server = commands.add_parser("serve")
    server.add_argument("run", type=Path)
    server.add_argument("--port", type=int, default=0)
    args = parser.parse_args()
    run = args.run.resolve()
    try:
        if args.command == "publish":
            publish(run)
        else:
            serve(run, args.port)
    except (OSError, ValueError, TypeError, KeyError) as exc:
        parser.exit(1, f"{exc}\n")


if __name__ == "__main__":
    main()

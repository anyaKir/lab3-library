"""Flask-адаптер для процесса библиотеки."""
import logging
import os
from datetime import datetime

from flask import Flask, jsonify, request

from domain import (
    Clock,
    DomainInvariantViolation,
    EntityNotFound,
    GatewayUnresolved,
    SystemClock,
    TransitionNotAllowed,
)
from service import (
    InMemoryBookRepository,
    InMemoryLoanRepository,
    InMemoryReaderRepository,
    LendingProcess,
)


app = Flask(__name__)
app.json.ensure_ascii = False  # ← читаемая кириллица в JSON-ответах

logging.basicConfig(
    level=os.getenv("LOG_LEVEL", "INFO").upper(),
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
logger = logging.getLogger("library_api")

# Все объекты создаются один раз при старте приложения.
clock = Clock(SystemClock())
books = InMemoryBookRepository()
readers = InMemoryReaderRepository()
loans = InMemoryLoanRepository()
process = LendingProcess(books=books, readers=readers, loans=loans, clock=clock)


def serialize_event(event):
    return {
        "name": event.name,
        "occurred_at": event.occurred_at,
        "data": event.data,
    }


def serialize_loan(loan):
    return {
        "id": loan.id,
        "book_id": loan.book_id,
        "reader_id": loan.reader_id,
        "state": loan.state_name,
        "allowed_events": loan.allowed_events,
        "reservation_date": loan.reservation_date.isoformat(),
        "issued_at": loan.issued_at.isoformat() if loan.issued_at else None,
        "due_at": loan.due_at.isoformat() if loan.due_at else None,
        "returned_at": (
            loan.returned_at.isoformat() if loan.returned_at else None
        ),
        "closed_at": loan.closed_at.isoformat() if loan.closed_at else None,
        "extended": loan.extended,
        "damage_penalty": loan.damage_penalty,
        "overdue_penalty": loan.overdue_penalty,
        "total_penalty": loan.total_penalty,
        "events": [serialize_event(e) for e in loan.events],
    }


def serialize_book(book):
    return {
        "id": book.id,
        "title": book.title,
        "total_copies": book.total_copies,
        "issued_copies": book.issued_copies,
        "reserved_copies": book.reserved_copies,
        "available_copies": book.available_copies,
        "waiting_readers": book.waiting_readers,
        "has_queue": book.has_queue(),
    }


def serialize_reader(reader):
    return {
        "id": reader.id,
        "name": reader.name,
        "overdue_books": reader.overdue_books,
        "unpaid_fines": reader.unpaid_fines,
        "has_debt": reader.has_debt(),
        "blocked": reader.blocked,
    }


def json_body():
    data = request.get_json(silent=True)
    if not isinstance(data, dict):
        raise DomainInvariantViolation(
            "Тело запроса должно быть JSON-объектом"
        )
    return data


@app.errorhandler(TransitionNotAllowed)
def handle_transition_error(error):
    return jsonify(
        error="transition_not_allowed", message=str(error)
    ), 409


@app.errorhandler(DomainInvariantViolation)
def handle_invariant_error(error):
    logger.info("Ошибка бизнес-правила или валидации: %s", error)
    return jsonify(
        error="domain_invariant_violation", message=str(error)
    ), 422


@app.errorhandler(EntityNotFound)
def handle_not_found(error):
    return jsonify(error="not_found", message=str(error)), 404


@app.errorhandler(GatewayUnresolved)
def handle_gateway_error(error):
    logger.exception("Не удалось определить ветвь шлюза")
    return jsonify(
        error="gateway_unresolved", message=str(error)
    ), 500


@app.errorhandler(400)
def handle_bad_request(error):
    return jsonify(
        error="bad_request", message="Некорректный запрос"
    ), 400


@app.errorhandler(404)
def handle_route_not_found(error):
    return jsonify(
        error="route_not_found", message="Маршрут не найден"
    ), 404


@app.get("/health")
def health():
    return jsonify(status="ok"), 200


@app.post("/books")
def create_book():
    data = json_body()
    book = process.register_book(
        book_id=data.get("book_id", ""),
        title=data.get("title", ""),
        total_copies=data.get("total_copies", 1),
    )
    return jsonify(serialize_book(book)), 201


@app.get("/books/<book_id>")
def get_book(book_id):
    return jsonify(serialize_book(process.get_book(book_id))), 200


@app.post("/books/<book_id>/queue")
def add_reader_to_queue(book_id):
    data = json_body()
    book = process.get_book(book_id)
    reader_id = data.get("reader_id")
    if not isinstance(reader_id, str) or not reader_id.strip():
        raise DomainInvariantViolation("reader_id обязателен")
    process.get_reader(reader_id)
    book.add_to_queue(reader_id)
    return jsonify(serialize_book(book)), 200


@app.post("/readers")
def create_reader():
    data = json_body()
    reader = process.register_reader(
        reader_id=data.get("reader_id", ""),
        name=data.get("name", ""),
    )
    if "overdue_books" in data or "unpaid_fines" in data:
        process.set_reader_debt(
            reader.id,
            data.get("overdue_books", 0),
            data.get("unpaid_fines", 0),
        )
    return jsonify(serialize_reader(reader)), 201


@app.get("/readers/<reader_id>")
def get_reader(reader_id):
    return jsonify(serialize_reader(process.get_reader(reader_id))), 200


@app.patch("/readers/<reader_id>/debt")
def update_reader_debt(reader_id):
    data = json_body()
    reader = process.set_reader_debt(
        reader_id,
        data.get("overdue_books", 0),
        data.get("unpaid_fines", 0),
    )
    return jsonify(serialize_reader(reader)), 200


@app.post("/loans")
def create_loan():
    data = json_body()
    loan = process.reserve_book(
        book_id=data.get("book_id", ""),
        reader_id=data.get("reader_id", ""),
    )
    return jsonify(serialize_loan(loan)), 201


@app.get("/loans/<loan_id>")
def get_loan(loan_id):
    return jsonify(serialize_loan(process.get_loan(loan_id))), 200


@app.post("/loans/<loan_id>/issue")
def issue_loan(loan_id):
    return jsonify(serialize_loan(process.issue_book(loan_id))), 200


@app.post("/loans/<loan_id>/refuse")
def refuse_loan(loan_id):
    return jsonify(serialize_loan(process.refuse_issue(loan_id))), 200


@app.post("/loans/<loan_id>/extend")
def extend_loan(loan_id):
    return jsonify(serialize_loan(process.extend_loan(loan_id))), 200


@app.post("/loans/<loan_id>/refuse-extension")
def refuse_extension(loan_id):
    return jsonify(serialize_loan(process.refuse_extension(loan_id))), 200


@app.post("/loans/<loan_id>/return")
def return_loan(loan_id):
    data = request.get_json(silent=True)
    if data is None:
        data = {}
    if not isinstance(data, dict):
        raise DomainInvariantViolation(
            "Тело запроса должно быть JSON-объектом"
        )
    loan = process.accept_return(
        loan_id,
        damaged=data.get("damaged", False),
        damage_amount=data.get("damage_amount", 500),
    )
    return jsonify(serialize_loan(loan)), 200


@app.post("/loans/<loan_id>/close")
def close_loan(loan_id):
    return jsonify(serialize_loan(process.close_loan(loan_id))), 200


@app.post("/internal/tick")
def tick():
    data = request.get_json(silent=True)
    if data is None:
        data = {}
    if not isinstance(data, dict):
        raise DomainInvariantViolation(
            "Ожидается JSON-объект в теле запроса"
        )
    raw = data.get("now")
    if raw is None:
        now = clock.now()
    else:
        if not isinstance(raw, str):
            raise DomainInvariantViolation(
                "now должен быть строкой ISO 8601"
            )
        try:
            requested_time = datetime.fromisoformat(raw)
        except ValueError as error:
            raise DomainInvariantViolation(
                "now должен содержать корректную дату ISO 8601"
            ) from error
        now = clock.set(requested_time)

    processed = process.process_timers(now)
    logger.info("Обработаны таймеры: %d", len(processed))
    return jsonify(
        now=now.isoformat(timespec="seconds"),
        processed=processed,
    ), 200


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.getenv("PORT", "8000")))
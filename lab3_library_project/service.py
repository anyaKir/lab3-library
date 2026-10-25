"""Прикладной сервис процесса выдачи книг."""
from datetime import datetime, timedelta
from uuid import uuid4

from domain import (
    Book,
    ClockPort,
    DomainInvariantViolation,
    EntityNotFound,
    GatewayUnresolved,
    Loan,
    Reader,
    add_working_days,
)


class InMemoryRepository:
    def __init__(self):
        self._items = {}

    def add(self, item) -> None:
        self._items[item.id] = item

    def get(self, item_id: str):
        item = self._items.get(item_id)
        if item is None:
            raise EntityNotFound(f"Сущность '{item_id}' не найдена")
        return item

    def all(self) -> list:
        return list(self._items.values())


class InMemoryBookRepository(InMemoryRepository):
    pass


class InMemoryReaderRepository(InMemoryRepository):
    pass


class InMemoryLoanRepository(InMemoryRepository):
    pass


class LendingProcess:
    """Оркестрация процесса библиотеки (прикладной слой)."""

    def __init__(
        self,
        books: InMemoryBookRepository,
        readers: InMemoryReaderRepository,
        loans: InMemoryLoanRepository,
        clock: ClockPort,
    ):
        self.books = books
        self.readers = readers
        self.loans = loans
        self.clock = clock

    # ---------- Каталог и читатели ----------

    def register_book(
        self, book_id: str, title: str, total_copies: int
    ) -> Book:
        if not isinstance(book_id, str) or not book_id.strip():
            raise DomainInvariantViolation("book_id обязателен")
        if not isinstance(title, str) or not title.strip():
            raise DomainInvariantViolation("title обязателен")
        if not isinstance(total_copies, int) or isinstance(total_copies, bool):
            raise DomainInvariantViolation(
                "total_copies должен быть целым числом"
            )
        if total_copies < 1:
            raise DomainInvariantViolation(
                "total_copies должен быть не меньше 1"
            )
        if any(b.id == book_id for b in self.books.all()):
            raise DomainInvariantViolation(
                "Книга с таким ID уже существует"
            )
        book = Book(
            id=book_id.strip(),
            title=title.strip(),
            total_copies=total_copies,
        )
        self.books.add(book)
        return book

    def register_reader(self, reader_id: str, name: str) -> Reader:
        if not isinstance(reader_id, str) or not reader_id.strip():
            raise DomainInvariantViolation("reader_id обязателен")
        if not isinstance(name, str) or not name.strip():
            raise DomainInvariantViolation("name обязателен")
        if any(r.id == reader_id for r in self.readers.all()):
            raise DomainInvariantViolation(
                "Читатель с таким ID уже существует"
            )
        reader = Reader(id=reader_id.strip(), name=name.strip())
        self.readers.add(reader)
        return reader

    def set_reader_debt(
        self, reader_id: str, overdue_books: int, unpaid_fines: float
    ) -> Reader:
        reader = self.readers.get(reader_id)
        if (
            isinstance(overdue_books, bool)
            or not isinstance(overdue_books, int)
            or overdue_books < 0
        ):
            raise DomainInvariantViolation(
                "overdue_books должен быть целым неотрицательным числом"
            )
        if (
            isinstance(unpaid_fines, bool)
            or not isinstance(unpaid_fines, (int, float))
            or unpaid_fines < 0
        ):
            raise DomainInvariantViolation(
                "unpaid_fines должен быть неотрицательным числом"
            )
        reader.overdue_books = overdue_books
        reader.unpaid_fines = float(unpaid_fines)
        return reader

    def get_book(self, book_id: str) -> Book:
        return self.books.get(book_id)

    def get_reader(self, reader_id: str) -> Reader:
        return self.readers.get(reader_id)

    # ---------- Бронирование ----------

    def reserve_book(self, book_id: str, reader_id: str) -> Loan:
        book = self.books.get(book_id)
        reader = self.readers.get(reader_id)

        if reader.blocked:
            raise DomainInvariantViolation(
                "Читательский билет заблокирован"
            )
        if book.available_copies <= 0:
            book.add_to_queue(reader_id)
            raise DomainInvariantViolation(
                "Нет свободных экземпляров; читатель добавлен в очередь"
            )

        now = self.clock.now()
        book.reserve_copy()
        loan = Loan(
            id=str(uuid4()),
            book_id=book_id,
            reader_id=reader_id,
            reservation_date=now.date(),
        )
        loan.record_event("Бронь зарегистрирована", now)
        self.loans.add(loan)
        return loan

    def get_loan(self, loan_id: str) -> Loan:
        return self.loans.get(loan_id)

    # ---------- Шлюзы ----------

    @staticmethod
    def gateway_has_debt(reader: Reader) -> bool:
        return reader.has_debt()

    @staticmethod
    def gateway_has_queue(book: Book) -> bool:
        return book.has_queue()

    @staticmethod
    def resolve_gateway(*, has_debt=None, has_queue=None) -> str:
        if has_debt is not None:
            return "debt_exists" if has_debt else "no_debt"
        if has_queue is not None:
            return "queue_exists" if has_queue else "no_queue"
        raise GatewayUnresolved("Не удалось определить ветвь шлюза")

    # ---------- Выдача и продление ----------

    def check_reader_debt(self, reader_id: str) -> bool:
        return self.gateway_has_debt(self.readers.get(reader_id))

    def issue_book(self, loan_id: str) -> Loan:
        loan = self.loans.get(loan_id)
        reader = self.readers.get(loan.reader_id)
        book = self.books.get(loan.book_id)

        if reader.blocked:
            raise DomainInvariantViolation(
                "Выдача невозможна: читательский билет заблокирован"
            )
        if self.check_reader_debt(reader.id):
            loan.apply("refuse_issue", self.clock.now())
            return loan
        loan.apply("issue", self.clock.now(), book=book)
        return loan

    def refuse_issue(self, loan_id: str) -> Loan:
        loan = self.loans.get(loan_id)
        loan.apply("refuse_issue", self.clock.now())
        return loan

    def check_extension(self, loan_id: str) -> bool:
        loan = self.loans.get(loan_id)
        book = self.books.get(loan.book_id)
        return (
            loan.state_name in ("Выдана", "Продлена")
            and not loan.extended
            and not self.gateway_has_queue(book)
            and loan.due_at is not None
            and self.clock.now().date() <= loan.due_at
        )

    def extend_loan(self, loan_id: str) -> Loan:
        loan = self.loans.get(loan_id)
        book = self.books.get(loan.book_id)
        has_queue = self.gateway_has_queue(book)
        if has_queue:
            loan.apply("refuse_extension", self.clock.now())
            return loan
        loan.apply("extend", self.clock.now(), has_queue=False)
        return loan

    def refuse_extension(self, loan_id: str) -> Loan:
        loan = self.loans.get(loan_id)
        loan.apply("refuse_extension", self.clock.now())
        return loan

    # ---------- Возврат, штрафы и закрытие ----------

    def inspect_book(
        self, loan_id: str, damaged: bool, damage_amount: float = 500
    ) -> Loan:
        if not isinstance(damaged, bool):
            raise DomainInvariantViolation(
                "damaged должно быть логическим значением"
            )
        if (
            isinstance(damage_amount, bool)
            or not isinstance(damage_amount, (int, float))
            or damage_amount < 0
        ):
            raise DomainInvariantViolation(
                "damage_amount должен быть неотрицательным числом"
            )
        if damaged and damage_amount == 0:
            raise DomainInvariantViolation(
                "При повреждении штраф должен быть больше нуля"
            )

        loan = self.loans.get(loan_id)
        book = self.books.get(loan.book_id)
        loan.apply(
            "accept_return",
            self.clock.now(),
            damaged=damaged,
            damage_amount=damage_amount,
        )
        book.return_copy()

        reader = self.readers.get(loan.reader_id)
        if loan.total_penalty > 0:
            reader.unpaid_fines += loan.total_penalty
        return loan

    def accept_return(
        self, loan_id: str, damaged: bool = False, damage_amount: float = 500
    ) -> Loan:
        return self.inspect_book(loan_id, damaged, damage_amount)

    def close_loan(self, loan_id: str) -> Loan:
        loan = self.loans.get(loan_id)
        loan.apply("close", self.clock.now())
        return loan

    # ---------- Таймеры ----------

    @staticmethod
    def reservation_expired(loan: Loan, now: datetime) -> bool:
        deadline = add_working_days(loan.reservation_date, 3)
        return loan.state_name == "Забронирована" and now.date() > deadline

    @staticmethod
    def reminder_due(loan: Loan, now: datetime) -> bool:
        return (
            loan.state_name in ("Выдана", "Продлена")
            and loan.due_at is not None
            and now.date() == loan.due_at - timedelta(days=2)
        )

    @staticmethod
    def return_due_passed(loan: Loan, now: datetime) -> bool:
        return (
            loan.state_name in ("Выдана", "Продлена")
            and loan.due_at is not None
            and now.date() > loan.due_at
        )

    @staticmethod
    def overdue_escalation_due(loan: Loan, now: datetime) -> bool:
        return (
            loan.state_name == "Просрочена"
            and loan.due_at is not None
            and now.date() >= loan.due_at + timedelta(days=30)
        )

    def on_reservation_expired(self, loan: Loan, now: datetime) -> dict:
        book = self.books.get(loan.book_id)
        loan.apply("on_reservation_expired", now)
        book.release_reservation()

        next_reader_id = book.pass_to_next_reader()
        result = {
            "loan_id": loan.id,
            "event": "reservation_expired",
            "next_reader_id": next_reader_id,
        }

        if next_reader_id:
            book.reserve_copy()
            next_loan = Loan(
                id=str(uuid4()),
                book_id=book.id,
                reader_id=next_reader_id,
                reservation_date=now.date(),
            )
            next_loan.record_event(
                "Бронь передана следующему читателю", now
            )
            self.loans.add(next_loan)
            result["next_loan_id"] = next_loan.id
        else:
            result["event"] = "reservation_expired_returned_to_fund"
        return result

    def on_reminder_due(self, loan: Loan, now: datetime) -> dict:
        loan.apply("remind", now)
        return {"loan_id": loan.id, "event": "reminder"}

    def on_due_date_passed(self, loan: Loan, now: datetime) -> dict:
        loan.apply("on_due_date_passed", now)
        return {"loan_id": loan.id, "event": "due_date_passed"}

    def on_overdue_30_days(self, loan: Loan, now: datetime) -> dict:
        loan.apply("on_overdue_30_days", now)
        reader = self.readers.get(loan.reader_id)
        reader.block_card(now)
        return {"loan_id": loan.id, "event": "overdue_30_days"}

    def process_timers(self, now: datetime) -> list[dict]:
        processed = []
        for loan in self.loans.all():
            if self.reservation_expired(loan, now):
                processed.append(self.on_reservation_expired(loan, now))
                continue

            if self.reminder_due(loan, now):
                already = any(
                    e.name == "Напоминание о сроке возврата отправлено"
                    and e.occurred_at[:10] == now.date().isoformat()
                    for e in loan.events
                )
                if not already:
                    processed.append(self.on_reminder_due(loan, now))

            if self.return_due_passed(loan, now):
                processed.append(self.on_due_date_passed(loan, now))

            if self.overdue_escalation_due(loan, now):
                processed.append(self.on_overdue_30_days(loan, now))
        return processed
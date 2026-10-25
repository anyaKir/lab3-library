from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from typing import Any, Protocol


class TransitionNotAllowed(Exception):
    """Недопустимый переход состояния."""


class DomainInvariantViolation(Exception):
    """Нарушение бизнес-правила или валидации."""


class EntityNotFound(Exception):
    """Сущность не найдена."""


class GatewayUnresolved(Exception):
    """Не удалось определить ветвь бизнес-шлюза."""


@dataclass(frozen=True)
class DomainEvent:
    name: str
    occurred_at: str
    data: dict[str, Any] = field(default_factory=dict)


def add_working_days(start: date, days: int) -> date:
    """Добавляет рабочие дни, исключая субботу и воскресенье."""
    current = start
    added = 0
    while added < days:
        current += timedelta(days=1)
        if current.weekday() < 5:
            added += 1
    return current


class SystemClockPort(Protocol):
    """Порт системного времени (для тестируемости домена)."""

    def system_now(self) -> datetime:
        ...


class ClockPort(Protocol):
    """Порт часов приложения (системное время + сдвиг)."""

    def now(self) -> datetime:
        ...


class SystemClock:
    """Реальные системные часы. Единственное место с datetime.now()."""

    def system_now(self) -> datetime:
        return datetime.now()


class Clock:
    """Часы приложения: системное время со сдвигом.

    Перевод часов назад не допускается.
    """

    def __init__(self, system_clock: SystemClockPort):
        self._system = system_clock
        self._offset = timedelta(0)
        self._last = self._system.system_now()

    def now(self) -> datetime:
        current = self._system.system_now() + self._offset
        # Из-за микросекундной разницы не считаем это переводом назад.
        if current < self._last:
            return self._last
        self._last = current
        return current

    def set(self, target: datetime) -> datetime:
        current = self.now()
        if target < current:
            raise DomainInvariantViolation(
                "Перевод часов приложения назад не допускается"
            )
        self._offset = target - self._system.system_now()
        self._last = target
        return target


# ---------------------------------------------------------------------
# Book — фонд, резервирование экземпляров и очередь читателей
# ---------------------------------------------------------------------

@dataclass
class Book:
    id: str
    title: str
    total_copies: int
    issued_copies: int = 0
    reserved_copies: int = 0
    waiting_readers: list[str] = field(default_factory=list)

    def __post_init__(self):
        if not isinstance(self.total_copies, int) or isinstance(self.total_copies, bool):
            raise DomainInvariantViolation(
                "total_copies должен быть целым числом"
            )
        if self.total_copies < 1:
            raise DomainInvariantViolation(
                "total_copies должен быть не меньше 1"
            )
        if not isinstance(self.title, str) or not self.title.strip():
            raise DomainInvariantViolation("Название книги обязательно")
        if not isinstance(self.id, str) or not self.id.strip():
            raise DomainInvariantViolation("id книги обязателен")
        self._check_invariant()

    def _check_invariant(self):
        if self.issued_copies < 0 or self.reserved_copies < 0:
            raise DomainInvariantViolation(
                "Количество выданных и зарезервированных не может быть отрицательным"
            )
        if self.issued_copies + self.reserved_copies > self.total_copies:
            raise DomainInvariantViolation(
                "Сумма выданных и зарезервированных превышает общий фонд"
            )

    @property
    def available_copies(self) -> int:
        return self.total_copies - self.issued_copies - self.reserved_copies

    def reserve_copy(self) -> None:
        if self.available_copies <= 0:
            raise DomainInvariantViolation(
                "Нет доступных экземпляров для бронирования"
            )
        self.reserved_copies += 1
        self._check_invariant()

    def issue_copy(self) -> None:
        if self.reserved_copies <= 0:
            raise DomainInvariantViolation(
                "Нельзя выдать книгу без активной брони"
            )
        self.reserved_copies -= 1
        self.issued_copies += 1
        self._check_invariant()

    def release_reservation(self) -> None:
        if self.reserved_copies <= 0:
            raise DomainInvariantViolation(
                "Нет зарезервированного экземпляра для снятия брони"
            )
        self.reserved_copies -= 1
        self._check_invariant()

    def return_copy(self) -> None:
        if self.issued_copies <= 0:
            raise DomainInvariantViolation(
                "Нет выданного экземпляра для возврата"
            )
        self.issued_copies -= 1
        self._check_invariant()

    def has_queue(self) -> bool:
        return bool(self.waiting_readers)

    def add_to_queue(self, reader_id: str) -> None:
        if not isinstance(reader_id, str) or not reader_id.strip():
            raise DomainInvariantViolation("reader_id обязателен")
        if reader_id in self.waiting_readers:
            raise DomainInvariantViolation(
                "Читатель уже находится в очереди на эту книгу"
            )
        self.waiting_readers.append(reader_id)

    def pass_to_next_reader(self) -> str | None:
        """Передаёт освободившуюся бронь первому читателю в очереди.

        Резерв НЕ создаёт — это делает вызывающий код, чтобы избежать
        двойного резервирования.
        """
        if not self.waiting_readers:
            return None
        return self.waiting_readers.pop(0)


# ---------------------------------------------------------------------
# Reader — задолженность и блокировка читательского билета
# ---------------------------------------------------------------------

@dataclass
class Reader:
    id: str
    name: str
    overdue_books: int = 0
    unpaid_fines: float = 0.0
    blocked: bool = False

    def __post_init__(self):
        if not isinstance(self.id, str) or not self.id.strip():
            raise DomainInvariantViolation("reader_id обязателен")
        if not isinstance(self.name, str) or not self.name.strip():
            raise DomainInvariantViolation("name обязателен")
        if self.overdue_books < 0:
            raise DomainInvariantViolation(
                "overdue_books не может быть отрицательным"
            )
        if self.unpaid_fines < 0:
            raise DomainInvariantViolation(
                "unpaid_fines не может быть отрицательным"
            )

    def has_debt(self) -> bool:
        return self.overdue_books > 0 or self.unpaid_fines > 0

    def block_card(self, now: datetime) -> None:
        self.blocked = True


# ---------------------------------------------------------------------
# Состояния Loan — паттерн GoF «Состояние»
# ---------------------------------------------------------------------

class LoanState:
    name = "Не определена"
    events: tuple[str, ...] = ()

    def _deny(self, event: str):
        raise TransitionNotAllowed(
            f"Событие '{event}' недопустимо в состоянии '{self.name}'"
        )

    def issue(self, loan, book, now):
        self._deny("issue")

    def refuse_issue(self, loan, now):
        self._deny("refuse_issue")

    def on_reservation_expired(self, loan, now):
        self._deny("on_reservation_expired")

    def extend(self, loan, has_queue, now):
        self._deny("extend")

    def on_due_date_passed(self, loan, now):
        self._deny("on_due_date_passed")

    def accept_return(self, loan, now):
        self._deny("accept_return")

    def close(self, loan, now):
        self._deny("close")

    def on_overdue_30_days(self, loan, now):
        self._deny("on_overdue_30_days")

    def refuse_extension(self, loan, now):
        self._deny("refuse_extension")

    def remind(self, loan, now):
        self._deny("remind")


class Reserved(LoanState):
    name = "Забронирована"
    events = ("issue", "refuse_issue", "on_reservation_expired")

    def issue(self, loan, book, now):
        book.issue_copy()
        loan.issued_at = now.date()
        loan.due_at = now.date() + timedelta(days=14)
        loan._record("Книга выдана на 14 дней", now)
        return Issued()

    def refuse_issue(self, loan, now):
        # Бронь остаётся активной — читатель может прийти повторно.
        loan._record("Выдача отклонена: задолженность", now)
        return self

    def on_reservation_expired(self, loan, now):
        loan._record("Бронь снята: срок истёк", now)
        return Cancelled()


class Issued(LoanState):
    name = "Выдана"
    events = (
        "extend", "on_due_date_passed", "accept_return",
        "remind", "refuse_extension",
    )

    def extend(self, loan, has_queue, now):
        if has_queue:
            raise DomainInvariantViolation(
                "Продление невозможно: на книгу есть очередь"
            )
        if loan.extended:
            raise DomainInvariantViolation(
                "Продление невозможно: срок уже продлевался"
            )
        if loan.due_at is None or now.date() > loan.due_at:
            raise DomainInvariantViolation(
                "Продление невозможно после истечения срока возврата"
            )
        loan.due_at += timedelta(days=7)
        loan.extended = True
        loan._record("Срок возврата продлён на 7 дней", now)
        return Extended()

    def on_due_date_passed(self, loan, now):
        if loan.due_at is None or now.date() <= loan.due_at:
            raise DomainInvariantViolation("Срок возврата ещё не истёк")
        loan._record("Зафиксирована просрочка", now)
        return Overdue()

    def accept_return(self, loan, now):
        loan._accept_return(now)
        return Returned()

    def remind(self, loan, now):
        loan._record("Напоминание о сроке возврата отправлено", now)
        return self

    def refuse_extension(self, loan, now):
        loan._record("В продлении отказано: на книгу есть очередь", now)
        return self


class Extended(LoanState):
    name = "Продлена"
    events = (
        "on_due_date_passed", "accept_return",
        "remind", "refuse_extension",
    )

    def on_due_date_passed(self, loan, now):
        if loan.due_at is None or now.date() <= loan.due_at:
            raise DomainInvariantViolation("Срок возврата ещё не истёк")
        loan._record("Зафиксирована просрочка", now)
        return Overdue()

    def accept_return(self, loan, now):
        loan._accept_return(now)
        return Returned()

    def remind(self, loan, now):
        loan._record("Напоминание о сроке возврата отправлено", now)
        return self

    def refuse_extension(self, loan, now):
        loan._record("В продлении отказано: срок уже продлевался", now)
        return self


class Overdue(LoanState):
    name = "Просрочена"
    events = ("accept_return", "on_overdue_30_days")

    def accept_return(self, loan, now):
        loan._accept_return(now)
        return Returned()

    def on_overdue_30_days(self, loan, now):
        if loan.due_at is None or now.date() < loan.due_at + timedelta(days=30):
            raise DomainInvariantViolation(
                "30 дней после срока возврата ещё не прошло"
            )
        loan._record("Дело передано администрации", now)
        return Escalated()


class Returned(LoanState):
    name = "Возвращена"
    events = ("close",)

    def close(self, loan, now):
        loan.closed_at = now.date()
        loan._record("Выдача закрыта", now)
        return Closed()


class Closed(LoanState):
    name = "Закрыта"
    events = ()


class Cancelled(LoanState):
    name = "Снята"
    events = ()


class Escalated(LoanState):
    name = "Передана администрации"
    events = ()


# ---------------------------------------------------------------------
# Loan — агрегат жизненного цикла выдачи
# ---------------------------------------------------------------------

@dataclass
class Loan:
    id: str
    book_id: str
    reader_id: str
    reservation_date: date
    _state: LoanState = field(default_factory=Reserved, repr=False)
    issued_at: date | None = None
    due_at: date | None = None
    returned_at: date | None = None
    closed_at: date | None = None
    extended: bool = False
    damage_penalty: float = 0.0
    overdue_penalty: float = 0.0
    events: list[DomainEvent] = field(default_factory=list)

    @property
    def state_name(self) -> str:
        return self._state.name

    @property
    def allowed_events(self) -> list[str]:
        return list(self._state.events)

    @property
    def total_penalty(self) -> float:
        return self.damage_penalty + self.overdue_penalty

    def _record(self, name: str, now: datetime, **data):
        self.events.append(
            DomainEvent(name, now.isoformat(timespec="seconds"), data)
        )

    def record_event(self, name: str, now: datetime, **data):
        self._record(name, now, **data)

    def _accept_return(self, now: datetime):
        if self.issued_at and now.date() < self.issued_at:
            raise DomainInvariantViolation(
                "Дата возврата раньше даты выдачи"
            )
        self.returned_at = now.date()
        if self.due_at and self.returned_at > self.due_at:
            days = (self.returned_at - self.due_at).days
            self.overdue_penalty = float(days * 100)
            self._record(
                "Начислен штраф за просрочку",
                now,
                days=days,
                amount=self.overdue_penalty,
            )
        self._record("Книга принята от читателя", now)

    def apply(self, event: str, now: datetime, **kwargs):
        """Единая точка перехода между состояниями.

        Публичный метод агрегата; изменять состояние в обход него нельзя
        (у _state нет публичного сеттера).
        """
        if event not in self._state.events:
            # Делегируем в состояние — оно бросит TransitionNotAllowed
            # с корректным сообщением.
            getattr(self._state, event, None)
            raise TransitionNotAllowed(
                f"Событие '{event}' недопустимо в состоянии "
                f"'{self._state.name}'"
            )

        if event == "issue":
            new_state = self._state.issue(self, kwargs["book"], now)
        elif event == "refuse_issue":
            new_state = self._state.refuse_issue(self, now)
        elif event == "on_reservation_expired":
            new_state = self._state.on_reservation_expired(self, now)
        elif event == "extend":
            new_state = self._state.extend(
                self, kwargs.get("has_queue", False), now
            )
        elif event == "on_due_date_passed":
            new_state = self._state.on_due_date_passed(self, now)
        elif event == "accept_return":
            new_state = self._state.accept_return(self, now)
            if kwargs.get("damaged", False):
                amount = kwargs.get("damage_amount", 500)
                if amount <= 0:
                    raise DomainInvariantViolation(
                        "При повреждении штраф должен быть больше нуля"
                    )
                self.damage_penalty = float(amount)
                self._record(
                    "Составлен акт о повреждении и начислен штраф",
                    now,
                    amount=self.damage_penalty,
                )
        elif event == "close":
            new_state = self._state.close(self, now)
        elif event == "on_overdue_30_days":
            new_state = self._state.on_overdue_30_days(self, now)
        elif event == "refuse_extension":
            new_state = self._state.refuse_extension(self, now)
        elif event == "remind":
            new_state = self._state.remind(self, now)
        else:
            raise DomainInvariantViolation(f"Неизвестное событие: {event}")

        self._state = new_state
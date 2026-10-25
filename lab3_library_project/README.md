# Лабораторная работа №3 — жизненный цикл выдачи книги

## Требования
- Python 3.12+ для локального запуска; Docker Desktop для запуска в контейнере.
- Данные хранятся в памяти и сбрасываются при перезапуске — это соответствует условию методички.

## Запуск локально (PowerShell)
```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
$env:PORT=8000
python app.py
```

## Запуск в Docker
```powershell
docker compose up --build
```
Проверка готовности: `curl.exe http://localhost:8000/health`

## Основные маршруты
- `POST /loans` — создать бронь; JSON: `{"book_id":"book-1","reader_id":"reader-1"}`
- `GET /loans/<id>` — состояние, допустимые события и журнал доменных событий
- `POST /loans/<id>/issue` — выдать книгу на 14 дней
- `POST /loans/<id>/refuse` — отказать в выдаче, бронь остаётся активной
- `POST /loans/<id>/extend` — продлить на 7 дней; JSON `{"has_queue":false}`
- `POST /loans/<id>/return` — принять книгу; JSON `{"damaged":true,"damage_amount":500}`
- `POST /loans/<id>/close` — закрыть выдачу после приёма книги
- `POST /internal/tick` — сдвинуть часы вперёд и обработать таймеры; JSON `{"now":"2027-01-20T12:00:00"}`
- `GET /health` — проверка готовности

## Пример проверки через PowerShell
Создать бронь:
```powershell
$r = Invoke-RestMethod -Method Post -Uri http://localhost:8000/loans -ContentType "application/json" -Body '{"book_id":"book-1","reader_id":"reader-1"}'
$id = $r.id
$r | ConvertTo-Json -Depth 8
```
Выдать книгу и проверить состояние:
```powershell
Invoke-RestMethod -Method Post -Uri "http://localhost:8000/loans/$id/issue" -ContentType "application/json" -Body '{}'
Invoke-RestMethod -Method Get -Uri "http://localhost:8000/loans/$id" | ConvertTo-Json -Depth 8
```
Продление:
```powershell
Invoke-RestMethod -Method Post -Uri "http://localhost:8000/loans/$id/extend" -ContentType "application/json" -Body '{"has_queue":false}'
```
Возврат и закрытие:
```powershell
Invoke-RestMethod -Method Post -Uri "http://localhost:8000/loans/$id/return" -ContentType "application/json" -Body '{"damaged":false}'
Invoke-RestMethod -Method Post -Uri "http://localhost:8000/loans/$id/close" -ContentType "application/json" -Body '{}'
```
Недопустимое событие: повторная выдача после закрытия должна вернуть HTTP 409:
```powershell
Invoke-WebRequest -Method Post -Uri "http://localhost:8000/loans/$id/issue" -ContentType "application/json" -Body '{}'
```
Некорректные данные должны возвращать 422, отсутствующая сущность — 404.

## Важные замечания
1. Веб-слой находится в `app.py`; доменная модель и классы состояний — в `domain.py`. `domain.py` не импортирует Flask.
2. Часы реализованы через `Clock`, а обработка таймеров выполняется маршрутом `/internal/tick`.
3. Для учебной демонстрации начисление штрафа за просрочку установлено 100 рублей за день, штраф за повреждение по умолчанию 500 рублей. Если в вашей методичке/отчёте указана другая ставка, измените константы перед сдачей.
4. Данные репозитория находятся в памяти; после перезапуска контейнера заявки исчезают.

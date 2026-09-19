# silvercare-api

Бэкенд демо «SilverCare: Контроль лекарств» — напоминания о приёме лекарств для пожилых.
Python + FastAPI + PostgreSQL. Без auth, без миграций (таблицы создаются на старте).

## Запуск локально

```bash
git clone <repo-url> && cd silvercare-api
cp .env.example .env            # впиши свой DATABASE_URL
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
uvicorn app.main:app --reload
```

Проверка:

```bash
curl http://127.0.0.1:8000/health
curl -X POST http://127.0.0.1:8000/seed
curl http://127.0.0.1:8000/patients/1/today
curl -X POST http://127.0.0.1:8000/intakes/1/take
curl http://127.0.0.1:8000/stats
```

## Эндпоинты

- `GET /health` — проверка API и БД.
- `POST /seed` — демо-данные (идемпотентно): Айгуль (id=1), Серик (id=2).
- `GET /patients/{id}/today` — слоты на сегодня + `next` (ближайший pending).
- `POST /intakes/{id}/take` — отметить приём.
- `GET /stats` — taken / missed по пациентам и итого.

Время — локальное Asia/Almaty. Слот становится `missed` через 60 минут после назначенного времени, если не отмечен.

## Деплой на Railway

1. Репозиторий подключён к Railway-сервису, деплой при пуше в `main`.
2. Команда запуска берётся из `Procfile`.
3. В переменных сервиса задать `DATABASE_URL` (Railway Postgres).

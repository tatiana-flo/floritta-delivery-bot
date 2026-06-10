# Floritta Delivery ETA Bot — Python

Telegram-бот для группы доставки Floritta. Считает оптимальный маршрут курьеру через Google Routes API с живым трафиком.

**Стек:** Python 3.11+, python-telegram-bot 21, httpx, SQLite. Деплой на Railway.

---

## Что умеет

1. **Приём списка посткодов** от менеджера — `📋 Принято N посткод(ов)` + кнопка `🚀 Старт`
2. **Расчёт маршрута** по нажатию кнопки — приоритетные ⭐ первыми, далее greedy nearest-neighbor, +7 мин паркинг/донести на каждой точке
3. **Мид-маршрутный апдейт приоритетов** — менеджер пишет `<посткод> приоритет` → мгновенный пересчёт
4. **Отмена посткодов** — `<посткод> отменили`
5. **Whitelist менеджеров** — опционально, только перечисленные могут изменять список
6. **Игнор фото/стикеров/обычной переписки** — реагирует только на постовые коды и кнопки

Полная архитектура в `/Users/tatiana/Documents/Floritta_Brain/DELIVERY_BOT_HANDOFF.md`.

---

## Структура проекта

```
floritta-delivery-bot/
├── README.md              ← этот файл
├── .env.example           ← шаблон env-переменных
├── .gitignore
├── requirements.txt       ← Python deps
├── Procfile               ← Railway/Heroku start command
├── runtime.txt            ← Python версия
├── railway.json           ← Railway конфиг
└── src/
    ├── __init__.py
    ├── bot.py             ← entry point: `python -m src.bot`
    ├── config.py          ← загрузка env-переменных
    ├── classifier.py      ← регексы и классификация сообщений
    ├── routes.py          ← Google Routes API + greedy маршрутизация
    ├── handlers.py        ← Telegram handlers (message + callback_query)
    └── storage.py         ← SQLite-хранилище списков по chat_id
```

---

## Локальный запуск (тест перед деплоем)

```bash
# 1. Клонировать репозиторий
cd /Users/tatiana/Documents/Floritta_Brain/floritta-delivery-bot
python3 -m venv .venv
source .venv/bin/activate

# 2. Установить зависимости
pip install -r requirements.txt

# 3. Заполнить .env
cp .env.example .env
# Открой .env в редакторе и заполни:
#   TELEGRAM_BOT_TOKEN_DELIVERY_ETA — токен от @BotFather
#   GOOGLE_API_KEY_DELIVERY_ETA     — Google Routes API key
#   CHAT_ID_DELIVERY_ETA            — оставить -1003698121587
#   SHOP_ADDRESS_DELIVERY_ETA       — оставить
# (опционально) MANAGER_USERNAMES_DELIVERY_ETA, MANAGER_IDS_DELIVERY_ETA

# 4. Запустить
python -m src.bot
```

Бот будет в режиме long-poll слушать Telegram. Останови `Ctrl+C`.

---

## Деплой на Railway (production)

### Шаг 1. Залить код на GitHub

```bash
cd /Users/tatiana/Documents/Floritta_Brain/floritta-delivery-bot
git init
git add .
git commit -m "Initial commit: Floritta Delivery ETA Bot"

# Создай новый репозиторий на github.com/new, имя: floritta-delivery-bot, Private
git remote add origin https://github.com/<твой-логин>/floritta-delivery-bot.git
git branch -M main
git push -u origin main
```

### Шаг 2. Создать проект на Railway

1. Зайди на https://railway.app → Sign in with GitHub
2. **New Project** → **Deploy from GitHub repo**
3. Выбери `floritta-delivery-bot`
4. Railway автоматически определит Python через `runtime.txt` и `requirements.txt`
5. Деплой стартанёт сразу, но упадёт — нужны env переменные

### Шаг 3. Добавить переменные окружения

В Railway проекте → твой сервис → таб **Variables** → **+ New Variable**. Добавь:

| Имя | Значение |
|---|---|
| `TELEGRAM_BOT_TOKEN_DELIVERY_ETA` | токен от @BotFather |
| `GOOGLE_API_KEY_DELIVERY_ETA` | ключ Google Routes API |
| `CHAT_ID_DELIVERY_ETA` | `-1003698121587` |
| `SHOP_ADDRESS_DELIVERY_ETA` | `Unit 3 The Willows, 80 Willow Walk, London SE1 5SY, UK` |
| `MANAGER_USERNAMES_DELIVERY_ETA` | (опционально) через запятую без `@` |
| `MANAGER_IDS_DELIVERY_ETA` | (опционально) через запятую |
| `PARKING_MIN_DELIVERY_ETA` | `7` (или другое число) |
| `DATABASE_PATH_DELIVERY_ETA` | `/data/bot.db` (см. шаг 4) |
| `LOG_LEVEL` | `INFO` |

После добавления переменных нажми **Deploy** — сервис рестартанёт и подцепит их.

### Шаг 4. (Рекомендуется) Подключить Volume для SQLite

Без Volume база `bot.db` ephemeral — при каждом редеплое сохранённые списки теряются. Для прода:

1. В Railway → твой сервис → таб **Settings** → секция **Volumes** → **+ Add Volume**
2. Mount path: `/data`
3. Сохрани и редеплой
4. Убедись что `DATABASE_PATH_DELIVERY_ETA=/data/bot.db` (как в шаге 3)

Без Volume бот тоже работает, просто список нужно отправлять каждый день заново (если сервис рестартовал ночью).

### Шаг 5. Проверка

В Railway → твой сервис → таб **Deployments** → последний деплой → **View Logs**.
Должно быть:

```
2026-... INFO    __main__ — Starting Floritta Delivery ETA Bot
2026-... INFO    __main__ — Chat ID: -1003698121587
2026-... INFO    __main__ — Bot running (polling for message + callback_query)
```

Теперь иди в Telegram-чат `Nazar x Kunjal x Floritta`, отправь список постов кодов — бот должен ответить за пару секунд.

---

## Настройка Telegram-бота (обязательно)

1. **Privacy mode off** — иначе бот не видит обычные сообщения в группах:
   - @BotFather → `/mybots` → выбери бота → **Bot Settings** → **Group Privacy** → **Turn off**
   - **ВАЖНО:** после изменения **удали бота из группы и добавь заново** — настройка применяется только при добавлении

2. **Бот — админ в группе:**
   - Открой группу → шапка → Administrators → Add Admin → твой бот
   - Минимум прав: «Удалять сообщения» (для force-admin)

---

## Google Routes API

1. Google Cloud Console → APIs & Services → Library
2. Найди и включи **Routes API** (не путать с Directions API, который старый)
3. APIs & Services → Credentials → **Create Credentials** → **API key**
4. (Рекомендуется) **Restrict key** → API restrictions → **Routes API** only
5. Billing → должен быть привязан рабочий счёт
6. Лимит: ~$5 за 1000 элементов matrix. Для 5 стопов = ~36 элементов = ~$0.18 за запрос. На объёмах малого бизнеса это копейки.

---

## Debug / Troubleshooting

| Симптом | Что проверить |
|---|---|
| Бот не отвечает на список | Privacy mode off? Бот — админ? CHAT_ID совпадает? |
| Бот пишет «GOOGLE_API_KEY... не задан» | `GOOGLE_API_KEY_DELIVERY_ETA` в Variables Railway или в .env |
| Бот пишет «HTTP 400 ... INVALID_ARGUMENT» | Routes API не включён в Google Cloud, или ключ ограничен другим API |
| Бот пишет «HTTP 403» | Billing не подключён в Google Cloud |
| Бот пишет «HTTP 429» | Квота Google Routes — обычно $200/мес free credit, после — pay-as-you-go |
| Кнопка Старт не реагирует | Telegram Trigger подписан на callback_query? У бота privacy off? |
| Список теряется после рестарта | Не подключён Volume на Railway, см. шаг 4 деплоя |
| Whitelist не работает | username в env с маленькой буквы, без `@`, через запятую |

Логи на Railway: **Deployments → последний → View Logs**. Все события бота туда же.

---

## Локальная разработка с тестом

Можно использовать [polling-режим с временным ботом]. Создай в @BotFather второго бота `@floritta_dev_bot`, добавь в тестовую группу, и запусти локально с `TELEGRAM_BOT_TOKEN_DELIVERY_ETA=...` от dev-бота. Так не сломаешь прод.

---

## Convensions (Floritta-wide)

- Все переменные имеют суффикс `_DELIVERY_ETA` — чтобы при добавлении других ботов не путались. См. `/Users/tatiana/Documents/Floritta_Brain/CLAUDE.md`.
- Никогда не хардкодим секреты в коде. Только `.env` / Railway Variables.
- Workflow JSON старой n8n-версии остаётся в `/Users/tatiana/Documents/Floritta_Brain/delivery_eta_workflow.json` как backup на случай если решим вернуться.

---

## Что ещё можно добавить (idea backlog)

- AnswerCallbackQuery toast — показывать «Считаю маршрут...» сразу после нажатия кнопки
- Кэш маршрутов 60 секунд — не дёргать API при повторных нажатиях
- Лог в Google Sheets — каждый построенный маршрут с timestamp для аналитики
- Сохранять историю отправленных списков для аудита
- Команда `/status` — показать текущий сохранённый список и время сохранения
- Команда `/clear` — менеджер может очистить сохранённый список вручную

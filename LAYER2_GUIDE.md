# Layer 2: Контакты с Crawl4AI и Yandex LLM

## Описание

Layer 2 — это слой обогащения данных кандидатов с контактной информацией (email, телефон) путем:

1. **Проверки наличия** `/sveden/employees` эндпоинта на сайте университета
2. **Краулинга** страницы сотрудников с использованием Crawl4AI
3. **Парсинга** HTML-контента с помощью Yandex Cloud LLM (или regex как fallback)

## Установка

### Зависимости

```bash
pip install -e .
```

Основные зависимости:
- `crawl4ai>=0.3.0` — асинхронный краулер с поддержкой JavaScript
- `httpx>=0.27` — HTTP клиент
- `python-dotenv` — загрузка переменных окружения

### Конфигурация Yandex API (опционально)

Если хотите использовать Yandex GPT для парсинга (более высокая точность), заполните `.env`:

```bash
# .env
YANDEX_FOLDER_ID=your-folder-id
YANDEX_API_KEY=your-api-key
```

Если переменные не установлены, будет использоваться regex-парсер (менее точный, но работает без API).

## Использование

### 1. Запуск Layer 2 как отдельного шага

```bash
python -m app step layer2
```

### 2. Запуск Layer 2 как часть полного пайплайна

Обновите `config.yaml`:

```yaml
run:
  layer1: true
  vak: true
  match: true
  layer2: true

limits:
  layer2_workers: 2
  layer2_request_delay_sec: 2.0
```

Затем запустите:

```bash
python -m app run
```

### 3. Параметры конфигурации

```yaml
limits:
  layer2_workers: 2                    # Количество параллельных workers
  layer2_request_delay_sec: 2.0        # Задержка между запросами (сек)
```

## Как это работает

### Алгоритм

```
Для каждого кандидата:
  1. Проверить наличие /sveden/employees на домене университета
  2. Если существует:
     a. Подготовить URL поиска: /sveden/employees?search={surname}
     b. Краулить страницу с Crawl4AI (если доступен)
     c. Если Crawl4AI не доступен, использовать обычный HTTP GET
     d. Парсить HTML:
        - Если Yandex API настроен: использовать LLM (более точно)
        - Иначе: использовать regex patterns для email и phone
     e. Сохранить результаты в БД
  3. Если не существует:
     - Отметить как page_not_found
```

### Входные данные

Извлекаются из таблицы `candidates`:
- `candidate_id` — уникальный идентификатор
- `full_name` — полное имя кандидата
- `university_id` → связь с `universities.domain`

Обработке подлежат кандидаты с `email IS NULL`.

### Выходные данные

Заполняются в таблице `candidates`:
- `email` — адрес электронной почты (если найден)
- `phone` — номер телефона (если найден)
- `contact_type` — тип контакта (`personal` или `university`)
- `contact_source_url` — URL страницы, откуда получены контакты

## Структура контракта

### Layer2Contract (внутренний)

```python
@dataclass
class Layer2Contract:
    candidate_id: str              # c_00123
    crawl_status: str              # 'page_found', 'page_not_found', 'error'
    contact_type: str | None       # 'personal', 'university'
    email: str | None              # a.p.ivanov@urfu.ru
    phone: str | None              # +7-xxx-xxx-xx-xx
    source_url: str | None         # https://urfu.ru/.../search?...
    confidence: str | None         # 'high', 'medium', 'low'
    error_message: str | None      # Если произошла ошибка
```

## Парсер

### YandexLLMParser

Извлекает контакты двумя способами:

#### 1. Yandex Cloud GPT (если настроен API)

```
Prompt: "Найди email и телефон для {full_name} в HTML"
↓
Yandex GPT анализирует → JSON ответ
{
    "email": "xxx@example.com",
    "phone": "+7...",
    "contact_type": "personal",
    "confidence": "high"
}
```

**Преимущества:**
- Высокая точность понимания контекста
- Может найти контакты даже в неструктурированном тексте
- Распознает контакты разных людей

**Недостатки:**
- Требует API ключ
- Медленнее (время ответа API)
- Платная услуга

#### 2. Regex fallback (если API недоступен)

Использует паттерны:
- Email: `[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Z|a-z]{2,}`
- Phone: `(?:\+7|8)?[\s\-]?\(?\d{3}\)?[\s\-]?\d{3}[\s\-]?\d{2}[\s\-]?\d{2}`

**Преимущества:**
- Работает без API
- Быстро
- Надежно для стандартных форматов

**Недостатки:**
- Может найти контакты других людей
- Не понимает контекст
- Меньше точность

## Примеры логов

```
INFO: Processing 42 candidates with layer2
INFO: Processed c_00123: page_found
INFO: Processed c_00124: page_not_found
ERROR: Error processing candidate: Connection timeout
INFO: Layer 2 processing completed
```

## Troubleshooting

### Yandex API не работает

```
WARNING: Yandex API error: 401
```

**Решение:** Проверьте `YANDEX_FOLDER_ID` и `YANDEX_API_KEY` в `.env`

### Crawl4AI не установлен

```
WARNING: Crawl4AI error for https://...: No module named 'crawl4ai'
```

**Решение:** Установите: `pip install crawl4ai>=0.3.0`

### Много ошибок при краулинге

```
WARNING: Failed to fetch https://...: Connection timeout
```

**Решение:** Увеличьте `layer2_request_delay_sec` в config.yaml

## Производительность

### Рекомендуемые параметры

**Малые наборы данных (< 100 кандидатов):**
```yaml
limits:
  layer2_workers: 2
  layer2_request_delay_sec: 1.5
```

**Большие наборы данных (> 1000 кандидатов):**
```yaml
limits:
  layer2_workers: 4
  layer2_request_delay_sec: 2.5
```

### Темп обработки

- ~3-5 кандидатов в минуту (с regex парсингом)
- ~1-2 кандидата в минуту (с Yandex API парсингом)

## API использования

### Запуск напрямую из кода

```python
from app.sources.universities.layer2 import run_layer2
from pathlib import Path

run_layer2(
    db_path=Path("data/state.sqlite"),
    run_id=1,
    request_delay_sec=2.0,
    workers=2,
)
```

### Парсинг отдельного HTML

```python
from app.sources.universities.layer2 import YandexLLMParser

parser = YandexLLMParser()
result = parser.parse(
    html="<html>...",
    full_name="Иванов Алексей Петрович",
    candidate_id="c_00123"
)
# result = {"email": "...", "phone": "...", "confidence": "..."}
```

## Ограничения

1. **Чувствительность к структуре HTML** — если сайт университета не содержит контакты или они в необычной структуре
2. **API лимиты Yandex** — ограничения на количество запросов
3. **JavaScript контент** — контакты, генерируемые JS, требуют Crawl4AI
4. **Защита от скрейпинга** — некоторые университеты блокируют краулинг

## Лучшие практики

1. **Запускайте Layer2 после Layer1** — нужны данные кандидатов
2. **Использование Yandex API** — когда точность критична
3. **Мониторинг логов** — отслеживайте ошибки и adjust параметры
4. **Кэширование** — результаты хранятся в БД
5. **Rate limiting** — соблюдайте ограничения университетских серверов

## Примечания

- Layer2 запускается только для кандидатов с `email IS NULL`
- Обработано максимум 100 кандидатов за раз (для управления памятью)
- Результаты автоматически сохраняются в БД
- Поддерживается retry при ошибках сети

# Layer 2 Implementation Summary

## Что было реализовано

### 1. **Модуль Layer 2** (`app/sources/universities/layer2.py`)
   - Проверка наличия `/sveden/employees` эндпоинта
   - Асинхронный краулинг страниц с использованием **Crawl4AI**
   - Парсинг контентa с помощью **Yandex Cloud LLM** (с regex fallback)
   - Извлечение email и телефонов кандидатов
   - Сохранение результатов в базу данных

### 2. **Интеграция в пайплайн**
   - Обновлена `app/pipeline/ingest.py` — добавлена поддержка Layer 2
   - Layer 2 запускается после Layer1/VAK (когда доступны данные кандидатов)
   - Поддержка параллельной обработки с configurable workers

### 3. **CLI поддержка**
   - Добавлена команда: `python -m app step layer2`
   - Поддержка в полном пайплайне: `python -m app run`
   - Обновлены choices для argparse

### 4. **Конфигурация**
   - Обновлена `app/config.py`:
     - Удалена валидация ошибки для layer2
     - Добавлены параметры: `layer2_workers`, `layer2_request_delay_sec`
   - Обновлен `.env.example`:
     - `YANDEX_FOLDER_ID` — для Yandex Cloud API
     - `YANDEX_API_KEY` — для аутентификации

### 5. **Зависимости**
   - Добавлена `crawl4ai>=0.3.0` — асинхронный браузер-краулер
   - Использование `httpx` для HTTP запросов к Yandex API

### 6. **Документация**
   - `LAYER2_GUIDE.md` — полное руководство использования
   - `config.layer2.yaml` — пример конфигурации

## Архитектура

```
Кандидат
    ↓
1. Проверка /sveden/employees
    ↓
2. Поиск по фамилии (GET /sveden/employees?search=surname)
    ↓
3. Краулинг (Crawl4AI или HTTP GET)
    ↓
4. Парсинг:
    - Если Yandex API настроен → LLM парсинг
    - Иначе → Regex parsingwit
    ↓
5. Сохранение (email, phone, contact_type, source_url)
```

## Входные/Выходные контракты

### Входное:
```python
{
    "candidate_id": "c_00123",
    "full_name": "Иванов Алексей Петрович",
    "university_domain": "urfu.ru"
}
```

### Выходное:
```python
{
    "candidate_id": "c_00123",
    "crawl_status": "page_found",
    "email": "a.p.ivanov@urfu.ru",
    "phone": "+7-xxx-xxx-xx-xx",
    "contact_type": "personal",
    "source_url": "https://urfu.ru/sveden/employees?search=...",
    "confidence": "high"
}
```

## Использование

### 1. Включить Layer2 в config.yaml
```yaml
run:
  layer2: true

limits:
  layer2_workers: 2
  layer2_request_delay_sec: 2.0
```

### 2. Запустить Layer2
```bash
# Отдельный шаг
python -m app step layer2

# Часть полного пайплайна
python -m app run

# С кастомной конфигурацией
python -m app --config config.layer2.yaml run
```

### 3. Конфигурировать Yandex API (опционально)
```bash
# .env
YANDEX_FOLDER_ID=your-folder-id
YANDEX_API_KEY=your-api-key
```

## Особенности реализации

### ✓ Преимущества
- **Асинхронный краулинг** — Crawl4AI поддерживает JavaScript
- **Два режима парсинга** — LLM для точности, regex как fallback
- **Graceful degradation** — если API недоступен, использует regex
- **Стандартный контракт** — интеграция с existing pipeline
- **Параллельная обработка** — configurable workers
- **Логирование** — полное отслеживание процесса

### ⚠️ Ограничения
- Требует `/sveden/employees` на сайте университета
- Эффективность зависит от структуры HTML сайта
- Может быть заблокирован при агрессивном краулинге
- Yandex API требует настройки и квоты

## Примеры команд

```bash
# Установка зависимостей
pip install -e .

# Запуск Layer2 отдельно
python -m app step layer2

# Запуск всего пайплайна с Layer2
python -m app --config config.layer2.yaml run --full

# Просмотр статуса
python -m app status

# Экспорт результатов
python -m app export --out output/candidates.xlsx
```

## Примеры конфигураций

### Быстрый режим (regex only)
```yaml
run:
  layer2: true
limits:
  layer2_workers: 4
  layer2_request_delay_sec: 0.5
# Без YANDEX_* переменных в .env
```

### Точный режим (Yandex API)
```yaml
run:
  layer2: true
limits:
  layer2_workers: 2
  layer2_request_delay_sec: 3.0
```

## Отладка

```bash
# Просмотр логов
tail -f app.log

# Проверка импортов
python -c "from app.sources.universities.layer2 import run_layer2; print('OK')"

# Проверка конфигурации
python -c "from app.config import load_config; c = load_config(); print(f'Layer2: {c.run.layer2}')"
```

## Дальнейшее развитие

1. **Кэширование результатов** — избежать повторного краулинга
2. **Расширенный парсинг** — VK профили, социальные сети
3. **ML-модель** — обучить свою модель для лучшей точности
4. **Batch processing** — обработка больших объемов
5. **Мониторинг качества** — метрики успеха и ошибок

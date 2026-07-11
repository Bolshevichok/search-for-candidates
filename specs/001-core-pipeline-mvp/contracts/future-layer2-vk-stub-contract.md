# Contract (для будущей реализации): layer2 и VK — заготовки этой фичи

Эта фича создаёт только пустые модули с докстрингами ниже (FR-015) — сама логика не реализуется.
Документ существует, чтобы будущий разработчик layer2/VK получил готовый контракт входа/выхода без
пересмотра архитектуры, и чтобы `matching`/`export` этой фичи уже сейчас были совместимы со схемой,
которую эти модули будут заполнять.

## Layer2 (краулер + ИИ-парсер контактов) — заготовка `app/sources/universities/layer2.py`

**Вход** (кого искать) — только карточки с вузом (`site_and_vak` / `site_and_vak_probable` /
`site_no_vak`; НЕ `vak_no_site` — нет домена работодателя):

```json
{
  "candidate_id": "c_00123",
  "full_name": "Иванов Алексей Петрович",
  "university_domain": "urfu.ru",
  "department": "Институт радиоэлектроники и информационных технологий"
}
```

**Выход** (сливается обратно по `candidate_id` в `candidates.email/phone/contact_type/
contact_source_url`):

```json
{
  "candidate_id": "c_00123",
  "crawl_status": "page_found",
  "contact_type": "personal",
  "email": "a.p.ivanov@urfu.ru",
  "phone": null,
  "source_url": "https://urfu.ru/.../staff/persons/ivanov-ap",
  "confidence": "high"
}
```

Полная механика (discovery по `/sveden/struct`, приоритизация краулера, кэш правил по вузу, уровни
уверенности контакта) — `candidate-pipeline-architecture.md`, §6.

## VK — заготовка `app/vk/__init__.py`

**Вход** (MVP, только внутри паблика вуза):

```json
{ "last_name": "Иванов", "first_name": "Алексей", "university_vk_group_id": "12345678", "sex": null }
```

**Выход** (сливается в `candidates.vk_url/vk_score/vk_status`):

```json
{ "candidate_id": "c_00123", "vk_url": "https://vk.com/id1", "vk_score": 0.82, "vk_status": "candidates_found" }
```

Канонические значения `vk_status`: `candidates_found` / `not_found` / `skipped_no_group` /
`error`. Полная спека — `../../vk/vk-matching-spec.md`.

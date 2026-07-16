from __future__ import annotations

import argparse
import asyncio
import logging
from urllib.parse import urlparse

from app.sources.http_client import HttpClient
from app.sources.universities import layer2

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")


def _normalize_domain(site: str) -> str:
    """Принимает и "urfu.ru", и "https://urfu.ru/sveden/employees" -- всегда
    возвращает голый домен, как ждёт university_domain по всему layer2."""
    site = site.strip()
    if "://" not in site:
        site = f"https://{site}"
    return urlparse(site).netloc or site


def _print_diagnostics() -> None:
    print("=== Диагностика окружения ===")
    print(f"crawl4ai установлен:  {layer2.CRAWL4AI_AVAILABLE}")
    print(f"openai установлен:    {layer2.OPENAI_AVAILABLE}")
    yandex = layer2.YandexLLMParser()
    print(f"Yandex LLM доступен:  {yandex.available}  (folder_id={'да' if yandex.folder_id else 'нет'}, "
          f"api_key={'да' if yandex.api_key else 'нет'}, model={yandex.model!r})")
    if not layer2.CRAWL4AI_AVAILABLE:
        print("  -> Crawl4AI не установлен, краулинг пойдёт через обычный HTTP GET (без JS-рендеринга).")
    if not yandex.available:
        print("  -> YandexGPT недоступен, парсинг HTML пойдёт через regex-эвристику вокруг найденного имени.")
    print()


async def run(full_name: str, domain: str) -> None:
    _print_diagnostics()

    base_url = f"https://{domain}".rstrip("/")
    candidate_id = "manual_test"

    with HttpClient(request_delay_sec=0.0) as client:
        from app.matching.normalize import split_fio_parts
        target_last, target_first, target_patr = split_fio_parts(full_name)
        pattern = layer2._build_loose_name_pattern(target_last, target_first, target_patr)
        if not pattern:
            print("Не удалось разобрать ФИО на фамилию/имя -- нечего искать.")
            return

        print(f"=== Этапы 1-2: обход {base_url} (до {layer2._MAX_FALLBACK_PAGES} страниц) в поиске '{full_name}' ===")
        crawler = layer2.Crawl4AICrawler()
        state = layer2._new_site_crawl_state(base_url)
        match = await layer2._advance_and_find(state, crawler, client, pattern)
        print(f"Страниц обойдено: {len(state.visited)}")
        if not match:
            print(f"'{full_name}' не найдено ни на одной обойдённой странице.")
            return
        matched_url, html = match
        print(f"Найдено на: {matched_url}")
        print(f"Длина HTML: {len(html)} символов")
        print(html[:500].replace("\n", " ") + ("..." if len(html) > 500 else ""))
        print()

        print("=== Этап 3: парсинг контактов (YandexGPT / regex) ===")
        parser = layer2.YandexLLMParser()
        parse_result = await parser.parse(html, full_name, candidate_id)
        for key, value in parse_result.items():
            print(f"  {key}: {value}")
        print()

        print("=== Этап 4: итоговый Layer2Contract (полный fallback-путь) ===")
        contract = await layer2.crawl_and_parse_contact(
            candidate_id=candidate_id,
            full_name=full_name,
            university_domain=domain,
            client=client,
            employees_directory=None,
        )
        for key, value in contract.to_dict().items():
            print(f"  {key}: {value}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--full-name", help="ФИО кандидата, например: Иванов Иван Иванович")
    parser.add_argument("--site", help="Домен или URL сайта университета, например: urfu.ru")
    args = parser.parse_args()

    full_name = args.full_name or input("ФИО кандидата: ").strip()
    site = args.site or input("Сайт университета (домен или URL): ").strip()
    domain = _normalize_domain(site)

    asyncio.run(run(full_name, domain))


if __name__ == "__main__":
    main()

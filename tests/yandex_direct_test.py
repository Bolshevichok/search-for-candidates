"""Direct Yandex LLM test script."""

from app.sources.universities.layer2 import YandexLLMParser


def main() -> None:
    parser = YandexLLMParser()
    html = "<html><body><p>Contact: ivanov@example.com</p><p>Phone: +7 901 111 22 33</p></body></html>"
    result = parser.parse(html=html, full_name="Иванов Иван Иванович", candidate_id="test_yandex")
    print(result)


if __name__ == "__main__":
    main()

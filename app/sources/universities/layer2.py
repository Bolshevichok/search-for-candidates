"""Layer 2 contacts crawler + AI parser — future implementation (FR-015).

Input contract (per candidate with a university):
    {
        "candidate_id": "c_00123",
        "full_name": "Иванов Алексей Петрович",
        "university_domain": "urfu.ru",
        "department": "Институт радиоэлектроники и информационных технологий"
    }

Output contract (merged back into candidates by candidate_id):
    {
        "candidate_id": "c_00123",
        "crawl_status": "page_found",
        "contact_type": "personal",
        "email": "a.p.ivanov@urfu.ru",
        "phone": null,
        "source_url": "https://urfu.ru/.../staff/personov/ivanov-ap",
        "confidence": "high"
    }

Full mechanics: candidate-pipeline-architecture.md §6.
"""

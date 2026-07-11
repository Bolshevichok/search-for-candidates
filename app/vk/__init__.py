"""VK profile search — future implementation (FR-015).

Input contract (MVP — search within university VK group only):
    {
        "last_name": "Иванов",
        "first_name": "Алексей",
        "university_vk_group_id": "12345678",
        "sex": null
    }

Output contract (merged into candidates.vk_url / vk_score / vk_status):
    {
        "candidate_id": "c_00123",
        "vk_url": "https://vk.com/id1",
        "vk_score": 0.82,
        "vk_status": "candidates_found"
    }

Canonical vk_status values: candidates_found / not_found / skipped_no_group / error.
Full spec: research/vk/vk-matching-spec.md
Stub contract: specs/001-core-pipeline-mvp/contracts/future-layer2-vk-stub-contract.md
"""

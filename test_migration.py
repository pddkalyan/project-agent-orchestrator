import json
from scripts.movie_studio_core__created_by_chatgpt__model_gpt_5_6_sol__task_TASK_0005 import ProductionLedger, MovieBible

data = {
    "schema_version": 2,
    "project_id": "test",
    "spend_limit_usd_micros": 0,
    "production": {
        "aspect_ratio": "16:9",
        "target_episode_minutes": 20,
        "local_video_inference": False,
        "final_storage_provider": "Google Drive"
    },
    "movie_bible": {
        "revision": 1,
        "story_rules": {},
        "characters": {},
        "voices": {},
        "locations": {},
        "continuity_facts": {}
    },
    "shots": {
        "s1": {
            "shot_id": "s1",
            "asset_version": "",
            "has_dialogue_or_audio": False,
            "status": "PLANNED",
            "reviews": {},
            "canonical": False,
            "upscale_allowed": False,
            "generation_epoch": 0,
            "generation_owner_job_id": None
        }
    },
    "generation_jobs": {},
    "idempotency_index": {},
    "authorization_index": {},
    "provider_request_index": {}
}

ledger = ProductionLedger._from_dict(data)
print(ledger.episode_id)

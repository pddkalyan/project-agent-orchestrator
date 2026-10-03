# Cloud Video Provider Contract

Movie/story logic must not call a vendor directly.

Minimum provider operations:
- capabilities()
- estimate_or_quota()
- generate(scene_spec)
- status(job_id)
- cancel(job_id)
- download(job_id)
- cleanup(job_id)

SceneSpec should carry character references, environment, camera, action, dialogue, audio requirements, continuity constraints, duration, aspect ratio, and quality target.

Selection policy:
1. Respect spend limit.
2. Respect required capabilities.
3. Prefer available free quota.
4. Run the smallest validation first.
5. Persist quality/time/quota results.
6. Allow provider replacement without changing story logic.

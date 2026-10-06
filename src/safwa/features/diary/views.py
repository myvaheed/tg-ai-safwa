"""The `ai_*` view over the Diary."""

from __future__ import annotations

from tg_agent_shell.ai.sql import Searchable, SqlView

AI_DIARY = SqlView(
    "ai_diary",
    """SELECT d.id, d.entry_date, d.body, d.feeling_score,
       (SELECT group_concat('[' || c.meta || '](media:' || m.media_id || ')', ' ')
        FROM diary_media m JOIN chat_media c ON c.id = m.media_id
        WHERE m.entry_id = d.id) AS media,
       d.created_at, d.updated_at, relevance('diary', d.id) AS relevance
FROM diary_entries d""",
    doc="""- `ai_diary(id, entry_date, body, feeling_score, media, created_at, updated_at, relevance)`
  - `entry_date` is `YYYY-MM-DD`; `feeling_score` is 0-10 and NULL for a day that said nothing
  - `media` is the day's photos as `[words](media:N)`, NULL for none; `body` is NULL for a day of photos alone""",
    searchable=Searchable("diary", ("body",)),
)

VIEWS = (AI_DIARY,)

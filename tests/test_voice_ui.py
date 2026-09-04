"""A recording becomes owner words, or it says why it did not."""

from __future__ import annotations

from sqlalchemy import select
from ui_harness import (
    ScriptedTranscriber,
    capture_dialogue_turns,
    services_for,
    voice_message_for,
)

from tg_agent_shell.foundation.kinds import MessageKind
from tg_agent_shell.history import TelegramMessage
from tg_agent_shell.telegram.dialogue import ASR_MAX_DURATION_SECONDS, voice_message


async def test_voice_message_becomes_one_owner_dialogue_turn(sessions, monkeypatch) -> None:
    """TG-RELAY-004 — tests/brd/telegram_history.feature"""
    turns = capture_dialogue_turns(monkeypatch)
    transcriber = ScriptedTranscriber("Renew the passport this week.")
    services = services_for(sessions, transcriber=transcriber)
    message = voice_message_for(940)

    await voice_message(message, services)

    assert message.bot.downloads == ["voice-940"]
    assert transcriber.clips[0].filename == "voice.ogg"
    assert transcriber.clips[0].duration_seconds == 12.0
    posted = message.sent_messages
    assert len(posted) == 1
    assert "Renew the passport this week." in posted[0].text
    async with sessions() as session:
        rows = list(await session.scalars(select(TelegramMessage)))
    dialogue_rows = [row for row in rows if row.kind == MessageKind.DIALOGUE_USER.value]
    assert [row.direction for row in dialogue_rows] == ["out"]
    assert turns == [("Renew the passport this week.", turns[0][1])]
    assert turns[0][1].role == "user"
    assert turns[0][1].message_id == posted[0].message_id


async def test_long_transcript_is_split_and_answered_once(sessions, monkeypatch) -> None:
    turns = capture_dialogue_turns(monkeypatch)
    transcript = " ".join(f"word{index}" for index in range(1_200))
    services = services_for(sessions, transcriber=ScriptedTranscriber(transcript))
    message = voice_message_for(941, duration=600)

    await voice_message(message, services)

    assert len(message.sent_messages) > 1
    assert "User Name Surname:" in message.sent_messages[0].text
    assert all("User Name Surname:" not in item.text for item in message.sent_messages[1:])
    async with sessions() as session:
        rows = list(await session.scalars(select(TelegramMessage)))
    dialogue_rows = [row for row in rows if row.kind == MessageKind.DIALOGUE_USER.value]
    assert len(dialogue_rows) == len(message.sent_messages)
    assert len(turns) == 1
    assert turns[0][0] == transcript


async def test_decode_progress_is_shown_then_removed(sessions, monkeypatch) -> None:
    capture_dialogue_turns(monkeypatch)
    transcriber = ScriptedTranscriber("Done at last.", progress_at=(30.0, 90.0))
    services = services_for(sessions, transcriber=transcriber)
    message = voice_message_for(946, duration=120)

    await voice_message(message, services)

    status = message.sent_messages[0]
    assert "Transcribing" in status.text
    assert "25%" in status.text
    assert [text for _id, text, _markup in message.bot.edits if "75%" in text]
    assert status.message_id in message.bot.deleted
    assert "Done at last." in message.sent_messages[-1].text
    async with sessions() as session:
        rows = list(await session.scalars(select(TelegramMessage)))
    # The percentage is transient: it leaves no row behind and never becomes dialogue.
    assert all(row.kind != MessageKind.STATUS.value for row in rows)


async def test_voice_message_without_a_transcriber_explains_itself(sessions) -> None:
    services = services_for(sessions, transcriber=None)
    message = voice_message_for(942)

    await voice_message(message, services)

    assert "SAFWA_ASR_PROVIDER" in message.answers[-1]
    assert message.bot.downloads == []


async def test_failed_transcription_reports_and_changes_nothing(sessions, monkeypatch) -> None:
    turns = capture_dialogue_turns(monkeypatch)
    services = services_for(
        sessions, transcriber=ScriptedTranscriber(error="upstream refused the file")
    )
    message = voice_message_for(943)

    await voice_message(message, services)

    assert "could not transcribe" in message.answers[-1]
    assert "upstream refused the file" in message.answers[-1]
    assert turns == []
    async with sessions() as session:
        rows = list(await session.scalars(select(TelegramMessage)))
    assert all(row.kind != MessageKind.DIALOGUE_USER.value for row in rows)


async def test_overlong_recording_is_refused_before_download(sessions, monkeypatch) -> None:
    turns = capture_dialogue_turns(monkeypatch)
    transcriber = ScriptedTranscriber("never reached")
    services = services_for(sessions, transcriber=transcriber)
    message = voice_message_for(944, duration=ASR_MAX_DURATION_SECONDS + 1)

    await voice_message(message, services)

    assert message.bot.downloads == []
    assert transcriber.clips == []
    assert turns == []
    assert "transcribes up to" in message.answers[-1]

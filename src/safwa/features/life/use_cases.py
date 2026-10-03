"""How the Life settings are written: the birth date and how many years the grid holds."""

from __future__ import annotations

from datetime import date

from sqlalchemy.ext.asyncio import AsyncSession

from tg_agent_shell.foundation.errors import DomainError

from .model import LIFE_YEARS_DEFAULT, LIFE_YEARS_MAX, LIFE_YEARS_MIN, LifeSettings


async def life_settings(session: AsyncSession) -> LifeSettings:
    """The Life settings as they stand, or as they are out of the box."""
    return await session.get(LifeSettings, 1) or LifeSettings(
        id=1, birth_date=None, years=LIFE_YEARS_DEFAULT
    )


async def _settings_row(session: AsyncSession) -> LifeSettings:
    row = await session.get(LifeSettings, 1)
    if row is None:
        row = LifeSettings(id=1, years=LIFE_YEARS_DEFAULT)
        session.add(row)
    return row


async def set_birth_date(session: AsyncSession, born: date, today: date) -> None:
    """`today` is the owner's local day; a birth date is a day before it."""
    if not (born < today and born.year > today.year - LIFE_YEARS_MAX):
        raise DomainError(
            f"A birth date is a day before today, at most {LIFE_YEARS_MAX} years ago."
        )
    (await _settings_row(session)).birth_date = born


async def set_life_years(session: AsyncSession, years: int) -> None:
    if not LIFE_YEARS_MIN <= years <= LIFE_YEARS_MAX:
        raise DomainError(f"The grid holds from {LIFE_YEARS_MIN} to {LIFE_YEARS_MAX} years.")
    (await _settings_row(session)).years = years

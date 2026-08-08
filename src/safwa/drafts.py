from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from .domain import (
    DomainError,
    StaleStateError,
    _bump_workspace,
    _record_event,
    _sync_commitment_for_stage,
    propagate_ancestors,
    utcnow,
    validate_action_fields,
    validate_parent,
)
from .enums import (
    EFFORT_POINTS,
    ActorType,
    CardKind,
    CardStage,
    Category,
    DraftStatus,
    EnergyType,
    Priority,
)
from .models import (
    Card,
    CardCategory,
    CardDependency,
    CardDraft,
    CardDraftBundle,
    CardEnergyType,
    CardTag,
    CardValue,
    DraftCategory,
    DraftDependency,
    DraftEnergyType,
    DraftTag,
    DraftValue,
    Tag,
    Value,
    new_correlation_id,
)


@dataclass(frozen=True)
class DraftValidation:
    valid: bool
    errors: tuple[str, ...]


class DraftService:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def create_bundle(self, origin: str, cards: list[dict]) -> CardDraftBundle:
        bundle = CardDraftBundle(
            origin=origin,
            expires_at=utcnow() + timedelta(days=7),
        )
        self.session.add(bundle)
        await self.session.flush()
        created: list[CardDraft] = []
        references: dict[str, CardDraft] = {}
        for index, payload in enumerate(cards):
            provenance = dict(payload.get("field_provenance", {}))
            draft_ref = str(payload.get("draft_ref", index))
            provenance["draft_ref"] = draft_ref
            draft = CardDraft(
                bundle_id=bundle.id,
                parent_id=payload.get("parent_id"),
                expected_parent_version=payload.get("expected_parent_version"),
                parent_draft_id=None,
                root_confirmed=bool(payload.get("root_confirmed", False)),
                kind=payload.get("kind", CardKind.ACTION.value),
                title=str(payload.get("title", "")).strip(),
                note=str(payload.get("note", "")).strip(),
                stage=self._normalize_stage(payload.get("stage")),
                priority=payload.get("priority", Priority.MEDIUM.value),
                hard_time=bool(payload.get("hard_time", False)),
                effort_points=payload.get("effort_points"),
                repeatable=bool(payload.get("repeatable", False)),
                field_provenance=provenance,
            )
            self.session.add(draft)
            await self.session.flush()
            await self._set_links(draft, payload)
            created.append(draft)
            references[draft_ref] = draft
        for draft, payload in zip(created, cards, strict=True):
            parent_ref = payload.get("parent_draft_ref")
            parent_index = payload.get("parent_draft_index")
            if parent_ref is not None and str(parent_ref) in references:
                draft.parent_draft_id = references[str(parent_ref)].id
                draft.root_confirmed = False
            elif isinstance(parent_index, int) and 0 <= parent_index < len(created):
                draft.parent_draft_id = created[parent_index].id
                draft.root_confirmed = False
            elif payload.get("parent_draft_id") in {item.id for item in created}:
                draft.parent_draft_id = payload["parent_draft_id"]
                draft.root_confirmed = False
        if created:
            bundle.active_draft_id = created[0].id
        for draft in created:
            await self.validate(draft)
        return bundle

    @staticmethod
    def _normalize_stage(value: object) -> str:
        """Accept only persisted stage enums; unknown model vocabulary means Backlog."""
        try:
            return CardStage(str(value).strip().casefold()).value
        except ValueError:
            return CardStage.BACKLOG.value

    async def _set_links(self, draft: CardDraft, payload: dict) -> None:
        for value_id in payload.get("value_ids", []):
            value = await self.session.get(Value, value_id)
            if value:
                self.session.add(
                    DraftValue(draft_id=draft.id, value_id=value.id, expected_version=value.version)
                )
        for tag_id in payload.get("tag_ids", []):
            tag = await self.session.get(Tag, tag_id)
            if tag:
                self.session.add(
                    DraftTag(draft_id=draft.id, tag_id=tag.id, expected_version=tag.version)
                )
        for category in payload.get("categories", []):
            self.session.add(
                DraftCategory(
                    draft_id=draft.id,
                    category=Category(str(category).strip().casefold()).value,
                )
            )
        for energy in payload.get("energy_types", []):
            self.session.add(
                DraftEnergyType(
                    draft_id=draft.id,
                    energy_type=EnergyType(str(energy).strip().casefold()).value,
                )
            )
        for dependency in payload.get("dependencies", []):
            card = await self.session.get(Card, dependency["card_id"])
            if card:
                self.session.add(
                    DraftDependency(
                        draft_id=draft.id,
                        blocker_card_id=card.id,
                        expected_version=card.version,
                        copy_to_repeat=bool(dependency.get("copy_to_repeat", False)),
                    )
                )

    async def get_bundle_drafts(self, bundle_id: int) -> list[CardDraft]:
        return list(
            await self.session.scalars(
                select(CardDraft)
                .where(CardDraft.bundle_id == bundle_id)
                .order_by(CardDraft.created_at, CardDraft.id)
            )
        )

    async def validate(self, draft: CardDraft) -> DraftValidation:
        errors: list[str] = []
        try:
            kind = CardKind(draft.kind)
        except ValueError:
            kind = CardKind.ACTION
            errors.append("Choose a valid card kind")
        if kind is not CardKind.ACTION:
            # AI may occasionally infer Action-only planning fields before it
            # settles on a Goal or Idea.  These fields are invalid by design,
            # so sanitize the persisted draft rather than trapping the owner
            # in an uncreatable review screen.
            draft.effort_points = None
            draft.repeatable = False
            await self.session.execute(
                delete(DraftCategory).where(DraftCategory.draft_id == draft.id)
            )
            await self.session.execute(
                delete(DraftEnergyType).where(DraftEnergyType.draft_id == draft.id)
            )
        normalized_stage = self._normalize_stage(draft.stage)
        if draft.stage != normalized_stage:
            draft.stage = normalized_stage
        if not draft.title.strip():
            errors.append("Add a title")
        if not draft.parent_id and not draft.parent_draft_id and not draft.root_confirmed:
            errors.append("Choose a parent or explicitly make the card root-level")
        try:
            await validate_parent(self.session, kind, draft.parent_id)
        except DomainError as error:
            errors.append(str(error))
        if draft.parent_draft_id:
            parent_draft = await self.session.get(CardDraft, draft.parent_draft_id)
            if parent_draft is None or parent_draft.bundle_id != draft.bundle_id:
                errors.append("Draft parent is missing")
            elif parent_draft.kind == CardKind.ACTION.value:
                errors.append("An Action cannot be a parent")
            elif kind is CardKind.GOAL:
                errors.append("A Goal must be root-level")
            elif kind is CardKind.IDEA and parent_draft.kind != CardKind.GOAL.value:
                errors.append("An Idea may only be placed under a Goal")
        categories = set(
            await self.session.scalars(
                select(DraftCategory.category).where(DraftCategory.draft_id == draft.id)
            )
        )
        energies = set(
            await self.session.scalars(
                select(DraftEnergyType.energy_type).where(DraftEnergyType.draft_id == draft.id)
            )
        )
        try:
            validate_action_fields(
                kind, draft.effort_points, draft.repeatable, categories, energies
            )
        except DomainError as error:
            errors.append(str(error))
        try:
            CardStage(draft.stage)
        except ValueError:
            errors.append("Choose a valid stage")
        try:
            Priority(draft.priority)
        except ValueError:
            errors.append("Choose a valid priority")
        if draft.effort_points is not None and draft.effort_points not in EFFORT_POINTS:
            errors.append("Effort must be 1, 2, 3, 5, 8, or 13")
        unresolved = draft.field_provenance.get("unresolved", [])
        if unresolved:
            errors.append("Resolve AI references: " + ", ".join(str(item) for item in unresolved))
        draft.validation_errors = list(dict.fromkeys(errors))
        draft.status = DraftStatus.READY.value if not errors else DraftStatus.EDITING.value
        return DraftValidation(not errors, tuple(draft.validation_errors))

    async def update(self, draft_id: int, **fields) -> CardDraft:
        draft = await self.session.get(CardDraft, draft_id)
        if draft is None or draft.status in {
            DraftStatus.COMMITTED.value,
            DraftStatus.DISCARDED.value,
        }:
            raise DomainError("Draft is not editable")
        allowed = {
            "parent_id",
            "parent_draft_id",
            "root_confirmed",
            "kind",
            "title",
            "note",
            "stage",
            "priority",
            "hard_time",
            "effort_points",
            "repeatable",
        }
        for name, value in fields.items():
            if name not in allowed:
                raise DomainError(f"Unsupported draft field: {name}")
            setattr(draft, name, self._normalize_stage(value) if name == "stage" else value)
        if "kind" in fields and fields["kind"] != CardKind.ACTION.value:
            draft.effort_points = None
            draft.repeatable = False
            await self.session.execute(
                delete(DraftCategory).where(DraftCategory.draft_id == draft.id)
            )
            await self.session.execute(
                delete(DraftEnergyType).where(DraftEnergyType.draft_id == draft.id)
            )
        if "parent_id" in fields:
            parent = (
                await self.session.get(Card, fields["parent_id"]) if fields["parent_id"] else None
            )
            draft.expected_parent_version = parent.version if parent else None
        provenance = dict(draft.field_provenance or {})
        unresolved = list(provenance.get("unresolved", []))
        if "parent_id" in fields or fields.get("root_confirmed"):
            provenance.pop("parent_query", None)
            unresolved = [
                item for item in unresolved if not str(item).casefold().startswith("parent")
            ]
        provenance["unresolved"] = unresolved
        draft.field_provenance = provenance
        draft.reviewed_at = None
        bundle = await self.session.get(CardDraftBundle, draft.bundle_id)
        if bundle is not None:
            # Editing is activity: retain the draft for another configured default window.
            bundle.expires_at = utcnow() + timedelta(days=7)
        await self.validate(draft)
        return draft

    async def mark_reviewed(self, draft_id: int) -> CardDraft:
        draft = await self.session.get(CardDraft, draft_id)
        if draft is None:
            raise DomainError("Draft does not exist")
        validation = await self.validate(draft)
        if not validation.valid:
            raise DomainError("Draft is incomplete: " + "; ".join(validation.errors))
        draft.status = DraftStatus.REVIEWED.value
        draft.reviewed_at = utcnow()
        return draft

    async def discard(self, draft_id: int) -> None:
        draft = await self.session.get(CardDraft, draft_id)
        if draft is None:
            return
        draft.status = DraftStatus.DISCARDED.value
        draft.reviewed_at = None

    async def commit_bundle(
        self,
        bundle_id: int,
        *,
        actor: ActorType = ActorType.USER_UI,
    ) -> list[Card]:
        bundle = await self.session.get(CardDraftBundle, bundle_id)
        if bundle is None or bundle.status in {
            DraftStatus.COMMITTED.value,
            DraftStatus.DISCARDED.value,
            DraftStatus.EXPIRED.value,
        }:
            raise DomainError("Draft bundle is not committable")
        drafts = [
            draft
            for draft in await self.get_bundle_drafts(bundle_id)
            if draft.status != DraftStatus.DISCARDED.value
        ]
        if not drafts:
            raise DomainError("Draft bundle is empty")
        for draft in drafts:
            validation = await self.validate(draft)
            if not validation.valid or draft.reviewed_at is None:
                raise DomainError("Every card must be valid and reviewed before Create all")
            if draft.parent_id:
                parent = await self.session.get(Card, draft.parent_id)
                if parent is None or parent.version != draft.expected_parent_version:
                    raise StaleStateError("The selected parent changed; review the draft again")
            for value_link in await self.session.scalars(
                select(DraftValue).where(DraftValue.draft_id == draft.id)
            ):
                value = await self.session.get(Value, value_link.value_id)
                if value is None or value.version != value_link.expected_version:
                    raise StaleStateError("A selected Value changed; review the draft again")
            for tag_link in await self.session.scalars(
                select(DraftTag).where(DraftTag.draft_id == draft.id)
            ):
                tag = await self.session.get(Tag, tag_link.tag_id)
                if tag is None or tag.version != tag_link.expected_version:
                    raise StaleStateError("A selected Tag changed; review the draft again")
            for dependency in await self.session.scalars(
                select(DraftDependency).where(DraftDependency.draft_id == draft.id)
            ):
                blocker = await self.session.get(Card, dependency.blocker_card_id)
                if blocker is None or blocker.version != dependency.expected_version:
                    raise StaleStateError("A selected blocker changed; review the draft again")

        created: list[Card] = []
        by_draft: dict[int, Card] = {}
        pending = list(drafts)
        correlation_id = new_correlation_id()
        while pending:
            progressed = False
            for draft in list(pending):
                if draft.parent_draft_id and draft.parent_draft_id not in by_draft:
                    continue
                parent_id = (
                    by_draft[draft.parent_draft_id].id if draft.parent_draft_id else draft.parent_id
                )
                card = Card(
                    parent_id=parent_id,
                    kind=draft.kind,
                    title=draft.title.strip(),
                    note=draft.note.strip(),
                    manual_stage=draft.stage,
                    effective_stage=draft.stage,
                    priority=draft.priority,
                    hard_time=draft.hard_time,
                    effort_points=draft.effort_points,
                    repeatable=draft.repeatable,
                    repeat_series_id=None,
                )
                self.session.add(card)
                await self.session.flush()
                if draft.repeatable:
                    card.repeat_series_id = card.id
                for item in await self.session.scalars(
                    select(DraftValue).where(DraftValue.draft_id == draft.id)
                ):
                    self.session.add(CardValue(card_id=card.id, value_id=item.value_id))
                for item in await self.session.scalars(
                    select(DraftTag).where(DraftTag.draft_id == draft.id)
                ):
                    self.session.add(CardTag(card_id=card.id, tag_id=item.tag_id))
                for item in await self.session.scalars(
                    select(DraftCategory).where(DraftCategory.draft_id == draft.id)
                ):
                    self.session.add(CardCategory(card_id=card.id, category=item.category))
                for item in await self.session.scalars(
                    select(DraftEnergyType).where(DraftEnergyType.draft_id == draft.id)
                ):
                    self.session.add(CardEnergyType(card_id=card.id, energy_type=item.energy_type))
                for item in await self.session.scalars(
                    select(DraftDependency).where(DraftDependency.draft_id == draft.id)
                ):
                    self.session.add(
                        CardDependency(
                            blocked_card_id=card.id,
                            blocker_card_id=item.blocker_card_id,
                            copy_to_repeat=item.copy_to_repeat,
                        )
                    )
                await _record_event(self.session, card, "create", actor, None, correlation_id)
                await _sync_commitment_for_stage(self.session, card)
                draft.committed_card_id = card.id
                draft.status = DraftStatus.COMMITTED.value
                by_draft[draft.id] = card
                created.append(card)
                pending.remove(draft)
                progressed = True
            if not progressed:
                raise DomainError("Draft parent graph contains a cycle")
        changed_parents = {card.parent_id for card in created if card.parent_id}
        for parent_id in changed_parents:
            await propagate_ancestors(self.session, parent_id)
        bundle.status = DraftStatus.COMMITTED.value
        bundle.committed_at = utcnow()
        await _bump_workspace(self.session)
        return created

    async def set_categories(self, draft_id: int, categories: set[Category]) -> CardDraft:
        await self.session.execute(delete(DraftCategory).where(DraftCategory.draft_id == draft_id))
        for category in categories:
            self.session.add(DraftCategory(draft_id=draft_id, category=category.value))
        draft = await self.session.get(CardDraft, draft_id)
        if draft is None:
            raise DomainError("Draft does not exist")
        draft.reviewed_at = None
        await self.validate(draft)
        return draft

    async def set_energy_types(self, draft_id: int, values: set[EnergyType]) -> CardDraft:
        await self.session.execute(
            delete(DraftEnergyType).where(DraftEnergyType.draft_id == draft_id)
        )
        for value in values:
            self.session.add(DraftEnergyType(draft_id=draft_id, energy_type=value.value))
        draft = await self.session.get(CardDraft, draft_id)
        if draft is None:
            raise DomainError("Draft does not exist")
        draft.reviewed_at = None
        await self.validate(draft)
        return draft

    async def toggle_value(self, draft_id: int, value_id: int) -> CardDraft:
        draft = await self.session.get(CardDraft, draft_id)
        value = await self.session.get(Value, value_id)
        if draft is None or value is None or value.archived_at is not None:
            raise DomainError("Draft or Value does not exist")
        link = await self.session.get(DraftValue, {"draft_id": draft_id, "value_id": value_id})
        if link:
            await self.session.delete(link)
        else:
            self.session.add(
                DraftValue(draft_id=draft_id, value_id=value_id, expected_version=value.version)
            )
        provenance = dict(draft.field_provenance or {})
        provenance["unresolved"] = [
            item
            for item in provenance.get("unresolved", [])
            if not str(item).casefold().startswith("value")
        ]
        draft.field_provenance = provenance
        draft.reviewed_at = None
        await self.validate(draft)
        return draft

    async def toggle_tag(self, draft_id: int, tag_id: int) -> CardDraft:
        draft = await self.session.get(CardDraft, draft_id)
        tag = await self.session.get(Tag, tag_id)
        if draft is None or tag is None or tag.archived_at is not None:
            raise DomainError("Draft or Tag does not exist")
        link = await self.session.get(DraftTag, {"draft_id": draft_id, "tag_id": tag_id})
        if link:
            await self.session.delete(link)
        else:
            self.session.add(
                DraftTag(draft_id=draft_id, tag_id=tag_id, expected_version=tag.version)
            )
        provenance = dict(draft.field_provenance or {})
        provenance["unresolved"] = [
            item
            for item in provenance.get("unresolved", [])
            if not str(item).casefold().startswith("tag")
        ]
        draft.field_provenance = provenance
        draft.reviewed_at = None
        await self.validate(draft)
        return draft

    async def toggle_dependency(self, draft_id: int, blocker_id: int) -> CardDraft:
        draft = await self.session.get(CardDraft, draft_id)
        blocker = await self.session.get(Card, blocker_id)
        if draft is None or blocker is None or blocker.archived_at is not None:
            raise DomainError("Draft or blocker does not exist")
        link = await self.session.get(
            DraftDependency, {"draft_id": draft_id, "blocker_card_id": blocker_id}
        )
        if link:
            await self.session.delete(link)
        else:
            self.session.add(
                DraftDependency(
                    draft_id=draft_id,
                    blocker_card_id=blocker_id,
                    expected_version=blocker.version,
                )
            )
        draft.reviewed_at = None
        await self.validate(draft)
        return draft

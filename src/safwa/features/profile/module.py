"""Owner profile and Settings, including their startup reconciliation."""

from ...bootstrap.module_manifest import FeatureModule
from .use_cases import sync_diary_reminder

MODULE = FeatureModule(name="profile", recover=sync_diary_reminder)

"""Register the optional access tables when an application includes protection."""

from __future__ import annotations

from ..telegram.manifest import FeatureModule
from . import model as model

MODULE = FeatureModule(name="access")

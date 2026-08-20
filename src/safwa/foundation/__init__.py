"""Cross-feature primitives: everything here is used by more than one feature.

Nothing feature-specific belongs here.  A type earns its place by having at least two
unrelated consumers; until then it lives with its owner.
"""

from __future__ import annotations

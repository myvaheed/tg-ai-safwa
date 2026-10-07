"""The search index: items found by their words and their meaning. See docs/SEARCH.md."""

# An application that uses any part of the index has its tables, and `create_all` creates
# only what an import has declared.
from . import model  # noqa: F401

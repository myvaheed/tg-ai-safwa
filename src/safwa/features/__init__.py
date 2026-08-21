"""Safwa features. Each package declares its `MODULE` in its own `module.py`.

The package `__init__` stays empty on purpose: importing one leaf of a feature — its
views, its presenter — must never drag in the wiring manifest that names every other
feature.
"""

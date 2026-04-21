"""
core.managers
=============
Manager layer — department leads that receive a brief from the Orchestrator,
read department memory, dispatch to specialists, and return a synthesis.

Public entry points:
  Manager                 — the class bound to one department
  dispatch_manager        — run a Manager by name with a brief
  load_departments        — auto-discover active departments from the company folder
  build_flex_specialist   — the generic on-demand specialist available to every manager
"""

from core.managers.base import (
    Manager,
    ManagerResult,
    build_flex_specialist,
    dispatch_manager,
)
from core.managers.loader import (
    DepartmentConfig,
    SpecialistConfig,
    load_departments,
    load_specialists_for_department,
)

__all__ = [
    "Manager",
    "ManagerResult",
    "build_flex_specialist",
    "dispatch_manager",
    "DepartmentConfig",
    "SpecialistConfig",
    "load_departments",
    "load_specialists_for_department",
]

"""The tool-module contract and auto-discovery.

A tool is a package under app/tools/<id>/ whose __init__.py defines

    def create(ctx: AppContext) -> Tool

See the README ("Adding a tool") for a walkthrough.
"""
from __future__ import annotations

import importlib
import logging
import pkgutil
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

from flask import Blueprint

import app.tools as tools_pkg

log = logging.getLogger(__name__)


@dataclass
class Tool:
    id: str  # url-safe slug; API lives under /api/<id>/, assets under /tools/<id>/
    title: str
    description: str
    icon: str  # name of an icon in static/lib/icons.js
    blueprint: Blueprint | None = None  # API routes; url_prefix is set for you
    ui_dir: Path | None = None  # folder holding ui.js (and optionally ui.css)
    start: Callable[[], None] | None = None  # optional: launch background work
    order: int = 100  # tab order, lowest first


@dataclass
class ToolSlot:
    """What the registry knows about a tool, including ones that failed to load."""
    id: str
    tool: Tool | None = None
    error: str | None = None
    title: str = field(default="")


def discover(ctx, only: set[str] | None = None) -> list[ToolSlot]:
    slots: list[ToolSlot] = []
    for mod in pkgutil.iter_modules(tools_pkg.__path__):
        if mod.name.startswith("_") or not mod.ispkg:
            continue
        if only is not None and mod.name not in only:
            continue
        try:
            module = importlib.import_module(f"app.tools.{mod.name}")
            tool = module.create(ctx)
            if tool.id != mod.name:
                raise ValueError(f"tool id {tool.id!r} must equal its folder name {mod.name!r}")
            slots.append(ToolSlot(mod.name, tool=tool, title=tool.title))
        except Exception as e:  # a broken tool must not take the dashboard down
            log.exception("tool %s failed to load", mod.name)
            slots.append(ToolSlot(mod.name, error=f"{type(e).__name__}: {e}", title=mod.name))
    slots.sort(key=lambda s: (s.tool.order if s.tool else 1000, s.id))
    return slots

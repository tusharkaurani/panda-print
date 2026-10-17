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

# ids the core already uses for its own API paths and page routes
RESERVED_IDS = {"printers", "setup", "csrf", "tools", "links", "health"}


@dataclass
class ToolTab:
    """One tab of a tool. The shell shows a tab per entry and calls the tool's UI mount() once per tab
    (kit.tabId tells it which). A tool with no tabs gets a single implicit one."""
    id: str  # url-safe; the page address becomes #<tool id>/<tab id>
    title: str
    icon: str = ""  # name from static/lib/icons.js; defaults to the tool's icon


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
    needs_printer: bool = False  # True: the shell shows this tool only once a printer is added,
    #                              and gives its UI a printer selector (kit.printer)
    tabs: list[ToolTab] = field(default_factory=list)  # empty: one tab with the tool's own title


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
            if mod.name in RESERVED_IDS:
                raise ValueError(f"{mod.name!r} is reserved by the dashboard; pick another folder name")
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

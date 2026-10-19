"""Builtin tools module.

This module contains all the built-in tools available to the Jarvis system.
Each tool is implemented using the common Tool interface for consistency.
"""

# Import all tool classes
from .screenshot import ScreenshotTool
from .web_search import WebSearchTool
from .local_files import LocalFilesTool
from .fetch_web_page import FetchWebPageTool
from .weather import WeatherTool
from .stop import StopTool

# Import supporting functions that may still be used elsewhere

__all__ = [
    # Tool classes
    'ScreenshotTool',
    'WebSearchTool',
    'LocalFilesTool',
    'FetchWebPageTool',
    'WeatherTool',
    'StopTool',
]

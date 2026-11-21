"""Parameter parser subpackage for tool input type detection and decomposition."""

from __future__ import annotations

from otlp_parser.parsers.parameter.base import ParameterParser
from otlp_parser.parsers.parameter.registry import ParserRegistry
from otlp_parser.parsers.parameter.command_line import CommandLineParser
from otlp_parser.parsers.parameter.json_parser import JsonParser
from otlp_parser.parsers.parameter.code import CodeParser
from otlp_parser.parsers.parameter.path import PathParser
from otlp_parser.parsers.parameter.url import UrlParser

__all__ = [
    "ParameterParser",
    "ParserRegistry",
    "CommandLineParser",
    "JsonParser",
    "CodeParser",
    "PathParser",
    "UrlParser",
]

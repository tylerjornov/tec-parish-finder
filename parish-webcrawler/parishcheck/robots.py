"""A small robots.txt reader.

Python's built-in parser ignores the `*` and `$` wildcards that most sites use, so this one follows the
common (Google-style) rules: the longest matching rule wins, and Allow beats Disallow on a tie.
"""

from __future__ import annotations

import re
from typing import Optional

PRODUCT_TOKEN = "parishinfoverifier"


class RobotsRules:
    def __init__(self, text: str = "", agent: str = PRODUCT_TOKEN):
        self.rules: list[tuple[bool, re.Pattern, int]] = []   # (allow?, compiled pattern, pattern length)
        self.crawl_delay: Optional[float] = None
        self.sitemaps: list[str] = []
        self._parse(text or "", agent.lower())

    # -- parsing --------------------------------------------------------------------------
    def _parse(self, text: str, agent: str) -> None:
        groups: list[tuple[list[str], list[tuple[str, str]]]] = []   # ([agents], [(directive, value)])
        cur_agents: list[str] = []
        cur_rules: list[tuple[str, str]] = []
        last_was_agent = False
        for line in text.splitlines():
            line = line.split("#", 1)[0].strip()
            if not line or ":" not in line:
                continue
            key, _, value = line.partition(":")
            key, value = key.strip().lower(), value.strip()
            if key == "sitemap":
                if value:
                    self.sitemaps.append(value)
                continue
            if key == "user-agent":
                if not last_was_agent and (cur_agents or cur_rules):
                    groups.append((cur_agents, cur_rules))
                    cur_agents, cur_rules = [], []
                cur_agents.append(value.lower())
                last_was_agent = True
                continue
            last_was_agent = False
            if key in ("allow", "disallow", "crawl-delay"):
                cur_rules.append((key, value))
        if cur_agents or cur_rules:
            groups.append((cur_agents, cur_rules))

        chosen = None
        for agents, rules in groups:                       # a group that names us wins
            if any(a != "*" and a in agent for a in agents):
                chosen = rules
                break
        if chosen is None:
            for agents, rules in groups:
                if "*" in agents:
                    chosen = rules
                    break
        for key, value in chosen or []:
            if key == "crawl-delay":
                try:
                    self.crawl_delay = float(value)
                except ValueError:
                    pass
            elif value:                                     # an empty Disallow means "allow everything"
                self.rules.append((key == "allow", self._compile(value), len(value)))

    @staticmethod
    def _compile(pattern: str) -> re.Pattern:
        anchored_end = pattern.endswith("$")
        if anchored_end:
            pattern = pattern[:-1]
        rx = re.escape(pattern).replace(r"\*", ".*")
        return re.compile("^" + rx + ("$" if anchored_end else ""))

    # -- asking ---------------------------------------------------------------------------
    def allowed(self, path_and_query: str) -> bool:
        best: Optional[tuple[int, bool]] = None
        for allow, rx, length in self.rules:
            if rx.match(path_and_query):
                if best is None or length > best[0] or (length == best[0] and allow):
                    best = (length, allow)
        return True if best is None else best[1]

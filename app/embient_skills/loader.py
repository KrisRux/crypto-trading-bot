"""
Embient Trading Skills loader.

Parses the SKILL.md files from the agent-trading-skills repository
(https://github.com/SKE-Labs/agent-trading-skills) and makes them
available as structured knowledge for the trading engine.

Each skill file has:
- YAML frontmatter (name, description, metadata)
- Markdown body with trading rules, thresholds, and workflows
"""

import logging
import os
import re
from dataclasses import dataclass, field
from pathlib import Path

logger = logging.getLogger(__name__)

SKILLS_DIR = Path(__file__).parent / "data"


@dataclass
class Skill:
    name: str
    description: str
    category: str
    version: str = "1.0"
    author: str = ""
    body: str = ""  # Full markdown body
    key_rules: list[str] = field(default_factory=list)  # Extracted NEVER/ALWAYS rules
    tables: list[dict] = field(default_factory=list)  # Parsed decision tables

    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "description": self.description,
            "category": self.category,
            "version": self.version,
            "author": self.author,
            "body": self.body,
            "key_rules": self.key_rules,
        }


def _parse_frontmatter(content: str) -> tuple[dict, str]:
    """Split YAML frontmatter from markdown body."""
    match = re.match(r'^---\s*\n(.*?)\n---\s*\n(.*)', content, re.DOTALL)
    if not match:
        return {}, content

    yaml_str = match.group(1)
    body = match.group(2)

    # Simple YAML parser (avoids pyyaml dependency)
    meta: dict = {}
    current_key = ""
    for line in yaml_str.split("\n"):
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if ":" in line and not line.startswith(" "):
            key, _, val = line.partition(":")
            key = key.strip()
            val = val.strip().strip('"').strip("'")
            if val:
                meta[key] = val
            else:
                current_key = key
                meta[key] = {}
        elif current_key and ":" in line:
            key, _, val = line.partition(":")
            key = key.strip()
            val = val.strip().strip('"').strip("'")
            if isinstance(meta[current_key], dict):
                meta[current_key][key] = val

    return meta, body


def _extract_key_rules(body: str) -> list[str]:
    """Extract key rules (NEVER/ALWAYS statements) from the body."""
    rules = []
    in_rules_section = False
    for line in body.split("\n"):
        stripped = line.strip()
        if re.match(r'^#{1,3}\s+key\s+rules', stripped, re.IGNORECASE):
            in_rules_section = True
            continue
        if in_rules_section:
            if stripped.startswith("#"):
                break  # Next section
            # Capture bullet points with NEVER/ALWAYS
            if stripped.startswith("- ") or stripped.startswith("* "):
                rule_text = stripped[2:].strip()
                if rule_text:
                    rules.append(rule_text)
    return rules


def _extract_tables(body: str) -> list[dict]:
    """Extract markdown tables as list of {headers, rows}."""
    tables = []
    lines = body.split("\n")
    i = 0
    while i < len(lines):
        line = lines[i].strip()
        if "|" in line and i + 1 < len(lines) and re.match(r'^[\s|:-]+$', lines[i + 1].strip()):
            # This is a table header
            headers = [c.strip() for c in line.split("|") if c.strip()]
            rows = []
            i += 2  # Skip separator
            while i < len(lines) and "|" in lines[i]:
                cells = [c.strip() for c in lines[i].split("|") if c.strip()]
                if cells:
                    rows.append(cells)
                i += 1
            tables.append({"headers": headers, "rows": rows})
            continue
        i += 1
    return tables


def load_skill(skill_dir: Path, category: str) -> Skill | None:
    """Load a single skill from its directory."""
    skill_file = skill_dir / "SKILL.md"
    if not skill_file.exists():
        return None

    try:
        content = skill_file.read_text(encoding="utf-8")
    except Exception:
        logger.warning("Could not read %s", skill_file)
        return None

    meta, body = _parse_frontmatter(content)
    metadata = meta.get("metadata", {})

    skill = Skill(
        name=meta.get("name", skill_dir.name),
        description=meta.get("description", ""),
        category=category,
        version=metadata.get("version", "1.0") if isinstance(metadata, dict) else "1.0",
        author=metadata.get("author", "") if isinstance(metadata, dict) else "",
        body=body.strip(),
        key_rules=_extract_key_rules(body),
        tables=_extract_tables(body),
    )
    return skill


class SkillsLibrary:
    """
    Loads and provides access to all Embient trading skills.
    Skills are organized by category and can be queried by name or category.
    """

    def __init__(self, skills_dir: Path | str | None = None):
        self.skills_dir = Path(skills_dir) if skills_dir else SKILLS_DIR
        self.skills: dict[str, Skill] = {}
        self.categories: dict[str, list[str]] = {}
        self._load_all()

    def _load_all(self):
        if not self.skills_dir.exists():
            logger.warning("Skills directory not found: %s", self.skills_dir)
            return

        for category_dir in sorted(self.skills_dir.iterdir()):
            if not category_dir.is_dir():
                continue
            category = category_dir.name
            self.categories[category] = []

            for skill_dir in sorted(category_dir.iterdir()):
                if not skill_dir.is_dir():
                    continue
                skill = load_skill(skill_dir, category)
                if skill:
                    self.skills[skill.name] = skill
                    self.categories[category].append(skill.name)

        logger.info("Loaded %d Embient skills across %d categories",
                     len(self.skills), len(self.categories))

    def get(self, name: str) -> Skill | None:
        return self.skills.get(name)

    def get_by_category(self, category: str) -> list[Skill]:
        names = self.categories.get(category, [])
        return [self.skills[n] for n in names if n in self.skills]

    def get_all_rules(self, categories: list[str] | None = None) -> list[str]:
        """Get all key rules, optionally filtered by category."""
        rules = []
        for skill in self.skills.values():
            if categories and skill.category not in categories:
                continue
            rules.extend(skill.key_rules)
        return rules

    def get_relevant_skills(self, context: str) -> list[Skill]:
        """Find skills relevant to a context string (simple keyword match)."""
        context_lower = context.lower()
        relevant = []
        for skill in self.skills.values():
            desc_lower = skill.description.lower()
            name_lower = skill.name.lower()
            if any(word in desc_lower or word in name_lower
                   for word in context_lower.split()):
                relevant.append(skill)
        return relevant

    def build_knowledge_prompt(self, skill_names: list[str] | None = None,
                               categories: list[str] | None = None) -> str:
        """
        Build a knowledge context string from selected skills.
        Useful for feeding into an LLM or for rule-based signal filtering.
        """
        skills_to_use = []
        if skill_names:
            skills_to_use = [self.skills[n] for n in skill_names if n in self.skills]
        elif categories:
            for cat in categories:
                skills_to_use.extend(self.get_by_category(cat))
        else:
            skills_to_use = list(self.skills.values())

        parts = []
        for skill in skills_to_use:
            parts.append(f"## {skill.name} ({skill.category})")
            parts.append(skill.body)
            if skill.key_rules:
                parts.append("\nKey Rules:")
                for rule in skill.key_rules:
                    parts.append(f"  - {rule}")
            parts.append("")

        return "\n".join(parts)

    def summary(self) -> dict:
        return {
            "total_skills": len(self.skills),
            "categories": {cat: len(names) for cat, names in self.categories.items()},
        }

    def list_all(self) -> list[dict]:
        return [s.to_dict() for s in self.skills.values()]

    def extract_numeric_params(self, skill_name: str) -> dict:
        """
        Extract numeric thresholds and parameter values from a skill's body text
        and tables using regex patterns.

        Returns a dict with any of:
          rsi_oversold, rsi_overbought,
          macd_fast, macd_slow, macd_signal,
          bb_period, bb_std,
          sma_fast, sma_slow
        """
        skill = self.get(skill_name)
        if not skill:
            return {}

        params: dict = {}
        body = skill.body

        # RSI extreme zones from table cells: "| >70 | Overbought ... |" / "| <30 | Oversold ... |"
        for m in re.finditer(r'[|]\s*[<>]=?\s*(\d+)\s*[|]\s*([^|\n]+)', body):
            val = int(m.group(1))
            label = m.group(2).strip().lower()
            if "overbought" in label:
                params["rsi_overbought"] = val
            elif "oversold" in label:
                params["rsi_oversold"] = val

        # MACD default settings: "Default settings: 12, 26, 9"
        m = re.search(r'[Dd]efault\s+settings[:\s]+(\d+)[,\s]+(\d+)[,\s]+(\d+)', body)
        if m:
            params["macd_fast"] = int(m.group(1))
            params["macd_slow"] = int(m.group(2))
            params["macd_signal"] = int(m.group(3))

        # BB default settings: "Default settings: 20 SMA, 2 StdDev"
        m = re.search(
            r'[Dd]efault\s+settings[:\s]+(\d+)\s+SMA[,\s]+(\d+(?:\.\d+)?)\s+StdDev',
            body,
        )
        if m:
            params["bb_period"] = int(m.group(1))
            params["bb_std"] = float(m.group(2))

        # MA periods — pick "Day trading" row: "| 9 EMA | 21 EMA | Day trading |"
        m = re.search(
            r'[|]\s*(\d+)\s+(?:EMA|SMA)\s*[|]\s*(\d+)\s+(?:EMA|SMA)\s*[|]\s*[Dd]ay\s+trading',
            body,
        )
        if m:
            params["sma_fast"] = int(m.group(1))
            params["sma_slow"] = int(m.group(2))

        return params

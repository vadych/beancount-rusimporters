"""Движок категоризации + загрузка правил из rusimporters.toml.

Правила — внешняя приватная конфигурация рядом с журналом, в пакете только
механика. Формат [x.mappings] совместим с rustledger: совпадение по подстроке,
case-insensitive, более длинные паттерны первыми.

Порядок срабатывания (первое совпадение побеждает):
    rules банка → mappings банка → rules common → mappings common
"""

import re
import tomllib
from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path


@dataclass
class Rule:
    pattern: str
    account: str
    min_amount: Decimal | None = None
    max_amount: Decimal | None = None
    tags: frozenset = frozenset()
    direction: str | None = None     # 'expense' | 'income' | None (оба)
    substring: bool = False          # True для записей из [x.mappings]

    def __post_init__(self):
        if self.min_amount is not None:
            self.min_amount = Decimal(str(self.min_amount))
        if self.max_amount is not None:
            self.max_amount = Decimal(str(self.max_amount))
        if self.substring:
            needle = self.pattern.casefold()
            self._match = lambda desc: needle in desc.casefold()
        else:
            rx = re.compile(self.pattern, re.I)
            self._match = lambda desc: rx.search(desc) is not None

    def matches(self, desc, amount):
        if not self._match(desc):
            return False
        if self.min_amount is not None and amount < self.min_amount:
            return False
        if self.max_amount is not None and amount > self.max_amount:
            return False
        return True


def classify(desc, amount, rules, default_account):
    """(account, tags) первого подошедшего правила или (default_account, set())."""
    for rule in rules:
        if rule.matches(desc, amount):
            return rule.account, set(rule.tags)
    return default_account, set()


_CONFIG_CACHE: dict[str, dict] = {}
_RULES_CACHE: dict[tuple, list] = {}


def _load(config_path):
    key = str(Path(config_path).resolve())
    if key not in _CONFIG_CACHE:
        with open(config_path, "rb") as f:
            _CONFIG_CACHE[key] = tomllib.load(f)
    return _CONFIG_CACHE[key]


def _section_rules(cfg, section, direction):
    out = []
    sec = cfg.get(section, {})
    for d in sec.get("rules", []):
        # direction=None (ТКС/ВТБ): направление не фильтруем, правила для обоих.
        if direction is not None and d.get("direction") not in (None, direction):
            continue
        out.append(Rule(**d))
    mappings = sec.get("mappings", {})
    # Стабильная сортировка: длинные первыми, при равной длине — порядок в файле.
    for pattern in sorted(mappings, key=len, reverse=True):
        out.append(Rule(pattern=pattern, account=mappings[pattern], substring=True))
    return out


def load_rules(config_path, bank, direction):
    """Скомпилированные правила банка + common. direction: 'expense' | 'income' | None."""
    key = (str(Path(config_path).resolve()), bank, direction)
    if key not in _RULES_CACHE:
        cfg = _load(config_path)
        _RULES_CACHE[key] = (_section_rules(cfg, bank, direction)
                             + _section_rules(cfg, "common", direction))
    return _RULES_CACHE[key]


def load_settings(config_path, bank):
    """[defaults] + скалярные ключи секции банка (unknown_account и т.п.)."""
    cfg = _load(config_path)
    settings = dict(cfg.get("defaults", {}))
    for k, v in cfg.get(bank, {}).items():
        if k not in ("rules", "mappings"):
            settings[k] = v
    return settings

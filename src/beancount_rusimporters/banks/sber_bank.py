import re
import datetime
from decimal import Decimal
import beangulp
from ..utils import build_account_map
from ..categorize import load_rules, load_settings
from ..core import NormalizedRow, build_transaction
import pandas as pd

MONTHS = {
    "янв.": 1, "февр.": 2, "мар.": 3, "апр.": 4, "мая": 5, "июн.": 6,
    "июл.": 7, "авг.": 8, "сент.": 9, "окт.": 10, "нояб.": 11, "дек.": 12,
}


def parse_date(raw):
    """Парсит дату вида '01 июл. 2026, 10:02' в datetime.date."""
    m = re.match(r"(\d+) (\S+) (\d+), (\d+):(\d+)", raw.strip())
    day, mon_ru, year, hh, mm = m.groups()
    return datetime.date(int(year), MONTHS[mon_ru], int(day))


def last4(card_str):
    """Нормализует номер карты/счёта до последних 4 цифр.
    Бонусный счёт не обрезается."""
    if card_str == "BONUS_NUMBER_1":
        return card_str
    return card_str[-4:]


def normalize_row(row, is_expense, card_col):
    amt = row["Сумма в рублях"]
    if amt == 0:
        return None

    sign = -1 if is_expense else 1
    return NormalizedRow(
        date=parse_date(row["Дата"]),
        amount=Decimal(str(sign * amt)),
        currency="RUB",
        description=str(row["Описание"]).strip(),
        card_last4=last4(str(row[card_col])),
        raw_category=str(row["Категория"]),
    )


class SberImporter(beangulp.Importer):

    def __init__(self, config_path):
        self._config_path = config_path

    def identify(self, filepath):
        """Распознаёт файлы выгрузки Сбербанка по имени."""
        name = filepath.lower()
        return name.endswith(".xlsx") and ("outcome_" in name or "income_" in name)

    def account(self, filepath):
        return "Assets:Кэш:Вадим:Сбер:Платежный"

    def extract(self, filepath, existing=None):
        account_map = build_account_map(existing)
        is_expense = "outcome_" in filepath.lower()
        direction = "expense" if is_expense else "income"
        rules = load_rules(self._config_path, "sber", direction)
        settings = load_settings(self._config_path, "sber")
        default_todo = settings.get(
            "unknown_expense" if is_expense else "unknown_income")
        unknown_account = settings.get("unknown_account", "Assets:Unknown")

        df = pd.read_excel(filepath)
        card_col = "Номер счета/карты списания" if is_expense else "Номер счета/карты зачисления"

        entries = []
        for idx, raw_row in df.iterrows():
            row = normalize_row(raw_row, is_expense, card_col)
            if row is None:
                continue
            txn = build_transaction(row, account_map, rules, default_todo,
                                    filepath, idx, unknown_account=unknown_account)
            if txn:
                entries.append(txn)
        return entries

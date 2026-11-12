import datetime
import csv
from decimal import Decimal
import beangulp
from ..utils import build_account_map
from ..categorize import load_rules, load_settings
from ..core import NormalizedRow, build_transaction


def parse_date(raw):
    """Парсит дату вида '27.07.2026 01:51:40' в datetime.date."""
    return datetime.datetime.strptime(raw.strip(), "%d.%m.%Y %H:%M:%S").date()


def last4(card_str):
    """Нормализует номер карты до последних 4 цифр для сравнения
    с account-number из metadata."""
    return card_str.strip().lstrip("*")[-4:] if card_str else ""


def normalize_row(row):
    if row["Номер карты"] == "*4471":  # Пропускаем карту ребенка.
        return None

    if row["Статус"].strip().lower() not in ("ок", "ok"):
        return None

    amt = Decimal(row["Сумма операции"].replace(",", "."))
    if amt == 0:
        return None

    return NormalizedRow(
        date=parse_date(row["Дата операции"]),
        amount=amt,
        currency="RUB",
        description=row["Описание"].strip(),
        card_last4=last4(row["Номер карты"]),
        raw_category=row["Категория по-умолчанию"],
    )


class TKSImporter(beangulp.Importer):

    def __init__(self, config_path):
        self._config_path = config_path

    def identify(self, filepath):
        if not filepath.lower().endswith(".csv"):
            return False
        try:
            with open(filepath, encoding="utf-8-sig") as f:
                header = f.readline()
            return "Дата операции" in header and "Номер карты" in header
        except (UnicodeDecodeError, OSError):
            return False

    def account(self, filepath):
        return "archive:tks"

    def extract(self, filepath, existing=None):
        account_map = build_account_map(existing)
        rules = load_rules(self._config_path, "tks", None)
        settings = load_settings(self._config_path, "tks")

        entries = []
        with open(filepath, encoding="utf-8-sig") as f:
            reader = csv.DictReader(f, delimiter=";")
            for idx, raw_row in enumerate(reader):
                row = normalize_row(raw_row)
                if row is None:
                    continue
                txn = build_transaction(
                    row, account_map, rules,
                    settings.get("unknown_expense", "Expenses:Unknown"),
                    filepath, idx,
                    unknown_account=settings.get("unknown_account", "Assets:Unknown"))
                if txn:
                    entries.append(txn)
        return entries

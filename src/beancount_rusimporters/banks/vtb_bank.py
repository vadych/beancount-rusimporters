import csv
import datetime
from decimal import Decimal
import beangulp
from ..utils import build_account_map
from ..categorize import load_rules, load_settings
from ..core import NormalizedRow, build_transaction


def normalize_row(row):
    if row["Статус операции"] != "Выполнено":
        return None

    amt = Decimal(row["Сумма в валюте счёта"].replace(",", "."))
    if amt == 0:
        return None

    sign = -1 if row["Списание/Зачисление"] == "Списание" else 1
    card_field = row["Карта"] or row["Номер счёта/Кредитного договора"]

    return NormalizedRow(
        date=datetime.datetime.strptime(
            row["Дата и время операции"].strip(), "%d.%m.%Y %H:%M").date(),
        amount=amt * sign,
        currency=row["Валюта счёта"],
        description=row["Наименование операции"].strip(),
        card_last4=card_field.strip().lstrip("*")[-4:] if card_field else "",
        raw_category=row["Категория"],
    )


class VTBImporter(beangulp.Importer):

    def __init__(self, config_path):
        self._config_path = config_path

    def identify(self, filepath):
        if not filepath.lower().endswith(".csv"):
            return False
        try:
            with open(filepath, encoding="utf-8-sig") as f:
                header = f.readline()
            return "Списание/Зачисление" in header and "Категория" in header
        except (UnicodeDecodeError, OSError):
            return False

    def account(self, filepath):
        return "archive:vtbbank"

    def extract(self, filepath, existing=None):
        account_map = build_account_map(existing)
        rules = load_rules(self._config_path, "vtb", None)
        settings = load_settings(self._config_path, "vtb")

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
                    settings.get("unknown_account", "Assets:Unknown"))
                if txn:
                    entries.append(txn)
        return entries

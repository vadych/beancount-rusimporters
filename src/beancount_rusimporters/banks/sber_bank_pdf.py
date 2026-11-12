"""Сбер PDF: даты операций, ежедневные balance, Beancount v3 / beangulp.

Подключение:
    from beancount_rusimporters.sber_bank_pdf import SberPdfImporter
    CONFIG = [..., SberPdfImporter("rusimporters.toml")]

Балансы восстановлены только по операциям, присутствующим в PDF.
При расширении выписки заменяйте прежние балансы пересекающихся дней.
"""
import datetime as dt
import os
import re
from collections import Counter, defaultdict
from dataclasses import dataclass
from decimal import Decimal
from functools import lru_cache

import pdfplumber
import beangulp
from beancount.core import amount, data
from ..core import NormalizedRow, build_transaction
from ..utils import build_account_map
from ..categorize import load_rules, load_settings

DATE = r"\d{2}\.\d{2}\.\d{4}"
MONEY = r"[+−-]?\d[\d\s]*,\d{2}"
ZERO = Decimal("0.00")
DAY = dt.timedelta(days=1)


def parse_date(value):
    return dt.datetime.strptime(value, "%d.%m.%Y").date()


def parse_money(value):
    value = re.sub(r"\s", "", value).replace("−", "-")
    if not re.fullmatch(r"[+-]?\d+,\d{2}", value):
        raise ValueError(f"Некорректная сумма: {value!r}")
    return Decimal(value.replace(",", "."))


@dataclass(frozen=True)
class Operation:
    date: dt.date
    time: str
    processed_date: dt.date
    auth_code: str
    category: str
    description: str
    card: str | None
    value: Decimal
    balance: Decimal
    page: int


@dataclass(frozen=True)
class Statement:
    account_number: str
    start_date: dt.date
    end_date: dt.date
    opening: Decimal
    closing: Decimal
    credits: Decimal
    debits: Decimal
    operations: tuple


@dataclass(frozen=True)
class DailyBalance:
    day: dt.date
    credits: Decimal
    debits: Decimal
    delta: Decimal
    bank_balance: Decimal
    correction: Decimal
    balance: Decimal

    def comment(self):
        return (f"За {self.day}: поступления {self.credits:.2f} RUB; "
                f"списания {self.debits:.2f} RUB; дельта {self.delta:+.2f} RUB. "
                f"Остаток банка {self.bank_balance:.2f} RUB; "
                f"поправка по датам операций {self.correction:+.2f} RUB. "
                "По операциям, известным из этой выписки.")


def _lines(page):
    groups = []
    for word in sorted(page.extract_words(), key=lambda w: (w["top"], w["x0"])):
        if not groups or abs(word["top"] - groups[-1][0]["top"]) > 2:
            groups.append([])
        groups[-1].append(word)
    return [sorted(group, key=lambda w: w["x0"]) for group in groups]


def _text(words, left=0, right=float("inf")):
    return " ".join(w["text"] for w in words if left <= w["x0"] < right)


def _page_operations(page, page_number):
    lines = _lines(page)
    header = next((i for i, line in enumerate(lines)
                   if "ДАТА ОПЕРАЦИИ" in _text(line)
                   and "КАТЕГОРИЯ" in _text(line)), None)

    if header is None:
        text = "\n".join(_text(line) for line in lines)

        signature_page = (
            "Согласно статье 6 закона" in text
            and "Об электронной подписи" in text
            and "ДАТА ОПЕРАЦИИ" not in text
            and not re.search(rf"{DATE}\s+\d{{2}}:\d{{2}}", text)
        )
        if signature_page:
            return []

        raise ValueError(
            f"Страница {page_number}: не найдена таблица операций"
        )

    headings = {w["text"]: w["x0"] for w in lines[header]}
    category_x = headings["КАТЕГОРИЯ"] - 1
    amount_x = headings["СУММА"] - 1
    balance_x = headings["ОСТАТОК"] - 1
    starts = [i for i in range(header + 1, len(lines))
              if re.fullmatch(DATE, _text(lines[i], 0, 100))
              and re.fullmatch(r"\d{2}:\d{2}", _text(lines[i], 100, category_x))]
    result = []
    for pos, start in enumerate(starts):
        stop = starts[pos + 1] if pos + 1 < len(starts) else len(lines)
        block = []
        for line in lines[start:stop]:
            text = _text(line)
            if text.startswith(("Продолжение на следующей", "Дата формирования", "Для проверки")) or text == "*":
                break
            block.append(line)
        first = block[0]
        processing = [i for i, line in enumerate(block[1:], 1)
                      if re.fullmatch(DATE, _text(line, 0, 100))]
        if len(processing) != 1:
            raise ValueError(
                f"Страница {page_number}: неоднозначная дата обработки: {_text(first)}")
        split = processing[0]
        auth = _text(block[split], 100, category_x)
        if not re.fullmatch(r"\d+", auth):
            raise ValueError(
                f"Страница {page_number}: некорректный код {auth!r}")
        category = " ".join(_text(line, category_x, amount_x)
                            for line in block[:split]).strip()
        description = " ".join(_text(line, category_x, amount_x)
                               for line in block[split:]).strip()
        description = re.sub(r"\s+", " ", description)
        card = re.search(r"Операция по карте\s*\*{4}(\d{4})", description)
        description = re.sub(
            r"\.?\s*Операция по (?:карте|сч[её]ту)\s*\*{4}\d{4}\s*$", "", description).rstrip(". ")
        raw_value = _text(first, amount_x, balance_x)
        value = parse_money(raw_value)
        value = value if raw_value.startswith("+") else -abs(value)
        extra = " ".join(_text(line, amount_x, balance_x)
                         for line in block[1:]).strip()
        if extra:
            raise ValueError(
                f"Страница {page_number}: дополнительная сумма/валюта {extra!r}; нужна проверка")
        if not description or not category:
            raise ValueError(
                f"Страница {page_number}: пустое описание/категория")
        result.append(Operation(
            parse_date(_text(first, 0, 100)), _text(first, 100, category_x),
            parse_date(_text(block[split], 0, 100)), auth, category,
            description, card.group(1) if card else None, value,
            parse_money(_text(first, balance_x)), page_number,
        ))
    return result


def _parse_statement(filepath):
    with pdfplumber.open(filepath) as pdf:
        text = pdf.pages[0].extract_text() or ""
        if "Выписка по платёжному счёту" not in text or "Сбер" not in text:
            raise ValueError("Не выписка Сбербанка по платёжному счёту")
        if "Российский рубль" not in text:
            raise ValueError("Поддерживаются только рублёвые счета")
        number = re.search(r"Номер сч[её]та\s+([\d ]+)", text)
        period = re.search(rf"За период\s+({DATE})\s*[—–-]\s*({DATE})", text)
        balances = re.findall(rf"Остаток на\s+({DATE})\s+({MONEY})", text)
        credits = re.search(rf"Пополнение\s+({MONEY})", text)
        debits = re.search(rf"Списание\s+({MONEY})", text)
        if not number or not period or len(balances) != 2 or not credits or not debits:
            raise ValueError("Не удалось полностью прочитать шапку выписки")
        number = re.sub(r"\s", "", number.group(1))
        if len(number) != 20:
            raise ValueError(f"Некорректный номер счёта: {number!r}")
        start_date, end_date = map(parse_date, period.groups())
        if start_date > end_date or [parse_date(x[0]) for x in balances] != [start_date, end_date]:
            raise ValueError("Некорректные даты периода/остатков")
        operations = tuple(op for i, page in enumerate(pdf.pages, 1)
                           for op in _page_operations(page, i))
    report = Statement(number, start_date, end_date,
                       parse_money(balances[0][1]), parse_money(
                           balances[1][1]),
                       parse_money(credits.group(1)), parse_money(debits.group(1)), operations)
    actual_credits = sum((op.value for op in operations if op.value > 0), ZERO)
    actual_debits = -sum((op.value for op in operations if op.value < 0), ZERO)
    if (actual_credits, actual_debits) != (report.credits, report.debits):
        raise ValueError(f"Итоги PDF не совпали: поступления {actual_credits}/{report.credits}; "
                         f"списания {actual_debits}/{report.debits}")
    if report.opening + actual_credits - actual_debits != report.closing:
        raise ValueError("Не сошлись начальный и конечный остатки")
    for op in operations:
        if not start_date <= op.processed_date <= end_date:
            raise ValueError(
                f"Дата обработки {op.processed_date} вне периода выписки")
    # Строки PDF идут от последнего движения к первому; не сортировать по времени покупки.
    running = report.opening
    for op in reversed(operations):
        running += op.value
        if running != op.balance:
            raise ValueError(f"Страница {op.page}, код {op.auth_code}: нарушена цепочка остатков "
                             f"({running} != {op.balance})")
    return report


@lru_cache(maxsize=4)
def _cached_statement(filepath, mtime_ns, size):
    return _parse_statement(filepath)


def parse_statement(filepath):
    filepath = os.fspath(filepath)
    stat = os.stat(filepath)
    return _cached_statement(filepath, stat.st_mtime_ns, stat.st_size)


def daily_balances(report):
    """Банк по датам обработки + поправка по всем известным переходящим операциям."""
    posted = defaultdict(lambda: ZERO)
    credits = defaultdict(lambda: ZERO)
    debits = defaultdict(lambda: ZERO)
    for op in report.operations:
        posted[op.processed_date] += op.value
        if op.value > 0:
            credits[op.date] += op.value
        else:
            debits[op.date] -= op.value
    result = []
    bank = report.opening
    day = report.start_date
    previous = None
    while day <= report.end_date:
        bank += posted[day]
        correction = sum((op.value for op in report.operations
                          if op.date <= day < op.processed_date), ZERO)
        correction -= sum((op.value for op in report.operations
                           if op.processed_date <= day < op.date), ZERO)
        balance = bank + correction
        delta = credits[day] - debits[day]
        if previous is not None and balance - previous != delta:
            raise ValueError(f"{day}: не сошлась ежедневная дельта")
        result.append(DailyBalance(
            day, credits[day], debits[day], delta, bank, correction, balance))
        previous = balance
        day += DAY
    return result


def enable_duplicate_comments():
    """Совместимое старое имя: теперь печатает комментарии дублей И балансов."""
    from beancount.parser import printer
    for name in ("Transaction", "Balance"):
        original = getattr(printer.EntryPrinter, name)
        if getattr(original, "_sber_daily_comments", False):
            continue

        def wrap(method):
            def render(self, entry, oss):
                text = entry.meta.get("__sber_pdf_comment__")
                if text:
                    for line in text.splitlines():
                        oss.write(f"; {line}\n")
                method(self, entry, oss)
            render._sber_daily_comments = True
            return render

        setattr(printer.EntryPrinter, name, wrap(original))


class SberPdfImporter(beangulp.Importer):
    def __init__(self, config_path):
        self._config_path = config_path

    def identify(self, filepath):
        if not os.fspath(filepath).lower().endswith(".pdf"):
            return False
        with pdfplumber.open(filepath) as pdf:
            text = pdf.pages[0].extract_text() or ""
        return "Выписка по платёжному счёту" in text and "Сбер" in text

    def account(self, filepath):
        # Только архивная группировка; счёт проводок выбирается по open metadata.
        return "Assets:Кэш:Вадим:Сбер:Платежный"

    def date(self, filepath):
        return parse_statement(filepath).end_date

    def extract(self, filepath, existing=None):
        enable_duplicate_comments()
        filepath = os.fspath(filepath)
        report = parse_statement(filepath)
        existing = list(existing or [])
        last4 = report.account_number[-4:]
        candidates = {entry.account for entry in existing if isinstance(entry, data.Open)
                      for key, value in entry.meta.items()
                      if (key == "account-number" or key.startswith("account-number-"))
                      and isinstance(value, str) and value[-4:] == last4}
        if len(candidates) != 1:
            raise ValueError(f"Счёт ****{last4}: нужно одно соответствие в open metadata, "
                             f"найдено {sorted(candidates)}. Передай журнал через --existing.")
        asset_account = candidates.pop()
        account_map = build_account_map(existing)
        rules_expense = load_rules(self._config_path, "sber", "expense")
        rules_income = load_rules(self._config_path, "sber", "income")
        settings = load_settings(self._config_path, "sber")
        counts = Counter(op.auth_code for op in report.operations)
        old_codes = {entry.meta.get("auth-code") for entry in existing
                     if isinstance(entry, data.Transaction)
                     and any(p.account == asset_account for p in entry.postings)}
        entries = []
        for op in sorted(reversed(report.operations), key=lambda op: (op.date, op.time)):
            if op.value == 0:
                continue
            row = NormalizedRow(op.date, op.value, "RUB",
                                op.description, last4, op.category)
            txn = build_transaction(
                row, account_map,
                rules_expense if op.value < 0 else rules_income,
                settings.get("unknown_expense") if op.value < 0
                else settings.get("unknown_income"),
                filepath, op.page,
                unknown_account=settings.get(
                    "unknown_account", "Assets:Unknown"),
            )
            if txn is None:
                continue
            txn.meta["time"] = op.time
            txn.meta["processed-date"] = op.processed_date
            txn.meta["auth-code"] = op.auth_code
            if op.card:
                txn.meta["card"] = op.card
            if counts[op.auth_code] > 1 or op.auth_code in old_codes:
                txn = txn._replace(flag="!")
                txn.meta[
                    "__sber_pdf_comment__"] = f"Возможный дубликат: повторяется auth-code {op.auth_code}."
            entries.append(txn)
        for daily in daily_balances(report):
            meta = data.new_metadata(filepath, 1)
            meta["__sber_pdf_comment__"] = daily.comment()
            entries.append(data.Balance(meta, daily.day + DAY, asset_account,
                                        amount.Amount(daily.balance, "RUB"), None, None))
        self.sort(entries)
        return entries

    def deduplicate(self, entries, existing):
        # Не скрывать подозрительные операции; оставлять ! и комментарий.
        return None

    def sort(self, entries, reverse=False):
        entries.sort(key=lambda entry: (
            entry.date, 0 if isinstance(entry, data.Balance) else 1,
            entry.meta.get("time", ""),
        ), reverse=reverse)

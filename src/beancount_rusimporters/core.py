from dataclasses import dataclass
from decimal import Decimal
import datetime
from beancount.core import data, amount
from .categorize import classify


@dataclass
class NormalizedRow:
    date: datetime.date
    amount: Decimal
    currency: str
    description: str
    card_last4: str
    raw_category: str | None = None


def build_transaction(row, account_map, rules, default_todo, filepath, idx,
                      unknown_account="Assets:Unknown"):
    other_account, tags = classify(
        row.description, abs(row.amount), rules, default_todo)
    if other_account == "__INTERNAL_TRANSFER__":
        return None

    flag = "*" if other_account else "!"
    asset_account = account_map.get(row.card_last4, unknown_account)

    meta = data.new_metadata(filepath, idx)
    if row.raw_category:
        meta["bank-category"] = row.raw_category
    meta["account-number"] = row.card_last4 or "****"

    postings = [
        data.Posting(asset_account, amount.Amount(
            row.amount, row.currency), None, None, None, None),
    ]
    if other_account is not None:
        postings.append(
            data.Posting(other_account, None, None, None, None, None))

    return data.Transaction(
        meta=meta, date=row.date, flag=flag, payee=None,
        narration=row.description, tags=frozenset(tags), links=frozenset(),
        postings=postings,
    )

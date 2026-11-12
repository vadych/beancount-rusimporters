from beancount.core.data import Open


def build_account_map(existing):
    """Строит {последние_4_цифры: счёт} из metadata account-number*
    на open-директивах существующего journal. Один счёт может иметь
    несколько номеров карт (перевыпуск, доп. карты)."""
    mapping = {}
    if not existing:
        return mapping
    for entry in existing:
        if isinstance(entry, Open):
            for key, value in entry.meta.items():
                if key == "account-number" or key.startswith("account-number-"):
                    mapping[value[-4:]] = entry.account
    return mapping

# beancount-rusimporters

Импортёры банковских выписок для Beancount v3 на базе beangulp.
Правила категоризации задаются в отдельном TOML-файле.

## Поддерживаемые импортёры

| Банк | Класс | Модуль |
|---|---|---|
| Сбер | `SberImporter` | `beancount_rusimporters.sber_bank` |
| Сбер, PDF | `SberPdfImporter` | `beancount_rusimporters.sber_bank_pdf` |
| Т-Банк | `TKSImporter` | `beancount_rusimporters.tks_bank` |
| ВТБ | `VTBImporter` | `beancount_rusimporters.vtb_bank` |

## Установка

Требуется Python 3.13 или новее.

С помощью uv:

```bash
uv add git+https://github.com/vadych/beancount-rusimporters.git
```

Или pip:

```bash
pip install git+https://github.com/vadych/beancount-rusimporters.git
```

## Конфигурация

Скопируйте `rusimporters.example.toml` из репозитория в
`rusimporters.toml` рядом с вашим `import.py`. Замените примеры счетов
и правил на свои.

Конфигурация содержит:

- `[defaults]` — счета для нераспознанных расходов и доходов;
- `[common.mappings]` и `[[common.rules]]` — общие правила;
- `[sber]`, `[tks]`, `[vtb]` — настройки отдельных банков;
- `[<bank>.mappings]` и `[[<bank>.rules]]` — банковские правила.

Личные правила и банковские данные храните в собственной конфигурации,
а не в публичном репозитории.

### Сопоставление счетов

Для сопоставления банковских счетов с Beancount-счетами используется
metadata `account-number` на директивах `open` вашего журнала.

Пример:

```beancount
2026-01-01 open Assets:Кэш:Сбер:Карта RUB
  account-number: "1234"
```

Значение должно соответствовать идентификатору счёта или карты,
который использует конкретный импортёр.

### Правила категоризации

Правила проверяются в следующем порядке:

1. `[[<bank>.rules]]`
2. `[<bank>.mappings]`
3. `[[common.rules]]`
4. `[common.mappings]`

Первое совпадение определяет категорию.

#### Сопоставление подстрок

`mappings` ищет подстроку без учёта регистра. Более длинные шаблоны
проверяются первыми.

```toml
[common.mappings]
"EXAMPLE SUPERMARKET" = "Expenses:Продукты"
"EXAMPLE CAFE" = "Expenses:Рестораны"
```

#### Регулярные выражения

`rules` использует регулярные выражения без учёта регистра:

```toml
[[common.rules]]
pattern = '\bEXAMPLE TAXI\b'
account = "Expenses:Транспорт"
direction = "expense"
```

Дополнительные условия и параметры:

| Параметр | Назначение |
|---|---|
| `min_amount` | Нижняя граница суммы |
| `max_amount` | Верхняя граница суммы |
| `direction` | Направление: `"expense"` или `"income"` |
| `tags` | Теги, добавляемые к транзакции |

Пример:

```toml
[[common.rules]]
pattern = '^EXAMPLE SERVICE$'
account = "Expenses:Подписки"
min_amount = 100
max_amount = 1000
direction = "expense"
tags = ["Subscription"]
```

#### Банковские правила

Для правил, относящихся только к одному банку, используйте его секцию:

```toml
[[sber.rules]]
pattern = '\bКапитализация\b'
account = "Income:Проценты"
direction = "income"
```

## Подключение к beangulp

Создайте `import.py`:

```python
from pathlib import Path

import beangulp

from beancount_rusimporters.sber_bank import SberImporter
from beancount_rusimporters.sber_bank_pdf import SberPdfImporter
from beancount_rusimporters.tks_bank import TKSImporter
from beancount_rusimporters.vtb_bank import VTBImporter


BASE_DIR = Path(__file__).resolve().parent
CONFIG_PATH = BASE_DIR / "rusimporters.toml"

CONFIG = [
    SberImporter(config_path=CONFIG_PATH),
    SberPdfImporter(config_path=CONFIG_PATH),
    TKSImporter(config_path=CONFIG_PATH),
    VTBImporter(config_path=CONFIG_PATH),
]


if __name__ == "__main__":
    beangulp.Ingest(CONFIG)()
```

Оставьте в `CONFIG` только нужные импортёры.

Извлечение транзакций с передачей существующего журнала:

```bash
uv run python import.py extract downloads/ -e main.beancount > imported.beancount
```

При использовании pip запускайте скрипт через Python окружения,
в которое установлен пакет:

```bash
python import.py extract downloads/ -e main.beancount > imported.beancount
```

Перед добавлением результата в журнал проверьте категории,
переводы между своими счетами и возможные дубликаты.

## Интеграция со smart-importer

Для предсказания категорий и получателей по существующему журналу
можно подключить `smart-importer`.

Установка:

```bash
uv add smart-importer
```

Или:

```bash
pip install smart-importer
```

Добавьте в `import.py`:

```python
from smart_importer import PredictPostings, PredictPayees

HOOKS = [PredictPostings().hook, PredictPayees().hook]
```

Передайте hooks при создании `Ingest`:

```python
if __name__ == "__main__":
    beangulp.Ingest(CONFIG, hooks=HOOKS)()
```

### Настройка fallback для расходов

При использовании `PredictPostings` удалите из секции `[defaults]`
в `rusimporters.toml` строку:

```toml
unknown_expense = "Expenses:Unknown"
```

Иначе нераспознанный расход получает проводку на `Expenses:Unknown`,
которая мешает предсказанию категории.

Правила с явно заданными категориями можно оставить.

## Проверка результата

Подключите проверяемый файл к журналу через `include` и выполните:

```bash
uv run bean-check main.beancount
```

Или при установке через pip:

```bash
bean-check main.beancount
```

Проверка Beancount не заменяет сверку с банковской выпиской:
проверяйте остатки, полноту операций и корректность категорий.

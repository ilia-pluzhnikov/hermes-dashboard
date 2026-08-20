# AUDIT — hermes-dashboard @ a27d6a9eae38540f63347917c07728d319fd581c

Дата: 2026-08-20 · Аудитор: ассистент Ильи (King) · Скоуп: полный код движка
(`hermes_dashboard/`, `bin/`, `tools/`, `tests/`, `assets/`), три независимых
параллельных ревью: subprocess+сеть, диск+SQLite, секреты+динамический код.

Вердикт: **чисто** — красных находок нет. Ниже полный перечень фактов и
low/info-наблюдения (hardening-кандидаты, не блокеры).

## Subprocess-вызовы (полный перечень)

Все вызовы — списочная форма `subprocess.run([...])`; `shell=True`, `os.system`
и голый `Popen` отсутствуют во всём репозитории. Пользовательский/агентский
ввод (state.db, логи, config.yaml, jobs.json) в командные строки не попадает.

| Файл:строка | Команда | Аргументы из | Риск |
|---|---|---|---|
| `build.py:71,73→78` | `<venv_python\|sys.executable> -m hermes_dashboard.collectors.codex_quota\|anthropic_cost` | интерпретатор — `paths.venv_python` (конфиг); модули — хардкод | low |
| `build.py:300` | `<venv_python> -m hermes_dashboard.gen_config` | только при `views.config_map` и существующем файле интерпретатора | low |
| `gen_config.py:129→140,147` | `git -C <home> show/log <TRUTH_REF>…` | `TRUTH_REF` из конфига, **без `--`** | low (см. находку 1) |
| `gen_config.py:596` | `<paths.node> -e <константный JS>` | интерпретатор из конфига, код — константа | low |
| `gen_connectors.py:54` | `<venv_python> -c "<константный yaml.safe_load→json>" <home>/config.yaml` | код — хардкод | low |
| `gen_events.py:105`, `sysinfo.py:90–91` | `git -C <home> log -1 --format=…` | хардкод | none |
| `gen_cron.py:94`, `sysinfo.py:96` | `crontab -l` | хардкод | none |
| `gen_security.py:76` | `git -C <home> log -1 … -- <rel>` | `rel` из конфига, **после `--`** (образцово) | none |
| `sysinfo.py:28` | `systemctl is-active <paths.gateway_unit>` | unit из конфига | none |
| `settings.py:355` | `sys.executable -m hermes_dashboard.build --config <path>` | фиксировано; POST+CSRF на 127.0.0.1 | none |
| `bin/hermes-dashboard:14–16` | bash-`case` → `exec python -m …build\|settings\|unittest` | подкоманда из фикс. case | none |
| `tools/make_demo.py:246`, `tests/test_engine.py:145,337` | свои модули, tmp-пути | хардкод | none |

## Сеть

| Файл:строка | Куда | Условие включения |
|---|---|---|
| `collectors/anthropic_cost.py:80–87` | `https://api.anthropic.com/v1/organizations/cost_report` (константа) | только при `providers.paid[*].cost_cache` **и** admin-ключе в env/`.env` |
| `collectors/codex_quota.py:40–48` | косвенно: `agent.account_usage` из `$HERMES_HOME/hermes-agent` (httpx ядра агента) | только при `providers.primary.quota_cache` |
| `render.py:19–21` | `fonts.googleapis.com`/`fonts.gstatic.com` — `<link>` в HTML, запрос делает браузер зрителя | `views.web_fonts` (дефолт true); off → страница «никому не звонит» |
| `settings.py:863` | **входящий** сокет 127.0.0.1:8648 | только явный запуск `settings` |

Других сетевых путей нет (tools/tests/assets проверены; JS страниц — без
`fetch`/XHR/WebSocket/`eval`). Заявление «сеть только в anthropic_cost» верно
с одной поправкой: второй путь — quota-коллектор через ядро агента; оба пути
включаются только конфигом.

## Записи на диск

Ограничены заявленными категориями; в файлы агента движок не пишет никогда.

| Категория | Пути | Механизм |
|---|---|---|
| Страницы | `out_dir/*.html` | `tempfile.mkstemp` в каталоге цели → `chmod 644` → атомарный `replace`; при исключении tmp удаляется |
| Лок сборки | `out_dir/.hermes-dashboard.lock` | `O_CREAT\|O_EXCL` с pid; протухание 900 с; fail-open best-effort |
| История дельт | `paths.history_csv` (+ `mkdir` родителя) | `*.tmp` → `replace` |
| Кэши коллекторов и карты | `$HERMES_HOME/cache/*.json`, `cache/config-map.*.html` | `*.tmp` → `replace` |
| Settings-сервер (отдельный процесс, явный POST) | `dashboard.json`(+`.bak`), `budgets.env`(+`.bak`), CSV-выгрузка | `_atomic` tmp→replace |

`tools/make_demo.py` и тесты пишут только в свой демо-каталог / системный temp.

## SQLite (state.db / memory_store.db)

- Каждое подключение к базам агента — строго read-only URI `file:…?mode=ro`
  (`common.py:145 connect_ro()`; также `build.py:54`, `gen_cron.py:194,264`).
- Во всём движке **ни одного** INSERT/UPDATE/CREATE/DELETE/PRAGMA (grep = 0);
  соединения короткоживущие (1–2 SELECT → `close()` в `finally`), ошибки
  гасятся деградацией секции в «—».
- WAL-безопасность: `mode=ro` (а не `immutable=1` — тот был бы опасен при
  живом писателе) честно читает через `-wal`/`-shm`, писателя не блокирует,
  checkpoint не задерживает. rw-подключения есть только в make_demo/тестах —
  к синтетическим базам.

## .env и секреты

- `common.py:299–311 env_key_names()`: из `$HERMES_HOME/.env` читаются **только
  имена** переменных (`split("=",1)[0]`), значения отбрасываются на месте; даже
  имена в HTML не печатаются — только лейблы карточек коннекторов.
- Единственное исключение (документировано в README движка):
  `anthropic_cost.py:41–56` читает **значение** `ANTHROPIC_ADMIN_KEY` — уходит
  только в заголовок HTTPS-запроса, в вывод/кэш/HTML не попадает.
- `auth.json`: читаются только статусные поля (`last_status`,
  `last_error_reset_at`) — токены не читаются вовсе. `credentials/` не
  читается нигде.
- В статический HTML из файлов агента попадают только агрегаты и усечённые
  экранированные фрагменты; `config.yaml` в карте конфига — под двухслойной
  маскировкой (по имени пути и по форме значения → `‹hidden›`). Содержимое
  логов и памяти в HTML не встраивается (только счётчики/длины).
- Hygiene-тест сканирует репозиторий; runtime-selfcheck (`SECRET_VALUE_RE`)
  покрывает только страницу карты конфига — собранные index/connectors тестом
  не сканируются (см. находку 3).

## Динамический код

`eval`, `exec`, `pickle`/`marshal`/`shelve`, `importlib`/`__import__`,
`compile()` — отсутствуют (все совпадения — `re.compile` констант либо
regex-полей операторского конфига). Обфускации и base64-блобов нет
(единственный base64 — encode favicon в data:-URI). YAML — только
`yaml.safe_load`. Пограничное, by design: `gen_config`/`codex_quota` делают
`sys.path.insert($HERMES_HOME/hermes-agent)` и импортируют код ядра агента —
исполнение доверенного локального кода, включается только конфигом.

Settings-сервер из `build` не запускается никаким путём: диспетчер `bin/` —
взаимоисключающий `case` с `exec`; `build.py` не импортирует `settings` ни
прямо, ни транзитивно; у `settings.py` нет module-level сайд-эффектов.

## Находки (все low/info, не блокеры)

1. **[low]** Option-injection в `git show/log` через `config_map.truth_ref`
   (`gen_config.py:140,147` — ref без `--`); источник — доверенный конфиг
   владельца. Фикс — `--` по образцу `gen_security.py:76`.
2. **[low, by design]** `paths.venv_python`/`paths.node` из конфига исполняются
   как бинарники; запись в dashboard.json ⇒ RCE под пользователем дашборда.
   Осознанная модель доверия (один trust-домен).
3. **[low]** Нет secret-скана собранных index/connectors (только карты
   конфига). Компенсация: генераторы этих страниц секретных файлов не читают;
   в нашем деплое — внешний grep собранного out/ перед публикацией.
4. **[low]** Фиксированные имена `*.tmp` вне `build._atomic_write` — гонка
   возможна лишь между сборками с одним HERMES_HOME и разными out_dir; итог —
   потеря одного из одинаковых обновлений кэша, не рваный файл.
5. **[info]** Flash-сообщения settings-страницы без `esc()` — self-XSS за
   CSRF+basic-auth; settings-сервер в нашем деплое не запускается.
6. **[info]** `views.web_fonts` (дефолт true) — браузер зрителя ходит за
   шрифтами на Google Fonts; отключаемо конфигом.

## Выводы для нашего деплоя

- `venv_python=/usr/bin/python3` (системный python3 хоста) реально исполняет
  максимум одно: константный однострочник `yaml.safe_load→json` для карты
  коннекторов (`gen_connectors.py:54`) — и то лишь если у python сборки нет
  PyYAML. Quota-коллектор (`quota_cache=""`), cost-коллектор (нет `paid`) и
  `gen_config` (`views.config_map=false`) в нашей конфигурации **не
  запускаются** ⇒ импорт ядра агента и оба сетевых пути мертвы; build не
  делает ни одного исходящего запроса.
- Host-cron от root ежечасно исполняет по кругу: чтение файлов дома агента
  (read-only), `SELECT` из state.db (`mode=ro`, WAL-безопасно), `git log`
  (аргументы — хардкод), `crontab -l`, `systemctl is-active`, и атомарную
  запись HTML в свой `out_dir` + строку в свой `history.csv`. Записей внутрь
  дома агента при наших абсолютных путях инстанса — ноль.
- `state.db` при работающем gateway читать безопасно: `mode=ro` + мгновенные
  однозапросные соединения.
- Пробел «нет secret-скана собранных страниц» закрывается внешним grep-гейтом
  по `out/` перед публикацией (и далее — вручную при изменениях конфига).

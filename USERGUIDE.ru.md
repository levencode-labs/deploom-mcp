# Deploom MCP — руководство пользователя

[English](USERGUIDE.md) · [Обзор продукта](README.ru.md) · [Документация](docs/INDEX.ru.md) · [Последний релиз](https://github.com/levencode-labs/deploom-mcp/releases/latest)

Это руководство для установки **опубликованного wheel и подключения локального MCP**. Deploom Desktop, отдельная установка Core и клонирование исходников не нужны. Для первой пробы лучше использовать публичный/синтетический проект; работу с приватными зависимостями и сетевыми источниками нужно разрешать отдельно.

> **Версия в примерах:** Deploom MCP **0.2.11** (релиз 7 ноября 2026 года), канонический Skill **2.0.14**. При переходе на новый релиз берите wheel и суммы из [одного выпуска](https://github.com/levencode-labs/deploom-mcp/releases/latest), не смешивая версии.

## 1. Установите релиз

**Требования:** пакет объявляет Python `>=3.12,<3.15`; для первой проверки на Windows/Linux разумно выбрать **CPython 3.12 x86-64**. Нужны доступные платформенные wheel зависимостей из разрешённого Python index либо подготовленного wheelhouse. Node/npm и инструменты проекта потребуются для последующих проверок миграции.

### Windows (PowerShell)

```powershell
$deploomHome = Join-Path $env:LOCALAPPDATA 'Deploom'
python -m venv "$deploomHome\venv"  # Здесь должен вызываться Python 3.12
$py = Join-Path $deploomHome 'venv\Scripts\python.exe'
$wheel = 'deploom_mcp-0.2.11-py3-none-any.whl'
$base = 'https://github.com/levencode-labs/deploom-mcp/releases/download/v0.2.11'
New-Item -ItemType Directory -Force "$deploomHome\downloads" | Out-Null
Invoke-WebRequest "$base/$wheel" -OutFile "$deploomHome\downloads\$wheel"
Invoke-WebRequest "$base/SHA256SUMS.txt" -OutFile "$deploomHome\downloads\SHA256SUMS.txt"
$entry = Get-Content "$deploomHome\downloads\SHA256SUMS.txt" | Where-Object { $_ -match ([regex]::Escape($wheel) + '$') }
if (-not $entry -or @($entry).Count -ne 1) { throw 'Не найдена однозначная контрольная сумма wheel' }
$expected = ($entry -split '\s+')[0].ToLowerInvariant()
$actual = (Get-FileHash "$deploomHome\downloads\$wheel" -Algorithm SHA256).Hash.ToLowerInvariant()
if ($actual -ne $expected) { throw 'Контрольная сумма релиза не совпадает' }
& $py -m pip install "$deploomHome\downloads\$wheel"
if ($LASTEXITCODE -ne 0) { throw 'Ошибка установки' }
& $py -m pip check
& $py -c "import importlib.metadata as m; print(m.version('deploom-mcp'))"
```

Если `python` указывает на другую версию, вызовите установленный Python 3.12 по полному пути. `SHA256SUMS.txt` подтверждает соответствие файлу, опубликованному автором релиза, но **не является независимой криптографической подписью**. Для зависимостей используйте разрешённые index, сертификаты и proxy.

### Linux (Python 3.12)

```bash
python3.12 -m venv "$HOME/.local/share/deploom/venv"
PY="$HOME/.local/share/deploom/venv/bin/python"
"$PY" -m pip install 'https://github.com/levencode-labs/deploom-mcp/releases/download/v0.2.11/deploom_mcp-0.2.11-py3-none-any.whl'
"$PY" -m pip check
"$PY" -c 'import importlib.metadata as m; print(m.version("deploom-mcp"))'
```

Для подготовки переносимого офлайн-комплекта см. [инструкцию для коллег](docs/SHARING_WITH_COLLEAGUES.ru.md). Из опубликованного wheel Core устанавливается вместе с MCP.

## 2. Установите Skill (необязательно)

Канонический Skill **`deploom-upgrade`** одинаков для поддерживаемых клиентов. Его установка **не регистрирует MCP** и не меняет имеющиеся клиентские конфигурации.

```powershell
# Windows, в окружении из шага 1
& "$deploomHome\venv\Scripts\deploom-install-skill.exe" --client codex --scope user --dry-run
& "$deploomHome\venv\Scripts\deploom-install-skill.exe" --client codex --scope user --verify
```

Для OpenCode или Claude Code замените `--client` на `opencode` / `claude-code`. Для установки в проект можно выбрать `--scope project --project /absolute/project/path`. На Linux запускайте `$HOME/.local/share/deploom/venv/bin/deploom-install-skill`.

Если установленный Skill отличается от нового, сначала сохраните копию, изучите `--dry-run` и только затем применяйте предусмотренный явный путь обновления (`--force --verify`). Не исправляйте напрямую файлы Skill, зафиксированные контрольными суммами. После установки перезапустите клиент и убедитесь, что он действительно загрузил нужную версию.

## 3. Подключите MCP-клиент

### Codex

Указывайте абсолютный путь к Python **того** окружения, куда установлен wheel:

```powershell
codex mcp add deploom -- "$deploomHome\venv\Scripts\python.exe" -m deploom_mcp
```

Или дополните существующий `~/.codex/config.toml` (не удаляя другие записи):

```toml
[mcp_servers.deploom]
command = "C:/path/to/Deploom/venv/Scripts/python.exe"
args = ["-m", "deploom_mcp"]
```

Сверяйте синтаксис с `codex mcp --help` именно установленной версии. [Интеграция Codex](integrations/codex/README.md). Не отключайте sandbox и не расширяйте доверенные каталоги только ради прохождения теста.

### OpenCode V2

Добавьте сервер в **имеющийся** конфиг OpenCode; остальные серверы, модели и разрешения не удаляйте. Замените путь пользователя:

```jsonc
{
  "mcp": {
    "servers": {
      "deploom": {
        "type": "local",
        "command": [
          "C:/Users/YOUR_USER/AppData/Local/Deploom/venv/Scripts/python.exe",
          "-m", "deploom_mcp"
        ],
        "environment": {
          "DEPLOOM_MCP_STATE_DIR": "C:/Users/YOUR_USER/AppData/Local/Deploom/state"
        },
        "disabled": false
      }
    }
  }
}
```

На Linux нужен абсолютный `venv/bin/python`. **Конфиг OpenCode V1 и V2 различается**: проверьте собственную версию клиента. Подробнее: [интеграция OpenCode](integrations/opencode/README.ru.md).

### Другой MCP-клиент

Зарегистрируйте **stdio**-сервер: команда `/absolute/path/to/venv/python`, аргументы `-m deploom_mcp`. Перезапустите клиент, проверьте реальный каталог инструментов MCP и отсутствие ошибки запуска. `python -m deploom_mcp` в терминале ждёт сообщения протокола, а не обычный текст чата.

## 4. Первая проба — только план

Выберите публичный или синтетический проект. Пример запроса агенту:

> Загрузи `deploom-upgrade` и используй установленный Deploom MCP. Режим PLAN_ONLY, цель Yellow. Не меняй файлы, не устанавливай зависимости, не делай commit, push или публикацию. Выполни инвентаризацию workspaces, покажи реальное покрытие и scope разрешённых изменений. Вызови `get_upgrade_plan`, а не просто опиши его. Сообщи путь настоящего Blueprint, статус, блокеры и UNKNOWN evidence. Не объявляй runtime успешным по одному плану.

Минимальный пример `get_upgrade_plan`:

```json
{
  "project_path": "C:/projects/example-app",
  "target_level": "yellow",
  "min_lag_ok_pct": 80,
  "target_mode": "advisory",
  "objective": "minimum-change-to-policy",
  "deadline_seconds": 120
}
```

Передавайте только **разрешённый** registry, не указывая credentials в URL или prompt. Проверяйте полный Blueprint, а не только пересказ агента. Даже у предложенного плана могут быть частичное покрытие, unknown, лимиты кандидатов, несовместимые peers или устаревшая входная идентичность.

### Как читать результат

| Наблюдение | Что действительно подтверждено |
| :-- | :-- |
| `PLANNING_ONLY` / `NOT_VERIFIED` | Построено предложение; установка и приёмка проекта не доказаны. |
| Допустимое назначение в модели | Выполнены **учтённые в модели** ограничения, но не обязательно реальный npm tree. |
| Успешный `LOCKFILE_ONLY` | Проверен lockfile в объявленных границах; физическое дерево и runtime не наблюдались. |
| `npm ci`, `npm ls`, проверки реальных providers | Сведения о конкретном установленном дереве, но не обо всём поведении приложения. |
| Build/tests/runtime | Только фактически выполненные проверки на точном кандидате и платформе. |
| `UNKNOWN`, `PARTIAL`, `NOT_RUN` | Доказательств недостаёт; нельзя подменять такие статусы на PASS. |

Для npm workspaces инвентаризация и совместное планирование — разные возможности. Изменения удерживаемых деклараций или чужого owner недопустимы ради искусственного PASS. Перед explicit-goal выполнением изучите [матрицу поддержки](docs/GOAL_EXECUTION_SUPPORT.md).

## 5. Отдельно разрешите обновление

После разбора плана обозначьте scope и разрешение. Пример:

> Примени только согласованные изменения зависимостей в изолированном worktree. Сохрани остальные манифесты, lockfile-связи и исходники. Выполни чистую установку, проверь настоящих dependency owners и peer providers, обязательные тесты и runtime. Проведи независимый итоговый аудит. Покажи diff, результаты и недостающие доказательства. Не делай commit, push и релиз.

Агентские правки и host-authorized маршрут выполнения MCP — **разные контракты**. У `get_goal_plan`, `verify_goal_candidate`, `apply_goal_candidate` отдельные полномочия и требования к evidence. Импортированный план или утверждение агента не даёт разрешения на запись. См. [Goal execution](docs/GOAL_EXECUTION_SUPPORT.md).

## 6. Сеть и безопасность

**Локальный stdio не означает работу без сети.** Планировщик может обращаться к npm registry, OSV (по умолчанию `api.osv.dev`) и отдельным документированным источникам. Параметр `registry` не перенаправляет OSV в этот registry. Registry advisory-запросы могут передавать имена и версии пакетов. Проверьте корпоративную политику перед запуском на приватных зависимостях. При недоступных разрешённых источниках оставляйте evidence `UNKNOWN`.

**Remote Streamable HTTP:** [инструкция](docs/HTTP_DEPLOYMENT.md). У сервера нет встроенной аутентификации пользователей и изоляции проектных путей. Для удалённой работы нужны TLS/authenticated gateway, ограниченные права и mount, отдельные instances для недоверенных пользователей и лимиты процессов. `project_path` относится к файловой системе **сервера**.

Ошибки proxy/registry решайте через [System proxy](docs/SYSTEM_PROXY.md), не переключая приватный проект на сторонний registry или advisory API без разрешения.

## Частые проблемы

- **Не видно MCP:** проверьте полный путь к Python, версию установленного wheel, конфиг и stderr, перезапустите клиент.
- **Не загрузился Skill:** установка Skill и регистрация MCP — два независимых действия.
- **Ошибка registry, TLS или proxy:** сохраните `UNKNOWN`, исправьте разрешённый маршрут и повторите ограниченный запрос.
- **Монорепозиторий покрыт частично:** перечислите owners/workspaces, которые не вошли в план; не объявляйте полную приёмку.
- **План построен:** для Yellow/Green всё ещё нужны реальные npm/project проверки на конечном кандидате.

[Индекс документации](docs/INDEX.ru.md) · [Релизы](https://github.com/levencode-labs/deploom-mcp/releases/latest)

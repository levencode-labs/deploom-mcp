# Как передать Deploom MCP коллегам

[English](SHARING_WITH_COLLEAGUES.md) · [Руководство](../USERGUIDE.ru.md) · [Релизы](https://github.com/levencode-labs/deploom-mcp/releases/latest)

Для первой пробы проще использовать **локальный stdio**: каждый разработчик устанавливает wheel на свой компьютер, регистрирует Python-путь в MCP-клиенте и при необходимости устанавливает Skill. Опубликованный wheel уже включает закреплённый Core.

**Не передавайте** свой venv, конфиги с личными путями, ключи, credentials, кэши, state MCP или evidence проектов. Виртуальное окружение Windows нельзя переносить на другой компьютер как готовый пакет.

## Офлайн-комплект Python-зависимостей (Windows)

Комплект должен подходить **точной версии Python, ОС и архитектуре CPU** получателя. Зависимости могут иметь платформенные wheel. В отдельном пустом каталоге через разрешённый Python index/proxy:

```powershell
$release = '0.2.11'
$bundle = Join-Path $PWD 'deploom-trial'
$house = Join-Path $bundle 'wheelhouse'
New-Item -ItemType Directory -Force $house | Out-Null
$filename = "deploom_mcp-$release-py3-none-any.whl"
$base = "https://github.com/levencode-labs/deploom-mcp/releases/download/v$release"
$wheel = Join-Path $bundle $filename
Invoke-WebRequest "$base/$filename" -OutFile $wheel
Invoke-WebRequest "$base/SHA256SUMS.txt" -OutFile (Join-Path $bundle 'SHA256SUMS.txt')
$line = Get-Content (Join-Path $bundle 'SHA256SUMS.txt') | Where-Object { $_ -match ([regex]::Escape($filename) + '$') }
if (@($line).Count -ne 1) { throw 'Не найдена однозначная сумма wheel' }
$expected = ($line -split '\s+')[0].ToLowerInvariant()
$actual = (Get-FileHash $wheel -Algorithm SHA256).Hash.ToLowerInvariant()
if ($expected -ne $actual) { throw 'Сумма опубликованного wheel не совпадает' }
Copy-Item $wheel (Join-Path $house $filename)
python -m pip download --only-binary=:all: --dest $house $wheel
if ($LASTEXITCODE -ne 0) { throw 'Неполный wheelhouse — раздавать нельзя' }
Get-ChildItem $house -File | Get-FileHash -Algorithm SHA256 |
  Select-Object @{n='File';e={Split-Path $_.Path -Leaf}}, Hash |
  Export-Csv (Join-Path $bundle 'wheelhouse-sha256.csv') -NoTypeInformation -Encoding UTF8
```

Положите в комплект это руководство и `USERGUIDE.ru.md` той же ревизии. CSV описывает скачанные зависимости, но не является независимой подписью издателя. Передавайте файлы через разрешённый защищённый канал. Совместимость нужно отдельно проверить на чистом компьютере получателя.

### Установка у получателя

```powershell
# Из каталога распакованного комплекта:
python -m venv .venv  # Версия Python должна соответствовать wheelhouse
& .\.venv\Scripts\python.exe -m pip install --no-index --find-links .\wheelhouse deploom-mcp==0.2.11
if ($LASTEXITCODE -ne 0) { throw 'Ошибка автономной установки' }
& .\.venv\Scripts\python.exe -m pip check
& .\.venv\Scripts\deploom-install-skill.exe --client codex --scope user --dry-run
```

Выберите и проверьте путь установки Skill, прежде чем выполнять команду без `--dry-run`. Пропишите **локальный** Python этого компьютера в конфиг MCP-клиента по [руководству](../USERGUIDE.ru.md#3-подключите-mcp-клиент). Автономная установка wheel не делает планировщик офлайн: недоступные registry/advisory данные остаются `UNKNOWN`.

## Сетевой MCP (remote HTTP)

Deploom поддерживает Streamable HTTP наряду с локальным stdio. При этом **нет встроенной аутентификации пользователей и изоляции путей**. Не выставляйте порт наружу без TLS/authenticated gateway, разграничения доступа, отдельных экземпляров для недоверенных пользователей и минимально необходимых mounts.

См. [HTTP deployment](HTTP_DEPLOYMENT.md). Простой HTTP-сервер для скачивания wheel — **не** MCP endpoint. Удалённый MCP видит файловую систему **сервера**, а не пути на компьютере коллеги.

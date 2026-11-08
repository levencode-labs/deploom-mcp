# Deploom MCP + OpenCode

[English](README.md) · [Руководство](../../USERGUIDE.ru.md) · [Релизы](https://github.com/levencode-labs/deploom-mcp/releases/latest)

Deploom — локальный **stdio MCP-сервер** и необязательный канонический Skill `deploom-upgrade`. В опубликованный wheel уже входит закреплённый Core. Desktop-приложение не требуется.

## Установка

Сначала выполните [установку wheel](../../USERGUIDE.ru.md#1-установите-релиз) и запомните абсолютный путь к Python из его окружения. В том же окружении будет установщик Skill `deploom-install-skill`.

## Конфигурация OpenCode

У **OpenCode V1 и V2 разные конфиги**. Посмотрите фактическую версию и её документацию, не перезаписывайте другие MCP, провайдеры и permissions.

### Пример OpenCode V2

Добавьте сервер в существующий `mcp.servers` (замените путь):

```jsonc
{
  "mcp": {
    "servers": {
      "deploom": {
        "type": "local",
        "command": ["C:/path/to/Deploom/venv/Scripts/python.exe", "-m", "deploom_mcp"],
        "disabled": false
      }
    }
  }
}
```

Для **V1** используйте схему именно установленной версии (`mcp.<name>` и `enabled`, а не `mcp.servers` и `disabled`). Не вставляйте конфиг V2 в V1.

## Skill и первая проба

```powershell
& "C:/path/to/Deploom/venv/Scripts/deploom-install-skill.exe" --client opencode --scope user --dry-run
& "C:/path/to/Deploom/venv/Scripts/deploom-install-skill.exe" --client opencode --scope user --verify
```

Перезапустите клиент. Проверьте подключение MCP и фактическую загрузку Skill. Набор tools может зависеть от entrypoint и версии: число инструментов из старого релиза 0.2.2 нельзя автоматически приписывать текущему.

Для первой пробы возьмите открытый/синтетический проект, режим **PLAN_ONLY Yellow** и реальный вызов `get_upgrade_plan`. Требуйте coverage всех owners/workspaces, путь Blueprint, блокеры и UNKNOWN, **без изменений файлов и установки пакетов**. Пример — в [руководстве](../../USERGUIDE.ru.md#4-первая-проба--только-план). Для remote MCP смотрите [HTTP](../../docs/HTTP_DEPLOYMENT.md) и его требования к безопасности.

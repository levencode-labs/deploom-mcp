# Дистрибутив и проверка Deploom MCP

[English](DISTRIBUTION.md) · [Руководство](../USERGUIDE.ru.md) · [Релизы](https://github.com/levencode-labs/deploom-mcp/releases/latest)

Опубликованный wheel включает закреплённый [Deploom Core](https://github.com/levencode-labs/deploom-core), схемы Upgrade Blueprint, канонический Skill и установщик. Пользователю wheel **не нужны** Desktop/Electron, отдельный checkout Core и Git для запуска сервера.

`release-manifest.json` закрепляет ожидаемые версию, Core/gitlink, инвентарь исходников и содержимое Skill/схем. Эти сведения нужно сопоставлять с фактическими байтами и runtime provenance. Наличие manifest или квитанции установки Skill **не доказывает**, какой текст реально загрузил агент.

## Сборка из закреплённых исходников

Используйте чистый checkout, отдельный Python 3.12–3.13 и разрешённые зависимости/инструменты. **Не меняйте файлы Core внутри submodule.** Новый pin допустим только после review изменений в его собственном репозитории.

```bash
git submodule update --init --recursive
python scripts/check_skill.py --write
python scripts/update_release_manifest.py
python scripts/check_skill.py
python scripts/update_release_manifest.py --check
python -m build --no-isolation
```

Команды могут обновлять сгенерированные контрольные суммы. Выполняйте их только в подготовленной релизной задаче с закреплённым baseline. Изменения логотипов/README **не должны** менять Core gitlink, версию пакета, `release-manifest.json` или Skill.

## Установка и проверка релиза

В примерах используется wheel версии **0.2.19**.

```bash
python -m pip install dist/deploom_mcp-0.2.19-py3-none-any.whl
python -m pip check
python scripts/smoke_stdio.py
python scripts/smoke_wheel.py dist/deploom_mcp-0.2.19-py3-none-any.whl
```

Для будущих версий используйте **реальное** имя собранного файла. Smoke установленного wheel проверяет ресурсы и базовый запуск, но не доказывает поведение мигрированного приложения, ускорение или полную приёмку нового релиза. Отдельно проверьте wheel, восстановленный из sdist, и зафиксируйте SHA/toolchain.

## Граница публикации

Релиз требует точного commit/tag, CI-матрицы, проверки исходников и серверных digest-аттестаций по [релизному регламенту](RELEASING.md). Локальная сборка ничего не публикует на GitHub или PyPI. Нельзя передвигать опубликованный tag, подменять бинарные assets или молча расширять матрицу приёмки.

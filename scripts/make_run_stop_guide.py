"""Build the operator's run/stop guide as a .docx.

Not part of the application: a document for one person about one machine. It is written once
and read when something will not start, so it is generated from a script rather than
hand-formatted — the content stays reviewable in git history even though the .docx does not.
"""

from __future__ import annotations

import pathlib

from docx import Document
from docx.enum.table import WD_TABLE_ALIGNMENT
from docx.shared import Inches, Pt, RGBColor

# The project root, not scripts/ — where the person who needs it will look. The file is in
# .gitignore, so it stays on the machine it was written for.
OUT = pathlib.Path(__file__).resolve().parent.parent / "Run_Stop_Project.docx"

ACCENT = RGBColor(0x1F, 0x4E, 0x79)
MUTED = RGBColor(0x59, 0x59, 0x59)
CODE_BG = "F2F2F2"


def heading(doc, text: str, level: int = 1) -> None:
    h = doc.add_heading(text, level=level)
    for run in h.runs:
        run.font.color.rgb = ACCENT


def para(doc, text: str, *, bold: bool = False, italic: bool = False, size: int = 11):
    p = doc.add_paragraph()
    run = p.add_run(text)
    run.bold = bold
    run.italic = italic
    run.font.size = Pt(size)
    return p


def bullet(doc, text: str, level: int = 0):
    p = doc.add_paragraph(style="List Bullet")
    if level:
        p.paragraph_format.left_indent = Inches(0.25 + 0.25 * level)
    p.add_run(text)
    return p


def numbered(doc, text: str):
    p = doc.add_paragraph(style="List Number")
    p.add_run(text)
    return p


def code(doc, text: str):
    """A command block: monospaced, shaded, never broken across pages where avoidable."""
    p = doc.add_paragraph()
    p.paragraph_format.left_indent = Inches(0.25)
    p.paragraph_format.space_before = Pt(4)
    p.paragraph_format.space_after = Pt(8)
    run = p.add_run(text)
    run.font.name = "Consolas"
    run.font.size = Pt(10)
    run.font.color.rgb = RGBColor(0x1A, 0x1A, 0x1A)
    shading = p._p.get_or_add_pPr()
    from docx.oxml import OxmlElement
    from docx.oxml.ns import qn

    shd = OxmlElement("w:shd")
    shd.set(qn("w:val"), "clear")
    shd.set(qn("w:fill"), CODE_BG)
    shading.append(shd)
    return p


def note(doc, text: str):
    p = doc.add_paragraph()
    p.paragraph_format.left_indent = Inches(0.25)
    run = p.add_run(text)
    run.italic = True
    run.font.size = Pt(10)
    run.font.color.rgb = MUTED
    return p


def build() -> pathlib.Path:
    doc = Document()

    # Base style: a document read on a laptop screen, not printed.
    style = doc.styles["Normal"]
    style.font.name = "Calibri"
    style.font.size = Pt(11)
    style.paragraph_format.space_after = Pt(6)

    title = doc.add_heading("SOWSprint.ai — запуск и остановка", level=0)
    for run in title.runs:
        run.font.color.rgb = ACCENT

    para(
        doc,
        "Как поднять проект, когда вы садитесь за него, и как полностью остановить, когда "
        "переключаетесь на другой. Документ рассчитан на работу с разных машин: Windows-ноутбука "
        "и удалённого Linux-сервера.",
        italic=True,
    )
    note(
        doc,
        "Файл лежит в корне проекта и добавлен в .gitignore — он не попадает в репозиторий.",
    )

    # ---------------------------------------------------------------- главное правило
    heading(doc, "Главное, что нужно знать", 1)
    para(
        doc,
        "Контейнеры проекта настроены так, что НЕ запускаются сами при перезагрузке сервера. "
        "Это сделано намеренно: проект занимает порты 8000, 6333 и 6334, и если бы он "
        "поднимался автоматически, он мешал бы другим вашим проектам, а вы бы искали причину "
        "в чужом коде.",
    )
    para(doc, "Отсюда два следствия:", bold=True)
    bullet(doc, "После перезагрузки машины проект не работает — его нужно запустить вручную.")
    bullet(
        doc,
        "Пока проект запущен, порты заняты. Другой проект, которому нужен 8000, не стартует "
        "и скажет, кто именно держит порт.",
    )

    # ---------------------------------------------------------------- два способа
    heading(doc, "Два способа работать — выберите один", 1)

    table = doc.add_table(rows=1, cols=3)
    table.style = "Light Grid Accent 1"
    table.alignment = WD_TABLE_ALIGNMENT.CENTER
    hdr = table.rows[0].cells
    hdr[0].text = "Способ"
    hdr[1].text = "Когда удобен"
    hdr[2].text = "Что запускает"
    for row in (
        (
            "Скрипты start / stop",
            "Быстро поднять приложение и открыть в браузере",
            "Приложение и базу данных",
        ),
        (
            "Dev Containers в VS Code",
            "Работа с кодом: правка, тесты, отладка",
            "Среду разработки и базу данных",
        ),
    ):
        cells = table.add_row().cells
        for i, value in enumerate(row):
            cells[i].text = value

    note(
        doc,
        "Оба способа занимают одни и те же порты, поэтому одновременно они работать не могут. "
        "Это не ошибка настройки: так вы точно знаете, что именно сейчас запущено.",
    )

    # ---------------------------------------------------------------- сценарий 1
    doc.add_page_break()
    heading(doc, "Сценарий 1. Быстрый запуск через скрипты", 1)

    heading(doc, "Windows (PowerShell в VS Code или Windows Terminal)", 2)
    para(doc, "Откройте терминал в папке проекта и выполните:")
    code(doc, ".\\scripts\\start.ps1")
    para(doc, "Скрипт сам проверит Docker, убедится, что порты свободны, поднимет сервисы, "
              "дождётся готовности приложения и напечатает адрес.")
    para(doc, "Если PowerShell откажется запускать файл — один раз разрешите его:")
    code(doc, "Unblock-File .\\scripts\\start.ps1")

    heading(doc, "Linux или macOS", 2)
    code(doc, "./scripts/start.sh")

    heading(doc, "Что вы увидите в конце", 2)
    code(
        doc,
        "  SOWSprint.ai\n"
        "    Web interface   http://localhost:8000\n"
        "    Qdrant          http://localhost:6333  (dashboard)\n"
        "    Logs            docker compose logs -f sowsprint-app",
    )
    para(doc, "Откройте http://localhost:8000 в браузере. Если вы на удалённом сервере — "
              "замените localhost на адрес сервера, либо пробросьте порт через VS Code.")

    heading(doc, "Полезные варианты", 2)
    bullet(doc, "Пересобрать после правок в коде (src/ копируется в образ):")
    code(doc, "./scripts/start.sh --build          Linux\n.\\scripts\\start.ps1 -Build       Windows")
    bullet(doc, "Не ждать готовности, вернуть управление сразу:")
    code(doc, "./scripts/start.sh --no-wait        Linux\n.\\scripts\\start.ps1 -NoWait      Windows")

    # ---------------------------------------------------------------- остановка
    heading(doc, "Сценарий 2. Остановка, когда переключаетесь на другой проект", 1)
    para(
        doc,
        "Это главное действие, если вы работаете с несколькими проектами на одной машине. "
        "Остановка занимает несколько секунд и не удаляет данные.",
    )
    code(doc, "./scripts/stop.sh                  Linux\n.\\scripts\\stop.ps1                Windows")
    para(doc, "Скрипт останавливает ТОЛЬКО контейнеры этого проекта. Чужие проекты не трогает "
              "и в конце сообщает, какие порты остались заняты и кем.")

    note(
        doc,
        "Индекс Qdrant и сгенерированные документы сохраняются. Следующий запуск занимает "
        "секунды, а не повторный разбор всей базы знаний.",
    )

    para(doc, "Если нужно освободить порты полностью:", bold=True)
    code(
        doc,
        "./scripts/stop.sh --all            Linux  (остановит и чужие контейнеры на этих портах)\n"
        ".\\scripts\\stop.ps1 -All            Windows",
    )
    note(
        doc,
        "Флаг --all останавливает контейнеры других проектов. Применяйте осознанно: это "
        "затронет чужой проект.",
    )

    # ---------------------------------------------------------------- статус
    heading(doc, "Сценарий 3. Проверить, что сейчас запущено", 1)
    para(doc, "Когда что-то не стартует — начните отсюда. Скрипт показывает состояние "
              "контейнеров проекта, кто занимает порты и какие ещё проекты работают на машине.")
    code(doc, "./scripts/status.sh               Linux\n.\\scripts\\status.ps1             Windows")
    para(doc, "Пример вывода, когда запущен dev-контейнер вместо рабочего стека:")
    code(
        doc,
        "  Ports\n"
        "    X 8000   sowsprint-dev  (project: sowsprint-dev)\n"
        "    X 6333   sowsprint-dev-qdrant  (project: sowsprint-dev)\n"
        "\n"
        "  Other projects running on this machine\n"
        "    sowsprint-dev            2 container(s)",
    )

    # ---------------------------------------------------------------- dev containers
    doc.add_page_break()
    heading(doc, "Сценарий 4. Работа с кодом: Dev Containers в VS Code", 1)
    para(
        doc,
        "Расширение Dev Containers (Microsoft) поднимает среду разработки в контейнере. "
        "Внутри уже стоят Python 3.11, pytest, ruff, git и ffmpeg — те же версии, что в "
        "рабочем образе. Это одинаково работает и на Windows-ноутбуке, и на Linux-сервере.",
    )

    heading(doc, "Как открыть", 2)
    numbered(doc, "Установите расширение Dev Containers (ms-vscode-remote.remote-containers).")
    numbered(doc, "Откройте папку проекта в VS Code.")
    numbered(doc, "Нажмите F1 и выберите: Dev Containers: Reopen in Container.")
    numbered(
        doc,
        "Первый раз займёт несколько минут — собирается образ. Дальше секунды.",
    )
    note(
        doc,
        "Файл .devcontainer/devcontainer.json уже лежит в корне проекта, готовить ничего не нужно.",
    )

    heading(doc, "Что доступно внутри", 2)
    bullet(doc, "Терминал VS Code работает внутри контейнера: pytest и ruff сразу в PATH.")
    bullet(doc, "Порты 8000 (приложение) и 6333 (панель Qdrant) проброшены автоматически.")
    bullet(doc, "Правки в файлах применяются сразу — рабочая папка подключена, а не скопирована.")

    para(doc, "Запустить приложение из терминала внутри контейнера:", bold=True)
    code(doc, "python -m chainlit run app.py --host 0.0.0.0 --port 8000")
    para(doc, "Прогнать тесты:", bold=True)
    code(doc, "pytest -q\nruff check src tests app.py")

    heading(doc, "Как закрыть", 2)
    bullet(doc, "Закройте окно VS Code — контейнер остановится сам.")
    bullet(
        doc,
        "Или явно: F1 → Dev Containers: Close Remote Connection.",
    )
    bullet(doc, "Или из терминала, если нужно наверняка:")
    code(doc, "docker compose -f .devcontainer/docker-compose.yml down")

    note(
        doc,
        "Настройка shutdownAction: stopCompose в devcontainer.json означает, что контейнер "
        "останавливается при закрытии окна. Без этого он держал бы порт 8000 после того, как "
        "вы переключились на другой проект.",
    )

    # ---------------------------------------------------------------- переключение
    heading(doc, "Переключение между проектами: порядок действий", 1)
    para(
        doc,
        "Чтобы сервисы разных проектов не мешали друг другу, придерживайтесь одного правила: "
        "перед переходом на другой проект остановите текущий.",
    )
    numbered(doc, "Закрыли VS Code с этим проектом — dev-контейнер остановился сам.")
    numbered(doc, "./scripts/stop.sh — остановили рабочий стек, если он был запущен.")
    numbered(doc, "./scripts/status.sh — убедились, что порты 8000, 6333 и 6334 свободны.")
    numbered(doc, "Открыли другой проект.")

    para(doc, "Одной командой всё сразу:", bold=True)
    code(doc, "./scripts/stop.sh && ./scripts/status.sh      Linux\n.\\scripts\\stop.ps1; .\\scripts\\status.ps1     Windows")

    # ---------------------------------------------------------------- диагностика
    doc.add_page_break()
    heading(doc, "Если что-то не работает", 1)

    table = doc.add_table(rows=1, cols=2)
    table.style = "Light Grid Accent 1"
    hdr = table.rows[0].cells
    hdr[0].text = "Сообщение"
    hdr[1].text = "Что делать"
    for symptom, action in (
        (
            "port 8000 is held by container 'X' of project 'Y'",
            "Порт занят другим проектом. Остановите его или выполните ./scripts/status.sh, "
            "чтобы увидеть полную картину.",
        ),
        (
            "the dev container is running — that is the VS Code window",
            "Запущен dev-контейнер. Закройте окно VS Code (Dev Containers: Close Remote "
            "Connection) или выполните docker compose -f .devcontainer/docker-compose.yml down.",
        ),
        (
            "Docker is installed but not responding",
            "Windows: запустите Docker Desktop и дождитесь, пока значок кита перестанет "
            "анимироваться. Linux: sudo systemctl start docker.",
        ),
        (
            "still 'starting' after 240s",
            "Посмотрите журнал: docker compose logs -f sowsprint-app. Чаще всего не хватает "
            "ключей API в файле .env.",
        ),
        (
            "Скрипт не запускается в PowerShell",
            "Выполните один раз: Unblock-File .\\scripts\\start.ps1",
        ),
        (
            "Файлы не сохраняются в VS Code",
            "Проблема с владельцем файлов. Расширение выравнивает UID автоматически "
            "(updateRemoteUserUID). Пересоберите контейнер: Dev Containers: Rebuild Container.",
        ),
    ):
        cells = table.add_row().cells
        cells[0].text = symptom
        cells[1].text = action

    heading(doc, "Где что лежит", 1)
    for path, purpose in (
        ("docker-compose.yml", "Рабочий стек: приложение и Qdrant. restart: \"no\" — не стартует сам."),
        ("scripts/start.sh, start.ps1", "Запуск с проверкой портов и ожиданием готовности."),
        ("scripts/stop.sh, stop.ps1", "Остановка только этого проекта."),
        ("scripts/status.sh, status.ps1", "Состояние контейнеров и портов."),
        ("scripts/_common.sh, _common.ps1", "Общая логика скриптов. Не запускается напрямую."),
        (".devcontainer/", "Настройка VS Code Dev Containers."),
        (".env", "Ключи API и настройки. В репозиторий не попадает."),
    ):
        p = doc.add_paragraph()
        run = p.add_run(path)
        run.font.name = "Consolas"
        run.font.size = Pt(10)
        p.add_run(" — " + purpose)

    doc.add_paragraph()
    note(
        doc,
        "Остановка не удаляет данные. Индекс Qdrant и сгенерированные документы остаются на "
        "месте. Полное удаление данных выполняется отдельно и осознанно: docker compose down -v",
    )

    target = OUT
    doc.save(target)
    return target


if __name__ == "__main__":
    path = build()
    size = path.stat().st_size
    print(f"  ✅ {path.name} — {size / 1024:.0f} KB")

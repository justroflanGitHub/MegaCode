# MegaCode для Astra Linux SE 1.7.6

MegaCode — рабочее пространство для нескольких сессий Claude Code в одном
окне: плитки с живыми терминалами, перетаскивание (drag-to-swap), ввод
в несколько панелей сразу, теговые группы синхронизации и AI-чат. Эта
версия работает на Astra Linux SE 1.7.6 «Смоленск» (обновление 1.7.6)
нативно, без Wine.

Исходники одни на обе платформы: на Astra используется системный
`python3` (3.7.3), терминальные панели живут в Unix PTY (`openpty`),
а связь окон — через Unix-сокет в `/run/user/<uid>` вместо именованного
канала Windows.

## Что проверялось под цель

| Компонент | Решение для Astra 1.7.6 |
|---|---|
| Python | системный `python3` 3.7.3 (база Debian 10, glibc 2.28) |
| Qt | `PySide6-Essentials==6.5.3` — последнее издание с колёсами под cp37; тег `manylinux_2_28` точно совпадает с glibc 2.28 |
| Эмулятор терминала | pyte 0.8.1 + собственный PTY-бэкенд на stdlib (`os.openpty`) |
| Связь окон | `QLocalServer/QLocalSocket` → AF_UNIX-сокет в `$XDG_RUNTIME_DIR`, выборка хаба через `QLockFile` |
| Безопасность связи | секрет HMAC в `~/.local/state/megacode/sync` (0600, каталог 0700), проверка challenge–response, отказ от линка при запуске от root и на сетевых ФС |
| Сборка (опционально) | PyInstaller 5.13.2 onedir в контейнере Debian 10 — тот же glibc, что и в Astra |

## Быстрая установка (онлайн)

```bash
# из каталога с исходниками MegaCode
./install-astra.sh
```

Установщик кладёт приложение в `~/.local/share/megacode`, лаунчер — в
`~/.local/bin/megacode`, пункт меню — в `~/.local/share/applications`
(меню Fly подхватит автоматически). Root не нужен.

Запуск: `megacode` или из меню приложений.

## Установка .deb-пакетом (рекомендуется)

Самодостаточный пакет: Python, Qt и все системные библиотеки уже внутри,
зависимостей нет (`libc6 >= 2.28` — это сама Astra 1.7.6). Подходит для
изолированных машин: `dpkg -i` работает без интернета.

```bash
sudo dpkg -i megacode_0.1.0-1_amd64.deb
megacode        # или ярлык «MegaCode» в меню Fly
```

Пакет ставит бандл в `/opt/MegaCode`, обёртку — в `/usr/bin/megacode`,
ярлык — в `/usr/share/applications/megacode.desktop`. Удаление —
`sudo dpkg -r megacode`. Обновление — снова `dpkg -i` поверх.

`fontconfig` намеренно НЕ вендорится в бандл (единственная зависимость
кроме libc): библиотека связана с конфигами хоста, и вендоренная версия
из Debian 10 безусловно сканирует `/usr/share/fontconfig/conf.avail`,
ругаясь на элементы свежих конфигов (например `<reset-dirs/>` на Astra).
Системная версия всегда соответствует своим конфигам и есть на любом
десктопе.

Сборка пакета — в докер-стенде (см. «Контейнер-стенд»): образ собирает и
каталог `MegaCode/`, и `megacode_<версия>_amd64.deb` (версия берётся из
`pyproject.toml`).

## Установка на изолированную машину (air-gap, из исходников)

На машине с интернетом (с тем же `pip`, версия Python не важна — качаем
колёса под целевую архитектуру):

```bash
mkdir wheels
pip download --only-binary :all: --platform manylinux_2_28_x86_64 \
    --python-version 37 --implementation cp --abi cp37m \
    -r requirements-astra.txt -d wheels
```

> Если `pip download` с этими флагами отклонит abi3-колесо PySide6,
> просто скачайте его вручную:
> `pip download PySide6-Essentials==6.5.3 --no-deps -d wheels` и то же
> для `shiboken6==6.5.3`, `pyte==0.8.1`.

Перенесите каталог с исходниками и `wheels/` на Astra (флешка/внутренний
канал), затем:

```bash
./install-astra.sh --offline /путь/к/wheels
```

## Системные пакеты Astra (при необходимости)

Установке из `.deb` этот раздел не нужен — системные библиотеки
(`libGL`, `libxcb-cursor`, `libxkbcommon` и остальные) ввендорены в
бандл. Ниже — про запуск из исходников: сам Qt приносит свои библиотеки
в колёсах, но от системы ему нужно немного; на полноценном рабочем столе
Fly это уже есть. Если при старте появится ошибка вида
`libGL.so.1: cannot open shared object file`:

```bash
sudo apt install libgl1 libegl1 libxkbcommon0 libgssapi-krb5-2 libxcb-cursor0
```

Глифы-иконки тулбара (⛓ ⛶ ＋) в базовом DejaVu отсутствуют — кнопки при
этом читаются по подписям, но красивее с одним из пакетов:

```bash
sudo apt install fonts-noto-color-emoji   # или fonts-symbola, если есть в репо
```

## Отличия от Windows-версии

* **Панели** запускают `bash` (или `$SHELL`), а не PowerShell/cmd; вид
  «PowerShell/Command Prompt» в настройках-остатках автоматически
  превращается в пользовательский шелл. Кнопка **＋ Add** добавляет `bash`
  (на Windows — `cmd`): тот же вид, что предвыбран в лаунчере; Claude Code —
  соседний пункт меню.
* **Правая кнопка мыши** на панели открывает меню (Copy / Paste /
  Clear selection); **средняя** вставляет последнее выделение (PRIMARY,
  стандарт X11): текст, выделенный в панели, сразу становится содержимым
  PRIMARY — его вставит СКМ и в соседней панели, и в других приложениях.
  Само выделение рисуется инверсией цветов ячеек и читаемо в любой теме.
  На Windows поведение прежнее — QuickEdit правым кликом.
  Внутри TUI с перехватом мыши (claude, htop) локальные клики доступны с
  зажатым **Shift** — как в xterm.
* **Остановка**: Ctrl+C в терминале запуска корректно закрывает приложение
  (первое нажатие — аккуратный выход с завершением детей PTY, второе —
  немедленный). Окно по крестику закрывается как обычно.
* **Цветовые схемы**: кнопка **◐ Theme** в тулбаре (Claude dark / Paper
  light / Marine night); выбор сохраняется между запусками и перекрашивает
  и интерфейс, и палитры всех терминальных панелей.
* **QPA-платформа**: на Linux приложение по умолчанию стартует на `xcb`
  (целевая среда — Fly/X11). Для иной сессии задайте `QT_QPA_PLATFORM`
  вручную — явное значение всегда главнее.
* **Новый TERMINAL для устаревшего режима** «Open as separate windows»:
  используется `xterm -geometry WxH+X+Y` (геометрия при запуске — без
  гонок с оконным менеджером). Нет xterm — кнопка скрывается:
  `sudo apt install xterm`. Прочие эмуляторы (qterminal, konsole,
  fly-terminal) запускаются и раскладываются через `wmctrl`/`xdotool`,
  если они установлены.
* **Клавиша прерывания**: Ctrl+C в панели — это SIGINT через линию
  дисциплины PTY, как в любом терминале Linux.
* **Перевод строк**: ConPTY-компенсация LNM на Linux отключена — bare LF
  сохраняет колонку (семантика xterm, как ждут nano/vim). Поведение
  проверяется тестами на обеих платформах.
* **Claude Code** (`claude`) ищется в `PATH`, затем в типичных местах
  (`~/.local/bin`, `~/.npm-global/bin`, nvm-каталоги) — сессия Fly часто
  экспортирует урезанный PATH. AI-чат работает через тот же CLI
  (stream-json), `--resume` подхватывает сессию после перезапуска.
* **Запуск от root отключает связывание окон** (как elevated на Windows):
  обычный сокет пользователя не должен управлять шеллами root. Обойти
  можно принудительно — чип ⛓ → «Link windows» (сохраняется в настройках),
  но рекомендуемый режим — работа от обычного пользователя.

## Тесты на Astra / Debian-10-машине

```bash
python3 -m pip install --user "pytest==7.4.4"
PYTHONPATH=src python3 -m pytest
```

Сьют работает без X-сервера (`QT_QPA_PLATFORM=offscreen` выставляется
тестами автоматически). Часть тестов PTY-бэкенда запускает реальные
процессы (`python3`, `cat`) — они помечены и пропускаются на Windows.

## Контейнер-стенд (разработка)

Воспроизвести целевую среду (Debian 10, python 3.7, glibc 2.28) на любой
машине с Docker:

```bash
docker build -t megacode/testbed-debian10 -f docker/debian10-base.Dockerfile .
docker run --rm -v "$PWD:/work" megacode/testbed-debian10 sh -c \
    'cd /work && python -m pytest'
```

Автономный бандл (onedir, без Python на целевой машине) и `.deb`-пакет:

```bash
docker build -t megacode/astra-build -f docker/Dockerfile.astra-build .
mkdir -p dist-linux
docker run --rm -v "$PWD/dist-linux:/out" megacode/astra-build
# -> dist-linux/MegaCode/                 (бандл каталогом)
# -> dist-linux/megacode_<версия>_amd64.deb
```

Для отладки xcb-специфики (предупреждения отрисовки, поведение плагинов
платформы) есть образ с настоящим X-сервером —
`docker/xvfb-testbed.Dockerfile` + `docker/xvfb-repro.sh`; offscreen- и
Windows-платформы такие проблемы не воспроизводят.

## Структура платформенного слоя

```
src/megacode/
  conpty.py         # Windows: Pty поверх pywinpty/ConPTY (LNM_WORKAROUND=True)
  unixpty.py        # Linux:   Pty поверх os.openpty (stdlib, LNM_WORKAROUND=False)
  win32_helpers.py  # Windows: pid/сессия/повышение/DACL + HWND-геометрия
  posix_helpers.py  # Linux:   pid/сессия/root/chmod + путь сокета + mountinfo
  plat_helpers.py   # фасад: выбирает реализацию по sys.platform
  terminal_posix.py # Linux-вариант режима отдельных окон (xterm/wmctrl)
```

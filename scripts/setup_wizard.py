#!/usr/bin/env python3
"""Interactive Site Panel installer wizard (Russian menus, stdlib only).

Usage:
  python scripts/setup_wizard.py              # interactive
  python scripts/setup_wizard.py --express    # recommended local defaults
  python scripts/setup_wizard.py --express --yes
"""

from __future__ import annotations

import argparse
import os
import platform
import shutil
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
IS_WINDOWS = platform.system() == "Windows"


@dataclass
class Checks:
    python_ok: bool
    python_version: str
    npm_ok: bool
    npm_version: str
    docker_installed: bool
    docker_running: bool


@dataclass
class Plan:
    action: str  # install | start | stop
    mode: str = "local"
    skip_tests: bool = True
    skip_seed: bool = True
    skip_start: bool = False


def c(text: str, color: str) -> str:
    # Windows cmd.exe often prints raw ANSI escapes (←[32m). Disable unless VT enabled.
    if os.environ.get("NO_COLOR"):
        return text
    if (
        sys.platform == "win32"
        and not os.environ.get("WT_SESSION")
        and not os.environ.get("ANSICON")
    ):
        return text
    if not sys.stdout.isatty():
        return text
    colors = {
        "green": "\033[32m",
        "cyan": "\033[36m",
        "yellow": "\033[33m",
        "red": "\033[31m",
        "bold": "\033[1m",
        "reset": "\033[0m",
    }
    return f"{colors.get(color, '')}{text}{colors['reset']}"


def say(msg: str = "", color: str | None = None) -> None:
    print(c(msg, color) if color else msg)


def ask(prompt: str, default: str | None = None) -> str:
    suffix = f" [{default}]" if default is not None else ""
    try:
        raw = input(f"{prompt}{suffix}: ").strip()
    except EOFError:
        return default or ""
    return raw if raw else (default or "")


def ask_choice(prompt: str, choices: dict[str, str], default: str) -> str:
    say(prompt, "cyan")
    for key, label in choices.items():
        mark = " ←" if key == default else ""
        say(f"  {key}) {label}{mark}")
    while True:
        val = ask("Ваш выбор", default)
        if val in choices:
            return val
        say("Неверный пункт. Введите номер из списка.", "yellow")


def ask_yes_no(prompt: str, default: bool = True) -> bool:
    d = "Y/n" if default else "y/N"
    while True:
        raw = ask(f"{prompt} ({d})", "y" if default else "n").lower()
        if raw in ("y", "yes", "д", "да"):
            return True
        if raw in ("n", "no", "н", "нет"):
            return False
        say("Ответьте y/n (да/нет).", "yellow")


def run_cmd(args: list[str], *, capture: bool = True) -> tuple[int, str]:
    try:
        p = subprocess.run(
            args,
            cwd=str(ROOT),
            capture_output=capture,
            text=True,
            encoding="utf-8",
            errors="replace",
            shell=False,
        )
        out = (p.stdout or "") + (p.stderr or "")
        return p.returncode, out.strip()
    except FileNotFoundError:
        return 127, ""
    except OSError as e:
        return 1, str(e)


def find_python() -> list[str] | None:
    """Return argv prefix for a Python 3.12+ interpreter."""
    candidates: list[list[str]] = []
    if IS_WINDOWS:
        candidates.extend([["py", "-3.12"], ["py", "-3"], ["python"]])
    candidates.extend([["python3.12"], ["python3"], ["python"]])
    # Prefer the interpreter running this script if already OK
    if sys.version_info >= (3, 12):
        return [sys.executable]
    for cmd in candidates:
        code, out = run_cmd(
            [
                *cmd,
                "-c",
                "import sys; print(f'{sys.version_info[0]}.{sys.version_info[1]}')",
            ]
        )
        if code != 0:
            continue
        try:
            parts = out.strip().split(".")
            major, minor = int(parts[0]), int(parts[1])
            if (major, minor) >= (3, 12):
                return cmd
        except (ValueError, IndexError):
            continue
    return None


def detect() -> Checks:
    py = find_python()
    py_ok = False
    py_ver = "не найден"
    if py:
        code, out = run_cmd([*py, "-c", "import sys; print(sys.version.split()[0])"])
        if code == 0:
            py_ok = True
            py_ver = out
            # verify >= 3.12
            code2, _ = run_cmd(
                [*py, "-c", "import sys; raise SystemExit(0 if sys.version_info >= (3, 12) else 1)"]
            )
            py_ok = code2 == 0
            if not py_ok:
                py_ver = f"{out} (нужен 3.12+)"

    npm_ok = shutil.which("npm") is not None
    npm_ver = ""
    if npm_ok:
        _, npm_ver = run_cmd(["npm", "--version"])

    docker_installed = shutil.which("docker") is not None
    docker_running = False
    if docker_installed:
        code, _ = run_cmd(["docker", "info"])
        docker_running = code == 0

    return Checks(py_ok, py_ver, npm_ok, npm_ver or "-", docker_installed, docker_running)


def print_banner() -> None:
    say("")
    say("=" * 56, "bold")
    say("  Site Panel — мастер установки", "bold")
    say("=" * 56, "bold")
    say(f"  Папка: {ROOT}")
    say(f"  ОС:    {platform.system()} {platform.release()}")
    say("")


def print_checks(ch: Checks) -> None:
    say("Проверка окружения", "cyan")

    def row(ok: bool, title: str, detail: str, hint: str = "") -> None:
        mark = c("OK", "green") if ok else c("НЕТ", "red")
        say(f"  [{mark}] {title}: {detail}")
        if not ok and hint:
            say(f"       → {hint}", "yellow")

    row(
        ch.python_ok,
        "Python 3.12+",
        ch.python_version,
        "Скачайте: https://www.python.org/downloads/  (галочка Add to PATH)",
    )
    row(
        ch.npm_ok,
        "Node.js / npm",
        ch.npm_version if ch.npm_ok else "не найден",
        "Скачайте LTS: https://nodejs.org/",
    )
    if ch.docker_installed and ch.docker_running:
        row(True, "Docker", "запущен")
    elif ch.docker_installed:
        row(False, "Docker", "установлен, но не запущен", "Запустите Docker Desktop и повторите")
    else:
        row(
            False,
            "Docker",
            "не установлен (желателен)",
            (
                "https://www.docker.com/products/docker-desktop/ — без него нужна "
                "своя БД Postgres+Redis"
            ),
        )
    say("")


def require_for_plan(plan: Plan, ch: Checks) -> list[str]:
    errors: list[str] = []
    if plan.action != "install":
        return errors
    if not ch.python_ok:
        errors.append("Нужен Python 3.12+.")
    if plan.mode in ("local", "deps") and not ch.npm_ok:
        errors.append("Нужен Node.js / npm для панели.")
    if plan.mode == "docker" and not ch.docker_running:
        errors.append("Для режима docker нужен запущенный Docker.")
    return errors


def summarize(plan: Plan) -> None:
    say("Сводка", "cyan")
    if plan.action == "start":
        say("  Действие: запустить уже установленное")
    elif plan.action == "stop":
        say("  Действие: остановить")
    else:
        say(f"  Режим:           {plan.mode}")
        say(f"  Демо-данные:     {'нет' if plan.skip_seed else 'явно включены для fixture'}")
        say(f"  Тесты:           {'нет' if plan.skip_tests else 'да'}")
        say(f"  Старт после:     {'нет' if plan.skip_start else 'да'}")
    say("")


def run_install(plan: Plan) -> int:
    if IS_WINDOWS:
        ps1 = SCRIPTS / "install.ps1"
        args = [
            "powershell",
            "-NoProfile",
            "-ExecutionPolicy",
            "Bypass",
            "-File",
            str(ps1),
            "-Mode",
            plan.mode,
        ]
        if plan.skip_tests:
            args.append("-SkipTests")
        if not plan.skip_seed:
            args.append("-DemoSeed")
        if plan.skip_start:
            args.append("-SkipStart")
    else:
        sh = SCRIPTS / "install.sh"
        args = ["bash", str(sh), "--mode", plan.mode]
        if plan.skip_tests:
            args.append("--skip-tests")
        if not plan.skip_seed:
            args.append("--demo")
        if plan.skip_start:
            args.append("--skip-start")
    say("Запуск установки…", "cyan")
    say("  " + " ".join(args))
    say("")
    return subprocess.call(args, cwd=str(ROOT))


def run_start() -> int:
    if IS_WINDOWS:
        return subprocess.call(
            [
                "powershell",
                "-NoProfile",
                "-ExecutionPolicy",
                "Bypass",
                "-File",
                str(SCRIPTS / "start.ps1"),
            ],
            cwd=str(ROOT),
        )
    return subprocess.call(["bash", str(SCRIPTS / "start.sh")], cwd=str(ROOT))


def run_stop() -> int:
    if IS_WINDOWS:
        return subprocess.call(
            [
                "powershell",
                "-NoProfile",
                "-ExecutionPolicy",
                "Bypass",
                "-File",
                str(SCRIPTS / "stop.ps1"),
            ],
            cwd=str(ROOT),
        )
    return subprocess.call(["bash", str(SCRIPTS / "stop.sh")], cwd=str(ROOT))


def print_done(plan: Plan) -> None:
    say("")
    say("Готово.", "green")
    if plan.action == "install":
        say("  Создайте оператора: python scripts/bootstrap_operator.py --email you@example.com")
        say("  После первого входа включите MFA в разделе «Настройки».")
    say("  Панель: http://127.0.0.1:5173")
    say("  API:    http://127.0.0.1:8000/docs")
    if IS_WINDOWS:
        say("  Старт:  .\\scripts\\start.ps1   |  Стоп: .\\scripts\\stop.ps1")
    else:
        say("  Старт:  ./scripts/start.sh     |  Стоп: ./scripts/stop.sh")
    say("")


def custom_plan(ch: Checks) -> Plan:
    mode_choices: dict[str, str] = {
        "1": "local — на этом компьютере (рекомендуется)",
        "2": "docker — всё в Docker",
        "3": "deps — только пакеты и .env (своя БД)",
    }

    mode_key = ask_choice("Выберите режим установки:", mode_choices, "1")
    mode_map = {"1": "local", "2": "docker", "3": "deps"}
    mode = mode_map[mode_key]

    seed_default = False
    do_seed = ask_yes_no("Создать demo fixtures для локального теста?", seed_default)
    do_tests = ask_yes_no("Прогнать автотесты (дольше)?", False)
    do_start = False
    if mode == "local":
        do_start = ask_yes_no("Запустить панель после установки?", True)
    elif mode == "docker":
        do_start = True

    return Plan(
        action="install",
        mode=mode,
        skip_tests=not do_tests,
        skip_seed=not do_seed,
        skip_start=not do_start if mode == "local" else False,
    )


def express_plan() -> Plan:
    return Plan(
        action="install",
        mode="local",
        skip_tests=True,
        skip_seed=True,
        skip_start=False,
    )


def interactive_menu(ch: Checks) -> Plan | None:
    while True:
        choice = ask_choice(
            "Главное меню:",
            {
                "1": "Экспресс-установка (рекомендуется) — local, без demo, без тестов, автостарт",
                "2": "Настроить самому",
                "3": "Только запустить (если уже ставили)",
                "4": "Остановить",
                "0": "Выход",
            },
            "1",
        )
        if choice == "0":
            return None
        if choice == "1":
            return express_plan()
        if choice == "2":
            return custom_plan(ch)
        if choice == "3":
            return Plan(action="start")
        if choice == "4":
            return Plan(action="stop")


def pause_if_needed(yes: bool) -> None:
    if yes:
        return
    if not sys.stdin.isatty():
        return
    try:
        input("Нажмите Enter, чтобы закрыть… ")
    except EOFError:
        pass


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Site Panel setup wizard")
    parser.add_argument("--express", action="store_true", help="Recommended local install")
    parser.add_argument("--yes", action="store_true", help="No confirmation / no final pause")
    parser.add_argument("--mode", choices=("local", "docker", "deps"))
    parser.add_argument("--skip-tests", action="store_true")
    parser.add_argument("--skip-seed", action="store_true")
    parser.add_argument("--skip-start", action="store_true")
    args = parser.parse_args(argv)

    os.chdir(ROOT)
    print_banner()
    ch = detect()
    print_checks(ch)

    if args.express:
        plan = express_plan()
    elif args.mode:
        plan = Plan(
            action="install",
            mode=args.mode,
            skip_tests=args.skip_tests,
            skip_seed=args.skip_seed,
            skip_start=args.skip_start,
        )
    else:
        plan = interactive_menu(ch)
        if plan is None:
            say("Выход.", "yellow")
            return 0

    summarize(plan)

    if plan.action == "install":
        errs = require_for_plan(plan, ch)
        if errs:
            for e in errs:
                say(f"Ошибка: {e}", "red")
            pause_if_needed(args.yes)
            return 1
        if not ch.docker_running and plan.mode == "local":
            say(
                "Внимание: Docker не запущен. Нужны Postgres :5432 и Redis :6379 на этом ПК.",
                "yellow",
            )
        if not args.yes:
            if not ask_yes_no("Продолжить установку?", True):
                say("Отменено.", "yellow")
                return 0
        code = run_install(plan)
        if code == 0:
            print_done(plan)
        else:
            say(f"Установка завершилась с кодом {code}. Смотрите сообщения выше.", "red")
            say("Подробности: README-LOCAL.md / README-VPS.md", "yellow")
        pause_if_needed(args.yes)
        return code

    if plan.action == "start":
        if not args.yes and not ask_yes_no("Запустить API, worker и panel?", True):
            return 0
        code = run_start()
        if code == 0:
            print_done(Plan(action="start"))
        pause_if_needed(args.yes)
        return code

    if plan.action == "stop":
        if not args.yes and not ask_yes_no("Остановить сервисы?", True):
            return 0
        code = run_stop()
        say("Остановлено." if code == 0 else f"Код выхода: {code}", "green" if code == 0 else "red")
        pause_if_needed(args.yes)
        return code

    return 0


if __name__ == "__main__":
    raise SystemExit(main())

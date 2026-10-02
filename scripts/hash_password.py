"""Generate the PASSWORD_HASH value for .env.

Usage:
    python scripts/hash_password.py
    docker compose run --rm atlas python scripts/hash_password.py

    # password for content-plan-only access (CONTENT_PASSWORD_HASH):
    docker compose run --rm atlas python scripts/hash_password.py content
"""

from __future__ import annotations

import getpass
import sys

from argon2 import PasswordHasher


def main() -> int:
    pw = getpass.getpass("Придумайте пароль: ")
    if len(pw) < 10:
        print("Пароль слишком короткий: нужно не меньше 10 символов.", file=sys.stderr)
        return 1
    if getpass.getpass("Повторите пароль: ") != pw:
        print("Пароли не совпадают.", file=sys.stderr)
        return 1
    h = PasswordHasher().hash(pw)
    print("\nСкопируйте эту строку в файл .env (целиком, вместе с кавычками):\n")
    name = "CONTENT_PASSWORD_HASH" if sys.argv[1:] == ["content"] else "PASSWORD_HASH"
    print(f"{name}='{h}'")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

"""Create a secret for the second login factor (authenticator app).

Usage:
    python scripts/totp_setup.py
    docker compose run --rm jarvis python scripts/totp_setup.py
"""

from __future__ import annotations

import base64
import secrets
import sys
from pathlib import Path
from urllib.parse import quote

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.security import totp_code  # noqa: E402


def main() -> int:
    secret = base64.b32encode(secrets.token_bytes(20)).decode().rstrip("=")
    uri = f"otpauth://totp/{quote('Jarvis')}?secret={secret}&issuer=Jarvis&digits=6&period=30"
    print("1. В приложении-аутентификаторе (Google Authenticator, 1Password, Яндекс Ключ…)")
    print("   добавьте аккаунт вручную, «ключ настройки»:\n")
    print("   " + " ".join(secret[i : i + 4] for i in range(0, len(secret), 4)))
    print("\n   (или ссылкой: " + uri + ")")
    print("\n2. Проверьте: приложение сейчас должно показывать код", totp_code(secret))
    print("\n3. Добавьте в .env строку и перезапустите Jarvis:\n")
    print(f"TOTP_SECRET={secret}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

"""Prepare private local Compose credentials without printing or replacing them."""

from os import open as open_fd
from pathlib import Path
from secrets import token_urlsafe


def private_file(path: str, flags: int) -> int:
    return open_fd(path, flags, 0o600)


def main() -> None:
    root = Path(__file__).resolve().parents[1]
    config = root / ".env.agent"
    try:
        with open(config, "x", encoding="utf-8", opener=private_file) as file:
            file.write(
                "FIELD_SHIFT_AGENT_URL=http://agent:8000\n"
                f"FIELD_SHIFT_AGENT_API_KEY={token_urlsafe(32)}\n"
                f"FIELD_SHIFT_AGENT_SIGNING_KEY={token_urlsafe(32)}\n"
            )
    except FileExistsError:
        print("Using existing .env.agent.")
    else:
        print("Created .env.agent.")

    base = ".env" if (root / ".env").is_file() else ".env.example"
    print(f"docker compose --env-file {base} --env-file .env.agent --profile agent up --build -d")


if __name__ == "__main__":
    main()

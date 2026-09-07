import sys

from assistant import get_assistant


def main() -> None:
    bot = get_assistant()
    print("Office AI Assistant. Type 'exit' or 'quit' to stop.")
    while True:
        try:
            user_input = input("\nYou: ").strip()
        except (EOFError, KeyboardInterrupt):
            break
        if user_input.lower() in ("exit", "quit"):
            break
        if not user_input:
            continue

        reply = bot.send(user_input)
        print(f"\nAssistant: {reply}")


if __name__ == "__main__":
    sys.exit(main() or 0)

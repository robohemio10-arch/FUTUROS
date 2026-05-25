from smartcrypto.bot import run_once
from smartcrypto.settings import RuntimeSettings


if __name__ == "__main__":
    run_once(RuntimeSettings.from_env())

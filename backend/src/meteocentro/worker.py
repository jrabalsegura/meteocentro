"""Separate worker entry point. Scheduling and adapters start in phase 2."""


def main() -> int:
    print("No ingestion jobs are configured in phase 1.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

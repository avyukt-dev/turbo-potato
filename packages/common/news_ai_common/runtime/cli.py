"""Legacy executable alias; the final operator boundary owns registry-based safety."""


def main(argv=None):
    # Late CLI-only dependency: low-level runtime primitives remain independent.
    from news_ai_runtime.cli import main as operator_main

    return operator_main(argv)


if __name__ == "__main__":
    raise SystemExit(main())

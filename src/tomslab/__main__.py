"""Allow `python -m tomslab` to launch the app."""
from tomslab.main import main

if __name__ == "__main__":
    raise SystemExit(main())

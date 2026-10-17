"""AutoKeeper: an automated Call of Cthulhu 7th edition keeper for Chinese tabletop play."""

__version__ = "0.1.0"


def main() -> None:
    from .cli import app

    app()

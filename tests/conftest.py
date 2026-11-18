"""Load .env (if python-dotenv is installed) so live-model tests find credentials."""
try:
    from dotenv import load_dotenv

    load_dotenv()
except ImportError:
    pass

"""
Centralised configuration.
All env vars are loaded here — no os.getenv() calls anywhere else in the codebase.
"""

import os
from dotenv import load_dotenv

load_dotenv()


class Settings:
    # ── OpenAI ────────────────────────────────────────────────────────────
    OPENAI_API_KEY: str = os.getenv("OPENAI_API_KEY", "")

    # ── Jira ──────────────────────────────────────────────────────────────
    JIRA_URL: str        = os.getenv("JIRA_URL", "").rstrip("/")
    JIRA_USER: str       = os.getenv("JIRA_USER", "")
    JIRA_API_TOKEN: str  = os.getenv("JIRA_API_TOKEN", "")
    JIRA_PROJECT_KEY: str = os.getenv("JIRA_PROJECT_KEY", "")

    # ── Zephyr Scale ──────────────────────────────────────────────────────
    ZEPHYR_API_TOKEN: str = os.getenv("ZEPHYR_API_TOKEN", "")
    ZEPHYR_BASE_URL: str  = os.getenv("ZEPHYR_BASE_URL",
                                       "https://api.zephyrscale.smartbear.com/v2")

    # ── Email / SMTP ──────────────────────────────────────────────────────
    EMAIL_SENDER: str    = os.getenv("EMAIL_SENDER", "")
    EMAIL_PASSWORD: str  = os.getenv("EMAIL_PASSWORD", "")
    SMTP_SERVER: str     = os.getenv("SMTP_SERVER", "smtp.gmail.com")
    SMTP_PORT: int       = int(os.getenv("SMTP_PORT", "587"))

    # ── Quality Gate ──────────────────────────────────────────────────────
    QG_THRESHOLD: int    = int(os.getenv("QG_THRESHOLD", "18"))

    def validate(self) -> list[str]:
        """Return a list of missing required variable names."""
        required = {
            "OPENAI_API_KEY": self.OPENAI_API_KEY,
            "JIRA_URL":        self.JIRA_URL,
            "JIRA_USER":       self.JIRA_USER,
            "JIRA_API_TOKEN":  self.JIRA_API_TOKEN,
            "ZEPHYR_API_TOKEN": self.ZEPHYR_API_TOKEN,
            "EMAIL_SENDER":    self.EMAIL_SENDER,
            "EMAIL_PASSWORD":  self.EMAIL_PASSWORD,
        }
        return [k for k, v in required.items() if not v]


settings = Settings()

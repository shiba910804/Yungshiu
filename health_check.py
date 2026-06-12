from __future__ import annotations

import importlib.util
import json
import sqlite3
import sys
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from pathlib import Path


BASE_DIR = Path(__file__).resolve().parent
DB_PATH = BASE_DIR / "data" / "scraper.sqlite3"
CONFIG_PATH = BASE_DIR / "config" / "funds.json"
EXPORT_DIR = BASE_DIR / "exports"
LOG_DIR = BASE_DIR / "logs"
STATIC_CHART_PATH = BASE_DIR / "static" / "vendor" / "chart.umd.min.js"

EXPECTED_TABLES = {
    "fund_nav",
    "fund_dividend",
    "exchange_rate",
    "historical_fx_rate",
}
EXPECTED_CURRENCIES = {"USD", "ZAR", "AUD", "EUR", "CAD", "JPY"}
EXPECTED_EXPORTS = [
    "fund_nav.csv",
    "fund_dividend.csv",
    "exchange_rate.csv",
    "historical_fx_rate.csv",
    "data_quality_report.md",
    "fund_quality_report.csv",
    "exchange_rate_latest.csv",
    "historical_fx_quality_report.csv",
]


@dataclass
class CheckResult:
    level: str
    title: str
    detail: str = ""


class HealthCheck:
    def __init__(self) -> None:
        self.results: list[CheckResult] = []

    def ok(self, title: str, detail: str = "") -> None:
        self.results.append(CheckResult("OK", title, detail))

    def warning(self, title: str, detail: str = "") -> None:
        self.results.append(CheckResult("WARNING", title, detail))

    def info(self, title: str, detail: str = "") -> None:
        self.results.append(CheckResult("INFO", title, detail))

    def error(self, title: str, detail: str = "") -> None:
        self.results.append(CheckResult("ERROR", title, detail))

    @property
    def has_error(self) -> bool:
        return any(result.level == "ERROR" for result in self.results)

    @property
    def has_warning(self) -> bool:
        return any(result.level == "WARNING" for result in self.results)


def load_funds(check: HealthCheck) -> list[dict]:
    if not CONFIG_PATH.exists():
        check.error("fund config missing", str(CONFIG_PATH))
        return []
    try:
        funds = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
    except Exception as exc:
        check.error("fund config unreadable", str(exc))
        return []
    if not isinstance(funds, list):
        check.error("fund config invalid", "config/funds.json must be a list")
        return []
    if len(funds) == 8:
        check.ok("fund config count", "8 funds configured")
    else:
        check.warning("fund config count", f"{len(funds)} funds configured; expected 8")
    return funds


def connect_db(check: HealthCheck) -> sqlite3.Connection | None:
    if not DB_PATH.exists():
        check.error("database missing", str(DB_PATH))
        return None
    try:
        conn = sqlite3.connect(DB_PATH)
        conn.row_factory = sqlite3.Row
    except Exception as exc:
        check.error("database open failed", str(exc))
        return None
    check.ok("database exists", str(DB_PATH))
    return conn


def table_exists(conn: sqlite3.Connection, table: str) -> bool:
    row = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",
        (table,),
    ).fetchone()
    return row is not None


def check_python_and_dependencies(check: HealthCheck) -> None:
    version = sys.version_info
    if version >= (3, 10):
        check.ok("python version", sys.version.split()[0])
    else:
        check.error("python version", f"{sys.version.split()[0]} found; expected 3.10+")

    if importlib.util.find_spec("flask"):
        check.ok("flask dependency", "installed")
    else:
        check.error("flask dependency", "run setup.ps1 or pip install -r requirements.txt")


def check_files(check: HealthCheck) -> None:
    for path in [BASE_DIR / "scraper.py", BASE_DIR / "app.py", BASE_DIR / "README.md"]:
        if path.exists():
            check.ok("required file", path.name)
        else:
            check.error("required file missing", str(path))

    for path in [EXPORT_DIR, LOG_DIR, BASE_DIR / "templates", BASE_DIR / "static"]:
        if path.exists():
            check.ok("required directory", path.name)
        else:
            check.warning("required directory missing", str(path))

    if STATIC_CHART_PATH.exists() and STATIC_CHART_PATH.stat().st_size > 100_000:
        check.ok("local Chart.js", str(STATIC_CHART_PATH))
    else:
        check.warning("local Chart.js missing", "run setup.ps1 to download dashboard chart asset")


def check_tables(check: HealthCheck, conn: sqlite3.Connection) -> None:
    missing = [table for table in EXPECTED_TABLES if not table_exists(conn, table)]
    if missing:
        check.error("database tables missing", ", ".join(sorted(missing)))
        return
    check.ok("database tables", ", ".join(sorted(EXPECTED_TABLES)))

    for table in sorted(EXPECTED_TABLES):
        count = conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
        if count:
            check.ok(f"{table} rows", f"{count:,}")
        else:
            check.error(f"{table} rows", "0 rows")


def check_fund_coverage(check: HealthCheck, conn: sqlite3.Connection, funds: list[dict]) -> None:
    if not table_exists(conn, "fund_nav"):
        return
    for fund in funds:
        fund_id = str(fund.get("id", "")).strip()
        fund_name = str(fund.get("name", fund_id))
        if not fund_id:
            check.error("fund id missing", fund_name)
            continue

        nav = conn.execute(
            """
            SELECT MIN(nav_date) AS start_date, MAX(nav_date) AS end_date, COUNT(*) AS row_count
            FROM fund_nav
            WHERE fund_id=?
            """,
            (fund_id,),
        ).fetchone()
        if not nav or not nav["row_count"]:
            check.error("fund NAV missing", f"{fund_id} {fund_name}")
            continue
        check.ok(
            "fund NAV coverage",
            f"{fund_id}: {nav['start_date']} to {nav['end_date']} ({nav['row_count']:,} rows)",
        )

        if table_exists(conn, "fund_dividend"):
            div_count = conn.execute(
                "SELECT COUNT(*) FROM fund_dividend WHERE fund_id=?",
                (fund_id,),
            ).fetchone()[0]
            if div_count:
                check.ok("fund dividend coverage", f"{fund_id}: {div_count:,} rows")
            else:
                check.info("fund dividend empty", f"{fund_id}: may be accumulating or non-distributing")


def check_exchange_rates(check: HealthCheck, conn: sqlite3.Connection) -> None:
    if not table_exists(conn, "exchange_rate"):
        return
    rows = conn.execute(
        "SELECT currency, MAX(fetched_at) AS latest_fetch FROM exchange_rate GROUP BY currency"
    ).fetchall()
    found = {row["currency"] for row in rows}
    missing = EXPECTED_CURRENCIES - found
    if missing:
        check.error("HSBC currencies missing", ", ".join(sorted(missing)))
    else:
        latest = max(row["latest_fetch"] for row in rows if row["latest_fetch"])
        check.ok("HSBC currencies", f"{len(found)} currencies; latest fetched_at {latest}")


def check_historical_fx(check: HealthCheck, conn: sqlite3.Connection) -> None:
    if not table_exists(conn, "historical_fx_rate"):
        return
    today = date.today()
    try:
        target_start = today.replace(year=today.year - 10)
    except ValueError:
        target_start = today.replace(year=today.year - 10, day=28)
    stale_cutoff = today - timedelta(days=14)

    rows = conn.execute(
        """
        SELECT currency, MIN(rate_date) AS start_date, MAX(rate_date) AS end_date, COUNT(*) AS row_count
        FROM historical_fx_rate
        GROUP BY currency
        """
    ).fetchall()
    found = {row["currency"] for row in rows}
    missing = EXPECTED_CURRENCIES - found
    if missing:
        check.error("historical FX currencies missing", ", ".join(sorted(missing)))

    for row in rows:
        currency = row["currency"]
        start_date = datetime.strptime(row["start_date"], "%Y-%m-%d").date()
        end_date = datetime.strptime(row["end_date"], "%Y-%m-%d").date()
        if start_date > target_start + timedelta(days=14):
            check.warning(
                "historical FX start date",
                f"{currency}: starts {start_date}, expected around {target_start}",
            )
        elif end_date < stale_cutoff:
            check.warning(
                "historical FX stale",
                f"{currency}: latest date {end_date}, expected within 14 days",
            )
        else:
            check.ok(
                "historical FX coverage",
                f"{currency}: {row['start_date']} to {row['end_date']} ({row['row_count']:,} rows)",
            )


def check_exports(check: HealthCheck) -> None:
    if not EXPORT_DIR.exists():
        check.warning("exports directory missing", str(EXPORT_DIR))
        return
    for filename in EXPECTED_EXPORTS:
        path = EXPORT_DIR / filename
        if not path.exists():
            check.warning("export missing", filename)
        elif path.stat().st_size == 0:
            check.warning("export empty", filename)
        else:
            check.ok("export file", f"{filename} ({path.stat().st_size:,} bytes)")


def print_results(check: HealthCheck) -> None:
    width = max(len(result.level) for result in check.results) if check.results else 2
    for result in check.results:
        detail = f" - {result.detail}" if result.detail else ""
        print(f"[{result.level:<{width}}] {result.title}{detail}")

    print()
    errors = sum(1 for result in check.results if result.level == "ERROR")
    warnings = sum(1 for result in check.results if result.level == "WARNING")
    infos = sum(1 for result in check.results if result.level == "INFO")
    oks = sum(1 for result in check.results if result.level == "OK")
    print(f"Summary: {oks} OK, {infos} INFO, {warnings} WARNING, {errors} ERROR")
    if errors:
        print("Status: FAILED")
    elif warnings:
        print("Status: OK WITH WARNINGS")
    else:
        print("Status: OK")


def main() -> int:
    check = HealthCheck()
    check_python_and_dependencies(check)
    check_files(check)
    funds = load_funds(check)
    conn = connect_db(check)
    if conn:
        try:
            check_tables(check, conn)
            check_fund_coverage(check, conn, funds)
            check_exchange_rates(check, conn)
            check_historical_fx(check, conn)
        finally:
            conn.close()
    check_exports(check)
    print_results(check)
    return 1 if check.has_error else 0


if __name__ == "__main__":
    raise SystemExit(main())

from __future__ import annotations

import argparse
import csv
import html
import json
import logging
import re
import sqlite3
import ssl
import time
from dataclasses import dataclass
from datetime import datetime, timedelta
from html.parser import HTMLParser
from pathlib import Path
from typing import Iterable
from urllib.parse import quote, urlencode
from urllib.request import Request, urlopen


BASE_DIR = Path(__file__).resolve().parent
CONFIG_PATH = BASE_DIR / "config" / "funds.json"
DATA_DIR = BASE_DIR / "data"
DB_PATH = DATA_DIR / "scraper.sqlite3"
EXPORT_DIR = BASE_DIR / "exports"
LOG_DIR = BASE_DIR / "logs"
LOG_PATH = LOG_DIR / "scraper.log"

HSBC_URL = "https://www.hsbc.com.tw/currency-rates/"
HSBC_CURRENCY_ORDER = ["USD", "ZAR", "AUD", "EUR", "CAD", "JPY"]
HSBC_CURRENCIES = set(HSBC_CURRENCY_ORDER)
FRANKFURTER_RATES_URL = "https://api.frankfurter.dev/v2/rates"

MONEYDJ_BASE = "https://www.moneydj.com"
FUNDCLEAR_BASE = "https://www.fundclear.com.tw"
USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
LOGGER = logging.getLogger("scraper")


class TableParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=False)
        self.tables: list[list[list[str]]] = []
        self._table_stack = 0
        self._current_table: list[list[str]] | None = None
        self._current_row: list[str] | None = None
        self._current_cell: list[str] | None = None

    def handle_starttag(self, tag: str, attrs) -> None:
        tag = tag.lower()
        if tag == "table":
            self._table_stack += 1
            if self._table_stack == 1:
                self._current_table = []
        elif self._table_stack and tag == "tr":
            self._current_row = []
        elif self._table_stack and tag in {"td", "th"}:
            self._current_cell = []
        elif self._current_cell is not None and tag == "br":
            self._current_cell.append(" ")

    def handle_endtag(self, tag: str) -> None:
        tag = tag.lower()
        if tag in {"td", "th"} and self._current_cell is not None:
            if self._current_row is not None:
                text = clean_text("".join(self._current_cell))
                self._current_row.append(text)
            self._current_cell = None
        elif tag == "tr" and self._current_row is not None:
            if self._current_table is not None and any(self._current_row):
                self._current_table.append(self._current_row)
            self._current_row = None
        elif tag == "table" and self._table_stack:
            if self._table_stack == 1 and self._current_table is not None:
                self.tables.append(self._current_table)
            self._table_stack -= 1
            if self._table_stack == 0:
                self._current_table = None

    def handle_data(self, data: str) -> None:
        if self._current_cell is not None:
            self._current_cell.append(data)

    def handle_entityref(self, name: str) -> None:
        if self._current_cell is not None:
            self._current_cell.append(html.unescape(f"&{name};"))

    def handle_charref(self, name: str) -> None:
        if self._current_cell is not None:
            self._current_cell.append(html.unescape(f"&#{name};"))


@dataclass
class FetchResult:
    url: str
    text: str


def clean_text(value: str) -> str:
    value = html.unescape(value)
    value = re.sub(r"\s+", " ", value)
    return value.strip()


def parse_decimal(value: str) -> float | None:
    value = clean_text(value).replace(",", "")
    if not value or value == "-":
        return None
    match = re.search(r"-?\d+(?:\.\d+)?", value)
    return float(match.group(0)) if match else None


def parse_date(value: str, default_year: int | None = None) -> str | None:
    value = clean_text(value)
    match = re.search(r"(\d{4})[/-](\d{1,2})[/-](\d{1,2})", value)
    if match:
        year, month, day = map(int, match.groups())
        try:
            return datetime(year, month, day).date().isoformat()
        except ValueError:
            return None

    match = re.search(r"(\d{1,2})[/-](\d{1,2})", value)
    if match and default_year:
        month, day = map(int, match.groups())
        try:
            return datetime(default_year, month, day).date().isoformat()
        except ValueError:
            return None

    return None


def fetch(url: str, encoding: str = "utf-8", verify_tls: bool = True) -> FetchResult:
    ctx = None if verify_tls else ssl._create_unverified_context()
    req = Request(url, headers={"User-Agent": USER_AGENT})
    with urlopen(req, timeout=30, context=ctx) as response:
        data = response.read()
    return FetchResult(url=url, text=data.decode(encoding, errors="ignore"))


def post_json(url: str, payload: dict, verify_tls: bool = False) -> dict:
    ctx = None if verify_tls else ssl._create_unverified_context()
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    req = Request(
        url,
        data=body,
        headers={
            "User-Agent": USER_AGENT,
            "Accept": "application/json",
            "Content-Type": "application/json",
            "Origin": FUNDCLEAR_BASE,
            "Referer": f"{FUNDCLEAR_BASE}/",
        },
        method="POST",
    )
    with urlopen(req, timeout=60, context=ctx) as response:
        data = response.read()
    return json.loads(data.decode("utf-8", errors="ignore"))


def parse_tables(text: str) -> list[list[list[str]]]:
    parser = TableParser()
    parser.feed(text)
    return parser.tables


def init_db(db_path: Path = DB_PATH) -> sqlite3.Connection:
    DATA_DIR.mkdir(exist_ok=True)
    conn = sqlite3.connect(db_path)
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS fund_nav (
            fund_id TEXT NOT NULL,
            fund_name TEXT NOT NULL,
            nav_date TEXT NOT NULL,
            nav REAL,
            source TEXT NOT NULL,
            fetched_at TEXT NOT NULL,
            PRIMARY KEY (fund_id, nav_date, source)
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS fund_dividend (
            fund_id TEXT NOT NULL,
            fund_name TEXT NOT NULL,
            dividend_date TEXT NOT NULL,
            per_unit_dividend REAL,
            source TEXT NOT NULL,
            fetched_at TEXT NOT NULL,
            raw_row TEXT NOT NULL,
            PRIMARY KEY (fund_id, dividend_date, source, raw_row)
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS exchange_rate (
            currency TEXT NOT NULL,
            rate_timestamp TEXT NOT NULL,
            spot_buy REAL,
            spot_sell REAL,
            cash_buy REAL,
            cash_sell REAL,
            source TEXT NOT NULL,
            fetched_at TEXT NOT NULL,
            PRIMARY KEY (currency, rate_timestamp, source)
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS exchange_rate_snapshot (
            currency TEXT NOT NULL,
            fetched_at TEXT NOT NULL,
            rate_timestamp TEXT NOT NULL,
            spot_buy REAL,
            spot_sell REAL,
            cash_buy REAL,
            cash_sell REAL,
            source TEXT NOT NULL,
            PRIMARY KEY (currency, fetched_at, source)
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS historical_fx_rate (
            currency TEXT NOT NULL,
            rate_date TEXT NOT NULL,
            twd_per_unit REAL NOT NULL,
            base_currency TEXT NOT NULL,
            quote_currency TEXT NOT NULL,
            source TEXT NOT NULL,
            fetched_at TEXT NOT NULL,
            PRIMARY KEY (currency, rate_date)
        )
        """
    )
    conn.execute(
        """
        INSERT OR IGNORE INTO exchange_rate_snapshot
        (currency, fetched_at, rate_timestamp, spot_buy, spot_sell, cash_buy, cash_sell, source)
        SELECT currency, fetched_at, rate_timestamp, spot_buy, spot_sell, cash_buy, cash_sell, source
        FROM exchange_rate
        """
    )
    conn.commit()
    return conn


def load_funds(path: Path = CONFIG_PATH) -> list[dict]:
    return json.loads(path.read_text(encoding="utf-8"))


def setup_logging() -> None:
    LOG_DIR.mkdir(exist_ok=True)
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
        handlers=[
            logging.FileHandler(LOG_PATH, encoding="utf-8"),
            logging.StreamHandler(),
        ],
    )


def upsert_many(conn: sqlite3.Connection, sql: str, rows: Iterable[tuple]) -> int:
    rows = list(rows)
    if rows:
        conn.executemany(sql, rows)
        conn.commit()
    return len(rows)


def scrape_hsbc_rates(conn: sqlite3.Connection) -> int:
    result = fetch(HSBC_URL)
    fetched_at = datetime.now().isoformat(timespec="seconds")
    tables = parse_tables(result.text)

    update_match = re.search(r"匯率更新日期為:\s*&nbsp;?\s*([^<\r\n]+)", result.text)
    if not update_match:
        update_match = re.search(r"匯率更新日期為:\s*([^<\r\n]+)", clean_text(result.text))
    rate_timestamp = clean_text(update_match.group(1)) if update_match else fetched_at

    rows = []
    for table in tables:
        if not table or not any("即期匯率" in " ".join(row) for row in table[:2]):
            continue
        for row in table[1:]:
            if len(row) < 5:
                continue
            currency_match = re.search(r"\(([A-Z]{3})\)", row[0])
            if not currency_match:
                continue
            currency = currency_match.group(1)
            if currency not in HSBC_CURRENCIES:
                continue
            rows.append(
                (
                    currency,
                    rate_timestamp,
                    parse_decimal(row[1]),
                    parse_decimal(row[2]),
                    parse_decimal(row[3]),
                    parse_decimal(row[4]),
                    HSBC_URL,
                    fetched_at,
                )
            )
        if rows:
            break

    latest_count = upsert_many(
        conn,
        """
        INSERT OR REPLACE INTO exchange_rate
        (currency, rate_timestamp, spot_buy, spot_sell, cash_buy, cash_sell, source, fetched_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """,
        rows,
    )
    snapshot_rows = [
        (
            currency,
            fetched_at,
            rate_timestamp,
            spot_buy,
            spot_sell,
            cash_buy,
            cash_sell,
            source,
        )
        for (
            currency,
            rate_timestamp,
            spot_buy,
            spot_sell,
            cash_buy,
            cash_sell,
            source,
            fetched_at,
        ) in rows
    ]
    upsert_many(
        conn,
        """
        INSERT OR REPLACE INTO exchange_rate_snapshot
        (currency, fetched_at, rate_timestamp, spot_buy, spot_sell, cash_buy, cash_sell, source)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """,
        snapshot_rows,
    )
    return latest_count


def frankfurter_history_url(history_years: int | None = 10, history_days: int | None = None) -> str:
    if history_days is not None:
        start_date = (datetime.now().date() - timedelta(days=history_days)).isoformat()
    elif history_years is not None:
        start_date = history_start_iso(history_years)
    else:
        start_date = history_start_iso(10)

    params = {
        "base": "TWD",
        "quotes": ",".join(HSBC_CURRENCY_ORDER),
        "from": start_date,
        "to": datetime.now().date().isoformat(),
    }
    return f"{FRANKFURTER_RATES_URL}?{urlencode(params, safe=',')}"


def scrape_historical_fx_rates(
    conn: sqlite3.Connection,
    history_years: int | None = 10,
    history_days: int | None = None,
) -> int:
    url = frankfurter_history_url(history_years=history_years, history_days=history_days)
    result = fetch(url)
    data = json.loads(result.text)
    if not isinstance(data, list):
        raise ValueError("Unexpected Frankfurter response format")

    fetched_at = datetime.now().isoformat(timespec="seconds")
    rows = []
    for item in data:
        if not isinstance(item, dict):
            continue
        rate_date = parse_date(str(item.get("date", "")))
        base = str(item.get("base", ""))
        quote = str(item.get("quote", ""))
        rate = parse_decimal(str(item.get("rate", "")))
        if base != "TWD" or quote not in HSBC_CURRENCIES or not rate_date or not rate:
            continue

        rows.append(
            (
                quote,
                rate_date,
                round(1 / rate, 10),
                quote,
                "TWD",
                result.url,
                fetched_at,
            )
        )

    return upsert_many(
        conn,
        """
        INSERT OR REPLACE INTO historical_fx_rate
        (currency, rate_date, twd_per_unit, base_currency, quote_currency, source, fetched_at)
        VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        rows,
    )


def moneydj_url(path: str, fund_id: str) -> str:
    return f"{MONEYDJ_BASE}{path}?a={quote(fund_id)}"


def history_start_date(years: int) -> str:
    today = datetime.now().date()
    try:
        start = today.replace(year=today.year - years)
    except ValueError:
        start = today.replace(year=today.year - years, day=28)
    return start.strftime("%Y/%m/%d")


def history_start_iso(years: int) -> str:
    return history_start_date(years).replace("/", "-")


def fundclear_url(path: str) -> str:
    return f"{FUNDCLEAR_BASE}{path}"


def scrape_fundclear_nav(conn: sqlite3.Connection, fund: dict, history_years: int = 10) -> int:
    info = fund.get("fundclear") or {}
    kind = info.get("kind")
    if kind not in {"onshore", "offshore"}:
        return 0

    path = f"/api/{kind}/nav-profit/query-history"
    url = fundclear_url(path)
    if kind == "onshore":
        payload = {
            "orgId": info["org_id"],
            "fundNo": info["fund_id"],
            "fundClassCode": info["class_id"],
            "startDate": history_start_date(history_years),
            "endDate": datetime.now().strftime("%Y/%m/%d"),
        }
    else:
        payload = {
            "organizeCode": info["org_id"],
            "fundCode": info["fund_id"],
            "fundClassCode": info["class_id"],
            "startDate": history_start_date(history_years),
            "endDate": datetime.now().strftime("%Y/%m/%d"),
        }

    data = post_json(url, payload)
    fetched_at = datetime.now().isoformat(timespec="seconds")
    rows = []
    for item in data.get("tableList", []):
        nav_date = parse_date(str(item.get("navTxnDate", "")))
        nav = parse_decimal(str(item.get("navValue", "")))
        if nav_date and nav is not None:
            rows.append((fund["id"], fund["name"], nav_date, nav, url, fetched_at))

    return upsert_many(
        conn,
        """
        INSERT OR REPLACE INTO fund_nav
        (fund_id, fund_name, nav_date, nav, source, fetched_at)
        VALUES (?, ?, ?, ?, ?, ?)
        """,
        rows,
    )


def scrape_fundclear_dividends(conn: sqlite3.Connection, fund: dict, history_years: int = 10) -> int:
    info = fund.get("fundclear") or {}
    if info.get("kind") != "offshore":
        return 0

    path = "/api/offshore/fund-info/info-dividend/query"
    url = fundclear_url(path)
    start = history_start_date(history_years)
    end = datetime.now()
    payload = {
        "_pageNum": 1,
        "_pageSize": 500,
        "queryType": "1",
        "searchName": "",
        "organizeCode": info["org_id"],
        "fundCode": info["fund_id"],
        "fundClassCode": info["class_id"],
        "asiFreqList": [],
        "baseBeginDate": start[:7],
        "baseEndDate": end.strftime("%Y/%m"),
    }

    data = post_json(url, payload)
    fetched_at = datetime.now().isoformat(timespec="seconds")
    rows = []
    for item in data.get("list", []):
        dividend_date = parse_date(str(item.get("asiBaseDate", "")))
        dividend = parse_decimal(str(item.get("asiAmt", "")))
        if dividend_date:
            rows.append(
                (
                    fund["id"],
                    fund["name"],
                    dividend_date,
                    dividend,
                    url,
                    fetched_at,
                    json.dumps(item, ensure_ascii=False),
                )
            )

    return upsert_many(
        conn,
        """
        INSERT OR REPLACE INTO fund_dividend
        (fund_id, fund_name, dividend_date, per_unit_dividend, source, fetched_at, raw_row)
        VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        rows,
    )


def scrape_moneydj_nav(conn: sqlite3.Connection, fund: dict) -> int:
    fund_id = fund.get("id", "").strip()
    if not fund_id:
        print(f"[skip] {fund['name']}: missing MoneyDJ fund id")
        return 0

    path = "/funddj/ya/yp010001.djhtm" if fund.get("moneydj_kind") == "offshore" else "/funddj/ya/yp010000.djhtm"
    result = fetch(moneydj_url(path, fund_id), encoding="big5", verify_tls=False)
    tables = parse_tables(result.text)
    fetched_at = datetime.now().isoformat(timespec="seconds")
    current_year = datetime.now().year

    rows = []
    for table in tables:
        header = " ".join(table[0]) if table else ""
        if "淨值日期" in header and "最新淨值" in header and len(table) > 1:
            values = table[1]
            nav_date = parse_date(values[0], current_year) if values else None
            nav = parse_decimal(values[1]) if len(values) > 1 else None
            if nav_date and nav is not None:
                rows.append((fund_id, fund["name"], nav_date, nav, result.url, fetched_at))
            continue

        for row in table:
            if len(row) < 2:
                continue
            nav_date = parse_date(row[0], current_year)
            nav = parse_decimal(row[1])
            if nav_date and nav is not None:
                rows.append((fund_id, fund["name"], nav_date, nav, result.url, fetched_at))

    deduped = {(r[0], r[2], r[4]): r for r in rows}.values()
    return upsert_many(
        conn,
        """
        INSERT OR REPLACE INTO fund_nav
        (fund_id, fund_name, nav_date, nav, source, fetched_at)
        VALUES (?, ?, ?, ?, ?, ?)
        """,
        deduped,
    )


def scrape_moneydj_dividends(conn: sqlite3.Connection, fund: dict) -> int:
    fund_id = fund.get("id", "").strip()
    if not fund_id:
        return 0

    dividend_path = "/funddj/yp/wb05.djhtm" if fund.get("moneydj_kind") == "offshore" else "/funddj/yp/funddividend.djhtm"
    result = fetch(moneydj_url(dividend_path, fund_id), encoding="big5", verify_tls=False)
    tables = parse_tables(result.text)
    fetched_at = datetime.now().isoformat(timespec="seconds")
    rows = []

    for table in tables:
        if len(table) < 2:
            continue
        header = table[0]
        joined_header = " ".join(header)
        if not any(token in joined_header for token in ["配息", "除息", "收益分配"]):
            continue

        dividend_idx = next((i for i, col in enumerate(header) if "每單位" in col), None)
        if dividend_idx is None:
            dividend_idx = next(
                (
                    i
                    for i, col in enumerate(header)
                    if ("配息" in col or "分配" in col) and not col.endswith("日") and "率" not in col
                ),
                None,
            )
        for row in table[1:]:
            date_value = next((parse_date(cell) for cell in row if parse_date(cell)), None)
            if not date_value:
                continue
            dividend = parse_decimal(row[dividend_idx]) if dividend_idx is not None and dividend_idx < len(row) else None
            if dividend is None:
                numeric_cells = [parse_decimal(cell) for cell in row]
                numeric_cells = [value for value in numeric_cells if value is not None]
                dividend = numeric_cells[-1] if numeric_cells else None
            rows.append(
                (
                    fund_id,
                    fund["name"],
                    date_value,
                    dividend,
                    result.url,
                    fetched_at,
                    json.dumps(row, ensure_ascii=False),
                )
            )

    return upsert_many(
        conn,
        """
        INSERT OR REPLACE INTO fund_dividend
        (fund_id, fund_name, dividend_date, per_unit_dividend, source, fetched_at, raw_row)
        VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        rows,
    )


def scrape_funds(conn: sqlite3.Connection, history_years: int = 10) -> tuple[int, int]:
    nav_count = 0
    dividend_count = 0
    for fund in load_funds():
        if fund.get("fundclear"):
            try:
                nav_count += scrape_fundclear_nav(conn, fund, history_years=history_years)
            except Exception as exc:
                LOGGER.warning("FundClear NAV failed for %s; using MoneyDJ fallback: %s", fund["name"], exc)
                print(f"[fallback] FundClear NAV failed for {fund['name']}: {exc}")
                nav_count += scrape_moneydj_nav(conn, fund)
        else:
            nav_count += scrape_moneydj_nav(conn, fund)

        if (fund.get("fundclear") or {}).get("kind") == "offshore":
            try:
                dividend_count += scrape_fundclear_dividends(conn, fund, history_years=history_years)
            except Exception as exc:
                LOGGER.warning(
                    "FundClear dividend failed for %s; using MoneyDJ fallback: %s",
                    fund["name"],
                    exc,
                )
                print(f"[fallback] FundClear dividend failed for {fund['name']}: {exc}")
                dividend_count += scrape_moneydj_dividends(conn, fund)
        else:
            dividend_count += scrape_moneydj_dividends(conn, fund)
    return nav_count, dividend_count


def export_table(conn: sqlite3.Connection, table: str) -> Path:
    EXPORT_DIR.mkdir(exist_ok=True)
    output = EXPORT_DIR / f"{table}.csv"
    order_by = {
        "exchange_rate": "currency, fetched_at, rate_timestamp",
        "fund_nav": "fund_id, nav_date",
        "fund_dividend": "fund_id, dividend_date",
        "historical_fx_rate": "currency, rate_date",
    }.get(table)
    sql = f"SELECT * FROM {table}"
    if order_by:
        sql = f"{sql} ORDER BY {order_by}"
    cursor = conn.execute(sql)
    headers = [description[0] for description in cursor.description]
    with output.open("w", encoding="utf-8-sig", newline="") as fh:
        writer = csv.writer(fh)
        writer.writerow(headers)
        writer.writerows(cursor.fetchall())
    return output


def generate_fund_quality_report(conn: sqlite3.Connection) -> tuple[Path, list[dict]]:
    EXPORT_DIR.mkdir(exist_ok=True)
    rows = []
    for fund in load_funds():
        nav = conn.execute(
            """
            SELECT MIN(nav_date), MAX(nav_date), COUNT(*)
            FROM fund_nav
            WHERE fund_id = ?
            """,
            (fund["id"],),
        ).fetchone()
        dividend = conn.execute(
            """
            SELECT MIN(dividend_date), MAX(dividend_date), COUNT(*)
            FROM fund_dividend
            WHERE fund_id = ?
            """,
            (fund["id"],),
        ).fetchone()

        nav_start, nav_end, nav_rows = nav
        dividend_start, dividend_end, dividend_rows = dividend
        status = "OK" if nav_rows else "MISSING_NAV"
        note = ""
        if not dividend_rows:
            note = "No dividend rows found. This may be normal for accumulating or non-distributing funds."

        rows.append(
            {
                "fund_id": fund["id"],
                "fund_name": fund["name"],
                "nav_start": nav_start or "",
                "nav_end": nav_end or "",
                "nav_rows": nav_rows,
                "dividend_start": dividend_start or "",
                "dividend_end": dividend_end or "",
                "dividend_rows": dividend_rows,
                "status": status,
                "note": note,
            }
        )

    output = EXPORT_DIR / "fund_quality_report.csv"
    with output.open("w", encoding="utf-8-sig", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=list(rows[0].keys()))
        writer.writeheader()
        writer.writerows(rows)
    return output, rows


def generate_exchange_rate_latest_report(conn: sqlite3.Connection) -> tuple[Path, list[dict]]:
    EXPORT_DIR.mkdir(exist_ok=True)
    rows = []
    for currency in HSBC_CURRENCY_ORDER:
        latest = conn.execute(
            """
            SELECT currency, rate_timestamp, spot_buy, spot_sell, cash_buy, cash_sell, source, fetched_at
            FROM exchange_rate
            WHERE currency = ?
            ORDER BY fetched_at DESC, rate_timestamp DESC
            LIMIT 1
            """,
            (currency,),
        ).fetchone()
        if latest:
            rows.append(
                {
                    "currency": latest[0],
                    "rate_timestamp": latest[1],
                    "spot_buy": latest[2],
                    "spot_sell": latest[3],
                    "cash_buy": latest[4] if latest[4] is not None else "",
                    "cash_sell": latest[5] if latest[5] is not None else "",
                    "source": latest[6],
                    "fetched_at": latest[7],
                    "status": "OK",
                }
            )
        else:
            rows.append(
                {
                    "currency": currency,
                    "rate_timestamp": "",
                    "spot_buy": "",
                    "spot_sell": "",
                    "cash_buy": "",
                    "cash_sell": "",
                    "source": HSBC_URL,
                    "fetched_at": "",
                    "status": "MISSING",
                }
            )

    output = EXPORT_DIR / "exchange_rate_latest.csv"
    with output.open("w", encoding="utf-8-sig", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=list(rows[0].keys()))
        writer.writeheader()
        writer.writerows(rows)
    return output, rows


def generate_historical_fx_quality_report(conn: sqlite3.Connection) -> tuple[Path, list[dict]]:
    EXPORT_DIR.mkdir(exist_ok=True)
    rows = []
    for currency in HSBC_CURRENCY_ORDER:
        summary = conn.execute(
            """
            SELECT MIN(rate_date), MAX(rate_date), COUNT(*), MAX(fetched_at)
            FROM historical_fx_rate
            WHERE currency = ?
            """,
            (currency,),
        ).fetchone()
        start_date, end_date, row_count, last_fetched_at = summary
        rows.append(
            {
                "currency": currency,
                "rate_start": start_date or "",
                "rate_end": end_date or "",
                "rows": row_count,
                "last_fetched_at": last_fetched_at or "",
                "status": "OK" if row_count else "MISSING",
            }
        )

    output = EXPORT_DIR / "historical_fx_quality_report.csv"
    with output.open("w", encoding="utf-8-sig", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=list(rows[0].keys()))
        writer.writeheader()
        writer.writerows(rows)
    return output, rows


def generate_data_quality_report(conn: sqlite3.Connection) -> list[Path]:
    fund_path, fund_rows = generate_fund_quality_report(conn)
    rate_path, rate_rows = generate_exchange_rate_latest_report(conn)
    historical_fx_path, historical_fx_rows = generate_historical_fx_quality_report(conn)

    nav_count = conn.execute("SELECT COUNT(*) FROM fund_nav").fetchone()[0]
    dividend_count = conn.execute("SELECT COUNT(*) FROM fund_dividend").fetchone()[0]
    exchange_count = conn.execute("SELECT COUNT(*) FROM exchange_rate").fetchone()[0]
    historical_fx_count = conn.execute("SELECT COUNT(*) FROM historical_fx_rate").fetchone()[0]
    latest_fetch = conn.execute(
        """
        SELECT MAX(fetched_at)
        FROM (
            SELECT fetched_at FROM fund_nav
            UNION ALL
            SELECT fetched_at FROM fund_dividend
            UNION ALL
            SELECT fetched_at FROM exchange_rate
        )
        """
    ).fetchone()[0]

    md_path = EXPORT_DIR / "data_quality_report.md"
    lines = [
        "# Data Quality Report",
        "",
        f"Generated at: {datetime.now().isoformat(timespec='seconds')}",
        f"Latest fetched_at in database: {latest_fetch or ''}",
        "",
        "## Summary",
        "",
        f"- Fund NAV rows: {nav_count}",
        f"- Fund dividend rows: {dividend_count}",
        f"- Exchange rate rows: {exchange_count}",
        f"- Historical FX rows: {historical_fx_count}",
        f"- Expected funds: {len(fund_rows)}",
        f"- Expected currencies: {len(HSBC_CURRENCY_ORDER)}",
        "",
        "## Fund Coverage",
        "",
        "| Fund ID | Fund Name | NAV Range | NAV Rows | Dividend Range | Dividend Rows | Status | Note |",
        "|---|---|---:|---:|---:|---:|---|---|",
    ]
    for row in fund_rows:
        nav_range = f"{row['nav_start']} ~ {row['nav_end']}" if row["nav_start"] else ""
        dividend_range = (
            f"{row['dividend_start']} ~ {row['dividend_end']}" if row["dividend_start"] else ""
        )
        lines.append(
            "| {fund_id} | {fund_name} | {nav_range} | {nav_rows} | {dividend_range} | "
            "{dividend_rows} | {status} | {note} |".format(
                **row,
                nav_range=nav_range,
                dividend_range=dividend_range,
            )
        )

    lines.extend(
        [
            "",
            "## Latest HSBC Exchange Rates",
            "",
            "| Currency | Rate Time | Spot Buy | Spot Sell | Cash Buy | Cash Sell | Fetched At | Status |",
            "|---|---|---:|---:|---:|---:|---|---|",
        ]
    )
    for row in rate_rows:
        lines.append(
            "| {currency} | {rate_timestamp} | {spot_buy} | {spot_sell} | {cash_buy} | "
            "{cash_sell} | {fetched_at} | {status} |".format(**row)
        )

    lines.extend(
        [
            "",
            "## Historical FX Coverage",
            "",
            "| Currency | Rate Range | Rows | Last Fetched At | Status |",
            "|---|---:|---:|---|---|",
        ]
    )
    for row in historical_fx_rows:
        rate_range = f"{row['rate_start']} ~ {row['rate_end']}" if row["rate_start"] else ""
        lines.append(
            "| {currency} | {rate_range} | {rows} | {last_fetched_at} | {status} |".format(
                **row,
                rate_range=rate_range,
            )
        )

    lines.extend(
        [
            "",
            "## Notes",
            "",
            "- HSBC rates are live quoted rates captured at each scraper run.",
            "- Historical FX rates are reference rates from Frankfurter and are stored as TWD per 1 unit of foreign currency.",
            "- HSBC live rates and historical FX reference rates are stored separately because bank buy/sell quotes are not the same as market reference rates.",
            "- Funds with no dividend rows may be accumulating classes or funds that did not distribute during the covered period.",
        ]
    )
    md_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return [fund_path, rate_path, historical_fx_path, md_path]


def run_once(
    export: bool = False,
    report: bool = False,
    history_years: int = 10,
    fx_history: bool = False,
    fx_history_years: int = 10,
    fx_history_days: int | None = None,
) -> None:
    LOGGER.info("Scraper run started")
    conn = init_db()
    hsbc_count = scrape_hsbc_rates(conn)
    nav_count, dividend_count = scrape_funds(conn, history_years=history_years)
    fx_history_count = (
        scrape_historical_fx_rates(
            conn,
            history_years=None if fx_history_days is not None else fx_history_years,
            history_days=fx_history_days,
        )
        if fx_history or fx_history_days is not None
        else 0
    )
    LOGGER.info("HSBC exchange rows written: %s", hsbc_count)
    LOGGER.info("Fund NAV rows written: %s", nav_count)
    LOGGER.info("Fund dividend rows written: %s", dividend_count)
    if fx_history or fx_history_days is not None:
        LOGGER.info("Historical FX rows written: %s", fx_history_count)
    print(f"HSBC exchange rows: {hsbc_count}")
    print(f"Fund NAV rows: {nav_count}")
    print(f"Fund dividend rows: {dividend_count}")
    if fx_history or fx_history_days is not None:
        print(f"Historical FX rows: {fx_history_count}")

    if export:
        for table in ["exchange_rate", "fund_nav", "fund_dividend", "historical_fx_rate"]:
            output = export_table(conn, table)
            LOGGER.info("Exported %s: %s", table, output)
            print(f"Exported {table}: {output}")

    if report:
        for output in generate_data_quality_report(conn):
            LOGGER.info("Generated report: %s", output)
            print(f"Generated report: {output}")

    LOGGER.info("Scraper run finished")


def main() -> None:
    parser = argparse.ArgumentParser(description="Scrape HSBC exchange rates and configured fund data.")
    parser.add_argument("--once", action="store_true", help="Run once and exit.")
    parser.add_argument("--export", action="store_true", help="Export SQLite tables to CSV after scraping.")
    parser.add_argument("--report", action="store_true", help="Generate data quality reports after scraping.")
    parser.add_argument("--interval-minutes", type=int, default=60, help="Loop interval for real-time updates.")
    parser.add_argument("--history-years", type=int, default=10, help="FundClear NAV/dividend history window.")
    parser.add_argument("--fx-history", action="store_true", help="Backfill historical reference FX rates.")
    parser.add_argument("--fx-history-years", type=int, default=10, help="Historical FX backfill window.")
    parser.add_argument("--fx-history-days", type=int, default=None, help="Refresh recent historical FX days.")
    args = parser.parse_args()

    setup_logging()

    if args.once:
        run_once(
            export=args.export,
            report=args.report,
            history_years=args.history_years,
            fx_history=args.fx_history,
            fx_history_years=args.fx_history_years,
            fx_history_days=args.fx_history_days,
        )
        return

    while True:
        try:
            run_once(
                export=args.export,
                report=args.report,
                history_years=args.history_years,
                fx_history=args.fx_history,
                fx_history_years=args.fx_history_years,
                fx_history_days=args.fx_history_days,
            )
        except Exception:
            LOGGER.exception("Scraper run failed")
        time.sleep(max(args.interval_minutes, 1) * 60)


if __name__ == "__main__":
    main()

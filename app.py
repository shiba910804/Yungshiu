import csv
import io
import os
import sqlite3
from datetime import datetime, timedelta
from pathlib import Path

from flask import Flask, Response, abort, jsonify, render_template, request

app = Flask(__name__)
BASE_DIR = Path(__file__).resolve().parent
DB_PATH = BASE_DIR / "data" / "scraper.sqlite3"
EXPORT_DIR = BASE_DIR / "exports"
REPORT_DOWNLOADS = {
    "data_quality_report.md": "text/markdown; charset=utf-8",
    "fund_quality_report.csv": "text/csv; charset=utf-8",
    "exchange_rate_latest.csv": "text/csv; charset=utf-8",
    "historical_fx_quality_report.csv": "text/csv; charset=utf-8",
}


def get_db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def table_exists(conn, name):
    return conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (name,)
    ).fetchone() is not None


def years_ago_iso(years):
    today = datetime.now().date()
    try:
        start = today.replace(year=today.year - years)
    except ValueError:
        start = today.replace(year=today.year - years, day=28)
    return start.isoformat()


def period_start_date():
    period = request.args.get("period", "all")
    if period == "all":
        return None
    if period == "1m":
        return (datetime.now().date() - timedelta(days=31)).isoformat()
    try:
        years = int(period)
    except ValueError:
        abort(400)
    if years not in {1, 3, 5, 10}:
        abort(400)
    return years_ago_iso(years)


def chart_summary(values):
    values = [value for value in values if value is not None]
    if not values:
        return {"latest": None, "min": None, "max": None, "change_pct": None}
    first = values[0]
    latest = values[-1]
    change_pct = ((latest - first) / first * 100) if first else None
    return {
        "latest": latest,
        "min": min(values),
        "max": max(values),
        "change_pct": change_pct,
    }


# ── pages ──────────────────────────────────────────────────────────────────

def fx_mid_rate_expr():
    return """
        CASE
            WHEN spot_buy IS NOT NULL AND spot_sell IS NOT NULL THEN (spot_buy + spot_sell) / 2.0
            WHEN spot_buy IS NOT NULL THEN spot_buy
            WHEN spot_sell IS NOT NULL THEN spot_sell
            WHEN cash_buy IS NOT NULL AND cash_sell IS NOT NULL THEN (cash_buy + cash_sell) / 2.0
            WHEN cash_buy IS NOT NULL THEN cash_buy
            WHEN cash_sell IS NOT NULL THEN cash_sell
        END
    """


def intraday_fx_rows(conn, currency, period):
    if period not in {"1d", "5d"}:
        return None
    source_table = (
        "exchange_rate_snapshot"
        if table_exists(conn, "exchange_rate_snapshot")
        else "exchange_rate"
    )
    delta = timedelta(hours=24) if period == "1d" else timedelta(days=5)
    cutoff = (datetime.now() - delta).isoformat(timespec="seconds")
    rate_expr = fx_mid_rate_expr()
    return conn.execute(
        f"""
        SELECT fetched_at, rate_timestamp, twd_per_unit
        FROM (
            SELECT fetched_at, rate_timestamp, {rate_expr} AS twd_per_unit
            FROM {source_table}
            WHERE currency=? AND fetched_at >= ?
        )
        WHERE twd_per_unit IS NOT NULL
        ORDER BY fetched_at
        """,
        (currency, cutoff),
    ).fetchall()


def format_intraday_label(fetched_at):
    try:
        return datetime.fromisoformat(fetched_at).strftime("%m/%d %H:%M")
    except (TypeError, ValueError):
        return fetched_at


@app.route("/")
def index():
    if not DB_PATH.exists():
        return "資料庫尚未建立，請先執行 scraper.py", 503

    conn = get_db()

    last_update = conn.execute(
        """SELECT MAX(ts) as lu FROM (
               SELECT MAX(fetched_at) as ts FROM fund_nav
               UNION ALL SELECT MAX(fetched_at) FROM exchange_rate
           )"""
    ).fetchone()["lu"]

    nav_count = conn.execute("SELECT COUNT(*) FROM fund_nav").fetchone()[0]
    div_count = conn.execute("SELECT COUNT(*) FROM fund_dividend").fetchone()[0]
    er_count  = conn.execute("SELECT COUNT(*) FROM exchange_rate").fetchone()[0]
    hfx_count = (
        conn.execute("SELECT COUNT(*) FROM historical_fx_rate").fetchone()[0]
        if table_exists(conn, "historical_fx_rate") else None
    )
    fund_count = conn.execute("SELECT COUNT(DISTINCT fund_id) FROM fund_nav").fetchone()[0]
    dividend_fund_count = conn.execute(
        "SELECT COUNT(DISTINCT fund_id) FROM fund_dividend"
    ).fetchone()[0]
    currency_count = conn.execute(
        "SELECT COUNT(DISTINCT currency) FROM exchange_rate"
    ).fetchone()[0]
    hfx_currency_count = (
        conn.execute("SELECT COUNT(DISTINCT currency) FROM historical_fx_rate").fetchone()[0]
        if table_exists(conn, "historical_fx_rate") else 0
    )
    latest_hfx_date = (
        conn.execute("SELECT MAX(rate_date) FROM historical_fx_rate").fetchone()[0]
        if table_exists(conn, "historical_fx_rate") else None
    )

    fund_stats = conn.execute(
        """SELECT n.fund_id, n.fund_name,
                  n.nav_min_date, n.nav_max_date, n.nav_count,
                  COALESCE(d.div_min_date,'') as div_min_date,
                  COALESCE(d.div_max_date,'') as div_max_date,
                  COALESCE(d.div_count,0)     as div_count
           FROM (
               SELECT fund_id, fund_name,
                      MIN(nav_date) as nav_min_date, MAX(nav_date) as nav_max_date,
                      COUNT(*) as nav_count
               FROM fund_nav GROUP BY fund_id, fund_name
           ) n
           LEFT JOIN (
               SELECT fund_id,
                      MIN(dividend_date) as div_min_date, MAX(dividend_date) as div_max_date,
                      COUNT(*) as div_count
               FROM fund_dividend GROUP BY fund_id
           ) d ON n.fund_id = d.fund_id
           ORDER BY n.fund_name"""
    ).fetchall()

    exchange_rates = conn.execute(
        """SELECT e.currency, e.rate_timestamp,
                  e.spot_buy, e.spot_sell, e.cash_buy, e.cash_sell, e.fetched_at
           FROM exchange_rate e
           INNER JOIN (
               SELECT currency, MAX(fetched_at) as max_fa
               FROM exchange_rate GROUP BY currency
           ) latest ON e.currency = latest.currency AND e.fetched_at = latest.max_fa
           ORDER BY e.currency"""
    ).fetchall()

    hfx_coverage = (
        conn.execute(
            """SELECT currency,
                      MIN(rate_date) as min_date, MAX(rate_date) as max_date,
                      COUNT(*) as row_count, MAX(fetched_at) as last_fetch
               FROM historical_fx_rate
               GROUP BY currency ORDER BY currency"""
        ).fetchall()
        if table_exists(conn, "historical_fx_rate") else None
    )

    system_status = {
        "label": "資料完整",
        "tone": "ok",
        "detail": "八檔基金、六種即時匯率與歷史匯率皆已建立",
    }
    if fund_count < 8 or currency_count < 6 or hfx_currency_count < 6:
        system_status = {
            "label": "需檢查",
            "tone": "warning",
            "detail": "部分基金或匯率資料尚未齊全",
        }

    conn.close()

    return render_template(
        "index.html",
        last_update=last_update,
        nav_count=nav_count,
        div_count=div_count,
        er_count=er_count,
        hfx_count=hfx_count,
        fund_count=fund_count,
        dividend_fund_count=dividend_fund_count,
        currency_count=currency_count,
        hfx_currency_count=hfx_currency_count,
        latest_hfx_date=latest_hfx_date,
        system_status=system_status,
        fund_stats=fund_stats,
        exchange_rates=exchange_rates,
        hfx_coverage=hfx_coverage,
    )


# ── JSON API for charts ────────────────────────────────────────────────────

@app.route("/api/fund_list")
def api_fund_list():
    if not DB_PATH.exists():
        abort(503)
    conn = get_db()
    rows = conn.execute(
        "SELECT DISTINCT fund_id, fund_name FROM fund_nav ORDER BY fund_name"
    ).fetchall()
    conn.close()
    return jsonify([{"id": r["fund_id"], "name": r["fund_name"]} for r in rows])


@app.route("/api/fund_nav/<fund_id>")
def api_fund_nav(fund_id):
    if not DB_PATH.exists():
        abort(503)
    conn = get_db()
    start_date = period_start_date()
    params = [fund_id]
    sql = (
        "SELECT nav_date, nav FROM fund_nav "
        "WHERE fund_id=? AND nav IS NOT NULL"
    )
    if start_date:
        sql += " AND nav_date >= ?"
        params.append(start_date)
    sql += " ORDER BY nav_date"
    rows = conn.execute(sql, params).fetchall()
    fund_name = conn.execute(
        "SELECT fund_name FROM fund_nav WHERE fund_id=? LIMIT 1", (fund_id,)
    ).fetchone()
    conn.close()
    values = [r["nav"] for r in rows]
    return jsonify({
        "labels": [r["nav_date"] for r in rows],
        "data":   [r["nav"]      for r in rows],
        "name":   fund_name["fund_name"] if fund_name else fund_id,
        "summary": chart_summary(values),
    })


@app.route("/api/currency_list")
def api_currency_list():
    if not DB_PATH.exists():
        abort(503)
    conn = get_db()
    if not table_exists(conn, "historical_fx_rate"):
        conn.close()
        return jsonify([])
    rows = conn.execute(
        "SELECT DISTINCT currency FROM historical_fx_rate ORDER BY currency"
    ).fetchall()
    conn.close()
    return jsonify([r["currency"] for r in rows])


@app.route("/api/historical_fx/<currency>")
def api_historical_fx(currency):
    if not DB_PATH.exists():
        abort(503)
    conn = get_db()
    if not table_exists(conn, "historical_fx_rate"):
        conn.close()
        return jsonify({"labels": [], "data": []})
    period = request.args.get("period", "all")
    if period in {"1d", "5d"}:
        rows = intraday_fx_rows(conn, currency, period)
        if rows:
            conn.close()
            values = [r["twd_per_unit"] for r in rows]
            return jsonify({
                "labels": [format_intraday_label(r["fetched_at"]) for r in rows],
                "data": [r["twd_per_unit"] for r in rows],
                "summary": chart_summary(values),
            })

    start_date = period_start_date()
    params = [currency]
    sql = (
        "SELECT rate_date, twd_per_unit FROM historical_fx_rate "
        "WHERE currency=?"
    )
    if start_date:
        sql += " AND rate_date >= ?"
        params.append(start_date)
    sql += " ORDER BY rate_date"
    rows = conn.execute(sql, params).fetchall()
    conn.close()
    values = [r["twd_per_unit"] for r in rows]
    return jsonify({
        "labels": [r["rate_date"]     for r in rows],
        "data":   [r["twd_per_unit"]  for r in rows],
        "summary": chart_summary(values),
    })


# ── CSV downloads ──────────────────────────────────────────────────────────

def _csv_response(filename, headers, rows):
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(headers)
    for row in rows:
        w.writerow(list(row))
    buf.seek(0)
    return Response(
        ("\ufeff" + buf.getvalue()).encode("utf-8"),
        headers={
            "Content-Type": "text/csv; charset=utf-8",
            "Content-Disposition": f"attachment; filename={filename}",
        },
    )


@app.route("/download/<filename>")
def download(filename):
    allowed = {
        "fund_nav.csv", "fund_dividend.csv",
        "exchange_rate.csv", "historical_fx_rate.csv",
    }
    if filename in REPORT_DOWNLOADS:
        path = EXPORT_DIR / filename
        if not path.exists():
            abort(404)
        data = path.read_bytes()
        return Response(
            data,
            headers={
                "Content-Type": REPORT_DOWNLOADS[filename],
                "Content-Disposition": f"attachment; filename={filename}",
            },
        )

    if filename not in allowed:
        abort(404)
    if not DB_PATH.exists():
        abort(503)

    conn = get_db()

    if filename == "fund_nav.csv":
        rows = conn.execute(
            "SELECT fund_id,fund_name,nav_date,nav,source,fetched_at "
            "FROM fund_nav ORDER BY fund_id,nav_date"
        ).fetchall()
        hdrs = ["fund_id","fund_name","nav_date","nav","source","fetched_at"]

    elif filename == "fund_dividend.csv":
        rows = conn.execute(
            "SELECT fund_id,fund_name,dividend_date,per_unit_dividend,source,fetched_at "
            "FROM fund_dividend ORDER BY fund_id,dividend_date"
        ).fetchall()
        hdrs = ["fund_id","fund_name","dividend_date","per_unit_dividend","source","fetched_at"]

    elif filename == "exchange_rate.csv":
        rows = conn.execute(
            "SELECT currency,rate_timestamp,spot_buy,spot_sell,cash_buy,cash_sell,source,fetched_at "
            "FROM exchange_rate ORDER BY currency,rate_timestamp"
        ).fetchall()
        hdrs = ["currency","rate_timestamp","spot_buy","spot_sell","cash_buy","cash_sell","source","fetched_at"]

    else:  # historical_fx_rate.csv
        if not table_exists(conn, "historical_fx_rate"):
            conn.close()
            abort(404)
        rows = conn.execute(
            "SELECT currency,rate_date,twd_per_unit,base_currency,quote_currency,source,fetched_at "
            "FROM historical_fx_rate ORDER BY currency,rate_date"
        ).fetchall()
        hdrs = ["currency","rate_date","twd_per_unit","base_currency","quote_currency","source","fetched_at"]

    conn.close()
    return _csv_response(filename, hdrs, rows)


if __name__ == "__main__":
    debug = os.environ.get("FLASK_DEBUG", "").lower() in {"1", "true", "yes"}
    host = os.environ.get("FLASK_HOST", "0.0.0.0")
    port = int(os.environ.get("PORT", "5000"))
    app.run(debug=debug, host=host, port=port)

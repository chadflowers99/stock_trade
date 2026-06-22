from datetime import datetime
from pathlib import Path
import sqlite3

import streamlit as st


BASE_DIR = Path(__file__).resolve().parent
OUTPUT_DIR = BASE_DIR / "output"
PORTFOLIO_CSV = OUTPUT_DIR / "portfolio.csv"
DB_FILE = OUTPUT_DIR / "trades_archive.db"


def add_action_log(status, action, symbol, quantity, price, message):
    """Stores a recent in-session action log entry for visibility/debugging."""
    if "action_log" not in st.session_state:
        st.session_state.action_log = []

    st.session_state.action_log.insert(
        0,
        {
            "TIME": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "ACTION": action.upper(),
            "SYMBOL": symbol,
            "QTY": quantity,
            "PRICE": round(float(price), 2),
            "STATUS": status,
            "MESSAGE": message,
        },
    )

    st.session_state.action_log = st.session_state.action_log[:25]


def init_storage():
    """Ensures output folder, active lot CSV, and archive DB exist."""
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    if not PORTFOLIO_CSV.exists():
        with open(PORTFOLIO_CSV, "w", encoding="utf-8") as f:
            f.write("Symbol,Quantity,Buy_Price,Buy_Timestamp,Last_Updated\n")

    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    c.execute(
        """
        CREATE TABLE IF NOT EXISTS permanent_ledger (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            timestamp TEXT,
            symbol TEXT,
            action TEXT,
            quantity INTEGER,
            price REAL,
            avg_buy_price REAL,
            realized_pl REAL
        )
        """
    )
    conn.commit()
    conn.close()


def rebuild_active_lots_from_db():
    """Reconstructs active lots from DB using lowest-buy-first sell matching."""
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    c.execute(
        """
        SELECT id, timestamp, symbol, action, quantity, price
        FROM permanent_ledger
        ORDER BY id ASC
        """
    )
    rows = c.fetchall()
    conn.close()

    lots = []
    for _row_id, ts, symbol, action, qty, price in rows:
        action_upper = (action or "").upper()

        if action_upper == "BUY":
            lots.append(
                {
                    "symbol": symbol,
                    "quantity": int(qty),
                    "buy_price": float(price),
                    "buy_timestamp": ts,
                    "last_updated": ts,
                }
            )
            continue

        if action_upper == "SELL":
            remaining = int(qty)
            for lot in sorted(
                [l for l in lots if l["symbol"] == symbol and l["quantity"] > 0],
                key=lambda x: (x["buy_price"], x["buy_timestamp"]),
            ):
                if remaining <= 0:
                    break
                consumed = min(lot["quantity"], remaining)
                lot["quantity"] -= consumed
                lot["last_updated"] = ts
                remaining -= consumed

    return [lot for lot in lots if lot["quantity"] > 0]


def load_portfolio():
    """Reads active lots from CSV; auto-migrates old aggregated CSV if found."""
    lots = []
    if not PORTFOLIO_CSV.exists():
        return lots

    with open(PORTFOLIO_CSV, "r", encoding="utf-8") as f:
        lines = f.readlines()
        if len(lines) <= 1:
            return lots

        header = lines[0].strip()

        if header == "Symbol,Quantity,Avg_Price,Last_Updated":
            rebuilt_lots = rebuild_active_lots_from_db()
            if rebuilt_lots:
                save_portfolio(rebuilt_lots)
                return rebuilt_lots

            for line in lines[1:]:
                line = line.strip()
                if not line:
                    continue
                symbol, qty, avg_price, last_updated = line.split(",")
                lots.append(
                    {
                        "symbol": symbol,
                        "quantity": int(qty),
                        "buy_price": float(avg_price),
                        "buy_timestamp": last_updated,
                        "last_updated": last_updated,
                    }
                )
            return lots

        for line in lines[1:]:
            line = line.strip()
            if not line:
                continue
            parts = line.split(",")
            if len(parts) == 6:
                _, symbol, qty, buy_price, buy_timestamp, last_updated = parts
            elif len(parts) == 5:
                symbol, qty, buy_price, buy_timestamp, last_updated = parts
            else:
                continue

            lots.append(
                {
                    "symbol": symbol,
                    "quantity": int(qty),
                    "buy_price": float(buy_price),
                    "buy_timestamp": buy_timestamp,
                    "last_updated": last_updated,
                }
            )

    return lots


def save_portfolio(portfolio):
    """Writes active lots to CSV (one row per lot)."""
    with open(PORTFOLIO_CSV, "w", encoding="utf-8") as f:
        f.write("Symbol,Quantity,Buy_Price,Buy_Timestamp,Last_Updated\n")
        for lot in sorted(portfolio, key=lambda x: (x["symbol"], x["buy_timestamp"])):
            f.write(
                f"{lot['symbol']},{lot['quantity']},{lot['buy_price']:.2f},{lot['buy_timestamp']},{lot['last_updated']}\n"
            )


def log_to_db(symbol, action, qty, price, avg_buy, pl_value):
    """Appends a permanent trade record to SQLite."""
    now = datetime.now().isoformat()
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    c.execute(
        """
        CREATE TABLE IF NOT EXISTS permanent_ledger (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            timestamp TEXT,
            symbol TEXT,
            action TEXT,
            quantity INTEGER,
            price REAL,
            avg_buy_price REAL,
            realized_pl REAL
        )
        """
    )
    c.execute(
        """
        INSERT INTO permanent_ledger (timestamp, symbol, action, quantity, price, avg_buy_price, realized_pl)
        VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        (now, symbol, action, qty, price, avg_buy, pl_value),
    )
    conn.commit()
    conn.close()


def handle_trade(action, symbol, quantity, price):
    symbol = symbol.strip().upper()
    now = datetime.now().isoformat()
    portfolio = load_portfolio()

    if action == "buy":
        portfolio.append(
            {
                "symbol": symbol,
                "quantity": quantity,
                "buy_price": price,
                "buy_timestamp": now,
                "last_updated": now,
            }
        )
        save_portfolio(portfolio)
        log_to_db(symbol, "BUY", quantity, price, price, 0.0)
        return True, f"Bought {quantity} shares of {symbol} @ ${price:.2f}."

    symbol_lots = [lot for lot in portfolio if lot["symbol"] == symbol and lot["quantity"] > 0]
    total_qty = sum(lot["quantity"] for lot in symbol_lots)

    if total_qty <= 0:
        return False, f"No holdings found for {symbol}."

    if quantity > total_qty:
        return False, f"Cannot sell {quantity} shares. You only own {total_qty} shares of {symbol}."

    remaining_to_sell = quantity
    total_realized_pl = 0.0

    for lot in sorted(symbol_lots, key=lambda x: (x["buy_price"], x["buy_timestamp"])):
        if remaining_to_sell <= 0:
            break

        consumed = min(lot["quantity"], remaining_to_sell)
        lot_realized_pl = (price - lot["buy_price"]) * consumed
        total_realized_pl += lot_realized_pl

        log_to_db(symbol, "SELL", consumed, price, lot["buy_price"], lot_realized_pl)

        lot["quantity"] -= consumed
        lot["last_updated"] = now
        remaining_to_sell -= consumed

    portfolio = [lot for lot in portfolio if lot["quantity"] > 0]
    save_portfolio(portfolio)

    pl_status = "Profit" if total_realized_pl >= 0 else "Loss"
    return True, f"Sold {quantity} shares of {symbol}. Realized {pl_status}: ${abs(total_realized_pl):.2f}."


def load_running_realized_pl():
    """Returns running realized P/L by symbol and overall total from the archive DB."""
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    c.execute(
        """
        SELECT symbol, COALESCE(SUM(realized_pl), 0.0)
        FROM permanent_ledger
        GROUP BY symbol
        """
    )
    rows = c.fetchall()
    conn.close()

    pl_by_symbol = {symbol: float(total_pl or 0.0) for symbol, total_pl in rows}
    total_pl = sum(pl_by_symbol.values())
    return pl_by_symbol, total_pl


init_storage()

st.set_page_config(page_title="Stock Tracker", layout="centered")
st.title("Stock Trade Journal")
st.caption("Uses the same lot-level CSV logic as View CSV Active Holdings.")

with st.form("trade_form", clear_on_submit=True):
    col1, col2, col3 = st.columns(3)
    with col1:
        sym_input = st.text_input("Stock Symbol").upper()
    with col2:
        qty_input = st.number_input("Quantity", min_value=1, step=1)
    with col3:
        price_input = st.number_input("Price per Share", min_value=0.01, step=0.01, format="%.2f")

    btn_col1, btn_col2 = st.columns(2)
    with btn_col1:
        submit_buy = st.form_submit_button("Log Buy", use_container_width=True)
    with btn_col2:
        submit_sell = st.form_submit_button("Log Sell", use_container_width=True)

if (submit_buy or submit_sell) and not sym_input:
    attempted_action = "BUY" if submit_buy else "SELL"
    add_action_log("REJECTED", attempted_action, "", int(qty_input), float(price_input), "Missing stock symbol.")
    st.warning("Please enter a stock symbol.")
elif submit_buy:
    ok, msg = handle_trade("buy", sym_input, int(qty_input), float(price_input))
    add_action_log("ACCEPTED" if ok else "REJECTED", "BUY", sym_input, int(qty_input), float(price_input), msg)
    if ok:
        st.success(msg)
    else:
        st.error(msg)
elif submit_sell:
    ok, msg = handle_trade("sell", sym_input, int(qty_input), float(price_input))
    add_action_log("ACCEPTED" if ok else "REJECTED", "SELL", sym_input, int(qty_input), float(price_input), msg)
    if ok:
        st.success(msg)
    else:
        st.error(msg)

st.markdown("### Current CSV Active Holdings")
current_portfolio = load_portfolio()
running_pl_by_symbol, running_total_pl = load_running_realized_pl()

st.metric("Running Realized P/L", f"${running_total_pl:,.2f}")

if current_portfolio:
    display_rows = []
    for lot in sorted(current_portfolio, key=lambda x: (x["symbol"], x["buy_timestamp"])):
        display_rows.append(
            {
                "SYMBOL": lot["symbol"],
                "QTY": lot["quantity"],
                "BUY PRICE": round(lot["buy_price"], 2),
                "RUNNING P/L": round(running_pl_by_symbol.get(lot["symbol"], 0.0), 2),
            }
        )
    st.dataframe(display_rows, use_container_width=True, hide_index=True)
else:
    st.info("CSV file is empty (No active lots).")

st.markdown("### Recent Action Log")
if "action_log" in st.session_state and st.session_state.action_log:
    st.dataframe(st.session_state.action_log, use_container_width=True, hide_index=True)
else:
    st.caption("No trade attempts yet in this session.")

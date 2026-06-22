# Stock Trade

Simple local trade journal with two interfaces that write to the same data files:

- Streamlit web app: app.py
- Tkinter desktop app: enter_trade.py

Both interfaces share the same storage and behavior.

## Data Storage

All data is local in the project output folder:

- output/portfolio.csv: current active holdings (lot-level rows)
- output/trades_archive.db: permanent trade ledger (SQLite)

## Behavior

- Every buy creates a separate active lot row in CSV.
- Sells match lots using lowest-buy-first (then earliest timestamp as tie-breaker).
- Running realized P/L is calculated from the SQLite ledger.

## Requirements

- Python 3.10+
- streamlit (see requirements.txt)

Install dependencies:

```powershell
pip install -r requirements.txt
```

## Run

Run Streamlit app:

```powershell
streamlit run app.py
```

Run Tkinter app:

```powershell
python enter_trade.py
```

## Notes

- Avoid running Streamlit and Tkinter at the exact same time to prevent simultaneous writes.
- If legacy aggregated CSV format is detected, the app auto-migrates to lot-level format.

import sqlite3
import requests
import json
import os
import time

from datetime import datetime


DB_FILE = "finbit.db"

API_KEY = "2431ce60befa48bebfdaa7fcf3c864e4"


# ============================================
# INIT DB
# ============================================

def init_db():

    con = sqlite3.connect(DB_FILE)

    con.executescript("""

    CREATE TABLE IF NOT EXISTS operaciones (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        fecha TEXT,
        ticker TEXT,
        tipo TEXT,
        titulos REAL,
        precio_mxn REAL,
        total_mxn REAL,
        tc_dia REAL,
        origen TEXT,
        mercado TEXT,
        notas TEXT
    );

    CREATE TABLE IF NOT EXISTS portafolio (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        ticker TEXT UNIQUE,
        titulos REAL,
        cto_prom_mxn REAL,
        origen TEXT,
        mercado TEXT,
        activo INTEGER DEFAULT 1
    );

    CREATE TABLE IF NOT EXISTS config (
        key TEXT PRIMARY KEY,
        value TEXT
    );

    CREATE TABLE IF NOT EXISTS tickers (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        ticker TEXT UNIQUE,
        exchange TEXT,
        activo INTEGER DEFAULT 1,
        fecha TEXT
    );

    CREATE TABLE IF NOT EXISTS cache (
        ticker TEXT,
        interval TEXT,
        fecha TEXT,
        data TEXT,
        PRIMARY KEY (ticker, interval)
    );

    """)

    con.commit()
    con.close()


# ============================================
# TICKERS
# ============================================

def get_tickers():

    con = sqlite3.connect(DB_FILE)
    con.row_factory = sqlite3.Row

    rows = con.execute(
        "SELECT * FROM tickers WHERE activo=1 ORDER BY ticker"
    ).fetchall()

    con.close()

    return [dict(r) for r in rows]


def add_ticker(ticker, exchange=""):

    con = sqlite3.connect(DB_FILE)

    con.execute(
        "INSERT OR IGNORE INTO tickers (ticker, exchange, fecha) VALUES (?, ?, ?)",
        (
            ticker.upper(),
            exchange,
            datetime.now().isoformat()
        )
    )

    con.commit()
    con.close()


# ============================================
# CACHE
# ============================================

def get_cache(ticker, interval):

    con = sqlite3.connect(DB_FILE)

    row = con.execute(
        "SELECT data FROM cache WHERE ticker=? AND interval=?",
        (ticker, interval)
    ).fetchone()

    con.close()

    if row:
        return json.loads(row[0])

    return None


def save_cache(ticker, interval, data):

    con = sqlite3.connect(DB_FILE)

    con.execute(
        "INSERT OR REPLACE INTO cache (ticker, interval, fecha, data) VALUES (?, ?, ?, ?)",
        (
            ticker,
            interval,
            datetime.now().isoformat(),
            json.dumps(data)
        )
    )

    con.commit()
    con.close()


# ============================================
# API TWELVEDATA
# ============================================

API_BASE = "https://api.twelvedata.com"


def api_timeseries(symbol, interval="1day", exchange=""):

    cached = get_cache(symbol, interval)

    if cached:
        return cached

    params = {
        "symbol": symbol,
        "interval": interval,
        "outputsize": 200,
        "apikey": API_KEY,
        "order": "ASC"
    }

    if exchange:
        params["exchange"] = exchange

    r = requests.get(
        f"{API_BASE}/time_series",
        params=params,
        timeout=20
    )

    data = r.json()

    if "values" not in data:
        print("API error:", data)
        return None

    values = data["values"]

    save_cache(symbol, interval, values)

    return values


# ============================================
# AUTO EXCHANGE
# ============================================

def detect_exchange(ticker):

    t = ticker.upper()

    if t.endswith(".MX"):
        return "BMV"

    if t in [
        "AAPL","MSFT","NVDA","TSLA","META","AMZN",
        "AMD","SOXL","TQQQ","SPY","QQQ","AVGO"
    ]:
        return "NASDAQ"

    return ""


# ============================================
# TEST
# ============================================

if __name__ == "__main__":

    init_db()

    print("Finbit v4 DB OK")
    
    # BLOQUE 2 BUSCADOR + ANÁLISIS

# ============================================
# ANALISIS SIMPLE
# ============================================

def analizar_ticker(ticker):

    ticker = ticker.upper()

    exchange = detect_exchange(ticker)

    print("Analizando:", ticker, exchange)

    values = api_timeseries(
        ticker,
        interval="1day",
        exchange=exchange
    )

    if not values:
        print("Sin datos")
        return

    closes = [float(v["close"]) for v in values]

    precio = closes[-1]

    max20 = max(closes[-20:])
    min20 = min(closes[-20:])

    print("Precio:", precio)
    print("Max20:", max20)
    print("Min20:", min20)



# ============================================
# BUSCAR Y GUARDAR
# ============================================

def buscar_y_agregar():

    ticker = input("Ticker: ").strip().upper()

    if not ticker:
        return

    exchange = detect_exchange(ticker)

    add_ticker(ticker, exchange)

    print("Guardado:", ticker)

    analizar_ticker(ticker)



# ============================================
# LISTAR TICKERS
# ============================================

def listar_tickers():

    tks = get_tickers()

    print("Tickers guardados:")

    for t in tks:
        print("-", t["ticker"])


# ============================================
# SCANNER DESDE BD
# ============================================

def get_scanner_tickers():

    tks = get_tickers()

    lista = []

    for t in tks:

        lista.append(
            (
                t["ticker"],
                t.get("exchange", "")
            )
        )

    return lista
def scanner_dinamico():

    lista = get_scanner_tickers()

    if not lista:
        print("No hay tickers")
        return

    for ticker, exchange in lista:

        print("Scanner:", ticker)

        analizar_ticker(ticker)
# ============================================
# MENU TEST
# ============================================

if __name__ == "__main__":

    init_db()

    while True:

        print()
        print("1 Buscar ticker")
        print("2 Ver tickers")
        print("3 Analizar todos")
        print("0 Salir")

        op = input("Opcion: ")

        if op == "1":

            buscar_y_agregar()

        elif op == "2":

            listar_tickers()

        elif op == "3":

            scanner_dinamico()

            

        elif op == "0":

            break

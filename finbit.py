"""
╔══════════════════════════════════════════════════════════════╗
║          FINBIT PRO  v3.2                                    ║
║  python finbit.py → abre dashboard.html                     ║
╚══════════════════════════════════════════════════════════════╝

INSTALACIÓN (solo la primera vez):
    pip install requests pandas

API KEY GRATIS (800 calls/día):
    1. Ve a https://twelvedata.com  → Sign Up (gratis)
    2. Dashboard → API Keys → copia tu key
    3. Pégala abajo en API_KEY
"""

import sqlite3, requests, json, os, webbrowser, time, threading
import pandas as pd
from datetime import datetime, date
from collections import defaultdict
from flask import Flask, Response, request as flask_req, jsonify

# ═══════════════════════════════════════════════════════════
#   ⚙️  CONFIGURACIÓN
# ═══════════════════════════════════════════════════════════
# API keys: se leen de variables de entorno si existen (recomendado en Render)
# Si no existen, usa el valor hardcodeado (para desarrollo local)
API_KEY     = os.environ.get("TWELVEDATA_API_KEY",  "2431ce60befa48bebfdaa7fcf3c864e4")
API_KEY_2   = os.environ.get("TWELVEDATA_API_KEY_2","3c4971fd74eb4363bcbf877edb1616b4")

# ── Dual-key: KEY_1 cubre los primeros tickers, KEY_2 el resto
# Cada key tiene 800 calls/día y 8 calls/min en plan Basic.
# El pool rota automáticamente según el índice del ticker.
_TD_KEYS    = [k for k in [API_KEY, API_KEY_2] if k not in ("","TU_KEY_AQUI")]

TELEGRAM_TOKEN   = "TU_TOKEN_AQUI"
TELEGRAM_CHAT_ID = "TU_CHAT_ID_AQUI"
TELEGRAM_ACTIVO  = False

CAPITAL_TOTAL    = 15_000
RIESGO_POR_TRADE = 0.01
RR_MINIMO        = 3.0
ALERTA_SUBIDA    = 5.0
ALERTA_BAJADA    = 5.0

ALERTAR_ROCKET       = True
ALERTAR_BUY          = True
ALERTAR_WATCH        = False
ALERTAR_PORTAFOLIO   = True
ALERTAR_RADAR_ROCKET = True

DB_FILE     = "finbit.db"
OUTPUT_FILE = "dashboard.html"

PORTAFOLIO_INICIAL = [
   
  
]

# ── Exchanges vacíos "" = auto-detect TwelveData (más estable) ──
SCANNER_TICKERS = {
    "SOXL":("SOXL",""),  "TQQQ":("TQQQ",""),
    "NVDA":("NVDA",""),  "TSLA":("TSLA",""),
    "AAPL":("AAPL",""),  "META":("META",""),
    "PLTR":("PLTR",""),  "PYPL":("PYPL",""),
    "NFLX":("NFLX",""),  "NKE":("NKE",""),
}

# SerpApi eliminado completamente — todos los tickers usan TwelveData dual-key


# Universo para el radar (deduplicado automáticamente con SCANNER_TICKERS)
_UNIVERSO_EXTRA = {
    "MSFT":("MSFT",""), "GOOGL":("GOOGL",""),
    "AMZN":("AMZN",""), "SPXL":("SPXL",""),
    "UBER":("UBER",""), "ABNB":("ABNB",""),
    "DIS":("DIS",""),
}
UNIVERSO = {**SCANNER_TICKERS, **_UNIVERSO_EXTRA}


# ── BASE DE DATOS ─────────────────────────────────────────
def init_db():
    con = sqlite3.connect(DB_FILE)
    con.executescript("""
    CREATE TABLE IF NOT EXISTS operaciones (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        fecha TEXT NOT NULL, ticker TEXT NOT NULL, tipo TEXT NOT NULL,
        titulos REAL NOT NULL, precio_mxn REAL NOT NULL, total_mxn REAL NOT NULL,
        tc_dia REAL NOT NULL, origen TEXT, mercado TEXT, notas TEXT
    );
    CREATE TABLE IF NOT EXISTS portafolio (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        ticker TEXT UNIQUE NOT NULL, titulos REAL NOT NULL,
        cto_prom_mxn REAL NOT NULL, origen TEXT DEFAULT 'USA',
        mercado TEXT DEFAULT 'SIC', activo INTEGER DEFAULT 1
    );
    CREATE TABLE IF NOT EXISTS config (key TEXT PRIMARY KEY, value TEXT);
    CREATE TABLE IF NOT EXISTS tickers (
        id       INTEGER PRIMARY KEY AUTOINCREMENT,
        ticker   TEXT UNIQUE NOT NULL,
        exchange TEXT DEFAULT '',
        origen   TEXT DEFAULT 'USA',
        activo   INTEGER DEFAULT 1
    );
    """)
    con.commit()
    # Migración segura: agregar columnas nuevas si no existen (DB de versiones anteriores)
    migrations = [
        "ALTER TABLE tickers ADD COLUMN origen TEXT DEFAULT 'USA'",
        "ALTER TABLE portafolio ADD COLUMN mercado TEXT DEFAULT 'SIC'",
    ]
    for sql in migrations:
        try:
            con.execute(sql)
            con.commit()
        except Exception:
            pass  # Columna ya existe
    con.close()

def seed_portafolio(tc: float):
    con = sqlite3.connect(DB_FILE)
    cur = con.cursor()
    cur.execute("SELECT COUNT(*) FROM portafolio")
    if cur.fetchone()[0] == 0:
        for p in PORTAFOLIO_INICIAL:
            cur.execute(
                "INSERT OR IGNORE INTO portafolio (ticker,titulos,cto_prom_mxn,origen,mercado) VALUES(?,?,?,?,?)",
                (p["ticker"], p["titulos"], p["cto_prom_mxn"], p["origen"], p["mercado"]))
    con.commit(); con.close()

def get_portafolio():

    con = sqlite3.connect(DB_FILE)
    con.row_factory = sqlite3.Row

    rows = con.execute(
        "SELECT * FROM portafolio WHERE activo=1"
    ).fetchall()

    con.close()

    return [dict(r) for r in rows]


def get_tickers_db() -> dict:
    """Retorna tickers registrados por el usuario para el scanner/buscador."""
    con = sqlite3.connect(DB_FILE)
    con.row_factory = sqlite3.Row
    try:
        rows = con.execute("SELECT ticker, exchange FROM tickers WHERE activo=1").fetchall()
    except Exception:
        rows = []
    con.close()
    return {r["ticker"]: (r["ticker"], r["exchange"] or "") for r in rows}

def add_ticker_db(ticker: str, exchange: str = "", origen: str = "USA"):
    """
    Agrega un ticker al scanner personalizado.
    Guarda también el exchange en la DB para SerpApi.
    """
    ticker = ticker.upper().strip()
    # Validar: solo letras y puntos (TLEVISA.CPO), longitud razonable
    import re as _re
    if not ticker or not _re.match(r'^[A-Z0-9.]{1,15}$', ticker):
        print(f"  ⚠️  Ticker inválido: '{ticker}'")
        return
    con = sqlite3.connect(DB_FILE)
    try:
        con.execute("INSERT OR REPLACE INTO tickers (ticker, exchange, origen, activo) VALUES(?,?,?,1)",
                    (ticker, exchange.upper().strip(), origen))
        con.commit()
        print(f"  ✅ Ticker {ticker} guardado en DB (exchange: {exchange or 'auto'})")
    except Exception as e:
        print(f"  Error agregando ticker: {e}")
    con.close()


def get_all_scanner_tickers() -> dict:
    """
    Retorna el diccionario completo de tickers para el scanner.
    La DB tiene prioridad sobre el hardcode: si un ticker está en la DB
    con activo=0 (eliminado por el usuario), NO aparece aunque esté en SCANNER_TICKERS.
    """
    con = sqlite3.connect(DB_FILE)
    con.row_factory = sqlite3.Row
    try:
        rows = con.execute("SELECT ticker, exchange, activo FROM tickers").fetchall()
    except Exception:
        rows = []
    con.close()

    # Mapa completo de la DB: ticker → (activo, exchange)
    db_map = {r["ticker"]: (r["activo"], r["exchange"] or "") for r in rows}

    combinados = {}
    # Primero agregar hardcoded, pero solo si no están desactivados en DB
    for ticker, val in SCANNER_TICKERS.items():
        estado = db_map.get(ticker)
        if estado is None or estado[0] == 1:
            combinados[ticker] = val
    # Luego agregar los de la DB con activo=1 (custom añadidos por el usuario)
    for ticker, (activo, exchange) in db_map.items():
        if activo == 1:
            combinados[ticker] = (ticker, exchange)
    return combinados

def remove_ticker_db(ticker: str):
    """
    Desactiva un ticker del scanner.
    Si el ticker está hardcoded en SCANNER_TICKERS o UNIVERSO,
    lo inserta en la DB con activo=0 para que el override de DB prevalezca.
    """
    t = ticker.upper()
    con = sqlite3.connect(DB_FILE)
    # INSERT OR REPLACE garantiza que exista en la tabla aunque venga del hardcode
    exchange = ""
    for src in (SCANNER_TICKERS, UNIVERSO):
        if t in src:
            exchange = src[t][1]
            break
    con.execute(
        "INSERT OR REPLACE INTO tickers (ticker, exchange, origen, activo) VALUES (?,?,?,0)",
        (t, exchange, "USA")
    )
    con.commit()
    con.close()

def get_operaciones():
    con = sqlite3.connect(DB_FILE); con.row_factory = sqlite3.Row
    rows = con.execute("SELECT * FROM operaciones ORDER BY fecha DESC, id DESC").fetchall()
    con.close(); return [dict(r) for r in rows]

def upsert_portafolio_from_op(op: dict):
    """Actualiza portafolio en SQLite al importar una operación."""
    con = sqlite3.connect(DB_FILE)
    con.row_factory = sqlite3.Row
    cur = con.cursor()
    cur.execute("SELECT * FROM portafolio WHERE ticker=?", (op["ticker"],))
    existing = cur.fetchone()
    if op["tipo"] == "COMPRA":
        if existing:
            tp = existing["titulos"]; cp = existing["cto_prom_mxn"]
            tn = op["titulos"]; cn = op["precio_mxn"]; tt = tp + tn
            cto_new = (tp*cp + tn*cn) / tt
            cur.execute("UPDATE portafolio SET titulos=?, cto_prom_mxn=?, activo=1 WHERE ticker=?",
                        (tt, round(cto_new,4), op["ticker"]))
        else:
            cur.execute("INSERT INTO portafolio (ticker,titulos,cto_prom_mxn,origen,mercado,activo) VALUES(?,?,?,?,?,1)",
                        (op["ticker"], op["titulos"], op["precio_mxn"],
                         op.get("origen","USA"), op.get("mercado","SIC")))
    elif op["tipo"] == "VENTA" and existing:
        rest = existing["titulos"] - op["titulos"]
        if rest <= 0.0001:
            cur.execute("UPDATE portafolio SET titulos=0, activo=0 WHERE ticker=?", (op["ticker"],))
        else:
            cur.execute("UPDATE portafolio SET titulos=? WHERE ticker=?", (round(rest,6), op["ticker"]))
    con.commit(); con.close()


# ── TIPO DE CAMBIO ────────────────────────────────────────
def get_tipo_cambio(key: str) -> float:
    if key and key not in ("TU_KEY_AQUI", ""):
        try:
            r = requests.get("https://api.twelvedata.com/exchange_rate",
                params={"symbol":"USD/MXN","apikey":key}, timeout=8)
            d = r.json()
            if "rate" in d: return float(d["rate"])
        except Exception: pass
    try:
        r = requests.get("https://api.frankfurter.app/latest?from=USD&to=MXN", timeout=8)
        return float(r.json()["rates"]["MXN"])
    except Exception: pass
    try:
        url = "https://www.banxico.org.mx/SieAPIRest/service/v1/series/SF43718/datos/oportuno"
        r = requests.get(url, headers={"Bmx-Token":"adec2b6a30609a9e4f696c3b44f32d16b8a6ab3b0e83da2e18b0c2e24f892abc"}, timeout=8)
        return float(r.json()["bmx"]["series"][0]["datos"][0]["dato"].replace(",",""))
    except Exception: return 17.50


# ── TELEGRAM ──────────────────────────────────────────────
def tg_send(msg: str) -> bool:
    if not TELEGRAM_ACTIVO or TELEGRAM_TOKEN == "TU_TOKEN_AQUI": return False
    try:
        r = requests.post(f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage",
            json={"chat_id":TELEGRAM_CHAT_ID,"text":msg,"parse_mode":"HTML"}, timeout=10)
        return r.status_code == 200
    except Exception: return False


# ── API DE DATOS — DUAL KEY ────────────────────────────────
API_BASE = "https://api.twelvedata.com"

# Índice global del pool de keys (alterna entre KEY_1 y KEY_2)
_KEY_IDX: int = 0

def _next_key() -> str:
    """Devuelve la siguiente key del pool en round-robin."""
    global _KEY_IDX
    if not _TD_KEYS:
        return ""
    key = _TD_KEYS[_KEY_IDX % len(_TD_KEYS)]
    _KEY_IDX += 1
    return key


def api_timeseries(symbol: str, interval: str, outputsize: int = 200,
                   exchange: str = "", key: str = "") -> list | None:
    """
    Petición individual a TwelveData.
    Si no se pasa key, toma la siguiente del pool dual-key.
    """
    use_key = key or _next_key()
    if not use_key:
        return None
    out = min(outputsize, 5000)  # TwelveData permite hasta 5000 velas
    params = {"symbol": symbol, "interval": interval, "outputsize": out,
              "apikey": use_key, "order": "ASC"}
    if exchange:
        params["exchange"] = exchange
    for intento in range(2):
        try:
            r = requests.get(f"{API_BASE}/time_series", params=params, timeout=15)
            if r.status_code == 429:
                print(f"    ⏳ Rate limit ({symbol}) key …{use_key[-4:]} — esperando 15s...")
                time.sleep(15)
                continue
            d = r.json()
            if "values" in d and d["values"]:
                print(f"    ✅ TD ({symbol} {interval}) k={use_key[-4:]}: {len(d['values'])} velas")
                return d["values"]
            print(f"    ❌ TD ({symbol} {interval}): {str(d)[:120]}")
            return None
        except requests.exceptions.Timeout:
            print(f"    ⏱️  TD Timeout ({symbol}) intento {intento+1}/2")
            time.sleep(3)
        except Exception as e:
            print(f"    ❌ TD exception ({symbol}): {e}")
            time.sleep(3)
    return None


def api_timeseries_batch(symbols: list, interval: str,
                          outputsize: int = 100, key: str = "") -> dict:
    """
    Llama TwelveData con hasta 8 símbolos en UNA sola request.
    Si hay rate limit (429), espera 15s y reintenta UNA vez — sin bloquear minutos.
    """
    use_key = key or _next_key()
    if not use_key or not symbols:
        return {}
    out = min(outputsize, 5000)  # TwelveData permite hasta 5000 velas
    sym_str = ",".join(s.upper() for s in symbols)
    params  = {"symbol": sym_str, "interval": interval, "outputsize": out,
               "apikey": use_key, "order": "ASC"}
    try:
        r = requests.get(f"{API_BASE}/time_series", params=params, timeout=30)
        if r.status_code == 429:
            print(f"    ⏳ Rate limit batch k=…{use_key[-4:]} — esperando 15s...")
            time.sleep(15)
            r = requests.get(f"{API_BASE}/time_series", params=params, timeout=30)
        d = r.json()
        resultado = {}
        if len(symbols) == 1:
            sym = symbols[0].upper()
            if "values" in d and d["values"]:
                resultado[sym] = d["values"]
                print(f"    ✅ TD ({sym} {interval}) k=…{use_key[-4:]}: {len(d['values'])} velas")
            else:
                print(f"    ❌ TD ({sym}): {str(d)[:80]}")
        else:
            for sym in symbols:
                sym_up = sym.upper()
                entry  = d.get(sym_up, {})
                vals   = entry.get("values", []) if isinstance(entry, dict) else []
                if vals:
                    resultado[sym_up] = vals
                    print(f"    ✅ TD ({sym_up} {interval}) k=…{use_key[-4:]}: {len(vals)} velas")
                else:
                    err = entry.get("message","") if isinstance(entry, dict) else str(entry)[:60]
                    print(f"    ❌ TD ({sym_up}): {err or 'sin valores'}")
        return resultado
    except Exception as e:
        print(f"    ❌ TD batch exception k=…{use_key[-4:]}: {e}")
        return {}


def ohlcv_to_close(v): return [float(x["close"]) for x in v]
def ohlcv_to_volume(v): return [float(x.get("volume",0)) for x in v]


def get_timeseries(symbol: str, interval: str, outputsize: int = 200,
                   exchange: str = "") -> list | None:
    """
    Fuente única: TwelveData con pool dual-key.
    Si falla con exchange específico, reintenta sin él (auto-detect).
    """
    result = api_timeseries(symbol, interval, outputsize, exchange)
    if result:
        return result
    if exchange:
        print(f"    Reintentando {symbol} sin exchange (auto-detect)...")
        result = api_timeseries(symbol, interval, outputsize, "")
        if result:
            return result
    print(f"    Sin datos para {symbol} {interval} — continúa con otros")
    return None



# ── INDICADORES TÉCNICOS ──────────────────────────────────
def ema(s: pd.Series, n: int) -> pd.Series:
    return s.ewm(span=n, adjust=False).mean()

def macd(close: pd.Series):
    l = ema(close,12)-ema(close,26); sig = ema(l,9); return l, sig, l-sig

def rsi(close: pd.Series, n: int = 14) -> pd.Series:
    d = close.diff()
    g = d.clip(lower=0).ewm(com=n-1,min_periods=n).mean()
    lo = (-d).clip(lower=0).ewm(com=n-1,min_periods=n).mean()
    return 100 - 100/(1+g/lo)

def obv(closes: list, volumes: list) -> dict:
    """
    On-Balance Volume — detecta si los institucionales están comprando o vendiendo.
    OBV sube cuando el volumen de días alcistas supera el bajista y viceversa.
    Divergencia bajista: precio sube pero OBV baja → institucionales SALIENDO.
    Divergencia alcista: precio baja pero OBV sube → institucionales ACUMULANDO.
    """
    if not closes or not volumes or len(closes) < 10:
        return {"tendencia": "sin datos", "divergencia": False, "div_tipo": "", "valor": 0}

    c = closes; v = volumes
    obv_vals = [0.0]
    for i in range(1, len(c)):
        vol = float(v[i]) if v[i] else 0
        if c[i] > c[i-1]:
            obv_vals.append(obv_vals[-1] + vol)
        elif c[i] < c[i-1]:
            obv_vals.append(obv_vals[-1] - vol)
        else:
            obv_vals.append(obv_vals[-1])

    # Tendencia OBV: comparar última ventana de 10 velas
    ventana = 10
    obv_reciente = obv_vals[-ventana:]
    precio_reciente = c[-ventana:]

    obv_sube   = obv_reciente[-1] > obv_reciente[0]
    precio_sube= precio_reciente[-1] > precio_reciente[0]

    # Divergencia: precio y OBV van en direcciones opuestas
    div_bajista = precio_sube and not obv_sube   # precio sube, OBV baja → venta institucional
    div_alcista = not precio_sube and obv_sube   # precio baja, OBV sube → acumulación

    if div_bajista:
        tendencia   = "divergencia bajista"
        div_tipo    = "⚠️ Precio sube pero OBV baja — institucionales vendiendo"
    elif div_alcista:
        tendencia   = "divergencia alcista"
        div_tipo    = "💡 Precio baja pero OBV sube — institucionales acumulando"
    elif obv_sube:
        tendencia   = "alcista"
        div_tipo    = ""
    else:
        tendencia   = "bajista"
        div_tipo    = ""

    return {
        "tendencia":   tendencia,
        "divergencia": div_bajista or div_alcista,
        "div_tipo":    div_tipo,
        "div_bajista": div_bajista,
        "div_alcista": div_alcista,
        "valor":       round(obv_vals[-1], 0),
        "ok":          not div_bajista,   # False si hay divergencia bajista = señal de alerta
    }


# ── SECTORES ETF — contexto macro por sector ──────────────
# Para cada ticker del scanner, se verifica si su sector ETF está alcista.
# Fuente: TwelveData (ya tenemos la key). Se cachea igual que cualquier otro ticker.
_SECTOR_MAP = {
    # Semiconductores
    "NVDA":"SMH","SOXL":"SMH","AMD":"SMH",
    # Tech/QQQ
    "AAPL":"QQQ","META":"QQQ","GOOGL":"QQQ","MSFT":"QQQ","AMZN":"QQQ",
    "TQQQ":"QQQ","NFLX":"QQQ","PYPL":"QQQ",
    # Financieros
    "PLTR":"XLF",
    # Consumo discrecional
    "TSLA":"XLY","ABNB":"XLY","UBER":"XLY","NKE":"XLY","DIS":"XLY",
    # Broad market
    "SPXL":"SPY","UPRO":"SPY","TQQQ":"QQQ",
}

def get_sector_estado(ticker: str) -> dict:
    """
    Verifica si el ETF de sector del ticker está sobre su EMA50.
    Retorna: alcista / bajista / sin_datos
    Se cachea automáticamente — 0 calls extra si el ETF ya fue analizado.
    """
    sector_etf = _SECTOR_MAP.get(ticker.upper())
    if not sector_etf:
        return {"etf": None, "alcista": True, "desc": "Sector no mapeado — sin restricción"}

    vals = _get_cached(sector_etf, "1day", "")
    if not vals or len(vals) < 50:
        return {"etf": sector_etf, "alcista": True, "desc": f"{sector_etf}: sin datos suficientes"}

    closes = [float(x["close"]) for x in vals]
    c      = pd.Series(closes, dtype=float)
    e50    = float(ema(c, 50).iloc[-1])
    precio = closes[-1]
    sobre  = precio > e50
    pct    = (precio - e50) / e50 * 100

    return {
        "etf":     sector_etf,
        "alcista": sobre,
        "precio":  round(precio, 2),
        "ema50":   round(e50, 2),
        "pct":     round(pct, 2),
        "desc":    (f"{'✅' if sobre else '❌'} {sector_etf} {'sobre' if sobre else 'BAJO'} "
                    f"EMA50 ({pct:+.1f}%) — sector {'alcista' if sobre else 'BAJISTA'}"),
    }


def gestion_posicion(precio_entrada_mxn: float, precio_actual_mxn: float,
                      stop_mxn: float, objetivo_mxn: float,
                      titulos: float) -> dict:
    """
    Panel de gestión activa para posiciones abiertas.
    Calcula: breakeven, nivel de parciales, cuándo agregar, estado de la operación.
    """
    if not precio_entrada_mxn or precio_entrada_mxn <= 0:
        return {}

    ganancia_pct  = (precio_actual_mxn - precio_entrada_mxn) / precio_entrada_mxn * 100
    riesgo_orig   = precio_entrada_mxn - stop_mxn if stop_mxn else 0
    objetivo_pct  = (objetivo_mxn - precio_entrada_mxn) / precio_entrada_mxn * 100 if objetivo_mxn else 0

    # Breakeven: stop se mueve a precio de entrada + comisión (~0.3%)
    breakeven     = precio_entrada_mxn * 1.003

    # Nivel de parciales: 50% del camino al objetivo
    parciales_50  = precio_entrada_mxn + (objetivo_mxn - precio_entrada_mxn) * 0.5 if objetivo_mxn else None

    # Estado de la operación
    if ganancia_pct >= objetivo_pct * 0.9:
        estado_op   = "🎯 En objetivo"
        accion      = "Considera cerrar posición completa o tomar 75% de ganancias"
        color       = "var(--green)"
    elif ganancia_pct >= objetivo_pct * 0.5:
        estado_op   = "✅ En zona de parciales"
        accion      = f"Toma 50% de la posición ({titulos/2:.2f} tít). Mueve stop a breakeven {fmt(breakeven)}"
        color       = "var(--green)"
    elif ganancia_pct >= 3.0:
        estado_op   = "📈 Posición con ganancia"
        accion      = f"Mueve stop a breakeven ({fmt(breakeven)}). Dejar correr."
        color       = "var(--green)"
    elif ganancia_pct >= -1.0:
        estado_op   = "〰️ En breakeven"
        accion      = "Mantener. Si cae al stop original, salir sin dudar."
        color       = "var(--yellow)"
    elif ganancia_pct >= -5.0:
        estado_op   = "⚠️ En pérdida controlada"
        accion      = f"Stop en {fmt(stop_mxn)}. NO promediar a la baja."
        color       = "var(--yellow)"
    else:
        estado_op   = "🔴 Stop loss inminente"
        accion      = f"SALIR en {fmt(stop_mxn)} MXN. No negociar con el stop."
        color       = "var(--red)"

    return {
        "ganancia_pct":  round(ganancia_pct, 2),
        "estado_op":     estado_op,
        "accion":        accion,
        "color":         color,
        "breakeven":     round(breakeven, 2),
        "parciales_50":  round(parciales_50, 2) if parciales_50 else None,
        "riesgo_orig":   round(riesgo_orig, 2),
        "objetivo_pct":  round(objetivo_pct, 2),
    }


def calcular_dca(precio_actual_mxn: float, atr_mxn: float, soportes_mxn: list,
                 capital_total: float, es_etf_3x: bool = False) -> dict:
    """
    Plan de acumulación DCA en 3 escalones.
    TODOS los precios en MXN — sin conversiones internas.
    - precio_actual_mxn: precio actual en pesos
    - atr_mxn: ATR diario en pesos (atr_usd * tc)
    - soportes_mxn: lista de zonas S/R con precio en pesos
    - capital_total: capital disponible en pesos (dividido en 3 partes iguales)
    - es_etf_3x: espaciado mayor entre escalones por volatilidad
    """
    if not capital_total or capital_total <= 0 or precio_actual_mxn <= 0:
        return {}

    mult        = 1.5 if es_etf_3x else 1.0
    cap_escalon = round(capital_total / 3, 2)

    # Soportes ya en MXN — filtrar los que están bajo el precio actual
    soportes_validos = [
        z for z in soportes_mxn
        if z.get("fuerza", 0) >= 2 and z.get("precio_mxn", 0) < precio_actual_mxn
    ]

    if len(soportes_validos) >= 2:
        e1_precio = soportes_validos[0]["precio_mxn"]
        e2_precio = soportes_validos[1]["precio_mxn"] if len(soportes_validos) > 1 else e1_precio * 0.93
        e3_precio = soportes_validos[2]["precio_mxn"] if len(soportes_validos) > 2 else e2_precio * 0.93
        metodo = "soportes S/R"
    elif atr_mxn > 0:
        e1_precio = precio_actual_mxn - (atr_mxn * 1.0 * mult)
        e2_precio = precio_actual_mxn - (atr_mxn * 2.0 * mult)
        e3_precio = precio_actual_mxn - (atr_mxn * 3.0 * mult)
        metodo = "proyección ATR"
    else:
        e1_precio = precio_actual_mxn * (0.93 if es_etf_3x else 0.95)
        e2_precio = precio_actual_mxn * (0.86 if es_etf_3x else 0.90)
        e3_precio = precio_actual_mxn * (0.79 if es_etf_3x else 0.85)
        metodo = "porcentual"

    # Asegurar que los escalones tengan precios positivos y descendentes
    e1_precio = max(e1_precio, precio_actual_mxn * 0.01)
    e2_precio = max(e2_precio, precio_actual_mxn * 0.01)
    e3_precio = max(e3_precio, precio_actual_mxn * 0.01)

    stop_pct = 0.05 if es_etf_3x else 0.03

    escalones = []
    for i, ep in enumerate([e1_precio, e2_precio, e3_precio], 1):
        ep        = round(ep, 2)
        stop_e    = round(ep * (1 - stop_pct), 2)
        titulos_e = round(cap_escalon / ep, 4) if ep > 0 else 0
        dist_pct  = round((ep - precio_actual_mxn) / precio_actual_mxn * 100, 1)
        escalones.append({
            "escalon":   i,
            "precio":    ep,
            "stop":      stop_e,
            "capital":   cap_escalon,
            "titulos":   titulos_e,
            "distancia": dist_pct,
        })

    total_titulos = sum(e["titulos"] for e in escalones)
    costo_prom    = round(capital_total / total_titulos, 2) if total_titulos > 0 else 0

    return {
        "escalones":     escalones,
        "capital_total": capital_total,
        "cap_escalon":   cap_escalon,
        "costo_prom":    costo_prom,
        "total_titulos": round(total_titulos, 4),
        "metodo":        metodo,
        "es_etf_3x":     es_etf_3x,
        "activo":        True,
    }


def detectar_ganga(nombre: str, tf_1d: dict, sr: dict, obv_info: dict,
                   closes: list, volumes: list) -> dict:
    """
    Detecta oportunidades GANGA — acumulación anticipada antes de que los
    indicadores de tendencia se alineen completamente.

    Criterios (solo acciones, NO ETFs 3x):
      1. Soporte fuerte con >= 3 toques históricos debajo del precio actual
      2. OBV subiendo aunque el precio baje (divergencia alcista)
      3. RSI entre 25 y 40 (zona de sobreventa sin colapso)
      4. Volumen bajando en días rojos (vendedores agotándose)

    Retorna dict con: es_ganga (bool), razon (str), criterios_ok (list),
                      fuerza (int 0-4), soportes_fuertes (list)
    """
    if not tf_1d or not tf_1d.get("valido"):
        return {"es_ganga": False, "razon": "", "criterios_ok": [], "fuerza": 0}

    rsi_val  = tf_1d.get("rsi", 50)
    obv_ok   = obv_info.get("div_alcista", False)   # precio baja, OBV sube
    precio   = tf_1d.get("precio", 0)

    # Criterio 1: soporte fuerte (>= 3 toques) debajo del precio
    soportes_fuertes = [
        z for z in sr.get("soportes", [])
        if z.get("fuerza", 0) >= 3 and z.get("precio", precio + 1) < precio
    ]
    soporte_ok = len(soportes_fuertes) > 0

    # Criterio 2: OBV subiendo mientras precio baja
    obv_alcista = obv_ok

    # Criterio 3: RSI en zona de sobreventa sin colapso (25-40)
    rsi_ok = 25 <= rsi_val <= 40

    # Criterio 4: volumen bajando en días rojos (últimas 5 velas)
    vol_agotamiento = False
    if closes and volumes and len(closes) >= 5:
        dias_rojos = [(closes[i] - closes[i-1], volumes[i])
                      for i in range(max(len(closes)-5, 1), len(closes))
                      if closes[i] < closes[i-1]]
        if len(dias_rojos) >= 2:
            vols_rojos   = [v for _, v in dias_rojos]
            vol_media_20 = sum(volumes[-20:]) / max(len(volumes[-20:]), 1)
            vol_agotamiento = all(v < vol_media_20 * 0.85 for v in vols_rojos)

    criterios_ok = []
    if soporte_ok:
        criterios_ok.append(
            f"Soporte fuerte x{soportes_fuertes[0]['fuerza']} toques "
            f"(precio ${soportes_fuertes[0]['precio']:.2f})")
    if obv_alcista:
        criterios_ok.append("OBV sube mientras precio baja — acumulacion institucional")
    if rsi_ok:
        criterios_ok.append(f"RSI {rsi_val:.0f} en zona de sobreventa (25-40)")
    if vol_agotamiento:
        criterios_ok.append("Volumen bajando en dias rojos — vendedores agotandose")

    fuerza   = len(criterios_ok)
    es_ganga = fuerza >= 3   # minimo 3 de 4 criterios

    razon = ""
    if es_ganga:
        razon = ("Zona de soporte fuerte con senales de acumulacion institucional "
                 "antes de que los indicadores de tendencia se alineen. "
                 "Entrada escalonada (DCA) con stops ajustados al soporte.")

    return {
        "es_ganga":         es_ganga,
        "razon":            razon,
        "criterios_ok":     criterios_ok,
        "fuerza":           fuerza,
        "soportes_fuertes": soportes_fuertes,
    }


def render_ganga_panel(ganga: dict) -> str:
    """Panel explicativo del estado GANGA para el detail-panel."""
    if not ganga or not ganga.get("es_ganga"):
        return ""
    criterios  = ganga.get("criterios_ok", [])
    razon      = ganga.get("razon", "")
    sops       = ganga.get("soportes_fuertes", [])
    items_html = "".join(
        f'<li style="margin:3px 0;color:#14532d">{c}</li>' for c in criterios
    )
    sop_html = ""
    if sops:
        sop_html = (f'<div style="margin-top:8px;font-size:10px;color:#14532d">'
                    f'Soporte mas cercano: <strong>${sops[0]["precio"]:.2f}</strong> '
                    f'({sops[0]["fuerza"]}x toques)</div>')
    return (f'<div style="background:#f0fdf4;border:2px solid #86efac;border-radius:8px;'
            f'padding:12px 14px;margin-bottom:10px">'
            f'<div style="font-weight:700;font-size:13px;color:#14532d;margin-bottom:6px">'
            f'GANGA - Acumulacion anticipada</div>'
            f'<div style="font-size:11px;color:#166534;margin-bottom:8px">{razon}</div>'
            f'<div style="font-size:10px;color:#14532d;font-weight:600;margin-bottom:4px">'
            f'Criterios detectados:</div>'
            f'<ul style="font-size:11px;margin:0 0 0 14px">{items_html}</ul>'
            f'{sop_html}'
            f'<div style="margin-top:10px;font-size:10px;color:#166534;'
            f'background:#dcfce7;border-radius:5px;padding:6px 9px">'
            f'Usa el plan DCA de 3 escalones (abajo). '
            f'Primer escalon en el soporte fuerte. Stop bajo el ultimo escalon.</div>'
            f'</div>')


def render_dca_panel(dca: dict, precio_actual_mxn: float) -> str:
    """
    Panel visual del plan DCA — claro, accionable, sin tecnicismos.
    """
    if not dca or not dca.get("activo") or not dca.get("escalones"):
        return '<p class="hint" style="font-size:11px">Sin plan DCA calculado</p>'

    escalones  = dca["escalones"]
    cap_total  = dca["capital_total"]
    cap_e      = dca["cap_escalon"]
    costo_prom = dca["costo_prom"]
    metodo     = dca["metodo"]
    es_etf     = dca.get("es_etf_3x", False)

    aviso_etf = ""
    if es_etf:
        aviso_etf = ('<div style="background:#fff7e6;border:1px solid #ffd591;border-radius:5px;'
                     'padding:6px 10px;font-size:10px;color:#d46b08;margin-bottom:8px">'
                     '⚡ ETF 3x — escalones más espaciados por alta volatilidad. '
                     'Capital total dividido en 3 partes iguales.</div>')

    h = (f'<div style="font-size:11px;margin-bottom:6px;color:var(--muted)">'
         f'Plan de acumulación en 3 escalones · Capital: {fmt(cap_total)} dividido en {fmt(cap_e)} c/u '
         f'· Método: {metodo}</div>'
         f'{aviso_etf}')

    # Precio actual como referencia
    h += (f'<div style="display:flex;align-items:center;gap:8px;padding:5px 0;'
          f'border-bottom:2px solid var(--text);margin-bottom:4px">'
          f'<span style="width:22px;font-size:11px">◆</span>'
          f'<span style="flex:1;font-weight:600">Precio actual</span>'
          f'<span style="font-family:var(--mono);font-weight:600">{fmt(precio_actual_mxn)}</span>'
          f'</div>')

    colors = ["var(--green)", "#16a34a", "#14532d"]
    for i, e in enumerate(escalones):
        col   = colors[min(i, len(colors)-1)]
        dist  = e["distancia"]
        alert = " ⚠️ muy cerca" if abs(dist) < 2 else ""
        h += (f'<div style="display:flex;align-items:center;gap:8px;padding:5px 0;'
              f'border-bottom:1px solid var(--brd)">'
              f'<span style="width:22px;font-size:10px;color:{col};font-weight:600">E{e["escalon"]}</span>'
              f'<span style="flex:1;font-size:11px">'
              f'<span style="color:{col};font-family:var(--mono);font-weight:600">{fmt(e["precio"])}</span>'
              f'<span style="color:var(--muted);font-size:10px"> ({dist:.1f}%{alert})</span>'
              f'</span>'
              f'<span style="font-size:10px;color:var(--muted)">'
              f'{e["titulos"]} tít · stop {fmt(e["stop"])}</span>'
              f'</div>')

    h += (f'<div style="margin-top:8px;background:var(--surface2);border-radius:5px;'
          f'padding:7px 10px;font-size:11px">'
          f'<span style="color:var(--muted)">Si completas los 3 escalones → </span>'
          f'<strong>{dca["total_titulos"]} títulos</strong> a costo promedio '
          f'<strong style="color:var(--green)">{fmt(costo_prom)}</strong> por acción</div>')

    return h



def adx(highs: list, lows: list, closes: list, n: int = 14) -> float:
    """Average Directional Index — mide fuerza de tendencia. >25 = tendencia real."""
    if len(closes) < n+5: return 0.0
    h = pd.Series(highs, dtype=float)
    l = pd.Series(lows,  dtype=float)
    c = pd.Series(closes,dtype=float)
    tr  = pd.concat([h-l, (h-c.shift()).abs(), (l-c.shift()).abs()], axis=1).max(axis=1)
    pdm = (h.diff()).clip(lower=0).where((h.diff())>(l.diff().abs()), 0.0)
    ndm = (l.diff().abs()).clip(lower=0).where((l.diff().abs())>(h.diff()), 0.0)
    atr  = tr.ewm(span=n, adjust=False).mean()
    pdi  = 100 * pdm.ewm(span=n, adjust=False).mean() / atr.replace(0, 1e-9)
    ndi  = 100 * ndm.ewm(span=n, adjust=False).mean() / atr.replace(0, 1e-9)
    dx   = (100 * (pdi-ndi).abs() / (pdi+ndi).replace(0, 1e-9))
    return float(dx.ewm(span=n, adjust=False).mean().iloc[-1])


# ── FILTROS MACRO ──────────────────────────────────────────
# Cache de macro para no repetir calls en la misma corrida
_MACRO_CACHE: dict = {}

def get_vix() -> float:
    """Obtiene el VIX (Fear Index) actual. <18=calma, 18-25=precaución, >25=pánico."""
    global _MACRO_CACHE
    if "vix" in _MACRO_CACHE:
        return _MACRO_CACHE["vix"]
    # Fuente 1: TwelveData
    try:
        r = requests.get(f"{API_BASE}/quote", params={"symbol":"VIX","apikey":API_KEY}, timeout=8)
        d = r.json()
        if "close" in d:
            v = float(d["close"])
            _MACRO_CACHE["vix"] = v
            return v
    except Exception: pass
    # Fuente 2: serie histórica de TwelveData
    try:
        vals = api_timeseries("VIX", "1day", 5, "")
        if vals:
            v = float(vals[-1]["close"])
            _MACRO_CACHE["vix"] = v
            return v
    except Exception: pass
    # Fallback neutro: asumimos VIX moderado para no bloquear todo
    _MACRO_CACHE["vix"] = 20.0
    return 20.0

def get_spy_macro() -> dict:
    """Chequea si SPY (S&P500) está sobre su EMA200 — filtro macro alcista."""
    global _MACRO_CACHE
    if "spy" in _MACRO_CACHE:
        return _MACRO_CACHE["spy"]
    result = {"sobre_ema200": True, "precio": None, "ema200": None}
    try:
        vals = _get_cached("SPY", "1day", "")
        if vals and len(vals) >= 50:
            closes = ohlcv_to_close(vals)
            c = pd.Series(closes, dtype=float)
            e200 = float(ema(c, min(200, len(c)-1)).iloc[-1])
            precio = closes[-1]
            result = {"sobre_ema200": precio > e200, "precio": precio, "ema200": round(e200, 2)}
    except Exception: pass
    _MACRO_CACHE["spy"] = result
    return result

def regimen_mercado(vix: float, spy: dict) -> dict:
    """
    Clasifica el régimen actual del mercado.
    VERDE: VIX<18 + SPY sobre EMA200 → comprar con normalidad
    AMARILLO: VIX 18-25 o SPY cerca de EMA200 → solo alta convicción
    ROJO: VIX>25 o SPY bajo EMA200 → no comprar, evaluar salidas
    """
    spy_ok = spy.get("sobre_ema200", True)
    if vix < 18 and spy_ok:
        return {"color":"verde","label":"🟢 Mercado tranquilo","penalizacion":0,
                "mensaje":"Condiciones favorables para swing trading."}
    elif vix > 25 or not spy_ok:
        return {"color":"rojo","label":"🔴 Mercado en pánico",  "penalizacion":2,
                "mensaje":"⚠️ VIX elevado o S&P500 bajista. NO es momento de comprar. Evalúa stops."}
    else:
        return {"color":"amarillo","label":"🟡 Mercado precavido","penalizacion":1,
                "mensaje":"Cautela: solo entrar con score ≥7/8 y tendencia muy clara."}


# ── ETFs APALANCADOS ───────────────────────────────────────
_ETFS_APALANCADOS = {
    "SOXL","TQQQ","SPXL","UPRO","LABU","TECL","FNGU",
    "SOXS","SQQQ","SPXS","DPST","TNA","NAIL","HIBL",
    "WEBL","DFEN","WANT","PILL","CURE","MIDU",
}

def es_etf_apalancado(ticker: str) -> bool:
    return ticker.upper() in _ETFS_APALANCADOS

def score_minimo_entrada(ticker: str, vix: float) -> int:
    """Score mínimo para señal BUY según tipo de activo y condición macro."""
    if es_etf_apalancado(ticker):
        return 8 if vix > 20 else 7   # ETF 3x: exige casi perfección
    return 7 if vix > 20 else 6       # Acción normal: 6/8 en calma, 7/8 con miedo


# ── HISTORIAL DE SCORES (BD) ──────────────────────────────
def init_score_history():
    """Crea la tabla score_history si no existe."""
    con = sqlite3.connect(DB_FILE)
    con.execute("""
    CREATE TABLE IF NOT EXISTS score_history (
        id      INTEGER PRIMARY KEY AUTOINCREMENT,
        ticker  TEXT NOT NULL,
        fecha   TEXT NOT NULL,
        score   INTEGER,
        senal   TEXT,
        vix     REAL,
        precio  REAL,
        estado  TEXT
    )""")
    con.commit(); con.close()

def guardar_score(ticker: str, score: int, senal: str, vix: float,
                  precio: float, estado: str):
    """
    Guarda score en historial — máximo 1 entrada por ticker por día.
    Si ya existe una entrada hoy, la actualiza (no duplica).
    Así el historial acumula día a día correctamente.
    """
    from datetime import timezone, timedelta
    tz_mx  = timezone(timedelta(hours=-6))
    hoy    = datetime.now(tz_mx).strftime("%Y-%m-%d")
    ahora  = datetime.now(tz_mx).strftime("%Y-%m-%d %H:%M")
    con    = sqlite3.connect(DB_FILE)
    # Verificar si ya hay entrada de hoy para este ticker
    existe = con.execute(
        "SELECT id FROM score_history WHERE ticker=? AND fecha LIKE ?",
        (ticker.upper(), f"{hoy}%")
    ).fetchone()
    if existe:
        # Actualizar la entrada de hoy con los datos más recientes
        con.execute(
            "UPDATE score_history SET fecha=?,score=?,senal=?,vix=?,precio=?,estado=? WHERE id=?",
            (ahora, score, senal, round(vix,2), round(precio,4), estado, existe[0])
        )
    else:
        # Nueva entrada para este día
        con.execute(
            "INSERT INTO score_history (ticker,fecha,score,senal,vix,precio,estado) VALUES(?,?,?,?,?,?,?)",
            (ticker.upper(), ahora, score, senal, round(vix,2), round(precio,4), estado)
        )
    con.commit(); con.close()

def get_score_previo(ticker: str) -> dict | None:
    """Obtiene el penúltimo score guardado para detectar caídas."""
    con = sqlite3.connect(DB_FILE); con.row_factory = sqlite3.Row
    rows = con.execute(
        "SELECT score,senal,precio,fecha FROM score_history WHERE ticker=? ORDER BY id DESC LIMIT 2",
        (ticker.upper(),)
    ).fetchall()
    con.close()
    if len(rows) >= 2:
        return dict(rows[1])   # penúltimo = sesión anterior
    return None


def analizar_score_drop(ticker: str, score_actual: int) -> dict:
    """
    SCORE DROP con historial real de las ultimas 5 sesiones.
    Detecta:
      Caida puntual  : score bajo 2+ pts en la ultima sesion
      Caida sostenida: score bajando 3+ sesiones consecutivas
      Pico y declive : llego a maximo y cayo 3+ pts desde el
    Retorna severidad (none|warning|alert|critical) y descripcion.
    """
    con = sqlite3.connect(DB_FILE); con.row_factory = sqlite3.Row
    rows = con.execute(
        "SELECT score, fecha FROM score_history WHERE ticker=? ORDER BY id DESC LIMIT 6",
        (ticker.upper(),)
    ).fetchall()
    con.close()

    if len(rows) < 2:
        return {"severidad": "none", "desc": "", "caida_pts": 0, "sesiones_baja": 0}

    scores  = [r["score"] for r in rows]   # [mas_reciente ... mas_antiguo]
    hist    = [score_actual] + scores      # insertamos el actual al frente

    caida_puntual    = hist[0] - hist[1]
    pico_reciente    = max(hist[1:])
    caida_desde_pico = pico_reciente - hist[0]

    sesiones_baja = 0
    for i in range(len(hist) - 1):
        if hist[i] < hist[i+1]:
            sesiones_baja += 1
        else:
            break

    if caida_puntual >= 3 or (caida_desde_pico >= 4 and sesiones_baja >= 3):
        severidad = "critical"
        desc = (f"Score cayo {caida_puntual} pts en ultima sesion" if caida_puntual >= 3
                else f"Declive sostenido: -{caida_desde_pico} pts en {sesiones_baja} sesiones")
    elif caida_puntual >= 2 or (caida_desde_pico >= 3 and sesiones_baja >= 2):
        severidad = "alert"
        desc = (f"Score cayo {caida_puntual} pts vs sesion anterior" if caida_puntual >= 2
                else f"Score bajando {sesiones_baja} sesiones consecutivas ({pico_reciente}->{hist[0]})")
    elif sesiones_baja >= 2 or caida_desde_pico >= 2:
        severidad = "warning"
        desc = f"Score en tendencia bajista ({sesiones_baja} sesiones, -{caida_desde_pico} pts desde pico)"
    else:
        severidad = "none"
        desc = ""

    return {
        "severidad":     severidad,
        "desc":          desc,
        "caida_pts":     caida_puntual,
        "caida_pico":    caida_desde_pico,
        "sesiones_baja": sesiones_baja,
        "pico_reciente": pico_reciente,
        "hist_scores":   hist[:5],
    }



# ══════════════════════════════════════════════════════════
#   MÓDULO DE ZONAS DE SOPORTE / RESISTENCIA
#   Detecta automáticamente zonas clave usando:
#     · Pivotes de precio (máximos/mínimos locales)
#     · Clustering de zonas cercanas (tolerancia 1.5%)
#     · Clasificación por fuerza (toques × volumen)
#     · Relación precio actual → SOPORTE / RESISTENCIA / EN ZONA
# ══════════════════════════════════════════════════════════

def detectar_pivotes(highs: list, lows: list, closes: list,
                     volumes: list, orden: int = 3) -> dict:
    """
    Detecta pivotes de precio locales con ventana `orden`.
    Un pivote alto = máximo local rodeado de `orden` velas menores.
    Un pivote bajo = mínimo local rodeado de `orden` velas mayores.
    Retorna listas de pivotes con precio, índice y volumen promedio.
    """
    n = len(closes)
    if n < orden * 2 + 1:
        return {"altos": [], "bajos": []}

    altos, bajos = [], []

    for i in range(orden, n - orden):
        ventana_h = highs[i - orden: i + orden + 1]
        ventana_l = lows [i - orden: i + orden + 1]
        vol_zona  = float(pd.Series(volumes[i - orden: i + orden + 1]).mean())

        if highs[i] == max(ventana_h):
            altos.append({"precio": highs[i], "idx": i, "vol": vol_zona})
        if lows[i] == min(ventana_l):
            bajos.append({"precio": lows[i],  "idx": i, "vol": vol_zona})

    return {"altos": altos, "bajos": bajos}


def agrupar_zonas(pivotes: list, precio_ref: float,
                  tolerancia_pct: float = 0.015) -> list:
    """
    Agrupa pivotes cercanos (dentro de tolerancia_pct) en una zona.
    Cada zona tiene: precio_centro, fuerza (nº toques), vol_medio,
    distancia_pct al precio actual.
    """
    if not pivotes:
        return []

    pivotes_ord = sorted(pivotes, key=lambda x: x["precio"])
    zonas = []
    grupo = [pivotes_ord[0]]

    for p in pivotes_ord[1:]:
        ref = grupo[-1]["precio"]
        if abs(p["precio"] - ref) / ref <= tolerancia_pct:
            grupo.append(p)
        else:
            zonas.append(grupo)
            grupo = [p]
    zonas.append(grupo)

    resultado = []
    for g in zonas:
        centro = float(pd.Series([x["precio"] for x in g]).mean())
        vol_m  = float(pd.Series([x["vol"]    for x in g]).mean())
        dist   = (centro - precio_ref) / precio_ref * 100
        resultado.append({
            "precio":       round(centro, 4),
            "fuerza":       len(g),           # número de toques
            "vol_medio":    round(vol_m, 0),
            "distancia_pct": round(dist, 2),  # % desde precio actual
        })

    return sorted(resultado, key=lambda x: abs(x["distancia_pct"]))


def calcular_zonas_sr(highs: list, lows: list, closes: list,
                       volumes: list, tc: float = 1.0,
                       origen: str = "USA") -> dict:
    """
    Función principal del módulo S/R.
    Devuelve:
      soportes   : zonas BAJO el precio actual (ordenadas por cercanía)
      resistencias: zonas SOBRE el precio actual
      en_zona    : True si el precio está dentro de una zona clave (±1%)
      zona_actual: descripción de la zona si en_zona=True
      objetivo_sr: primer nivel de resistencia (meta de ganancia)
      stop_sr    : primer nivel de soporte (stop natural del mercado)
      contexto   : texto explicativo para el dashboard
    """
    if not highs or len(highs) < 20:
        return {"soportes": [], "resistencias": [], "en_zona": False,
                "zona_actual": "", "objetivo_sr": None, "stop_sr": None,
                "contexto": "Sin datos suficientes"}

    mult   = tc if origen == "USA" else 1.0
    precio = closes[-1]

    pivotes = detectar_pivotes(highs, lows, closes, volumes, orden=3)
    zonas_a = agrupar_zonas(pivotes["altos"], precio)
    zonas_b = agrupar_zonas(pivotes["bajos"],  precio)

    # Clasificar: soportes = zonas BAJO el precio, resistencias = zonas SOBRE
    soportes     = [z for z in zonas_b if z["distancia_pct"] <= 0]
    resistencias = [z for z in zonas_a if z["distancia_pct"] >= 0]

    # Ordenar: soportes de más cercano a más lejano (menos negativo primero)
    soportes     = sorted(soportes,     key=lambda x: -x["distancia_pct"])
    resistencias = sorted(resistencias, key=lambda x:  x["distancia_pct"])

    # Zona actual: ¿está el precio dentro de ±1% de una zona clave?
    en_zona, zona_actual = False, ""
    todas = zonas_a + zonas_b
    for z in todas:
        if abs(z["distancia_pct"]) <= 1.0 and z["fuerza"] >= 2:
            en_zona = True
            tipo    = "resistencia" if z["distancia_pct"] >= 0 else "soporte"
            zona_actual = (f"Precio en zona de {tipo} "
                           f"(${z['precio']*mult:,.2f} MXN, {z['fuerza']} toques)")
            break

    objetivo_sr = resistencias[0]["precio"] * mult if resistencias else None
    stop_sr     = soportes[0]["precio"]     * mult if soportes     else None

    # Contexto narrativo
    n_sop  = len(soportes)
    n_res  = len(resistencias)
    if en_zona:
        contexto = f"⚡ {zona_actual}. Posible rebote o ruptura inminente."
    elif n_sop == 0:
        contexto = "⚠️ Sin soporte visible por debajo — riesgo de caída libre."
    elif n_res == 0:
        contexto = "🚀 Sin resistencia visible — espacio libre al alza."
    else:
        s1 = soportes[0]["precio"] * mult
        r1 = resistencias[0]["precio"] * mult
        contexto = (f"Soporte próximo ${s1:,.2f} MXN ({soportes[0]['distancia_pct']:.1f}%) · "
                    f"Resistencia próxima ${r1:,.2f} MXN (+{resistencias[0]['distancia_pct']:.1f}%)")

    return {
        "soportes":     soportes[:5],          # top 5 más cercanos
        "resistencias": resistencias[:5],
        "en_zona":      en_zona,
        "zona_actual":  zona_actual,
        "objetivo_sr":  round(objetivo_sr, 2) if objetivo_sr else None,
        "stop_sr":      round(stop_sr,     2) if stop_sr     else None,
        "contexto":     contexto,
        "mult":         mult,
    }


def render_zonas_sr(sr: dict, precio_actual_mxn: float, tc: float) -> str:
    """
    Renderiza el panel de zonas S/R para insertar en el detail-panel
    del scanner y radar.
    """
    if not sr or not (sr.get("soportes") or sr.get("resistencias")):
        return '<p class="hint">Sin zonas identificadas</p>'

    mult       = sr.get("mult", tc)
    soportes   = sr.get("soportes",   [])
    resistencias= sr.get("resistencias", [])
    en_zona    = sr.get("en_zona", False)
    contexto   = sr.get("contexto", "")

    # Cabecera de contexto
    ctx_color = "#0958d9" if en_zona else "var(--muted)"
    ctx_bg    = "#e6f4ff" if en_zona else "var(--surface2)"
    h = (f'<div style="background:{ctx_bg};border-radius:5px;padding:7px 10px;'
         f'margin-bottom:8px;font-size:11px;color:{ctx_color}">{contexto}</div>')

    # Tabla unificada: resistencias arriba, precio en el medio, soportes abajo
    h += '<div style="font-size:11px">'

    # Resistencias (de más lejana a más cercana = orden descendente de precio)
    for z in reversed(resistencias[:4]):
        p_mxn  = z["precio"] * mult
        dist   = z["distancia_pct"]
        fuerza = z["fuerza"]
        bar_w  = min(fuerza * 20, 100)
        cerca  = abs(dist) <= 3
        h += (f'<div style="display:flex;align-items:center;gap:8px;padding:4px 0;'
              f'border-bottom:1px solid var(--brd);'
              f'{"background:#fff7e6;" if cerca else ""}">'
              f'<span style="width:20px;font-size:10px;color:var(--red)">R</span>'
              f'<span style="flex:1;font-family:var(--mono);color:var(--red);font-weight:{"600" if cerca else "400"}">'
              f'${p_mxn:,.2f}</span>'
              f'<span style="color:var(--muted);font-size:10px">+{dist:.1f}%</span>'
              f'<div style="width:40px;height:4px;background:var(--brd2);border-radius:2px">'
              f'<div style="width:{bar_w}%;height:100%;background:var(--red);border-radius:2px"></div></div>'
              f'<span style="font-size:9px;color:var(--muted)">{fuerza}×</span></div>')

    # Precio actual
    h += (f'<div style="display:flex;align-items:center;gap:8px;padding:5px 0;'
          f'border-bottom:1px solid var(--brd);border-top:2px solid var(--text)">'
          f'<span style="width:20px;font-size:10px;color:var(--text)">◆</span>'
          f'<span style="flex:1;font-family:var(--mono);font-weight:600">'
          f'${precio_actual_mxn:,.2f} <span style="font-size:10px;font-weight:400;color:var(--muted)">precio actual</span></span>'
          f'</div>')

    # Soportes (de más cercano a más lejano = orden descendente de precio)
    for z in soportes[:4]:
        p_mxn  = z["precio"] * mult
        dist   = z["distancia_pct"]
        fuerza = z["fuerza"]
        bar_w  = min(fuerza * 20, 100)
        cerca  = abs(dist) <= 3
        h += (f'<div style="display:flex;align-items:center;gap:8px;padding:4px 0;'
              f'border-bottom:1px solid var(--brd);'
              f'{"background:#f6ffed;" if cerca else ""}">'
              f'<span style="width:20px;font-size:10px;color:var(--green)">S</span>'
              f'<span style="flex:1;font-family:var(--mono);color:var(--green);font-weight:{"600" if cerca else "400"}">'
              f'${p_mxn:,.2f}</span>'
              f'<span style="color:var(--muted);font-size:10px">{dist:.1f}%</span>'
              f'<div style="width:40px;height:4px;background:var(--brd2);border-radius:2px">'
              f'<div style="width:{bar_w}%;height:100%;background:var(--green);border-radius:2px"></div></div>'
              f'<span style="font-size:9px;color:var(--muted)">{fuerza}×</span></div>')

    h += '</div>'
    h += '<p style="font-size:9px;color:var(--hint);margin-top:5px">R=resistencia · S=soporte · ×=toques históricos</p>'
    return h


def render_score_history(ticker: str, score_actual: int, tc_mult: float = 1.0) -> str:
    """
    Renderiza el mini-historial de scores de las últimas 6 sesiones.
    Muestra: sparkline visual + tabla compacta + badge de tendencia.
    """
    con = sqlite3.connect(DB_FILE); con.row_factory = sqlite3.Row
    rows = con.execute(
        "SELECT score, fecha, estado, precio FROM score_history "
        "WHERE ticker=? ORDER BY id DESC LIMIT 6",
        (ticker.upper(),)
    ).fetchall()
    con.close()

    if not rows:
        return '<p class="hint" style="font-size:10px">Sin historial aún — se acumulará con cada actualización</p>'

    hist = [{"score": r["score"], "fecha": r["fecha"][:10],
             "estado": r["estado"], "precio": r["precio"]} for r in rows]
    hist_scores = [h["score"] for h in hist]

    # Tendencia
    if len(hist_scores) >= 2:
        delta = hist_scores[0] - hist_scores[1]
        if delta > 0:   tend_icon, tend_col, tend_txt = "↑", "var(--green)", f"+{delta} pts"
        elif delta < 0: tend_icon, tend_col, tend_txt = "↓", "var(--red)",   f"{delta} pts"
        else:           tend_icon, tend_col, tend_txt = "→", "var(--muted)", "sin cambio"
    else:
        tend_icon, tend_col, tend_txt = "—", "var(--muted)", "primera sesión"

    # Sparkline SVG (mini gráfico de línea)
    total_c = 10   # máximo teórico
    max_s   = max(hist_scores + [1])
    pts     = hist_scores[::-1]   # cronológico: más antiguo → más reciente
    n_pts   = len(pts)
    w, h_svg= 80, 24
    xs = [int(i / max(n_pts - 1, 1) * w) for i in range(n_pts)]
    ys = [int((1 - p / total_c) * h_svg) for p in pts]
    pts_str   = " ".join(f"{x},{y}" for x,y in zip(xs,ys))
    dot_x, dot_y = xs[-1], ys[-1]
    sparkline = (f'<svg width="{w}" height="{h_svg}" style="vertical-align:middle;margin-right:6px">'
                 f'<polyline points="{pts_str}" '
                 f'fill="none" stroke="var(--text)" stroke-width="1.5" stroke-linejoin="round"/>'
                 f'<circle cx="{dot_x}" cy="{dot_y}" r="2" fill="var(--red)"/>'
                 f'</svg>')

    # Tabla compacta
    rows_html = ""
    for i, h_row in enumerate(hist):
        s    = h_row["score"]
        col  = "#52c41a" if s >= 7 else "#faad14" if s >= 5 else "#ff4d4f"
        est  = h_row["estado"] or "—"
        fecha= h_row["fecha"]
        bold = "font-weight:600;" if i == 0 else ""
        rows_html += (f'<div style="display:flex;gap:6px;align-items:center;'
                      f'padding:2px 0;font-size:10px;{bold}">'
                      f'<span style="color:var(--hint);width:55px">{fecha}</span>'
                      f'<span style="font-family:var(--mono);color:{col};width:28px">{s}/10</span>'
                      f'<span style="color:var(--muted);flex:1">{est}</span>'
                      f'</div>')

    return (f'<div style="display:flex;align-items:center;gap:8px;margin-bottom:6px">'
            f'{sparkline}'
            f'<span style="font-size:12px;font-weight:600;color:{tend_col}">'
            f'{tend_icon} {tend_txt}</span></div>'
            f'{rows_html}')


def render_obv_panel(obv_info: dict) -> str:
    """Panel OBV — flujo institucional. Claro y directo."""
    if not obv_info or obv_info.get("tendencia") == "sin datos":
        return '<p class="hint" style="font-size:11px">Sin datos de volumen</p>'

    tend   = obv_info.get("tendencia","—")
    div    = obv_info.get("divergencia", False)
    div_t  = obv_info.get("div_tipo","")
    ok     = obv_info.get("ok", True)

    if obv_info.get("div_bajista"):
        bg, col, icon = "#fff1f0","#7f1d1d","⚠️"
        msg = "Precio sube pero el volumen institucional BAJA — posible trampa al alza"
    elif obv_info.get("div_alcista"):
        bg, col, icon = "#f0fdf4","#14532d","💡"
        msg = "Precio baja pero el volumen institucional SUBE — posible acumulación"
    elif tend == "alcista":
        bg, col, icon = "#f0fdf4","#14532d","✅"
        msg = "Flujo institucional alcista — confirma la tendencia del precio"
    else:
        bg, col, icon = "#fffbeb","#78350f","→"
        msg = "Flujo institucional bajista — sin presión compradora institucional"

    return (f'<div style="background:{bg};border-radius:6px;padding:9px 12px;font-size:11px;color:{col}">'
            f'<div style="font-weight:600;margin-bottom:3px">{icon} OBV: {tend.title()}</div>'
            f'<div style="opacity:.85">{msg}</div>'
            f'{"<div style=margin-top:4px;font-size:10px;font-style:italic>" + div_t + "</div>" if div_t else ""}'
            f'</div>')


def render_sector_panel(sector_info: dict) -> str:
    """Panel de sector ETF — contexto del mercado para ese ticker."""
    if not sector_info or not sector_info.get("etf"):
        return '<p class="hint" style="font-size:11px">Sector no identificado</p>'

    etf     = sector_info["etf"]
    alcista = sector_info["alcista"]
    desc    = sector_info.get("desc","")
    pct     = sector_info.get("pct", 0)
    precio  = sector_info.get("precio")
    ema50   = sector_info.get("ema50")

    bg  = "#f0fdf4" if alcista else "#fff1f0"
    col = "#14532d" if alcista else "#7f1d1d"
    icon= "✅" if alcista else "❌"
    msg = (f"El sector {etf} está {'por ENCIMA' if alcista else 'por DEBAJO'} de su EMA50 "
           f"({pct:+.1f}%). "
           + ("El contexto del sector favorece la operación." if alcista
              else "El sector entero está bajista — riesgo elevado de seguir cayendo."))

    return (f'<div style="background:{bg};border-radius:6px;padding:9px 12px;font-size:11px;color:{col}">'
            f'<div style="font-weight:600;margin-bottom:3px">{icon} Sector: {etf}</div>'
            f'<div style="opacity:.85">{msg}</div>'
            f'{"<div style=margin-top:4px;font-family:var(--mono);font-size:10px>ETF precio: $" + str(precio) + " · EMA50: $" + str(ema50) + "</div>" if precio else ""}'
            f'</div>')


def render_gestion_panel(gestion: dict, precio_actual_mxn: float, stop_mxn: float) -> str:
    """Panel de gestión de posición abierta — qué hacer ahora con la posición."""
    if not gestion:
        return '<p class="hint" style="font-size:11px">Registra tu precio de entrada para ver la gestión</p>'

    color   = gestion.get("color","var(--muted)")
    estado  = gestion.get("estado_op","—")
    accion  = gestion.get("accion","—")
    pct     = gestion.get("ganancia_pct",0)
    be      = gestion.get("breakeven")
    parc    = gestion.get("parciales_50")

    pct_str = f"{pct:+.1f}%"
    pct_col = "var(--green)" if pct >= 0 else "var(--red)"

    h = (f'<div style="border-left:3px solid {color};padding:9px 12px;background:var(--surface2);border-radius:0 6px 6px 0">'
         f'<div style="font-weight:600;font-size:12px;color:{color};margin-bottom:5px">{estado}</div>'
         f'<div style="font-size:11px;margin-bottom:8px">{accion}</div>'
         f'<div style="display:flex;gap:12px;flex-wrap:wrap;font-size:11px">')

    h += f'<div><span style="color:var(--muted)">P&L: </span><span style="color:{pct_col};font-weight:600;font-family:var(--mono)">{pct_str}</span></div>'

    if be:
        h += f'<div><span style="color:var(--muted)">Breakeven: </span><span style="font-family:var(--mono)">{fmt(be)}</span></div>'
    if parc:
        h += f'<div><span style="color:var(--muted)">50% parciales: </span><span style="font-family:var(--mono);color:var(--green)">{fmt(parc)}</span></div>'
    if stop_mxn:
        h += f'<div><span style="color:var(--muted)">Stop: </span><span style="font-family:var(--mono);color:var(--red)">{fmt(stop_mxn)}</span></div>'

    h += '</div></div>'
    return h


def detectar_exit(ticker: str, tf_1d: dict, score_actual: int) -> dict:
    """
    Evalúa si hay señal de salida. Devuelve dict con flag y razones.
    Señal EXIT si:
      • Score cayó ≥2 puntos vs sesión anterior
      • RSI cruzó debajo de 50
      • Precio cerró debajo de EMA9
    """
    razones = []
    nivel = "ok"   # ok | warning | exit

    if not tf_1d.get("valido"):
        return {"exit": False, "nivel": "ok", "razones": []}

    rsi_val = tf_1d.get("rsi", 50)
    precio  = tf_1d.get("precio", 0)
    ema9    = tf_1d.get("ema9", 0)
    criterios = tf_1d.get("criterios", {})

    # Criterio 1: precio debajo de EMA9
    if precio < ema9:
        razones.append(f"Precio ${precio:.2f} < EMA9 ${ema9:.2f} — soporte perdido")
        nivel = "warning"

    # Criterio 2: RSI debajo de 50
    if rsi_val < 50:
        razones.append(f"RSI {rsi_val:.0f} < 50 — momentum muerto")
        nivel = "warning"

    # Criterio 3: MACD negativo
    if not criterios.get("macd", {}).get("ok", True):
        razones.append("MACD bajista — vendedores en control")

    # Criterio 4: Score Drop usando historial real
    score_drop = analizar_score_drop(ticker, score_actual)
    caida_score = score_drop["caida_pts"]
    if score_drop["severidad"] in ("alert", "critical"):
        razones.append(f"Score Drop {score_drop['severidad'].upper()}: {score_drop['desc']}")
        nivel = "exit" if score_drop["severidad"] == "critical" else "warning"

    # EXIT definitivo: ≥3 razones O score drop critico O precio+RSI ambos malos
    precio_bajo = precio < ema9
    rsi_bajo    = rsi_val < 50
    if len(razones) >= 3 or score_drop["severidad"] == "critical" or (precio_bajo and rsi_bajo):
        nivel = "exit"

    return {"exit": nivel == "exit", "nivel": nivel, "razones": razones,
            "score_drop": score_drop}

# ══════════════════════════════════════════════════════════
#   MOTOR DE DECISIÓN — evaluar_setup()
#   Aplica las 14 reglas en orden jerárquico.
#   Devuelve: estado, tipo_setup, bloqueadores, advertencias, decision_final
# ══════════════════════════════════════════════════════════
def evaluar_setup(nombre: str, tf_1d: dict, tfs: dict,
                  vix: float, spy: dict, tit_cartera: float,
                  exit_info: dict) -> dict:
    """
    Motor de decisión de alta convicción.
    Orden de evaluación:
      1. Bloqueos duros  → si hay alguno, estado = BLOQUEADO / LATERAL / RUPTURA
      2. EXIT activo     → prioridad absoluta si hay posición abierta
      3. Validación MTF  → 1H no puede estar vendiendo si 1D compra
      4. Clasificación   → Pullback vs Breakout
      5. Decisión final  → BUY / WATCH / SKIP / SHORT / ROCKET
    """
    if not tf_1d or not tf_1d.get("valido"):
        return {"estado":"SKIP","tipo_setup":"—","bloqueadores":[],
                "advertencias":[],"decision_final":"Sin datos técnicos.","confianza":0}

    c      = {k: v["ok"] for k, v in tf_1d.get("criterios", {}).items()}
    score  = tf_1d.get("score", 0)
    total  = tf_1d.get("total_criterios", 9)
    precio = tf_1d.get("precio", 0)
    ema9   = tf_1d.get("ema9", 0)
    ema21  = tf_1d.get("ema21", 0)
    ema50  = tf_1d.get("ema50", 0)
    ema200 = tf_1d.get("ema200", 0)
    soporte= tf_1d.get("soporte", 0)
    stop   = tf_1d.get("stop", 0)
    obj    = tf_1d.get("objetivo", 0)
    rsi_v  = tf_1d.get("rsi", 50)
    adx_v  = tf_1d.get("adx", 0)
    rr_v   = tf_1d.get("rr", 0)
    vol_ok = tf_1d.get("vol_ok", False)
    macd_ok= tf_1d.get("macd_alcista", False)
    exp    = tf_1d.get("explosion", False)

    regimen  = regimen_mercado(vix, spy)
    etf_3x   = es_etf_apalancado(nombre)
    spy_ok   = spy.get("sobre_ema200", True)
    min_score= score_minimo_entrada(nombre, vix)

    bloqueadores  = []   # razones que impiden entrada
    advertencias  = []   # razones que piden cautela
    pasa_filtros  = True

    # ── BLOQUEOS DUROS (cualquiera cancela la entrada) ────────────────────

    # R1: Mercado lateral — ADX < 20
    if adx_v < 20:
        bloqueadores.append(f"ADX {adx_v:.0f} < 20 — mercado lateral, señales técnicas no son fiables")
        pasa_filtros = False

    # R2: Precio bajo EMA9 — no entrar en debilidad
    if precio < ema9:
        bloqueadores.append(f"Precio ${precio:.2f} < EMA9 ${ema9:.2f} — entrada en debilidad prohibida")
        pasa_filtros = False

    # R3: Soporte roto
    if not c.get("soporte", True):
        bloqueadores.append(f"Soporte ${soporte:.2f} roto — señal de ruptura bajista")
        pasa_filtros = False

    # R4: Volumen sin confirmación (obligatorio para entrada)
    if not vol_ok:
        bloqueadores.append("Volumen < 1.5x media — movimiento sin confirmación institucional")
        pasa_filtros = False

    # R5: Filtro régimen SPY
    if not spy_ok:
        bloqueadores.append("S&P500 bajo EMA200 — mercado bajista macro, no comprar")
        pasa_filtros = False

    # R6: VIX en pánico y es ETF apalancado
    if etf_3x and vix > 20:
        bloqueadores.append(f"ETF 3x con VIX {vix:.1f} > 20 — volatility decay activo, prohibido")
        pasa_filtros = False

    # ── ADVERTENCIAS (no bloquean pero reducen confianza) ─────────────────

    # R7: MULTI-TIMEFRAME — solo 1W bloquea (1H eliminado, no disponible en plan free)
    tf_1w = tfs.get("1W", {})
    w1_senal = tf_1w.get("senal") if tf_1w.get("valido") else None
    w1_score = tf_1w.get("score", 0) if tf_1w.get("valido") else None

    # 1W vendiendo = bloqueo duro (operar contra tendencia semanal es suicidio)
    if w1_senal == "VENDER":
        bloqueadores.append(f"1W en VENDER (score {w1_score}/10) — tendencia semanal bajista, NO entrar")
        pasa_filtros = False

    # R8: SCORE DROP — usa historial real de BD
    score_drop = analizar_score_drop(nombre, score)
    if score_drop["severidad"] == "critical":
        bloqueadores.append(f"SCORE DROP CRITICO: {score_drop['desc']}")
        pasa_filtros = False
    elif score_drop["severidad"] == "alert":
        advertencias.append(f"Score Drop ALERTA: {score_drop['desc']}")
    elif score_drop["severidad"] == "warning":
        advertencias.append(f"Score Drop: {score_drop['desc']}")

    # R9: ESTRUCTURA HH/HL bajista = bloqueo duro
    estructura_1d = tf_1d.get("estructura", {})
    if estructura_1d.get("estructura") == "bajista":
        bloqueadores.append(f"Estructura LH+LL en 1D — price action bajista confirmado, NO entrar")
        pasa_filtros = False

    # R10: RSI sobrecomprado (advertencia)
    if rsi_v > 72:
        advertencias.append(f"RSI {rsi_v:.0f} > 72 — sobrecomprado, riesgo de pullback inmediato")

    # R11: R:R insuficiente (advertencia)
    if rr_v < 3:
        advertencias.append(f"R:R {rr_v:.1f}x < 3x — recompensa insuficiente para el riesgo")

    # ── DETECTAR ESTADO BASE ──────────────────────────────────────────────

    # Prioridad máxima: EXIT en posición abierta
    if exit_info and exit_info.get("exit") and tit_cartera > 0:
        return {
            "estado": "EXIT",
            "tipo_setup": "Salida urgente",
            "bloqueadores": exit_info.get("razones", []),
            "advertencias": advertencias,
            "decision_final": "⚠️ SALIR AHORA. Múltiples señales de deterioro activas. Respetar stop.",
            "confianza": 0,
            "pasa_filtros": False,
            "score_drop": score_drop,
        }

    # Estado por bloqueadores
    if not pasa_filtros:
        if adx_v < 20:
            estado_base = "LATERAL"
        elif not c.get("soporte", True):
            estado_base = "RUPTURA"
        else:
            estado_base = "BLOQUEADO"
        razon_principal = bloqueadores[0] if bloqueadores else "Filtros de seguridad activos"
        return {
            "estado": estado_base,
            "tipo_setup": "Bloqueado",
            "bloqueadores": bloqueadores,
            "advertencias": advertencias,
            "decision_final": f"🚫 NO ENTRAR: {razon_principal}",
            "confianza": 0,
            "pasa_filtros": False,
            "score_drop": score_drop,
        }

    # ── CLASIFICAR SETUP (solo si pasó todos los filtros) ────────────────

    # Pullback: precio retraza hacia EMA9/21, MACD ligeramente negativo pero RSI en zona neutra
    # Breakout: precio supera máximo reciente con volumen, MACD positivo, ADX acelerando
    precio_cerca_ema9 = abs(precio - ema9) / ema9 < 0.015   # dentro del 1.5% de EMA9
    nuevo_maximo_20   = precio >= obj * 0.97                 # cerca del máximo de 20 velas
    adx_acelerando    = adx_v >= 25

    if nuevo_maximo_20 and adx_acelerando and vol_ok:
        tipo_setup = "Breakout"
        setup_nota = "Ruptura de máximos con volumen y tendencia fuerte. Entrada agresiva válida."
    elif precio_cerca_ema9 and macd_ok and rsi_v < 65:
        tipo_setup = "Pullback"
        setup_nota = "Retroceso a EMA9 con momentum positivo. Entrada ideal en zona de soporte dinámico."
    elif c.get("emas") and macd_ok:
        tipo_setup = "Tendencia"
        setup_nota = "Tendencia alcista establecida. Entrada en continuación."
    else:
        tipo_setup = "Setup mixto"
        setup_nota = "Señales mixtas. Esperar mayor claridad antes de entrar."

    # ── SCORE AJUSTADO Y CONFIANZA FINAL ─────────────────────────────────
    penalizacion   = regimen["penalizacion"]
    score_ajustado = max(0, score - penalizacion)
    bonus_setup    = 1 if tipo_setup in ("Breakout", "Pullback") else 0
    confianza      = min(100, int((score_ajustado / total * 100) + bonus_setup * 10 - len(advertencias) * 5))

    # ── ESTADO FINAL ──────────────────────────────────────────────────────
    if exp and score_ajustado >= 8:
        estado_final = "ROCKET"
        decision     = f"🚀 EXPLOSIÓN: {setup_nota} Confianza {confianza}%."
    elif score_ajustado >= min_score and c.get("emas") and c.get("ema200") and macd_ok:
        estado_final = "BUY"
        decision     = f"✅ COMPRAR ({tipo_setup}): {setup_nota} Confianza {confianza}%. Stop obligatorio en ${stop:.2f}."
    elif score_ajustado >= 5:
        estado_final = "WATCH"
        adv_txt = " | ".join(advertencias[:2]) if advertencias else "Seguir monitoreando."
        decision = f"👁 VIGILAR: {adv_txt}"
    elif score_ajustado <= 2:
        estado_final = "SHORT"
        decision     = "↓ BAJISTA: Indicadores deteriorados. Evitar posiciones largas."
    else:
        estado_final = "SKIP"
        decision     = "— ESPERAR: Score insuficiente para el nivel de riesgo configurado."

    return {
        "estado":         estado_final,
        "tipo_setup":     tipo_setup,
        "bloqueadores":   bloqueadores,
        "advertencias":   advertencias,
        "decision_final": decision,
        "confianza":      confianza,
        "score_ajustado": score_ajustado,
        "pasa_filtros":   True,
        "setup_nota":     setup_nota if pasa_filtros else "",
        "score_drop":     score_drop,
    }


def detectar_estructura_hhhl(highs: list, lows: list, n_pivotes: int = 5) -> dict:
    """
    ESTRUCTURA HH/HL (Higher High / Higher Low) — Wyckoff/price action puro.
    Analiza los ultimos n_pivotes maximos y minimos locales para determinar
    si el activo esta en estructura alcista (HH+HL), bajista (LH+LL) o lateral.

    Un pivote maximo local = vela cuyo high supera los 2 vecinos de cada lado.
    Un pivote minimo local = vela cuyo low es inferior a los 2 vecinos de cada lado.

    Retorna:
      estructura : "alcista" | "bajista" | "lateral" | "indefinida"
      hh         : bool — hay Higher Highs consecutivos
      hl         : bool — hay Higher Lows consecutivos
      lh         : bool — hay Lower Highs consecutivos
      ll         : bool — hay Lower Lows consecutivos
      desc       : descripcion legible
      score_extra: +1 si alcista confirmada, -1 si bajista, 0 si lateral
    """
    if not highs or not lows or len(highs) < 10:
        return {"estructura":"indefinida","hh":False,"hl":False,
                "lh":False,"ll":False,"desc":"Datos insuficientes","score_extra":0}

    h = highs
    l = lows
    n = len(h)

    # Detectar pivotes locales (ventana de 2 velas)
    pivot_highs = []
    pivot_lows  = []
    for i in range(2, n - 2):
        if h[i] > h[i-1] and h[i] > h[i-2] and h[i] > h[i+1] and h[i] > h[i+2]:
            pivot_highs.append(h[i])
        if l[i] < l[i-1] and l[i] < l[i-2] and l[i] < l[i+1] and l[i] < l[i+2]:
            pivot_lows.append(l[i])

    # Tomar los ultimos n_pivotes
    ph = pivot_highs[-n_pivotes:] if len(pivot_highs) >= 2 else []
    pl = pivot_lows[-n_pivotes:]  if len(pivot_lows)  >= 2 else []

    if len(ph) < 2 or len(pl) < 2:
        return {"estructura":"indefinida","hh":False,"hl":False,
                "lh":False,"ll":False,"desc":"Pocos pivotes detectados","score_extra":0}

    # Contar secuencias
    hh = all(ph[i] > ph[i-1] for i in range(1, len(ph)))   # cada maximo mayor al anterior
    hl = all(pl[i] > pl[i-1] for i in range(1, len(pl)))   # cada minimo mayor al anterior
    lh = all(ph[i] < ph[i-1] for i in range(1, len(ph)))   # cada maximo menor al anterior
    ll = all(pl[i] < pl[i-1] for i in range(1, len(pl)))   # cada minimo menor al anterior

    # Condiciones relajadas (mayoria, no todos)
    hh_pct = sum(ph[i] > ph[i-1] for i in range(1, len(ph))) / max(len(ph)-1, 1)
    hl_pct = sum(pl[i] > pl[i-1] for i in range(1, len(pl))) / max(len(pl)-1, 1)
    lh_pct = sum(ph[i] < ph[i-1] for i in range(1, len(ph))) / max(len(ph)-1, 1)
    ll_pct = sum(pl[i] < pl[i-1] for i in range(1, len(pl))) / max(len(pl)-1, 1)

    if hh_pct >= 0.67 and hl_pct >= 0.67:
        estructura  = "alcista"
        desc        = f"Estructura HH+HL confirmada ({len(ph)} maximos, {len(pl)} minimos)"
        score_extra = 1
    elif lh_pct >= 0.67 and ll_pct >= 0.67:
        estructura  = "bajista"
        desc        = f"Estructura LH+LL confirmada — tendencia bajista de precio"
        score_extra = -1
    elif hh_pct >= 0.5 and hl_pct >= 0.5:
        estructura  = "alcista"
        desc        = f"Estructura parcialmente alcista (HH {hh_pct:.0%}, HL {hl_pct:.0%})"
        score_extra = 0
    else:
        estructura  = "lateral"
        desc        = "Sin estructura clara — precio en rango"
        score_extra = 0

    return {
        "estructura":  estructura,
        "hh": hh, "hl": hl, "lh": lh, "ll": ll,
        "hh_pct": round(hh_pct, 2), "hl_pct": round(hl_pct, 2),
        "desc":        desc,
        "score_extra": score_extra,
        "n_ph":        len(ph),
        "n_pl":        len(pl),
    }


def analizar_tf(closes, volumes, tf_label, capital, riesgo_pct, rr_min,
                titulos_en_cartera=0.0, tc=17.5, origen="USA",
                highs=None, lows=None) -> dict:
    if not closes or len(closes) < 20:
        return {"tf":tf_label,"valido":False}

    c = pd.Series(closes, dtype=float)
    v = pd.Series(volumes, dtype=float) if volumes else pd.Series([1.0]*len(closes))
    n = len(c)

    e9  = float(ema(c,9).iloc[-1])
    e21 = float(ema(c,21).iloc[-1])
    e50 = float(ema(c,min(50,n-1)).iloc[-1])
    e200= float(ema(c,min(200,n-1)).iloc[-1])

    ml,ms,mh = macd(c)
    ml_v=float(ml.iloc[-1]); ms_v=float(ms.iloc[-1]); mh_v=float(mh.iloc[-1])
    rv = float(rsi(c).iloc[-1])

    # ADX — fuerza de tendencia
    adx_val = 0.0
    if highs and lows and len(highs) == len(closes):
        try: adx_val = adx(highs, lows, closes)
        except Exception: adx_val = 0.0

    # ESTRUCTURA HH/HL — price action puro
    estructura_info = {"estructura":"indefinida","hh":False,"hl":False,
                       "lh":False,"ll":False,"desc":"","score_extra":0}
    if highs and lows and len(highs) == len(closes):
        try: estructura_info = detectar_estructura_hhhl(highs, lows, n_pivotes=5)
        except Exception: pass

    vol_now = float(v.iloc[-1])
    vol_avg = float(v.rolling(min(20,n)).mean().iloc[-1])
    # Sin volumen real (algunos tickers de plan free no lo reportan) → no bloquear
    _sin_volumen = (vol_avg < 1.0)
    vol_ok   = _sin_volumen or (vol_now >= vol_avg * 1.5)

    # OBV — detecta divergencias institucionales
    obv_info = obv(closes, volumes) if not _sin_volumen else {"tendencia":"sin datos","divergencia":False,"div_tipo":"","ok":True}

    precio   = float(c.iloc[-1])
    soporte  = float(c.rolling(min(20,n)).min().iloc[-1])
    # Máximo histórico solo como referencia de resistencia, NO como objetivo
    max_20   = float(c.rolling(min(20,n)).max().iloc[-1])

    # ── TRAILING STOP ATR ─────────────────────────────────────────────────
    if highs and lows and len(highs) == len(closes):
        h_s = pd.Series(highs, dtype=float)
        l_s = pd.Series(lows,  dtype=float)
        tr_s = pd.concat([
            h_s - l_s,
            (h_s - c.shift()).abs(),
            (l_s - c.shift()).abs()
        ], axis=1).max(axis=1)
        atr_val = float(tr_s.ewm(span=14, adjust=False).mean().iloc[-1])
        precio_max_trail = float(h_s.rolling(min(10, n)).max().iloc[-1])
        trailing_stop = precio_max_trail - 2.5 * atr_val
        trailing_stop = min(trailing_stop, precio * 0.97)
    else:
        atr_val       = 0.0
        trailing_stop = float(c.rolling(min(5,n)).min().iloc[-1]) * 0.97

    stop       = max(trailing_stop, float(c.rolling(min(5,n)).min().iloc[-1]) * 0.97)
    riesgo_acc = precio - stop

    # ── OBJETIVO REALISTA POR ATR ─────────────────────────────────────────
    # En lugar del máximo de 20 velas (que queda obsoleto tras caídas fuertes),
    # proyectamos cuánto puede moverse el activo en ~10 días según su volatilidad real.
    # Multiplicador: 3x ATR para swing de 5-10 días (conservador pero realista).
    # Si el máximo histórico está más cercano → usarlo como techo (resistencia real).
    if atr_val > 0:
        objetivo_atr = precio + (atr_val * 3.0)
        # Si hay resistencia histórica más cercana que la proyección ATR, usarla como techo
        # Esto evita proyectar "a través" de resistencias fuertes
        objetivo = min(objetivo_atr, max_20) if max_20 < objetivo_atr else objetivo_atr
    else:
        objetivo = max_20   # fallback si no hay ATR

    rr_val = (objetivo - precio) / riesgo_acc if riesgo_acc > 0 else 0

    emas_ok  = precio>e9>e21>e50
    e200_ok  = precio>e200
    macd_ok  = ml_v>ms_v
    macdh_ok = mh_v>0
    rsi_ok   = 40<=rv<=72
    vol_ok   = _sin_volumen or (vol_now >= vol_avg * 1.5)
    rr_ok    = rr_val>=rr_min
    sop_ok   = precio>soporte
    adx_ok   = adx_val >= 20
    hhhl_ok  = estructura_info["estructura"] == "alcista"   # HH+HL confirmado

    # mult necesario para mostrar valores en MXN en el semáforo
    mult = tc if origen == "USA" else 1.0

    criterios = {
        "emas":   {"ok":emas_ok,  "label":"EMAs 9>21>50",
                   "val":f"${e9*mult:,.2f}/${e21*mult:,.2f}/${e50*mult:,.2f}",
                   "razon":f"Precio ${precio*mult:,.2f} {'>' if emas_ok else '<'} EMA9 ${e9*mult:,.2f}"},
        "ema200": {"ok":e200_ok,  "label":"Precio > EMA200",
                   "val":f"${e200*mult:,.2f}",
                   "razon":(f"EMA200 en ${e200*mult:,.2f} MXN. "
                            f"Precio {((precio-e200)/e200*100):+.1f}% "
                            f"{'✅ ENCIMA' if e200_ok else '❌ DEBAJO'} de la EMA200. "
                            f"{'Tendencia alcista de largo plazo.' if e200_ok else 'Cuidado — bajo EMA200 es zona de peligro.'}")},
        "macd":   {"ok":macd_ok,  "label":"MACD alcista",
                   "val":f"{ml_v:.3f}",
                   "razon":f"MACD {ml_v:.3f} vs señal {ms_v:.3f}. {'✅ Momentum comprador.' if macd_ok else '❌ Sin momentum — vendedores en control.'}"},
        "macd_h": {"ok":macdh_ok, "label":"Histograma >0",
                   "val":f"{mh_v:+.3f}",
                   "razon":f"Histograma {'✅ positivo — momentum acelerando.' if macdh_ok else '❌ negativo — momentum frenando.'}"},
        "rsi":    {"ok":rsi_ok,   "label":"RSI 40-72",
                   "val":f"{rv:.0f}",
                   "razon":("⚠️ Sobrecomprado >72 — riesgo de corrección." if rv>72
                            else f"RSI {rv:.0f}: {'✅ zona alcista.' if rv>=55 else '✅ zona neutral — ok para entrar.' if rv>=40 else '❌ débil — no entrar.'}")},
        "volumen":{"ok":vol_ok,   "label":"Volumen≥1.5x med",
                   "val":f"{vol_now/vol_avg:.1f}x" if vol_avg and not _sin_volumen else "N/D",
                   "razon":("Sin datos de volumen — criterio omitido." if _sin_volumen
                            else f"Vol {vol_now:,.0f} vs media {vol_avg:,.0f}. {'✅ Confirmado (≥1.5x) — movimiento institucional.' if vol_ok else '⚠️ Insuficiente — posible falso movimiento.'}")},
        "rr":     {"ok":rr_ok,    "label":f"R:R>={rr_min:.0f}x",
                   "val":f"{rr_val:.1f}x",
                   "razon":(f"Stop ${stop*mult:,.2f} MXN · Objetivo ${objetivo*mult:,.2f} MXN · "
                            f"R:R {rr_val:.1f}x {'✅ válido.' if rr_ok else '❌ insuficiente — el riesgo supera la recompensa.'}")},
        "soporte":{"ok":sop_ok,   "label":"Sobre soporte",
                   "val":f"${soporte*mult:,.2f}",
                   "razon":f"Soporte en ${soporte*mult:,.2f} MXN. {'✅ Precio sobre soporte.' if sop_ok else '❌ Soporte roto — señal bajista.'}"},
        "adx":    {"ok":adx_ok,   "label":"ADX≥20 tendencia",
                   "val":f"{adx_val:.0f}",
                   "razon":f"ADX {adx_val:.0f}: {'✅ Tendencia real y fuerte.' if adx_val>=25 else '⚠️ Tendencia débil — precaución.' if adx_val>=20 else '❌ Sin tendencia — mercado lateral.'}"},
        "hhhl":   {"ok":hhhl_ok,  "label":"Estructura HH+HL",
                   "val":estructura_info["estructura"],
                   "razon":estructura_info["desc"] or f"Estructura: {estructura_info['estructura']}"},
        "obv":    {"ok":obv_info["ok"],"label":"OBV sin divergencia",
                   "val":obv_info["tendencia"],
                   "razon":(obv_info["div_tipo"] if obv_info["divergencia"]
                            else f"OBV {obv_info['tendencia']} — flujo institucional consistente con precio.")},
    }
    score = sum(1 for x in criterios.values() if x["ok"])
    total_criterios = len(criterios)   # 11 criterios
    explosion = (emas_ok and e200_ok and macd_ok and macdh_ok and 55<=rv<=72
                 and vol_ok and rr_val>=4.0 and adx_val>=25 and hhhl_ok
                 and obv_info["ok"])

    if score>=7 and emas_ok and e200_ok: senal="COMPRAR"
    elif score>=5:                        senal="MANTENER"
    else:                                 senal="VENDER"

    mult = tc if origen=="USA" else 1.0
    precio_mxn_sz  = precio*mult
    riesgo_acc_mxn = riesgo_acc*mult
    riesgo_mxn     = capital*riesgo_pct
    titulos_max    = riesgo_mxn/riesgo_acc_mxn if riesgo_acc_mxn>0 else 0
    tit_add        = max(0.0, round(titulos_max-titulos_en_cartera, 2))
    cap_add        = round(tit_add*precio_mxn_sz, 2)
    if cap_add > capital:
        tit_add = round(capital/precio_mxn_sz, 2)
        cap_add = round(tit_add*precio_mxn_sz, 2)
    pct_cap = round(cap_add/capital*100, 1) if capital else 0

    return {
        "tf":tf_label,"valido":True,
        "precio":precio,"ema9":e9,"ema21":e21,"ema50":e50,"ema200":e200,
        "rsi":rv,"macd_alcista":macd_ok,"ml":ml_v,"ms":ms_v,"mh":mh_v,
        "adx":adx_val,"atr":round(atr_val,4),"trailing_stop":round(stop,4),
        "estructura":estructura_info,
        "obv":obv_info,
        "rr":rr_val,"stop":stop,"objetivo":objetivo,"soporte":soporte,"vol_ok":vol_ok,
        "score":score,"total_criterios":total_criterios,"senal":senal,"explosion":explosion,
        "criterios":criterios,
        "entrada_sugerida":round(e9,4),
        "sizing":{
            "riesgo_mxn":round(riesgo_mxn,2),"riesgo_acc_mxn":round(riesgo_acc_mxn,2),
            "precio_mxn":round(precio_mxn_sz,2),"titulos_max":round(titulos_max,2),
            "titulos_en_cartera":titulos_en_cartera,"titulos_adicionales":tit_add,
            "capital_adicional":cap_add,"pct_capital":pct_cap,
            "es_fraccion":tit_add!=int(tit_add),
            "verificacion":f"{tit_add} tit x ${precio_mxn_sz:,.2f} = ${cap_add:,.2f} MXN",
        },
    }


# Cache de series históricas — se llena con batch al inicio de cada corrida
_TD_CACHE: dict = {}

def _get_cached(symbol: str, interval: str, exchange: str = "") -> list | None:
    """
    Devuelve datos del cache.
    Cache miss → petición individual rotando keys.
    None en cache (batch falló) → reintenta con cada key antes de rendirse.
    """
    global _TD_CACHE
    key = f"{symbol.upper()}:{interval}"

    if key not in _TD_CACHE:
        print(f"    [cache miss] {symbol} {interval} — petición individual...")
        result = None
        for k in (_TD_KEYS or [""]):
            result = api_timeseries(symbol, interval, 150, exchange, key=k)
            if result:
                break
        _TD_CACHE[key] = result

    elif _TD_CACHE[key] is None:
        print(f"    [retry] {symbol} {interval} — reintentando con keys disponibles...")
        for k in (_TD_KEYS or [""]):
            result = api_timeseries(symbol, interval, 150, exchange, key=k)
            if result:
                _TD_CACHE[key] = result
                break

    return _TD_CACHE[key]


def _precargar_cache_batch(symbols: list, intervals: list = None):
    """
    Precarga el cache con batches de hasta 8 símbolos por key.
    KEY_1 y KEY_2 alternan por chunk — cada una tiene su propio límite de 8 req/min.
    Sin esperas largas: si un chunk falla, reintenta inmediatamente con la otra key.
    Diseñado para completar en < 60s con 2 keys y 21 símbolos.
    """
    global _TD_CACHE, _KEY_IDX
    if intervals is None:
        intervals = ["1day", "1week"]

    syms = [s.upper() for s in symbols if s]
    seen = set(); syms = [s for s in syms if not (s in seen or seen.add(s))]
    CHUNK  = 8
    n_keys = len(_TD_KEYS)

    print(f"  [batch] {len(syms)} tickers × {len(intervals)} intervalos | "
          f"{n_keys} key(s) | chunks de {CHUNK}")

    _KEY_IDX = 0

    for interval in intervals:
        chunks = [syms[i:i+CHUNK] for i in range(0, len(syms), CHUNK)]
        for idx, chunk in enumerate(chunks):
            # Alternar key por chunk: chunk 0→KEY1, chunk 1→KEY2, chunk 2→KEY1...
            key_use = _TD_KEYS[idx % n_keys] if _TD_KEYS else ""
            print(f"  [batch] {interval} chunk {idx+1}/{len(chunks)} "
                  f"k=…{key_use[-4:] if key_use else 'N/A'} → {', '.join(chunk)}")

            batch = api_timeseries_batch(chunk, interval, outputsize=150, key=key_use)

            # Si algún ticker faltó, reintenta SOLO los faltantes con la otra key
            # Sin esperar — la otra key tiene cuota fresca
            faltantes = [s for s in chunk if s.upper() not in batch]
            if faltantes and n_keys > 1:
                otra_key = _TD_KEYS[(idx + 1) % n_keys]
                print(f"  [batch] Reintentando {faltantes} con k=…{otra_key[-4:]}...")
                batch2 = api_timeseries_batch(faltantes, interval, outputsize=150, key=otra_key)
                batch.update(batch2)

            # Guardar en cache
            for sym, vals in batch.items():
                _TD_CACHE[f"{sym}:{interval}"] = vals

            # Marcar como None los que definitivamente no llegaron
            for sym in chunk:
                if f"{sym}:{interval}" not in _TD_CACHE:
                    _TD_CACHE[f"{sym}:{interval}"] = None

            # Pausa mínima entre chunks del MISMO intervalo (evitar 429 en la misma key)
            # Con 2 keys alternas, cada key descansa 1 chunk completo entre usos
            if idx < len(chunks) - 1:
                time.sleep(4)

        time.sleep(3)  # pausa mínima entre intervalos

    con_datos = sum(1 for v in _TD_CACHE.values() if v)
    sin_datos = sum(1 for v in _TD_CACHE.values() if v is None)
    print(f"  [batch] ✅ Listo: {con_datos} con datos, {sin_datos} sin datos")

    con_datos = sum(1 for v in _TD_CACHE.values() if v)
    sin_datos = sum(1 for v in _TD_CACHE.values() if v is None)
    print(f"  [batch] ✅ Listo: {con_datos} con datos, {sin_datos} sin datos")




def analizar_ticker_1d(nombre, symbol, exchange, capital, riesgo_pct, rr_min,
                        titulos_en_cartera=0.0, tc=17.5, origen="USA",
                        skip_mtf=False) -> dict:
    print(f"  {nombre}...", end=" ", flush=True)

    values_1d = _get_cached(symbol, "1day", exchange)

    if not values_1d:
        print("sin datos")
        return {"nombre":nombre,"symbol":symbol,
                "tf":{"1D":{"tf":"1D","valido":False},"1H":{"tf":"1H","valido":False},"1W":{"tf":"1W","valido":False}},
                "senal":"SIN DATOS","precio_actual":None,"score_global":0,"confluencia":{}}

    closes_1d  = ohlcv_to_close(values_1d)
    volumes_1d = ohlcv_to_volume(values_1d)
    highs_1d   = [float(x.get("high", x["close"])) for x in values_1d]
    lows_1d    = [float(x.get("low",  x["close"])) for x in values_1d]

    tf_1d = analizar_tf(closes_1d, volumes_1d, "1D", capital, riesgo_pct, rr_min,
                         titulos_en_cartera, tc=tc, origen=origen,
                         highs=highs_1d, lows=lows_1d)

    # ── ZONAS DE SOPORTE / RESISTENCIA ────────────────────────────────────
    sr = calcular_zonas_sr(highs_1d, lows_1d, closes_1d, volumes_1d, tc=tc, origen=origen)

    score_1d = tf_1d.get("score", 0)
    tfs = {"1D": tf_1d, "1H": {"tf":"1H","valido":False}, "1W": {"tf":"1W","valido":False}}

    # Solo pedir 1W si el score 1D es prometedor (1H eliminado — no disponible en plan free)
    if score_1d >= 5 and not skip_mtf:
        try:
            vals_1w = _get_cached(symbol, "1week", exchange)
            if vals_1w:
                h1w = [float(x.get("high", x["close"])) for x in vals_1w]
                l1w = [float(x.get("low",  x["close"])) for x in vals_1w]
                tfs["1W"] = analizar_tf(ohlcv_to_close(vals_1w), ohlcv_to_volume(vals_1w), "1W",
                                         capital, riesgo_pct, rr_min, titulos_en_cartera,
                                         tc=tc, origen=origen, highs=h1w, lows=l1w)
        except Exception: pass

    senales = [tfs[t]["senal"] for t in ["1W","1D","1H"] if tfs[t].get("valido")]
    if not senales:                         senal_final = "SIN DATOS"
    elif senales.count("COMPRAR") >= 2:     senal_final = "COMPRAR"
    elif senales.count("VENDER") >= 2:      senal_final = "VENDER"
    else:                                   senal_final = "MANTENER"

    scores = [tfs[t]["score"] for t in ["1W","1D","1H"] if tfs[t].get("valido")]
    score_global = round(sum(scores)/len(scores), 1) if scores else 0
    confluencia  = {s: senales.count(s) for s in ["COMPRAR","VENDER","MANTENER"]}

    precio_actual = tf_1d["precio"] if tf_1d.get("valido") else None
    print(f"-> {senal_final} (score 1D:{score_1d} global:{score_global})")

    return {"nombre":nombre,"symbol":symbol,"tf":tfs,"senal":senal_final,
            "precio_actual":precio_actual,"score_global":score_global,
            "confluencia":confluencia,"sr":sr}


# ── ANÁLISIS PORTAFOLIO ───────────────────────────────────
def analizar_portafolio(tc, capital, riesgo_pct, rr_min):

    posiciones = get_portafolio()

    resultados = []

    tickers_db = get_tickers_db()  # Una sola query SQL para todo el portafolio

    for pos in posiciones:

        ticker = pos["ticker"]

        symbol, exchange = tickers_db.get(
            ticker,
            (
                ticker.replace(" CPO", "CPO").replace(" ", ""),
                "BMV" if pos["origen"] == "MX" else ""
            )
        )

        an = analizar_ticker_1d(
            ticker,
            symbol,
            exchange,
            capital,
            riesgo_pct,
            rr_min,
            titulos_en_cartera=pos["titulos"],
            tc=tc,
            origen=pos.get("origen", "USA")
        )

        precio_usd = an["precio_actual"]

        precio_mxn = (
            precio_usd * tc
            if precio_usd and pos["origen"] == "USA"
            else precio_usd
        )

        cto_mxn = pos["cto_prom_mxn"]

        valor_mxn = (precio_mxn or cto_mxn) * pos["titulos"]

        costo_total = cto_mxn * pos["titulos"]

        pl_mxn = valor_mxn - costo_total

        pl_pct = (pl_mxn / costo_total * 100) if costo_total else 0

        alertas = []
        if precio_mxn:
            cambio_pct = ((precio_mxn-cto_mxn)/cto_mxn)*100
            if cambio_pct >= ALERTA_SUBIDA:
                alertas.append(f"🟢 +{cambio_pct:.1f}% desde tu precio de compra — considera tomar ganancias")
            elif cambio_pct <= -ALERTA_BAJADA:
                alertas.append(f"🔴 {cambio_pct:.1f}% desde tu precio de compra — evalúa stop loss")

        tf_1d = an["tf"].get("1D",{})
        mult  = tc if pos["origen"]=="USA" else 1.0
        entrada_mxn = tf_1d.get("entrada_sugerida",0)*mult if tf_1d.get("valido") else None
        stop_mxn    = tf_1d.get("stop",0)*mult if tf_1d.get("valido") else None
        obj_mxn     = tf_1d.get("objetivo",0)*mult if tf_1d.get("valido") else None

        resultados.append({**pos, "analisis":an,
                "precio_actual_usd":precio_usd,"precio_actual_mxn":precio_mxn,
                "valor_mxn":valor_mxn,"costo_total":costo_total,"pl_mxn":pl_mxn,"pl_pct":pl_pct,
                "alertas":alertas,"entrada_mxn":entrada_mxn,"stop_mxn":stop_mxn,"obj_mxn":obj_mxn})
    return resultados


def correr_scanner(tc, capital, riesgo_pct, rr_min, tickers_extra: dict | None = None,
                   vix: float = 20.0, spy: dict | None = None):
    global _TD_CACHE
    _TD_CACHE = {}

    if spy is None: spy = {}
    regimen = regimen_mercado(vix, spy)

    port_map   = {p["ticker"]: p["titulos"] for p in get_portafolio()}
    tickers_db = get_tickers_db()
    combinados: dict = {}
    combinados.update(SCANNER_TICKERS)
    combinados.update(tickers_db)
    if tickers_extra:
        combinados.update(tickers_extra)

    n_db  = len([k for k in tickers_db if k not in SCANNER_TICKERS])
    n_ext = len([k for k in (tickers_extra or {}) if k not in combinados])
    print(f"  Scanner: {len(combinados)} tickers ({len(SCANNER_TICKERS)} base + {n_db} DB + {n_ext} extra)")
    print(f"  Régimen: {regimen['label']} | VIX={vix:.1f} | SPY {'✅' if spy.get('sobre_ema200') else '❌'} EMA200")

    # ── PRECARGAR CACHE CON BATCH ─────────────────────────────────────────
    # Incluimos los sector ETFs en el batch inicial para que get_sector_estado()
    # no haga requests individuales durante el análisis (evita rate limit silencioso)
    _sector_etfs = list(set(_SECTOR_MAP.values()))  # SMH, QQQ, XLF, XLY, SPY
    todos_syms   = list(set([v[0] for v in combinados.values()] + _sector_etfs))
    _precargar_cache_batch(todos_syms, ["1day", "1week"])

    resultados = []
    for nombre, (symbol, exchange) in combinados.items():
        try:
            tit = port_map.get(nombre, 0.0)
            origen_ticker = "MX" if exchange == "BMV" else "USA"
            an  = analizar_ticker_1d(nombre, symbol, exchange, capital, riesgo_pct, rr_min,
                                     tit, tc=tc, origen=origen_ticker)
            tf_1d = an["tf"].get("1D", {})
            if not tf_1d.get("valido"): continue

            precio_usd    = tf_1d["precio"]
            etf_peligroso = es_etf_apalancado(nombre)
            exit_info     = detectar_exit(nombre, tf_1d, tf_1d.get("score", 0))

            # ── SECTOR ETF ────────────────────────────────────────────────
            sector_info   = get_sector_estado(nombre)

            setup         = evaluar_setup(nombre, tf_1d, an["tf"], vix, spy, tit, exit_info)
            estado        = setup["estado"]
            score         = tf_1d["score"]
            score_ajustado= setup.get("score_ajustado", max(0, score - regimen["penalizacion"]))

            # Si sector bajista → degradar a WATCH (no bloquear completamente)
            if not sector_info["alcista"] and estado == "BUY":
                estado = "WATCH"
                setup["advertencias"].append(
                    f"Sector {sector_info['etf']} bajista — esperar recuperación del sector")

            # ── MODO GANGA — solo acciones, no ETFs 3x ────────────────────
            ganga_info = {}
            if not etf_peligroso and estado not in ("BUY", "ROCKET", "EXIT"):
                closes_scan  = [float(x["close"]) for x in _get_cached(symbol, "1day", exchange) or []]
                volumes_scan = [float(x.get("volume", 0)) for x in _get_cached(symbol, "1day", exchange) or []]
                ganga_info   = detectar_ganga(nombre, tf_1d, an.get("sr", {}),
                                              tf_1d.get("obv", {}), closes_scan, volumes_scan)
                if ganga_info.get("es_ganga") and estado in ("WATCH", "LATERAL", "SKIP", "BLOQUEADO"):
                    estado = "GANGA"

            try:
                guardar_score(nombre, score, tf_1d.get("senal", "—"), vix, precio_usd, estado)
            except Exception: pass

            c = {k: v["ok"] for k, v in tf_1d["criterios"].items()}

            # ── PLAN DCA ──────────────────────────────────────────────────────
            sr_scan  = an.get("sr", {})
            soportes_mxn = [dict(z, precio_mxn=z["precio"]*tc) for z in sr_scan.get("soportes", [])]
            dca_plan = calcular_dca(
                precio_actual_mxn = precio_usd * tc,
                atr_mxn           = tf_1d.get("atr", 0) * tc,
                soportes_mxn      = soportes_mxn,
                capital_total     = capital,
                es_etf_3x         = etf_peligroso,
            )

            resultados.append({
                "nombre": nombre, "estado": estado,
                "precio_usd": precio_usd, "precio_mxn": precio_usd * tc,
                "entrada_mxn": tf_1d.get("entrada_sugerida", precio_usd) * tc,
                "stop_mxn": tf_1d.get("stop", 0) * tc, "obj_mxn": tf_1d.get("objetivo", 0) * tc,
                "rsi": tf_1d["rsi"], "rr": tf_1d["rr"], "macd_ok": tf_1d["macd_alcista"],
                "ema200_ok": c.get("ema200", False),
                "score": score, "score_ajustado": score_ajustado,
                "total_criterios": tf_1d.get("total_criterios", 11),
                "criterios": tf_1d["criterios"], "sizing": tf_1d.get("sizing", {}),
                "tfs": an["tf"], "confluencia": an["confluencia"], "titulos_cartera": tit,
                "etf_apalancado": etf_peligroso,
                "exit_info": exit_info,
                "vix": vix, "regimen": regimen,
                "adx": tf_1d.get("adx", 0),
                "obv": tf_1d.get("obv", {}),
                "sector": sector_info,
                "setup": setup,
                "sr": an.get("sr", {}),
                "dca": dca_plan,
                "ganga": ganga_info,
            })
        except Exception as e:
            print(f"  [scanner] ❌ Error procesando {nombre}: {e}")
            continue

    orden = {"ROCKET":0,"BUY":1,"EXIT":2,"GANGA":3,"WATCH":4,"LATERAL":5,"SKIP":6,"SHORT":7,"RUPTURA":8,"BLOQUEADO":9}
    resultados.sort(key=lambda x: (orden.get(x["estado"], 9), -x["rr"]))
    return resultados


def radar_masivo(tc, capital, riesgo_pct, rr_min, scan_results: list | None = None,
                 vix: float = 20.0, spy: dict | None = None):
    """Radar: analiza UNIVERSO + DB en modo 1D. Reutiliza cache del scanner."""
    if spy is None: spy = {"sobre_ema200": True}
    port_map          = {p["ticker"]: p["titulos"] for p in get_portafolio()}
    tickers_db        = get_tickers_db()
    universo_completo = {**UNIVERSO, **tickers_db}
    total             = len(universo_completo)
    print(f"  Radar: {total} acciones (VIX={vix:.1f})...")

    # Límite de tiempo para que el radar no cuelgue indefinidamente
    _radar_start  = time.time()
    _RADAR_TIMEOUT = 540  # 9 minutos — suficiente para 17 tickers con pausas batch

    # Precargar TODO el universo del radar + sector ETFs en batch desde el inicio.
    # Antes solo precargaba los "faltantes" pero eso causaba que el cache del scanner
    # no tuviera los sector ETFs → requests individuales durante análisis → rate limit silencioso.
    _sector_etfs_r = list(set(_SECTOR_MAP.values()))
    todos_radar    = list(set([v[0] for v in universo_completo.values()] + _sector_etfs_r))
    syms_sin_cache = [s for s in todos_radar if f"{s.upper()}:1day" not in _TD_CACHE]
    if syms_sin_cache:
        print(f"  [Radar batch] Precargando {len(syms_sin_cache)} tickers (incluye sectores)...")
        _precargar_cache_batch(syms_sin_cache, ["1day"])

    resultados = []
    for i, (nombre, (symbol, exchange)) in enumerate(universo_completo.items()):
        # Abort si llevamos más de 2 minutos en el radar
        if time.time() - _radar_start > _RADAR_TIMEOUT:
            print(f"  [Radar] Timeout — procesados {i}/{total}")
            break

        if i%5==0: print(f"  [{i}/{total}]...", end="\r", flush=True)
        values = _get_cached(symbol, "1day", exchange)
        if not values or len(values)<30: continue

        closes  = ohlcv_to_close(values)
        volumes = ohlcv_to_volume(values)
        highs   = [float(x.get("high", x["close"])) for x in values]
        lows    = [float(x.get("low",  x["close"])) for x in values]
        tit     = port_map.get(nombre, 0.0)

        tf = analizar_tf(closes, volumes, "1D", capital, riesgo_pct, rr_min,
                          tit, tc=tc, origen="USA", highs=highs, lows=lows)
        if not tf["valido"]: continue

        # S/R para el radar
        sr_radar = calcular_zonas_sr(highs, lows, closes, volumes, tc=tc, origen="USA")

        # Construir tfs mínimo para evaluar_setup (radar solo tiene 1D)
        tfs_radar = {"1D": tf, "1H": {"valido":False}, "1W": {"valido":False}}
        exit_info = detectar_exit(nombre, tf, tf.get("score",0))

        # ── MOTOR DE DECISIÓN ──────────────────────────────────────────
        sector_info = get_sector_estado(nombre)
        setup  = evaluar_setup(nombre, tf, tfs_radar, vix, spy, tit, exit_info)
        estado = setup["estado"]
        score  = tf["score"]
        total_c= tf.get("total_criterios", 11)
        precio = tf["precio"]
        stop   = tf["stop"]
        objetivo = tf["objetivo"]
        pot_alza = ((objetivo-precio)/precio*100) if precio else 0

        # Sector bajista → degradar BUY a WATCH
        if not sector_info["alcista"] and estado == "BUY":
            estado = "WATCH"
            setup["advertencias"].append(
                f"Sector {sector_info['etf']} bajista — esperar recuperación del sector")

        # ── MODO GANGA radar — solo acciones, no ETFs 3x ──────────────────
        ganga_info_r = {}
        if not es_etf_apalancado(nombre) and estado not in ("BUY", "ROCKET", "EXIT"):
            ganga_info_r = detectar_ganga(nombre, tf, sr_radar,
                                          tf.get("obv", {}), closes, volumes)
            if ganga_info_r.get("es_ganga") and estado in ("WATCH", "LATERAL", "SKIP", "BLOQUEADO"):
                estado = "GANGA"

        # ── PLAN DCA RADAR ────────────────────────────────────────────────
        soportes_mxn_r = [dict(z, precio_mxn=z["precio"]*tc) for z in sr_radar.get("soportes", [])]
        dca_plan_r = calcular_dca(
            precio_actual_mxn = precio * tc,
            atr_mxn           = tf.get("atr", 0) * tc,
            soportes_mxn      = soportes_mxn_r,
            capital_total     = capital,
            es_etf_3x         = es_etf_apalancado(nombre),
        )

        resultados.append({
            "nombre":nombre,"estado":estado,
            "precio_usd":precio,"precio_mxn":precio*tc,
            "entrada_mxn":tf.get("entrada_sugerida",precio)*tc,
            "stop_mxn":stop*tc,"obj_mxn":objetivo*tc,
            "rsi":tf["rsi"],"rr":tf["rr"],"macd_ok":tf["macd_alcista"],
            "ema200_ok":tf["criterios"].get("ema200",{}).get("ok",False),
            "score":score,"score_ajustado":setup.get("score_ajustado",score),
            "total_criterios":total_c,
            "pot_alza":pot_alza,"criterios":tf["criterios"],"sizing":tf.get("sizing",{}),
            "titulos_cartera":tit,"adx":tf.get("adx",0),
            "obv":tf.get("obv",{}),
            "sector":sector_info,
            "etf_apalancado":es_etf_apalancado(nombre),
            "exit_info":exit_info,"setup":setup,
            "sr":sr_radar,
            "dca":dca_plan_r,
            "ganga":ganga_info_r,
            "vix":vix,"regimen":regimen_mercado(vix,spy),
        })

    print(f"  Radar completo: {len(resultados)} de {total} analizadas")
    orden = {"ROCKET":0,"BUY":1,"EXIT":2,"GANGA":3,"WATCH":4,"LATERAL":5,"SKIP":6,"SHORT":7,"RUPTURA":8,"BLOQUEADO":9}
    resultados.sort(key=lambda x:(orden.get(x["estado"],9),-x["pot_alza"]))
    return resultados


# ══════════════════════════════════════════════════════════
#   HELPERS HTML
# ══════════════════════════════════════════════════════════
def fmt(n, dec=2):
    if n is None: return "—"
    s = f"{abs(n):,.{dec}f}"
    return f"-${s}" if n < 0 else f"${s}"

def badge(cls, txt): return f'<span class="badge {cls}">{txt}</span>'
def badge_senal(s):
    m={"COMPRAR":("b-buy","Comprar"),"VENDER":("b-sell","Vender"),
       "MANTENER":("b-hold","Mantener"),"SIN DATOS":("b-none","Sin datos")}
    c,t=m.get(s,("b-none",s)); return badge(c,t)
def badge_estado(s):
    m={
        "ROCKET":   ("b-rocket", "🚀 Explosión"),
        "BUY":      ("b-buy",    "↑ Compra"),
        "GANGA":    ("b-ganga",  "🏷️ Ganga"),
        "WATCH":    ("b-hold",   "👁 Vigilar"),
        "SKIP":     ("b-none",   "Esperar"),
        "SHORT":    ("b-sell",   "↓ Bajista"),
        "EXIT":     ("b-exit",   "⚠️ EXIT YA"),
        "BLOQUEADO":("b-blocked","🔒 Bloqueado"),
        "LATERAL":  ("b-blocked","〰️ Lateral"),
        "RUPTURA":  ("b-sell",   "💥 Ruptura"),
    }
    c,t=m.get(s,("b-none",s)); return badge(c,t)

def badge_setup(tipo: str) -> str:
    m={
        "Breakout": ("background:#e6f4ff;color:#0958d9;border:1px solid #91caff", "⬆ Breakout"),
        "Pullback": ("background:#f6ffed;color:#389e0d;border:1px solid #b7eb8f", "↩ Pullback"),
        "Tendencia":("background:#f9f0ff;color:#722ed1;border:1px solid #d3adf7", "→ Tendencia"),
    }
    style, label = m.get(tipo, ("background:var(--surface2);color:var(--muted);border:1px solid var(--brd)", tipo))
    return f'<span style="font-size:9px;padding:2px 7px;border-radius:10px;{style};margin-left:4px">{label}</span>'

def render_decision_box(setup: dict) -> str:
    """Bloque visual de la decisión final del motor."""
    if not setup: return ""
    estado    = setup.get("estado","SKIP")
    decision  = setup.get("decision_final","—")
    bloqueos  = setup.get("bloqueadores",[])
    adverts   = setup.get("advertencias",[])
    confianza = setup.get("confianza",0)
    tipo      = setup.get("tipo_setup","—")

    # Color por estado
    if estado in ("BUY","ROCKET"):
        bg,brd,col = "#f6ffed","#b7eb8f","#135200"
    elif estado == "EXIT":
        bg,brd,col = "#fff1f0","#ffa39e","#7f1d1d"
    elif estado in ("BLOQUEADO","LATERAL","RUPTURA"):
        bg,brd,col = "#fff7e6","#ffd591","#613400"
    elif estado == "SHORT":
        bg,brd,col = "#fff1f0","#ffa39e","#7f1d1d"
    else:
        bg,brd,col = "var(--surface2)","var(--brd)","var(--muted)"

    # Barra de confianza
    conf_color = "#52c41a" if confianza>=70 else "#faad14" if confianza>=40 else "#ff4d4f"
    conf_bar   = (f'<div style="margin:8px 0 4px"><div style="font-size:10px;color:var(--muted);margin-bottom:3px">'
                  f'Confianza del setup: {confianza}%</div>'
                  f'<div style="height:5px;background:var(--brd2);border-radius:3px;overflow:hidden">'
                  f'<div style="height:100%;width:{confianza}%;background:{conf_color};border-radius:3px"></div></div></div>')

    # Bloqueadores
    bloqueos_html = ""
    if bloqueos:
        items = "".join(f'<li style="margin:2px 0">{b}</li>' for b in bloqueos)
        bloqueos_html = f'<ul style="margin:6px 0 0 14px;font-size:10px;color:#7f1d1d">{items}</ul>'

    # Advertencias
    adverts_html = ""
    if adverts:
        items = "".join(f'<li style="margin:2px 0">{a}</li>' for a in adverts)
        adverts_html = (f'<div style="margin-top:6px;font-size:10px;color:#78350f">'
                        f'<strong>Advertencias:</strong><ul style="margin:3px 0 0 14px">{items}</ul></div>')

    return (f'<div style="background:{bg};border:1px solid {brd};border-radius:var(--r);'
            f'padding:10px 13px;margin-bottom:10px">'
            f'<div style="font-weight:600;font-size:12px;color:{col};margin-bottom:4px">'
            f'Decisión: {decision}</div>'
            f'{conf_bar}'
            f'<div style="font-size:10px;color:var(--muted)">Setup: <strong>{tipo}</strong></div>'
            f'{bloqueos_html}{adverts_html}</div>')

def badge_etf_apalancado() -> str:
    return '<span class="b-etf">⚡ ETF 3x</span>'

def render_exit_banner(exit_info: dict) -> str:
    if not exit_info or not exit_info.get("razones"): return ""
    nivel = exit_info.get("nivel","ok")
    if nivel == "ok": return ""
    icon  = "🚨" if nivel=="exit" else "⚠️"
    color = "#7f1d1d" if nivel=="exit" else "#78350f"
    bg    = "#fff1f0" if nivel=="exit" else "#fffbeb"
    brd   = "#ffa39e" if nivel=="exit" else "#fde68a"
    razones_html = "".join(f'<li>{r}</li>' for r in exit_info["razones"])
    titulo = "SALIR AHORA — múltiples señales de deterioro" if nivel=="exit" else "Señales de alerta — monitorea de cerca"
    return (f'<div class="exit-banner" style="background:{bg};border-color:{brd};color:{color}">'
            f'<strong>{icon} {titulo}</strong><ul style="margin:6px 0 0 16px;font-size:11px">{razones_html}</ul></div>')
def dots(n,total=8):
    return "".join(f'<span class="dot {"on" if i<n else "off"}"></span>' for i in range(total))
def rsi_col(v):
    return ("var(--red)" if v>=75 else "var(--green)" if v>=55
            else "var(--yellow)" if v>=40 else "var(--red)")

def render_criterios(criterios: dict) -> str:
    h = '<div class="crit-list">'
    for d in criterios.values():
        ok=d["ok"]; color="var(--green)" if ok else "var(--red)"
        h += (f'<div class="crit-item"><div class="crit-row">'
              f'<span class="crit-dot" style="background:{color}"></span>'
              f'<span class="crit-label">{d["label"]}</span>'
              f'<span class="crit-val" style="color:{color}">{d["val"]}</span></div>'
              f'<div class="crit-reason">{d["razon"]}</div></div>')
    return h+"</div>"

def render_score_badge(score: int, total: int, senal: str) -> str:
    """Bloque visual: número X/total + badge de señal."""
    col = ("var(--green)" if senal=="COMPRAR" else
           "var(--red)"   if senal=="VENDER"  else "var(--yellow)")
    pct = min(score/total*100, 100) if total else 0
    senal_map = {"COMPRAR":("b-buy","Comprar"),"VENDER":("b-sell","Vender"),
                 "MANTENER":("b-hold","Mantener"),"SIN DATOS":("b-none","Sin datos")}
    bcls, btxt = senal_map.get(senal, ("b-none", senal))
    return (f'<div class="score-block">'
            f'<div class="score-num" style="color:{col}">{score}'
            f'<span class="score-denom">/{total}</span></div>'
            f'<div class="score-lbl">criterios cumplidos</div>'
            f'<div class="score-bar"><div class="score-fill" style="width:{pct:.0f}%;background:{col}"></div></div>'
            f'<div style="margin-top:8px"><span class="badge {bcls}" style="font-size:12px;padding:4px 12px">{btxt}</span></div>'
            f'</div>')

def render_tf_chips(tfs: dict) -> str:
    col={"COMPRAR":"var(--green)","VENDER":"var(--red)","MANTENER":"var(--yellow)"}
    h='<div class="tf-row">'
    for tf in ["1H","1D","1W"]:
        d=tfs.get(tf,{"tf":tf,"valido":False})
        if not d.get("valido"):
            h+=f'<div class="tf-chip tf-na"><div class="tf-name">{tf}</div><div class="tf-sig">—</div><div class="tf-sub">Solo 1D base</div></div>'
            continue
        s=d.get("senal","—"); c=col.get(s,"var(--muted)")
        h+=(f'<div class="tf-chip"><div class="tf-name">{tf}</div>'
            f'<div class="tf-sig" style="color:{c}">{s.title()}</div>'
            f'<div class="tf-sub" style="color:{rsi_col(d["rsi"])}">RSI {d["rsi"]:.0f}</div></div>')
    return h+"</div>"

def render_sizing(sz: dict, en_cartera: float, tc: float, origen: str) -> str:
    if not sz: return '<p class="hint">Sin datos de sizing</p>'
    tit_add=sz.get("titulos_adicionales",0); es_frac=sz.get("es_fraccion",False)
    cap_add=sz.get("capital_adicional",0); pct_cap=sz.get("pct_capital",0)
    riesgo_mxn=sz.get("riesgo_mxn",0); riesgo_acc_mxn=sz.get("riesgo_acc_mxn",0)
    precio_mxn=sz.get("precio_mxn",0); verificacion=sz.get("verificacion","")
    frac_note=' <span class="frac-badge">fracción</span>' if es_frac else ""
    color="var(--green)" if tit_add>0 else "var(--muted)"
    h=(f'<div class="sz-grid">'
       f'<div class="sz-row"><span>Máx a arriesgar (1%)</span><span class="num">{fmt(riesgo_mxn)}</span></div>'
       f'<div class="sz-row"><span>Distancia al stop MXN</span><span class="num">{fmt(riesgo_acc_mxn)}</span></div>'
       f'<div class="sz-row"><span>Precio por acción MXN</span><span class="num">{fmt(precio_mxn)}</span></div>'
       f'<div class="sz-row"><span>Ya tienes en cartera</span><span class="num">{en_cartera} tít.</span></div>'
       f'<div class="sz-row" style="border-top:2px solid var(--brd);padding-top:6px;margin-top:2px">'
       f'<span style="font-weight:500">Títulos adicionales</span>'
       f'<span class="num" style="color:{color};font-size:16px;font-weight:600">{tit_add}{frac_note}</span></div>'
       f'<div class="sz-row"><span>Capital a comprometer</span><span class="num" style="font-weight:600">{fmt(cap_add)}</span></div>'
       f'<div class="sz-row"><span>% del capital</span><span class="num">{pct_cap:.1f}%</span></div>')
    if verificacion:
        h+=f'<div class="sz-note" style="font-family:var(--mono);font-size:10px">{verificacion}</div>'
    if tit_add==0 and en_cartera>0:
        h+='<div class="sz-note">Máxima exposición alcanzada con tu riesgo configurado.</div>'
    elif tit_add==0:
        h+='<div class="sz-note">R:R insuficiente — no hay trade válido.</div>'
    elif es_frac:
        h+='<div class="sz-note">💡 Fracción de acción — disponible en GBM y Bitso.</div>'
    return h+"</div>"

def render_niveles(tf_d: dict, tc: float, origen: str) -> str:
    if not tf_d or not tf_d.get("valido"): return '<p class="hint">Sin datos</p>'
    mult = tc if origen=="USA" else 1.0
    items=[("EMA 9 (entrada)",tf_d["ema9"]*mult,"var(--green)"),
           ("EMA 21",tf_d["ema21"]*mult,"var(--text)"),
           ("EMA 50",tf_d["ema50"]*mult,"var(--text)"),
           ("EMA 200",tf_d["ema200"]*mult,"var(--text)"),
           ("Stop sugerido",tf_d["stop"]*mult,"var(--red)"),
           ("Objetivo",tf_d["objetivo"]*mult,"var(--green)"),
           ("RSI",None,rsi_col(tf_d["rsi"])),
           ("R:R",None,"var(--green)" if tf_d["rr"]>=3 else "var(--red)")]
    h=""
    for label,val,color in items:
        if label=="RSI":
            h+=f'<div class="pl-row"><span>{label}</span><span style="color:{color};font-family:var(--mono)">{tf_d["rsi"]:.0f}</span></div>'
        elif label=="R:R":
            h+=f'<div class="pl-row"><span>{label}</span><span style="color:{color};font-family:var(--mono)">{tf_d["rr"]:.1f}x</span></div>'
        else:
            h+=f'<div class="pl-row"><span>{label}</span><span style="color:{color};font-family:var(--mono)">{fmt(val)}</span></div>'
    return h

def render_rec(senal, tf_1d, entrada_mxn, stop_mxn, obj_mxn) -> str:
    if not tf_1d.get("valido"): return ""
    cls_map={"COMPRAR":"rec-buy","VENDER":"rec-sell","MANTENER":"rec-hold"}
    cls=cls_map.get(senal,"rec-hold")
    icons={"COMPRAR":"↑","VENDER":"↓","MANTENER":"→"}
    if senal=="COMPRAR":
        body=f"Señal de compra. Entrada ideal cerca de EMA9 ({fmt(entrada_mxn)} MXN). Stop: {fmt(stop_mxn)} · Objetivo: {fmt(obj_mxn)}."
    elif senal=="VENDER":
        body=f"Indicadores deteriorados. Evalúa cerrar. Stop: {fmt(stop_mxn)} MXN."
    else:
        body=f"Señales mixtas. No agregues tamaño. Stop: {fmt(stop_mxn)} MXN."
    return f'<div class="{cls} rec-box"><div class="rec-title">{icons.get(senal,"")} {senal}</div><p>{body}</p></div>'

def render_conf(conf: dict) -> str:
    comprar=conf.get("COMPRAR",0); vender=conf.get("VENDER",0)
    if comprar>=2:   cls,icon,txt="conf-bull","↑↑",f"Confluencia alcista — {comprar}/3 timeframes"
    elif vender>=2:  cls,icon,txt="conf-bear","↓↓",f"Confluencia bajista — {vender}/3 timeframes"
    else:            cls,icon,txt="conf-mix","⇅","Señales mixtas entre timeframes"
    return f'<div class="{cls} conf-banner"><span>{icon}</span><span>{txt}</span></div>'

def gbm_cell(entrada_mxn, stop_mxn, obj_mxn) -> str:
    if not entrada_mxn: return '<span class="hint">—</span>'
    return (f'<div style="font-size:10px;line-height:1.8;font-family:var(--mono)">'
            f'<div>🎯 <span style="color:var(--green)">{fmt(entrada_mxn)}</span></div>'
            f'<div>🛑 <span style="color:var(--red)">{fmt(stop_mxn)}</span></div>'
            f'<div>✅ <span style="color:var(--green)">{fmt(obj_mxn)}</span></div></div>')


# ── RENDER ROWS ───────────────────────────────────────────
def render_port_rows(posiciones, tc):
    h=""
    for pos in posiciones:
        an=pos.get("analisis") or {}; tfs=an.get("tf",{}); senal=an.get("senal","SIN DATOS")
        conf=an.get("confluencia",{}); tf_1d=tfs.get("1D",{})
        criterios=tf_1d.get("criterios",{}) if tf_1d.get("valido") else {}
        sz=tf_1d.get("sizing",{}) if tf_1d.get("valido") else {}
        score=tf_1d.get("score",0) if tf_1d.get("valido") else 0
        total_c=tf_1d.get("total_criterios",8) if tf_1d.get("valido") else 8
        rid=f"pr_{pos['ticker'].replace(' ','_')}"
        pl_cls="pos" if pos["pl_mxn"]>=0 else "neg"

        if pos.get("precio_actual_mxn"):
            precio_cell=fmt(pos["precio_actual_mxn"])
            if pos.get("precio_actual_usd") and pos["origen"]=="USA":
                precio_cell+=f'<br><span class="hint">USD {fmt(pos["precio_actual_usd"])}</span>'
        else:
            precio_cell="—"

        alertas_h="".join(f'<div class="alert-pill">{a}</div>' for a in pos.get("alertas",[]))

        # Score block central con señal
        score_block = render_score_badge(score, total_c, senal) if tf_1d.get("valido") else ""

        # ── S/R del portafolio ───────────────────────────────────────
        sr_port = an.get("sr", {})
        sr_html_port = render_zonas_sr(sr_port, pos.get("precio_actual_mxn") or pos["cto_prom_mxn"], tc)
        stop_sr_port = sr_port.get("stop_sr")
        obj_sr_port  = sr_port.get("objetivo_sr")
        precio_ref   = pos.get("precio_actual_mxn") or pos["cto_prom_mxn"]
        # Celda S/R inline: "S $X (-Y%) / R $X (+Z%)"
        if stop_sr_port or obj_sr_port:
            s_txt = (f'<div style="white-space:nowrap"><span style="color:var(--green);font-size:9px;font-weight:600">S</span> '
                     f'<span style="color:var(--green);font-family:var(--mono);font-size:11px">{fmt(stop_sr_port)}</span>'
                     f'<span style="color:var(--muted);font-size:9px"> ({(stop_sr_port-precio_ref)/precio_ref*100:.1f}%)</span></div>'
                     if stop_sr_port else "")
            r_txt = (f'<div style="white-space:nowrap"><span style="color:var(--red);font-size:9px;font-weight:600">R</span> '
                     f'<span style="color:var(--red);font-family:var(--mono);font-size:11px">{fmt(obj_sr_port)}</span>'
                     f'<span style="color:var(--muted);font-size:9px"> (+{(obj_sr_port-precio_ref)/precio_ref*100:.1f}%)</span></div>'
                     if obj_sr_port else "")
            sr_inline_port = f'<div style="font-size:10px;line-height:1.9">{s_txt}{r_txt}</div>'
        else:
            sr_inline_port = '<span class="hint" style="font-size:10px">—</span>'

        # ── GESTIÓN DE POSICIÓN ABIERTA ─────────────────────────────────
        gestion_info = {}
        if pos.get("precio_actual_mxn") and pos.get("cto_prom_mxn") and tf_1d.get("valido"):
            mult_g = tc if pos.get("origen","USA") == "USA" else 1.0
            gestion_info = gestion_posicion(
                precio_entrada_mxn = pos["cto_prom_mxn"],
                precio_actual_mxn  = pos["precio_actual_mxn"],
                stop_mxn           = tf_1d.get("stop", 0) * mult_g,
                objetivo_mxn       = tf_1d.get("objetivo", 0) * mult_g,
                titulos            = pos["titulos"],
            )
        gestion_html = render_gestion_panel(
            gestion_info,
            pos.get("precio_actual_mxn", 0),
            pos.get("stop_mxn"),
        )

        # OBV del portafolio
        obv_port  = tf_1d.get("obv", {}) if tf_1d.get("valido") else {}
        obv_html_port = render_obv_panel(obv_port)

        detail=(f'<div class="detail-panel">'
                f'{render_conf(conf) if conf else ""}'
                f'{alertas_h}'
                # ── Gestión de posición (lo más importante para posiciones abiertas)
                f'<div class="dp-sec" style="margin-bottom:10px">'
                f'<div class="dp-sec-t">🎯 Gestión de la posición</div>'
                f'{gestion_html}</div>'
                # ── Escenarios S/R prominente en portafolio ──
                f'<div style="display:grid;grid-template-columns:1fr 1fr;gap:10px;margin-bottom:12px">'
                f'<div class="esc-card esc-card-down">'
                f'<div class="esc-title" style="color:var(--red)">📉 Si cae — soporte próximo</div>'
                f'<div class="esc-price" style="color:var(--red)">{fmt(stop_sr_port) if stop_sr_port else "—"}</div>'
                f'<div class="esc-sub" style="color:#7f1d1d">'
                f'{"Puede caer hasta aquí · " + f"{(stop_sr_port-precio_ref)/precio_ref*100:.1f}% desde precio actual" if stop_sr_port else "Sin soporte claro debajo"}'
                f'</div></div>'
                f'<div class="esc-card esc-card-up">'
                f'<div class="esc-title" style="color:var(--green)">📈 Si sube — resistencia / vender en</div>'
                f'<div class="esc-price" style="color:var(--green)">{fmt(obj_sr_port) if obj_sr_port else "—"}</div>'
                f'<div class="esc-sub" style="color:#14532d">'
                f'{"Meta de venta o toma de ganancias · +" + f"{(obj_sr_port-precio_ref)/precio_ref*100:.1f}% potencial" if obj_sr_port else "Sin resistencia clara arriba"}'
                f'</div></div></div>'
                f'<div class="dp-grid">'
                f'<div class="dp-sec"><div class="dp-sec-t">Semáforo indicadores (1D)</div>'
                f'{render_criterios(criterios) if criterios else "<p class=hint>Sin datos API</p>"}</div>'
                f'<div style="display:flex;flex-direction:column;gap:8px">'
                f'<div class="dp-sec"><div class="dp-sec-t">Timeframes</div>{render_tf_chips(tfs)}</div>'
                f'<div class="dp-sec"><div class="dp-sec-t">Score técnico</div>{score_block}</div>'
                f'<div class="dp-sec"><div class="dp-sec-t">Tamaño de posición adicional</div>'
                f'{render_sizing(sz,pos["titulos"],tc,pos["origen"])}</div></div>'
                f'<div style="display:flex;flex-direction:column;gap:8px">'
                f'<div class="dp-sec"><div class="dp-sec-t">Niveles clave MXN</div>'
                f'{render_niveles(tf_1d if tf_1d.get("valido") else {},tc,pos["origen"])}</div>'
                f'{render_rec(senal,tf_1d,pos.get("entrada_mxn"),pos.get("stop_mxn"),pos.get("obj_mxn"))}'
                f'</div></div>'
                f'<div style="display:grid;grid-template-columns:1fr 1fr;gap:10px;margin-top:10px">'
                f'<div class="dp-sec"><div class="dp-sec-t">Flujo institucional (OBV)</div>{obv_html_port}</div>'
                f'<div class="dp-sec"><div class="dp-sec-t">Mapa de Soportes / Resistencias</div>{sr_html_port}</div>'
                f'</div>'
                f'</div>')

        h+=(f'<tr class="datarow" onclick="toggle(\'{rid}\')">'
            f'<td><strong>{pos["ticker"]}</strong><br><span class="hint">{pos["origen"]} · {pos["mercado"]}</span></td>'
            f'<td class="num">{pos["titulos"]}</td>'
            f'<td class="num">{fmt(pos["cto_prom_mxn"])}</td>'
            f'<td class="num">{precio_cell}</td>'
            f'<td class="num">{fmt(pos["costo_total"])}</td>'
            f'<td class="num {pl_cls}">{fmt(pos["pl_mxn"])}</td>'
            f'<td class="num {pl_cls}">{pos["pl_pct"]:+.1f}%</td>'
            f'<td>{badge_senal(senal)}</td>'
            f'<td>{sr_inline_port}</td>'
            f'<td>{gbm_cell(pos.get("entrada_mxn"),pos.get("stop_mxn"),pos.get("obj_mxn"))}</td>'
            f'</tr>'
            f'<tr class="detail" id="{rid}"><td colspan="10" style="padding:0">{detail}</td></tr>')
    return h


def render_scan_rows(scanner, tc):
    h=""
    for r in scanner:
        rid=f"sc_{r['nombre']}"
        rr_col=("var(--green)" if r["rr"]>=3 else "var(--yellow)" if r["rr"]>=2 else "var(--red)")
        rr_pct=min(r["rr"]/6,1)*100
        crit=r.get("criterios",{}); sz=r.get("sizing",{}); conf=r.get("confluencia",{})
        en_cartera=r.get("titulos_cartera",0)
        score=r.get("score",0)
        score_aj=r.get("score_ajustado",score)
        total_c=r.get("total_criterios",9)
        senal_1d = r.get("tfs",{}).get("1D",{}).get("senal","SIN DATOS")
        etf_peligroso = r.get("etf_apalancado", False)
        exit_info = r.get("exit_info", {})
        adx_val   = r.get("adx", 0)
        setup     = r.get("setup", {})
        tipo_setup= setup.get("tipo_setup","—")
        confianza = setup.get("confianza",0)
        penaliz   = r.get("regimen",{}).get("penalizacion",0)

        cartera_badge = (f'<br><span class="badge b-hold" style="font-size:9px">★ {en_cartera} en cartera</span>'
                         if en_cartera>0 else "")
        etf_badge   = badge_etf_apalancado() if etf_peligroso else ""
        setup_badge = badge_setup(tipo_setup) if tipo_setup not in ("—","Bloqueado","Salida urgente") else ""

        score_block    = render_score_badge(score_aj, total_c, senal_1d)
        score_adj_note = ""
        if penaliz > 0:
            score_adj_note = (f'<div class="score-adj">Técnico: {score}/{total_c} '
                              f'→ ajustado: {score_aj}/{total_c} (VIX -{penaliz}pt)</div>')

        exit_html     = render_exit_banner(exit_info) if exit_info else ""
        decision_html = render_decision_box(setup)

        # ── ZONAS S/R ────────────────────────────────────────────────────
        sr         = r.get("sr", {})
        sr_html    = render_zonas_sr(sr, r["precio_mxn"], tc)
        sr_badge   = ""
        if sr.get("en_zona"):
            sr_badge = '<span style="font-size:9px;background:#fff7e6;color:#d46b08;border:1px solid #ffd591;border-radius:8px;padding:1px 5px;margin-left:4px">⚡ en zona</span>'

        # ── CELDA S/R INLINE ─────────────────────────────────────────────
        stop_sr_mxn  = sr.get("stop_sr")
        obj_sr_mxn   = sr.get("objetivo_sr")
        if stop_sr_mxn or obj_sr_mxn:
            down_pct = ((stop_sr_mxn - r["precio_mxn"]) / r["precio_mxn"] * 100) if stop_sr_mxn else None
            up_pct   = ((obj_sr_mxn  - r["precio_mxn"]) / r["precio_mxn"] * 100) if obj_sr_mxn  else None
            lines = []
            if stop_sr_mxn:
                lines.append(f'<div><span style="color:var(--green);font-size:9px">S</span> '
                             f'<span style="color:var(--green);font-family:var(--mono)">{fmt(stop_sr_mxn)}</span>'
                             f'<span style="color:var(--muted);font-size:9px"> ({down_pct:.1f}%)</span></div>')
            if obj_sr_mxn:
                lines.append(f'<div><span style="color:var(--red);font-size:9px">R</span> '
                             f'<span style="color:var(--red);font-family:var(--mono)">{fmt(obj_sr_mxn)}</span>'
                             f'<span style="color:var(--muted);font-size:9px"> (+{up_pct:.1f}%)</span></div>')
            sr_cell_html = f'<div style="font-size:10px;line-height:1.9;white-space:nowrap">{"".join(lines)}</div>'
        else:
            sr_cell_html = '<span class="hint" style="font-size:10px">—</span>'

        # ── OBV + SECTOR + HISTORIAL ─────────────────────────────────────
        obv_html    = render_obv_panel(r.get("obv", {}))
        sector_html = render_sector_panel(r.get("sector", {}))
        hist_html   = render_score_history(r["nombre"], score_aj)
        dca_html    = render_dca_panel(r.get("dca", {}), r["precio_mxn"])
        ganga_html  = render_ganga_panel(r.get("ganga", {}))

        etf_warning = ""
        if etf_peligroso:
            min_s = 8 if r.get("vix",20)>20 else 7
            etf_warning = (f'<div style="background:#fff7e6;border:1px solid #ffd591;border-radius:var(--r);'
                           f'padding:9px 13px;margin-bottom:9px;font-size:11px;color:#d46b08">'
                           f'⚡ <strong>ETF 3x</strong> — Score mín: {min_s}/11. '
                           f'Solo con VIX &lt; 20 y SPY alcista.</div>')

        adx_color = "var(--green)" if adx_val>=25 else "var(--yellow)" if adx_val>=20 else "var(--red)"
        adx_label = "✅ Tendencia" if adx_val>=25 else "⚠️ Débil" if adx_val>=20 else "❌ Lateral"

        detail=(f'<div class="detail-panel">'
                f'{exit_html}'
                f'{ganga_html}'
                f'{decision_html}'
                f'{etf_warning}'
                f'{render_conf(conf) if conf else ""}'
                f'<div class="dp-grid">'
                f'<div class="dp-sec"><div class="dp-sec-t">Semáforo indicadores 1D</div>'
                f'{render_criterios(crit) if crit else "<p class=hint>Sin datos</p>"}</div>'
                f'<div style="display:flex;flex-direction:column;gap:8px">'
                f'<div class="dp-sec"><div class="dp-sec-t">Timeframes</div>'
                f'{render_tf_chips(r.get("tfs",{}))}</div>'
                f'<div class="dp-sec"><div class="dp-sec-t">Score técnico</div>'
                f'{score_block}{score_adj_note}</div>'
                f'<div class="dp-sec"><div class="dp-sec-t">Tamaño de posición</div>'
                f'{render_sizing(sz,en_cartera,tc,"USA")}</div></div>'
                f'<div style="display:flex;flex-direction:column;gap:8px">'
                f'<div class="dp-sec"><div class="dp-sec-t">Niveles clave MXN</div>'
                f'<div class="sz-grid" style="font-size:12px">'
                f'<div class="pl-row"><span>Precio actual</span><span class="num">{fmt(r["precio_mxn"])}</span></div>'
                f'<div class="pl-row"><span>Entrada EMA9</span><span class="num" style="color:var(--green)">{fmt(r["entrada_mxn"])}</span></div>'
                f'<div class="pl-row"><span>Stop dinámico</span><span class="num" style="color:var(--red)">{fmt(r["stop_mxn"])}</span></div>'
                f'<div class="pl-row"><span>Objetivo</span><span class="num" style="color:var(--green)">{fmt(r["obj_mxn"])}</span></div>'
                f'<div class="pl-row"><span>RSI</span><span class="num" style="color:{rsi_col(r["rsi"])}">{r["rsi"]:.0f}</span></div>'
                f'<div class="pl-row"><span>ADX</span><span class="num" style="color:{adx_color}">{adx_val:.0f} {adx_label}</span></div>'
                f'<div class="pl-row"><span>R:R</span><span class="num" style="color:{rr_col}">{r["rr"]:.1f}x</span></div>'
                f'</div></div></div></div>'
                f'<div style="display:grid;grid-template-columns:1fr 1fr;gap:10px;margin-top:10px">'
                f'<div class="dp-sec"><div class="dp-sec-t">Flujo institucional (OBV)</div>{obv_html}</div>'
                f'<div class="dp-sec"><div class="dp-sec-t">Contexto de sector</div>{sector_html}</div>'
                f'</div>'
                f'<div style="display:grid;grid-template-columns:1fr 1fr;gap:10px;margin-top:10px">'
                f'<div class="dp-sec"><div class="dp-sec-t">Zonas de soporte / resistencia</div>{sr_html}</div>'
                f'<div class="dp-sec"><div class="dp-sec-t">Historial de scores</div>{hist_html}</div>'
                f'</div>'
                f'<div style="margin-top:10px">'
                f'<div class="dp-sec"><div class="dp-sec-t">📥 Plan de acumulación DCA — si quieres entrar escalonado</div>'
                f'{dca_html}</div></div>'
                f'</div>')

        score_color = "var(--green)" if score_aj>=7 else "var(--yellow)" if score_aj>=5 else "var(--red)"
        conf_bar_mini = (f'<div style="width:36px;height:4px;background:var(--brd2);border-radius:2px;margin-top:2px">'
                         f'<div style="height:100%;width:{confianza}%;background:{"#52c41a" if confianza>=70 else "#faad14" if confianza>=40 else "#ff4d4f"};border-radius:2px"></div></div>'
                         if confianza>0 else "")
        h+=(f'<tr class="datarow" onclick="toggle(\'{rid}\')">'
            f'<td><strong>{r["nombre"]}</strong>{etf_badge}{setup_badge}{sr_badge}{cartera_badge}</td>'
            f'<td>{badge_estado(r["estado"])}</td>'
            f'<td class="num">{fmt(r["precio_mxn"])}<br><span class="hint">USD {r["precio_usd"]:.2f}</span></td>'
            f'<td class="num" style="color:var(--green)">{fmt(r["entrada_mxn"])}</td>'
            f'<td><div class="rrw"><div class="rrb"><div class="rrf" style="width:{rr_pct:.0f}%;background:{rr_col}"></div></div>'
            f'<span style="color:{rr_col};font-family:var(--mono);font-size:11px">{r["rr"]:.1f}x</span></div></td>'
            f'<td style="color:{rsi_col(r["rsi"])};font-weight:600;font-family:var(--mono)">{r["rsi"]:.0f}</td>'
            f'<td>{"<span style=color:var(--green)>▲</span>" if r["macd_ok"] else "<span style=color:var(--red)>▼</span>"}</td>'
            f'<td>{"<span style=color:var(--green)>↑</span>" if r["ema200_ok"] else "<span style=color:var(--red)>↓</span>"}</td>'
            f'<td>{gbm_cell(r["entrada_mxn"],r["stop_mxn"],r["obj_mxn"])}</td>'
            f'<td>{sr_cell_html}</td>'
            f'<td><span style="font-family:var(--mono);font-size:12px;color:{score_color};font-weight:600">'
            f'{score_aj}/{total_c}</span>{conf_bar_mini}'
            f'{"<br><span style=font-size:9px;color:var(--muted)>adj VIX</span>" if penaliz>0 else ""}</td>'
            f'</tr>'
            f'<tr class="detail" id="{rid}"><td colspan="11" style="padding:0">{detail}</td></tr>')
    return h




def render_hist_rows(ops):
    if not ops:
        return '<tr><td colspan="10" style="text-align:center;color:var(--muted);padding:28px;font-size:12px">Sin operaciones aún</td></tr>'
    h=""
    for op in ops:
        c="var(--green)" if op["tipo"]=="COMPRA" else "var(--red)"
        oid    = op['id']
        oticker= op['ticker']
        otype  = op['tipo']
        otit   = op['titulos']
        oprecio= op['precio_mxn']
        ofecha = op['fecha']
        onotas = (op.get('notas','') or '').replace("'","\\'")
        edit_fn= f"editOp({oid},'{oticker}','{otype}',{otit},{oprecio},'{ofecha}','{onotas}')"
        del_fn = f"delOp({oid},'{oticker}')"
        h+=(f'<tr>'
            f'<td>{op["fecha"][:10]}</td>'
            f'<td><strong>{op["ticker"]}</strong></td>'
            f'<td style="color:{c};font-weight:600">{op["tipo"]}</td>'
            f'<td class="num">{op["titulos"]}</td>'
            f'<td class="num">{fmt(op["precio_mxn"])}</td>'
            f'<td class="num">{fmt(op["total_mxn"])}</td>'
            f'<td style="font-family:var(--mono);font-size:11px">{op.get("tc_dia","—")}</td>'
            f'<td>{op.get("origen","—")}</td>'
            f'<td style="color:var(--muted);font-size:11px">{op.get("notas","") or "—"}</td>'
            f'<td>'
            f'<button class="btn-sm btn-edit" onclick="{edit_fn}">Editar</button> '
            f'<button class="btn-sm btn-del" onclick="{del_fn}">Borrar</button>'
            f'</td></tr>')
    return h


def render_radar_rows(radar, tc):
    if not radar:
        return '<tr><td colspan="11" style="text-align:center;color:var(--muted);padding:28px;font-size:12px">Sin datos</td></tr>'
    h=""
    for r in radar:
        rid=f"rd_{r['nombre']}"
        rr_col=("var(--green)" if r["rr"]>=3 else "var(--yellow)" if r["rr"]>=2 else "var(--red)")
        rr_pct=min(r["rr"]/6,1)*100
        estado     = r["estado"]
        setup      = r.get("setup",{})
        tipo_setup = setup.get("tipo_setup","—")
        confianza  = setup.get("confianza",0)
        score      = r.get("score",0)
        score_aj   = r.get("score_ajustado",score)
        total_c    = r.get("total_criterios",9)
        adx_val    = r.get("adx",0)
        etf_peligroso = r.get("etf_apalancado",False)
        exit_info  = r.get("exit_info",{})
        pot_col    = "var(--green)" if r["pot_alza"]>=10 else "var(--muted)"
        en_cartera = r.get("titulos_cartera",0)

        cartera_tag = (f'<br><span class="badge b-hold" style="font-size:9px">★ {en_cartera} tít</span>'
                       if en_cartera>0 else "")
        etf_badge   = badge_etf_apalancado() if etf_peligroso else ""
        setup_badge = badge_setup(tipo_setup) if tipo_setup not in ("—","Bloqueado","Salida urgente") else ""

        crit = r.get("criterios",{}); sz = r.get("sizing",{})
        senal_est = ("COMPRAR" if estado in ("ROCKET","BUY") else
                     "VENDER" if estado in ("SHORT","EXIT","RUPTURA") else "MANTENER")
        penaliz   = r.get("regimen",{}).get("penalizacion",0)  # para badge adj VIX
        score_block   = render_score_badge(score_aj, total_c, senal_est)
        decision_html = render_decision_box(setup)
        exit_html     = render_exit_banner(exit_info) if exit_info else ""

        # ── S/R y HISTORIAL para radar ────────────────────────────────
        sr        = r.get("sr", {})
        sr_html   = render_zonas_sr(sr, r["precio_mxn"], tc)
        hist_html = render_score_history(r["nombre"], score_aj)
        sr_badge  = ""
        if sr.get("en_zona"):
            sr_badge = '<span style="font-size:9px;background:#fff7e6;color:#d46b08;border:1px solid #ffd591;border-radius:8px;padding:1px 5px;margin-left:4px">⚡ en zona</span>'

        adx_color = "var(--green)" if adx_val>=25 else "var(--yellow)" if adx_val>=20 else "var(--red)"
        adx_label = "✅" if adx_val>=25 else "⚠️" if adx_val>=20 else "❌"

        obv_html_r    = render_obv_panel(r.get("obv", {}))
        sector_html_r = render_sector_panel(r.get("sector", {}))
        ganga_html_r  = render_ganga_panel(r.get("ganga", {}))

        detail=(f'<div class="detail-panel">'
                f'{exit_html}'
                f'{ganga_html_r}'
                f'{decision_html}'
                f'<div class="dp-grid">'
                f'<div class="dp-sec"><div class="dp-sec-t">Semáforo indicadores 1D</div>'
                f'{render_criterios(crit) if crit else "<p class=hint>Sin datos</p>"}</div>'
                f'<div style="display:flex;flex-direction:column;gap:8px">'
                f'<div class="dp-sec"><div class="dp-sec-t">Score técnico</div>{score_block}</div>'
                f'<div class="dp-sec"><div class="dp-sec-t">Tamaño de posición</div>'
                f'{render_sizing(sz,en_cartera,tc,"USA")}</div></div>'
                f'<div class="dp-sec"><div class="dp-sec-t">Niveles clave MXN</div>'
                f'<div class="sz-grid" style="font-size:12px">'
                f'<div class="pl-row"><span>Precio actual</span><span class="num">{fmt(r["precio_mxn"])}</span></div>'
                f'<div class="pl-row"><span>Entrada EMA9</span><span class="num" style="color:var(--green)">{fmt(r["entrada_mxn"])}</span></div>'
                f'<div class="pl-row"><span>Stop dinámico</span><span class="num" style="color:var(--red)">{fmt(r["stop_mxn"])}</span></div>'
                f'<div class="pl-row"><span>Objetivo</span><span class="num" style="color:var(--green)">{fmt(r["obj_mxn"])}</span></div>'
                f'<div class="pl-row"><span>Potencial</span><span class="num" style="color:var(--green)">{r["pot_alza"]:.1f}%</span></div>'
                f'<div class="pl-row"><span>RSI</span><span class="num" style="color:{rsi_col(r["rsi"])}">{r["rsi"]:.0f}</span></div>'
                f'<div class="pl-row"><span>ADX</span><span class="num" style="color:{adx_color}">{adx_val:.0f} {adx_label}</span></div>'
                f'<div class="pl-row"><span>R:R</span><span class="num" style="color:{rr_col}">{r["rr"]:.1f}x</span></div>'
                f'</div></div></div>'
                f'<div style="display:grid;grid-template-columns:1fr 1fr;gap:10px;margin-top:10px">'
                f'<div class="dp-sec"><div class="dp-sec-t">Flujo institucional (OBV)</div>{obv_html_r}</div>'
                f'<div class="dp-sec"><div class="dp-sec-t">Contexto de sector</div>{sector_html_r}</div>'
                f'</div>'
                f'<div style="display:grid;grid-template-columns:1fr 1fr;gap:10px;margin-top:10px">'
                f'<div class="dp-sec"><div class="dp-sec-t">Zonas de soporte / resistencia</div>{sr_html}</div>'
                f'<div class="dp-sec"><div class="dp-sec-t">Historial de scores</div>{hist_html}</div>'
                f'</div>'
                f'<div style="margin-top:10px">'
                f'<div class="dp-sec"><div class="dp-sec-t">📥 Plan de acumulación DCA — si quieres entrar escalonado</div>'
                f'{render_dca_panel(r.get("dca",{}),r["precio_mxn"])}</div></div>'
                f'</div>')

        score_color = "var(--green)" if score_aj>=7 else "var(--yellow)" if score_aj>=5 else "var(--red)"
        conf_mini   = (f'<div style="width:36px;height:3px;background:var(--brd2);border-radius:2px;margin-top:2px">'
                       f'<div style="height:100%;width:{confianza}%;background:{"#52c41a" if confianza>=70 else "#faad14"};border-radius:2px"></div></div>'
                       if confianza>0 else "")
        # ── CELDA S/R INLINE radar ──────────────────────────────────
        stop_sr_mxn_r  = sr.get("stop_sr")
        obj_sr_mxn_r   = sr.get("objetivo_sr")
        sr_cell_r = ""
        if stop_sr_mxn_r or obj_sr_mxn_r:
            d_pct = ((stop_sr_mxn_r - r["precio_mxn"]) / r["precio_mxn"] * 100) if stop_sr_mxn_r else None
            u_pct = ((obj_sr_mxn_r  - r["precio_mxn"]) / r["precio_mxn"] * 100) if obj_sr_mxn_r  else None
            lines_r = []
            if stop_sr_mxn_r:
                lines_r.append(f'<div style="white-space:nowrap"><span style="color:var(--green);font-size:9px">S</span> '
                               f'<span style="color:var(--green);font-family:var(--mono)">{fmt(stop_sr_mxn_r)}</span>'
                               f'<span style="color:var(--muted);font-size:9px"> ({d_pct:.1f}%)</span></div>')
            if obj_sr_mxn_r:
                lines_r.append(f'<div style="white-space:nowrap"><span style="color:var(--red);font-size:9px">R</span> '
                               f'<span style="color:var(--red);font-family:var(--mono)">{fmt(obj_sr_mxn_r)}</span>'
                               f'<span style="color:var(--muted);font-size:9px"> (+{u_pct:.1f}%)</span></div>')
            sr_cell_r = f'<div style="font-size:10px;line-height:1.9">{"".join(lines_r)}</div>'
        else:
            sr_cell_r = '<span class="hint" style="font-size:10px">—</span>'
        h+=(f'<tr class="datarow" onclick="toggle(\'{rid}\')">'
            f'<td><strong>{r["nombre"]}</strong>{etf_badge}{setup_badge}{sr_badge}{cartera_tag}</td>'
            f'<td>{badge_estado(estado)}</td>'
            f'<td class="num">{fmt(r["precio_mxn"])}<br><span class="hint">USD {r["precio_usd"]:.2f}</span></td>'
            f'<td class="num" style="color:var(--green)">{fmt(r["entrada_mxn"])}</td>'
            f'<td class="num" style="color:{pot_col};font-weight:{"600" if r["pot_alza"]>=10 else "400"}">{r["pot_alza"]:+.1f}%</td>'
            f'<td><div class="rrw"><div class="rrb"><div class="rrf" style="width:{rr_pct:.0f}%;background:{rr_col}"></div></div>'
            f'<span style="color:{rr_col};font-family:var(--mono);font-size:11px">{r["rr"]:.1f}x</span></div></td>'
            f'<td style="color:{rsi_col(r["rsi"])};font-weight:600;font-family:var(--mono)">{r["rsi"]:.0f}</td>'
            f'<td>{"<span style=color:var(--green)>▲</span>" if r["macd_ok"] else "<span style=color:var(--red)>▼</span>"}</td>'
            f'<td>{"<span style=color:var(--green)>↑</span>" if r["ema200_ok"] else "<span style=color:var(--red)>↓</span>"}</td>'
            f'<td><span style="font-family:var(--mono);font-size:12px;color:{score_color};font-weight:600">'
            f'{score_aj}/{total_c}</span>{conf_mini}</td>'
            f'<td>{sr_cell_r}</td>'
            f'<td>{gbm_cell(r["entrada_mxn"],r["stop_mxn"],r["obj_mxn"])}</td>'
            f'</tr>'
            f'<tr class="detail" id="{rid}"><td colspan="12" style="padding:0">{detail}</td></tr>')
    return h




def resumen_hist(ops):
    if not ops:
        return {"inv":0,"vta":0,"pl":0,"n":0,"n_compras":0,"n_ventas":0,
                "n_ops_cerradas":0,"ventas_ganadoras":0,"ventas_perdedoras":0,
                "tasa_acierto":0,"ganancia_promedio":0,"perdida_promedio":0,
                "expectativa":0,"mejor_op":0,"peor_op":0,"por_ticker":{},"por_mes":{}}
    inv=sum(o["total_mxn"] for o in ops if o["tipo"]=="COMPRA")
    vta=sum(o["total_mxn"] for o in ops if o["tipo"]=="VENTA")
    compras_q=defaultdict(list); resultados_ops=[]
    for op in sorted(ops, key=lambda x: x["fecha"]):
        t=op["ticker"]
        if op["tipo"]=="COMPRA":
            compras_q[t].append({"precio":op["precio_mxn"],"titulos":op["titulos"]})
        elif op["tipo"]=="VENTA" and compras_q[t]:
            cmp=compras_q[t][0]
            pl=(op["precio_mxn"]-cmp["precio"])*op["titulos"]
            resultados_ops.append({"pl":pl,"ticker":t,"fecha":op["fecha"][:7]})
            if op["titulos"]>=cmp["titulos"]: compras_q[t].pop(0)
            else: compras_q[t][0]["titulos"]-=op["titulos"]
    ganadoras=[r for r in resultados_ops if r["pl"]>0]
    perdedoras=[r for r in resultados_ops if r["pl"]<=0]
    n_c=len(resultados_ops)
    tasa=(len(ganadoras)/n_c*100) if n_c else 0
    gan_p=sum(r["pl"] for r in ganadoras)/len(ganadoras) if ganadoras else 0
    per_p=sum(r["pl"] for r in perdedoras)/len(perdedoras) if perdedoras else 0
    exp=(tasa/100*gan_p)+((1-tasa/100)*per_p) if n_c else 0
    mejor=max((r["pl"] for r in resultados_ops),default=0)
    peor=min((r["pl"] for r in resultados_ops),default=0)
    por_ticker=defaultdict(float)
    for r in resultados_ops: por_ticker[r["ticker"]]+=r["pl"]
    por_mes=defaultdict(float)
    for r in resultados_ops: por_mes[r["fecha"]]+=r["pl"]
    return {"inv":inv,"vta":vta,"pl":vta-inv,"n":len(ops),
            "n_compras":sum(1 for o in ops if o["tipo"]=="COMPRA"),
            "n_ventas":sum(1 for o in ops if o["tipo"]=="VENTA"),
            "n_ops_cerradas":n_c,"ventas_ganadoras":len(ganadoras),
            "ventas_perdedoras":len(perdedoras),"tasa_acierto":tasa,
            "ganancia_promedio":gan_p,"perdida_promedio":per_p,"expectativa":exp,
            "mejor_op":mejor,"peor_op":peor,
            "por_ticker":dict(sorted(por_ticker.items(),key=lambda x:-abs(x[1]))),
            "por_mes":dict(sorted(por_mes.items()))}


def render_por_ticker(por_ticker: dict) -> str:
    if not por_ticker: return '<p class="hint">Sin operaciones cerradas aún</p>'
    items = list(por_ticker.items())[:8]
    h=""
    for t,v in items:
        cls="pos" if v>=0 else "neg"
        h+=f'<div class="pl-row"><span><strong>{t}</strong></span><span class="num {cls}">{fmt(v)}</span></div>'
    return h

def render_por_mes(por_mes: dict) -> str:
    if not por_mes: return ""
    items_html=""
    for m,v in por_mes.items():
        bg="var(--green-l)" if v>=0 else "var(--red-l)"
        bdr="var(--green-b)" if v>=0 else "var(--red-b)"
        cls="pos" if v>=0 else "neg"
        items_html+=(f'<div style="background:{bg};border:1px solid {bdr};border-radius:6px;'
                     f'padding:7px 12px;font-size:11px">'
                     f'<div class="hint">{m}</div>'
                     f'<div class="num {cls}" style="font-size:13px">{fmt(v)}</div></div>')
    return (f'<div style="padding:14px 18px;border-top:1px solid var(--brd)">'
            f'<div class="hint" style="margin-bottom:10px;text-transform:uppercase;font-size:10px;letter-spacing:.06em">P&L mensual realizado</div>'
            f'<div style="display:flex;gap:8px;flex-wrap:wrap">{items_html}</div></div>')


# ── HTML PRINCIPAL ─────────────────────────────────────────
def generar_html(port_data, scan_data, radar_data, ops, tc, capital, riesgo_pct, rr_min,
                 vix: float = 20.0, spy: dict | None = None, regimen: dict | None = None):
    if spy     is None: spy     = {"sobre_ema200": True}
    if regimen is None: regimen = regimen_mercado(vix, spy)
    # Hora de México (UTC-6) — Render corre en UTC
    from datetime import timezone, timedelta
    tz_mx = timezone(timedelta(hours=-6))
    ts  = datetime.now(tz_mx).strftime("%d/%m/%Y %H:%M")
    res = resumen_hist(ops)

    total_valor = sum(p.get("valor_mxn",0) for p in port_data)
    total_costo = sum(p.get("costo_total",0) for p in port_data)
    total_pl    = total_valor-total_costo
    total_pl_pct= (total_pl/total_costo*100) if total_costo else 0
    n_alertas   = sum(len(p.get("alertas",[])) for p in port_data)

    # Serializar portafolio a JSON para sync en browser
    port_json = json.dumps([{
        "ticker":        p["ticker"],
        "titulos":       p["titulos"],
        "cto_prom_mxn":  p["cto_prom_mxn"],
        "origen":        p.get("origen","USA"),
        "mercado":       p.get("mercado","SIC"),
        "precio_actual_mxn": p.get("precio_actual_mxn"),
        "pl_mxn":        p.get("pl_mxn",0),
        "pl_pct":        p.get("pl_pct",0),
        "activo":        p.get("activo",1),
        "sr": (p.get("analisis") or {}).get("sr", {}),
    } for p in port_data], ensure_ascii=False)

    port_rows  = render_port_rows(port_data, tc)
    scan_rows  = render_scan_rows(scan_data, tc)
    radar_rows = render_radar_rows(radar_data, tc)
    hist_rows  = render_hist_rows(ops)

    n_radar =len(radar_data)
    n_rocket=sum(1 for r in radar_data if r["estado"]=="ROCKET")
    n_buy   =sum(1 for r in radar_data if r["estado"]=="BUY")
    n_watch =sum(1 for r in radar_data if r["estado"] in ("WATCH","LATERAL","BLOQUEADO","RUPTURA"))
    n_short =sum(1 for r in radar_data if r["estado"]=="SHORT")
    n_skip  =sum(1 for r in radar_data if r["estado"] in ("SKIP","EXIT"))
    total_univ=len(UNIVERSO)

    df=pd.DataFrame(ops) if ops else pd.DataFrame(columns=["fecha","tipo","total_mxn"])
    if not df.empty and "fecha" in df.columns:
        df["mes"]=pd.to_datetime(df["fecha"]).dt.strftime("%Y-%m")
        rm=df.groupby(["mes","tipo"])["total_mxn"].sum().unstack(fill_value=0)
        res_mes_html=rm.to_html(classes="mini-table",border=0) if not rm.empty else "<p class='hint'>Sin datos</p>"
    else:
        res_mes_html="<p class='hint'>Sin datos</p>"

    alert_banner=""
    if n_alertas:
        items="".join(f'<div class="alert-pill">{a} — {p["ticker"]}</div>'
                      for p in port_data for a in p.get("alertas",[]))
        alert_banner=f'<div class="notif-bar"><div class="ntitle">⚠ {n_alertas} alerta(s)</div>{items}</div>'

    tasa_color="var(--green)" if res.get("tasa_acierto",0)>=50 else "var(--red)"
    tasa_val=f'{res.get("tasa_acierto",0):.0f}%'
    tasa_bar=f'{min(res.get("tasa_acierto",0),100):.0f}%'
    tasa_msg=("✅ Sistema ganador — sigue operando con disciplina" if res.get("tasa_acierto",0)>=50
              else "⚠️ Menos del 50% de aciertos — revisa tus criterios"
              if res.get("n_ops_cerradas",0)>0 else "Aún sin operaciones cerradas")
    exp_cls="pos" if res.get("expectativa",0)>=0 else "neg"
    exp_msg=("✅ Expectativa positiva — sistema matemáticamente rentable"
             if res.get("expectativa",0)>0
             else "⚠️ Expectativa negativa — ajusta R:R o criterios de salida"
             if res.get("n_ops_cerradas",0)>0 else "Sin datos suficientes aún")
    pl_cls="pos" if total_pl>=0 else "neg"
    plpct_cls="pos" if total_pl_pct>=0 else "neg"
    pl_hist_cls="pos" if res["pl"]>=0 else "neg"
    al_cls="warn" if n_alertas>0 else ""

    por_ticker_html = render_por_ticker(res.get("por_ticker",{}))
    por_mes_html    = render_por_mes(res.get("por_mes",{}))

    return f"""<!DOCTYPE html>
<html lang="es"><head>
<meta charset="UTF-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>finbit pro</title>
<link href="https://fonts.googleapis.com/css2?family=DM+Sans:wght@300;400;500;600&family=DM+Mono:wght@400;500&display=swap" rel="stylesheet">
<style>
:root{{--red:#dc2626;--red-l:#fef2f2;--red-b:#fecaca;--green:#16a34a;--green-l:#f0fdf4;--green-b:#bbf7d0;--yellow:#b45309;--yellow-l:#fffbeb;--yellow-b:#fde68a;--purple:#7c3aed;--purple-l:#f5f3ff;--purple-b:#ddd6fe;--bg:#f5f5f3;--surface:#fff;--surface2:#f8f8f6;--brd:#e5e5e3;--brd2:#d1d1cf;--text:#111;--muted:#666;--hint:#aaa;--mono:'DM Mono',monospace;--sans:'DM Sans',sans-serif;--r:8px;--r2:12px}}
*{{margin:0;padding:0;box-sizing:border-box}}
body{{background:var(--bg);color:var(--text);font-family:var(--sans);font-size:13px;line-height:1.5}}
.topbar{{background:var(--surface);border-bottom:1px solid var(--brd);position:sticky;top:0;z-index:200}}
.topbar-inner{{max-width:1360px;margin:0 auto;padding:0 20px;height:50px;display:flex;align-items:center;justify-content:space-between;gap:12px}}
.logo{{font-size:16px;font-weight:600;letter-spacing:-.3px;color:var(--text)}}
.logo em{{color:var(--red);font-style:normal}}
.topbar-right{{display:flex;align-items:center;gap:14px;flex-wrap:wrap}}
.tc-chip{{font-size:11px;background:var(--surface2);border:1px solid var(--brd);border-radius:20px;padding:3px 10px;font-family:var(--mono)}}
.cfg-row{{display:flex;align-items:center;gap:8px;flex-wrap:wrap}}
.cfg-lbl{{font-size:10px;color:var(--muted)}}
.cfg-input{{width:80px;padding:3px 7px;border:1px solid var(--brd);border-radius:6px;font-size:12px;font-family:var(--mono);background:var(--surface);color:var(--text)}}
.cfg-btn{{padding:3px 10px;border:1px solid var(--brd);border-radius:6px;font-size:11px;background:var(--red);color:#fff;cursor:pointer;font-family:var(--sans)}}
.nav{{background:var(--surface);border-bottom:1px solid var(--brd);position:sticky;top:50px;z-index:199;overflow-x:auto}}
.nav-inner{{max-width:1360px;margin:0 auto;padding:0 20px;display:flex}}
.nb{{font-size:13px;padding:11px 18px;border:none;border-bottom:2px solid transparent;background:transparent;color:var(--muted);cursor:pointer;white-space:nowrap;font-family:var(--sans);margin-bottom:-1px;transition:all .15s}}
.nb.active{{color:var(--text);border-bottom-color:var(--red);font-weight:500}}
.nb:hover:not(.active){{color:var(--text)}}
.wrap{{max-width:1360px;margin:0 auto;padding:24px 20px 48px}}
.tab{{display:none}}.tab.active{{display:block}}
.kpis{{display:grid;grid-template-columns:repeat(auto-fit,minmax(130px,1fr));gap:10px;margin-bottom:18px}}
.kpi{{background:var(--surface);border:1px solid var(--brd);border-radius:var(--r2);padding:13px 15px}}
.kpi .lbl{{font-size:10px;color:var(--muted);text-transform:uppercase;letter-spacing:.05em;margin-bottom:5px}}
.kpi .val{{font-size:20px;font-weight:500;font-family:var(--mono)}}
.pos{{color:var(--green)}}.neg{{color:var(--red)}}.warn{{color:var(--yellow)}}
.tw{{background:var(--surface);border:1px solid var(--brd);border-radius:var(--r2);overflow:hidden;margin-bottom:18px}}
.tw-head{{padding:13px 17px;border-bottom:1px solid var(--brd);display:flex;justify-content:space-between;align-items:center;gap:8px;flex-wrap:wrap}}
.tw-head span{{font-size:13px;font-weight:500}}
.hint{{font-size:11px;color:var(--muted)}}
table{{width:100%;border-collapse:collapse}}
thead th{{padding:8px 13px;text-align:left;font-size:10px;text-transform:uppercase;letter-spacing:.06em;color:var(--muted);border-bottom:1px solid var(--brd);background:var(--surface2);white-space:nowrap;font-weight:400}}
.datarow{{border-bottom:1px solid var(--brd);cursor:pointer;transition:background .1s}}
.datarow:hover{{background:var(--surface2)}}
td{{padding:10px 13px;vertical-align:middle;font-size:12px}}
td strong{{font-size:13px;font-weight:500}}
.num{{font-family:var(--mono)}}
.badge{{display:inline-flex;padding:3px 8px;border-radius:20px;font-size:10px;font-weight:500;white-space:nowrap}}
.b-buy{{background:var(--green-l);color:var(--green);border:1px solid var(--green-b)}}
.b-sell{{background:var(--red-l);color:var(--red);border:1px solid var(--red-b)}}
.b-hold{{background:var(--yellow-l);color:var(--yellow);border:1px solid var(--yellow-b)}}
.b-none{{background:var(--surface2);color:var(--muted);border:1px solid var(--brd)}}
.b-rocket{{background:var(--purple-l);color:var(--purple);border:1px solid var(--purple-b)}}
.detail{{display:none}}.detail.open{{display:table-row}}
.detail-panel{{background:var(--surface2);border-left:3px solid var(--red);padding:14px 16px}}
.dp-grid{{display:grid;grid-template-columns:1fr 1fr 1fr;gap:10px}}
@media(max-width:860px){{.dp-grid{{grid-template-columns:1fr}}}}
.dp-sec{{background:var(--surface);border:1px solid var(--brd);border-radius:var(--r);padding:11px 13px}}
.dp-sec-t{{font-size:10px;text-transform:uppercase;letter-spacing:.06em;color:var(--muted);margin-bottom:9px}}
.crit-list{{display:flex;flex-direction:column}}
.crit-item{{border-bottom:1px solid var(--brd);padding-bottom:5px;margin-bottom:3px}}
.crit-item:last-child{{border-bottom:none}}
.crit-row{{display:flex;align-items:center;gap:7px;padding:3px 0}}
.crit-dot{{width:7px;height:7px;border-radius:50%;flex-shrink:0}}
.crit-label{{color:var(--muted);flex:1;font-size:11px}}
.crit-val{{font-family:var(--mono);font-size:10px}}
.crit-reason{{font-size:10px;color:var(--hint);margin:1px 0 4px 14px;line-height:1.5}}
/* Score block */
.score-block{{display:flex;flex-direction:column;align-items:center;padding:8px 0;gap:4px}}
.score-num{{font-size:42px;font-weight:600;font-family:var(--mono);line-height:1}}
.score-denom{{font-size:22px;color:var(--muted);font-weight:400}}
.score-lbl{{font-size:10px;color:var(--muted);text-transform:uppercase;letter-spacing:.06em}}
.score-bar{{width:100%;height:6px;background:var(--brd2);border-radius:3px;overflow:hidden;margin:4px 0}}
.score-fill{{height:100%;border-radius:3px;transition:width .3s}}
/* TF chips */
.tf-row{{display:grid;grid-template-columns:repeat(3,1fr);gap:6px}}
.tf-chip{{border:1px solid var(--brd);border-radius:6px;padding:7px 9px;text-align:center;background:var(--surface2)}}
.tf-na{{opacity:.4}}
.tf-name{{font-size:9px;color:var(--muted);text-transform:uppercase;letter-spacing:.07em;margin-bottom:3px}}
.tf-sig{{font-size:12px;font-weight:500}}
.tf-sub{{font-size:10px;color:var(--muted)}}
/* Sizing */
.sz-grid{{display:flex;flex-direction:column}}
.sz-row{{display:flex;justify-content:space-between;align-items:center;padding:5px 0;border-bottom:1px solid var(--brd);font-size:12px}}
.sz-row:last-child{{border-bottom:none}}
.sz-note{{font-size:10px;color:var(--muted);margin-top:7px;padding:6px 9px;background:var(--surface2);border-radius:5px;line-height:1.5}}
.frac-badge{{font-size:9px;background:var(--purple-l);color:var(--purple);border:1px solid var(--purple-b);border-radius:20px;padding:1px 6px;margin-left:4px}}
.pl-row{{display:flex;justify-content:space-between;align-items:center;padding:5px 0;border-bottom:1px solid var(--brd);font-size:12px}}
.pl-row:last-child{{border-bottom:none}}
.rec-box{{border-radius:var(--r);padding:10px 13px;font-size:12px;line-height:1.7}}
.rec-buy{{border:1px solid var(--green-b);background:var(--green-l);color:#14532d}}
.rec-hold{{border:1px solid var(--yellow-b);background:var(--yellow-l);color:#78350f}}
.rec-sell{{border:1px solid var(--red-b);background:var(--red-l);color:#7f1d1d}}
.rec-title{{font-weight:600;font-size:13px;margin-bottom:4px}}
.conf-banner{{display:flex;align-items:center;gap:8px;padding:7px 11px;border-radius:var(--r);font-size:12px;margin-bottom:9px;font-weight:500}}
.conf-bull{{background:var(--green-l);border:1px solid var(--green-b);color:#14532d}}
.conf-bear{{background:var(--red-l);border:1px solid var(--red-b);color:#7f1d1d}}
.conf-mix{{background:var(--yellow-l);border:1px solid var(--yellow-b);color:#78350f}}
.alert-pill{{background:var(--yellow-l);border:1px solid var(--yellow-b);border-radius:6px;padding:6px 10px;margin-bottom:6px;font-size:11px;color:var(--yellow)}}
.notif-bar{{background:var(--yellow-l);border:1px solid var(--yellow-b);border-radius:var(--r2);padding:12px 15px;margin-bottom:16px}}
.notif-bar .ntitle{{font-weight:500;color:var(--yellow);margin-bottom:7px}}
.rrw{{display:flex;align-items:center;gap:6px}}
.rrb{{height:4px;border-radius:2px;background:var(--brd2);width:42px;overflow:hidden}}
.rrf{{height:100%;border-radius:2px}}
/* search + filter bar */
.filter-bar{{display:flex;align-items:center;gap:8px;padding:10px 13px;border-bottom:1px solid var(--brd);flex-wrap:wrap;background:var(--surface2)}}
.filter-bar input,.filter-bar select{{padding:5px 9px;border:1px solid var(--brd);border-radius:6px;font-size:12px;background:var(--surface);color:var(--text);outline:none}}
.filter-bar input:focus{{border-color:var(--red)}}
.form-grid{{display:grid;grid-template-columns:repeat(auto-fit,minmax(175px,1fr));gap:11px;padding:16px}}
.fg{{display:flex;flex-direction:column;gap:4px}}
.fg label{{font-size:10px;color:var(--muted);text-transform:uppercase;letter-spacing:.06em}}
.fg input,.fg select{{border:1px solid var(--brd);border-radius:var(--r);padding:7px 10px;font-size:13px;font-family:var(--sans);color:var(--text);background:var(--surface);outline:none;transition:border-color .15s}}
.fg input:focus,.fg select:focus{{border-color:var(--red)}}
.btn{{background:var(--red);color:#fff;border:none;border-radius:var(--r);padding:8px 20px;font-size:13px;font-family:var(--sans);font-weight:500;cursor:pointer;transition:opacity .15s}}
.btn:hover{{opacity:.88}}
.btn-sm{{font-size:11px;padding:3px 9px;border-radius:5px;border:none;cursor:pointer;font-family:var(--sans)}}
.btn-edit{{background:var(--yellow-l);color:var(--yellow);border:1px solid var(--yellow-b)}}
.btn-del{{background:var(--red-l);color:var(--red);border:1px solid var(--red-b)}}
.nota-box{{background:#eff6ff;border:1px solid #bfdbfe;border-radius:var(--r);padding:9px 13px;font-size:11px;color:#1e40af;line-height:1.7;margin:14px 16px 0}}
.mini-table{{width:100%;border-collapse:collapse;font-size:12px}}
.mini-table th,.mini-table td{{padding:7px 12px;border-bottom:1px solid var(--brd);text-align:right}}
.mini-table th{{color:var(--muted);text-transform:uppercase;font-size:10px;font-weight:400;background:var(--surface2)}}
.modal-bg{{display:none;position:fixed;inset:0;background:rgba(0,0,0,.35);z-index:500;align-items:center;justify-content:center}}
.modal-bg.open{{display:flex}}
.modal{{background:var(--surface);border-radius:var(--r2);padding:22px 24px;width:440px;max-width:95vw}}
.modal h3{{font-size:15px;font-weight:600;margin-bottom:16px}}
.modal-row{{display:flex;gap:10px;margin-bottom:10px;flex-wrap:wrap}}
.modal-row .fg{{flex:1;min-width:160px}}
.modal-btns{{display:flex;gap:8px;justify-content:flex-end;margin-top:16px}}
.filter-btn{{background:var(--surface);border:1px solid var(--brd);border-radius:20px;padding:5px 13px;font-size:11px;cursor:pointer;font-family:var(--sans);color:var(--muted);transition:all .15s}}
.filter-btn.active,.filter-btn:hover{{background:var(--red);color:#fff;border-color:var(--red)}}

/* Buscador */
.b-input{{border:1px solid var(--brd);border-radius:var(--r);padding:7px 10px;font-size:13px;font-family:var(--sans);color:var(--text);background:var(--surface);outline:none;width:160px;transition:border-color .15s}}
.b-input:focus{{border-color:var(--red)}}
.ticker-chip{{display:inline-flex;align-items:center;gap:6px;background:var(--surface2);border:1px solid var(--brd);border-radius:20px;padding:4px 10px;font-size:12px}}
.ticker-chip button{{border:none;background:none;color:var(--red);cursor:pointer;font-size:13px;padding:0;line-height:1}}
.b-exit{{background:#fff1f0;color:#cf1322;border:2px solid #ffa39e;font-weight:700;animation:pulse-exit 1.5s infinite}}
@keyframes pulse-exit{{0%,100%{{opacity:1}}50%{{opacity:.6}}}}
.b-blocked{{background:#f0f0f0;color:#888;border:1px solid #ccc}}
.b-etf{{background:#fff7e6;color:#d46b08;border:1px solid #ffd591;font-size:9px;padding:2px 6px;border-radius:10px;margin-left:4px}}
.b-ganga{{background:#f0fdf4;color:#14532d;border:2px solid #86efac;font-weight:700}}
.vix-chip{{font-size:11px;border-radius:20px;padding:3px 10px;font-family:var(--mono);font-weight:600;border:1px solid}}
.vix-verde{{background:#f0fdf4;color:#16a34a;border-color:#bbf7d0}}
.vix-amarillo{{background:#fffbeb;color:#b45309;border-color:#fde68a}}
.vix-rojo{{background:#fef2f2;color:#dc2626;border-color:#fecaca}}
.regimen-bar{{padding:8px 16px;font-size:12px;font-weight:500;display:flex;align-items:center;gap:10px;border-bottom:1px solid var(--brd)}}
.regimen-verde{{background:#f0fdf4;color:#14532d}}
.regimen-amarillo{{background:#fffbeb;color:#78350f}}
.regimen-rojo{{background:#fef2f2;color:#7f1d1d}}
.exit-banner{{background:#fff1f0;border:2px solid #ffa39e;border-radius:var(--r);padding:10px 14px;margin-bottom:10px;font-size:12px;color:#7f1d1d}}
.exit-banner strong{{font-size:13px}}
.score-adj{{font-size:10px;color:var(--muted);margin-top:2px}}
.sr-zone-r{{color:var(--red);font-family:var(--mono)}}
.sr-zone-s{{color:var(--green);font-family:var(--mono)}}
.sr-zone-row{{display:flex;align-items:center;gap:8px;padding:4px 0;border-bottom:1px solid var(--brd);font-size:11px}}

@media(max-width:720px){{thead th:nth-child(n+5){{display:none}}td:nth-child(n+5){{display:none}}}}
</style></head><body>

<div class="topbar"><div class="topbar-inner">
  <div class="logo">fin<em>bit</em> <span style="font-size:11px;color:var(--muted);font-weight:400">pro v3.2</span></div>
  <div class="topbar-right">
    <span class="tc-chip">USD/MXN <strong>${tc:.4f}</strong></span>
    <span class="vix-chip vix-{regimen["color"]}" title="VIX={vix:.1f} — Fear Index. <18 calma, 18-25 precaución, >25 pánico">
      VIX {vix:.1f} {regimen["label"].split()[0]}
    </span>
    <div class="cfg-row">
      <span class="cfg-lbl">Capital</span>
      <input id="cfg_capital" class="cfg-input" type="number" value="{capital}" min="100" style="width:90px">
      <span class="cfg-lbl">Riesgo%</span>
      <input id="cfg_riesgo" class="cfg-input" type="number" value="{riesgo_pct*100:.0f}" min="0.5" max="10" step="0.5" style="width:60px">
      <span class="cfg-lbl">R:R mín</span>
      <input id="cfg_rr" class="cfg-input" type="number" value="{rr_min}" min="1" max="10" step="0.5" style="width:55px">
      <button class="cfg-btn" onclick="saveConfig()">Guardar</button>
    </div>
    <button onclick="actualizarDashboard()" id="btn_update" style="background:var(--green);color:#fff;border:none;border-radius:var(--r);padding:5px 14px;font-size:12px;font-family:var(--sans);cursor:pointer;font-weight:500;white-space:nowrap">↺ Actualizar</button>
    <button onclick="backupDB()" title="Descarga tu finbit.db — guárdala antes de cada deploy en Render" style="background:var(--surface);color:var(--text);border:1px solid var(--brd);border-radius:var(--r);padding:5px 11px;font-size:12px;font-family:var(--sans);cursor:pointer;white-space:nowrap">💾 Backup DB</button>
    <label title="Restaura una DB previamente descargada" style="background:var(--surface);color:var(--text);border:1px solid var(--brd);border-radius:var(--r);padding:5px 11px;font-size:12px;font-family:var(--sans);cursor:pointer;white-space:nowrap">
      📂 Restaurar DB<input type="file" accept=".db" onchange="restaurarDB(this)" style="display:none">
    </label>
    <span style="font-size:11px;color:var(--muted)">{ts}</span>
  </div>
</div></div>

<div class="nav"><div class="nav-inner">
  <button class="nb" onclick="showTab('portafolio',this)">Mi portafolio</button>
  <button class="nb" onclick="showTab('registrar',this)">Registrar operación</button>
  <button class="nb" onclick="showTab('historial',this)">Historial</button>
  <button class="nb active" onclick="showTab('scanner',this)">Scanner</button>
  <button class="nb" onclick="showTab('radar',this)">🔭 Radar automático</button>
  <button class="nb" onclick="showTab('buscador',this)">🔍 Buscador</button>
</div></div>

<div class="regimen-bar regimen-{regimen["color"]}">
  <span>{regimen["label"]}</span>
  <span style="opacity:.7;font-weight:400">{regimen["mensaje"]}</span>
  <span style="margin-left:auto;font-size:11px;opacity:.6">SPY {'✅ sobre' if spy.get('sobre_ema200') else '❌ bajo'} EMA200 · Score mín entrada: {'7/9 acciones · 8/9 ETFs 3x' if vix<20 else '8/9 acciones · 9/9 ETFs 3x'}</span>
</div>

<div class="modal-bg" id="editModal">
  <div class="modal">
    <h3>Editar operación</h3>
    <input type="hidden" id="edit_id">
    <div class="modal-row">
      <div class="fg"><label>Ticker</label><input type="text" id="edit_ticker"></div>
      <div class="fg"><label>Tipo</label><select id="edit_tipo"><option value="COMPRA">Compra</option><option value="VENTA">Venta</option></select></div>
    </div>
    <div class="modal-row">
      <div class="fg"><label>Títulos</label><input type="number" id="edit_titulos" step="0.01"></div>
      <div class="fg"><label>Precio MXN</label><input type="number" id="edit_precio" step="0.01"></div>
      <div class="fg"><label>Fecha</label><input type="date" id="edit_fecha"></div>
    </div>
    <div class="fg" style="margin-bottom:0"><label>Notas</label><input type="text" id="edit_notas"></div>
    <div class="modal-btns">
      <button class="btn" style="background:var(--muted);font-size:12px;padding:6px 16px" onclick="closeModal()">Cancelar</button>
      <button class="btn" style="font-size:12px;padding:6px 16px" onclick="saveEdit()">Guardar</button>
    </div>
  </div>
</div>

<div class="wrap">

<!-- ══ PORTAFOLIO ══ -->
<div id="tab-portafolio" class="tab">
  <div style="padding:20px 0 14px">
    <h2 style="font-size:20px;font-weight:600;letter-spacing:-.4px">Mi portafolio</h2>
    <p class="hint">Capital ${capital:,.0f} MXN · Riesgo {riesgo_pct*100:.0f}% · R:R mín 1:{rr_min:.0f} · Las operaciones registradas se reflejan al instante</p>
  </div>
  <div class="nota-box" style="margin:0 0 14px">
    💡 <strong>Precios:</strong> Costo prom. ya está en MXN (SIC/GBM). Precio actual viene en USD y se convierte con TC {tc:.4f} solo para P&amp;L.<br>
    💡 <strong>Orden GBM:</strong> EMA9 = entrada ideal. Stop = límite de pérdida. Objetivo = meta de venta.
  </div>
  {alert_banner}
  <div class="kpis">
    <div class="kpi"><div class="lbl">Valor total</div><div class="val">{fmt(total_valor)}</div></div>
    <div class="kpi"><div class="lbl">Costo total</div><div class="val">{fmt(total_costo)}</div></div>
    <div class="kpi"><div class="lbl">P&L total</div><div class="val {pl_cls}">{fmt(total_pl)}</div></div>
    <div class="kpi"><div class="lbl">Rendimiento</div><div class="val {plpct_cls}">{total_pl_pct:+.1f}%</div></div>
    <div class="kpi"><div class="lbl">Posiciones</div><div class="val" id="kpi-pos">{len(port_data)}</div></div>
    <div class="kpi"><div class="lbl">Alertas</div><div class="val {al_cls}">{n_alertas}</div></div>
  </div>
  <div class="tw">
    <div class="tw-head">
      <span>Posiciones actuales</span>
      <div class="filter-bar" style="padding:0;border:none;background:transparent;gap:6px">
        <input id="port_search" placeholder="🔍 Buscar ticker..." style="width:160px" oninput="filtrarPortafolio()">
        <select id="port_origen" onchange="filtrarPortafolio()" style="font-size:11px">
          <option value="">Todos los orígenes</option>
          <option value="USA">USA (SIC)</option>
          <option value="MX">México (BMV)</option>
        </select>
        <select id="port_senal" onchange="filtrarPortafolio()" style="font-size:11px">
          <option value="">Todas las señales</option>
          <option value="Comprar">Comprar</option>
          <option value="Mantener">Mantener</option>
          <option value="Vender">Vender</option>
        </select>
      </div>
    </div>
    <div style="overflow-x:auto"><table>
      <thead><tr><th>Emisora</th><th>Títulos</th><th>Cto. prom MXN</th><th>Precio actual</th>
        <th>Valor MXN</th><th>P&L MXN</th><th>% Var</th><th>Señal</th>
        <th class="sr-th" title="Soporte / Resistencia automáticos — clic para mapa completo">📊 S/R</th>
        <th style="color:var(--green)">Orden GBM 🎯</th></tr></thead>
      <tbody id="port_tbody">{port_rows}</tbody>
    </table></div>
  </div>
</div>

<!-- ══ REGISTRAR ══ -->
<div id="tab-registrar" class="tab">
  <div style="padding:20px 0 14px"><h2 style="font-size:20px;font-weight:600;letter-spacing:-.4px">Registrar operación</h2></div>
  <div class="tw">
    <div class="tw-head"><span>Nueva operación</span></div>
    <div class="nota-box">💡 Precio en MXN tal como aparece en GBM/SIC. Al guardar actualiza automáticamente tu portafolio con costo promedio ponderado.</div>
    <div class="form-grid">
      <div class="fg"><label>Ticker</label><input type="text" id="f_ticker" placeholder="NVDA, SOXL..."></div>
      <div class="fg"><label>Tipo</label><select id="f_tipo"><option value="COMPRA">Compra</option><option value="VENTA">Venta</option></select></div>
      <div class="fg"><label>Fecha</label><input type="date" id="f_fecha" value="{date.today().isoformat()}"></div>
      <div class="fg"><label>Títulos (fracciones OK)</label><input type="number" id="f_titulos" step="0.01" min="0.01" placeholder="10 o 0.5"></div>
      <div class="fg"><label>Precio por título MXN</label><input type="number" id="f_precio" step="0.01" placeholder="1500.00"></div>
      <div class="fg"><label>Origen</label><select id="f_origen"><option value="USA">USA (SIC)</option><option value="MX">México (BMV)</option></select></div>
      <div class="fg"><label>Mercado</label><select id="f_mercado"><option value="SIC">SIC</option><option value="BMV">BMV</option></select></div>
      <div class="fg"><label>Notas</label><input type="text" id="f_notas" placeholder="Señal MACD+EMA..."></div>
    </div>
    <div style="padding:0 16px 16px">
      <div id="f_preview" style="background:var(--surface2);border:1px solid var(--brd);border-radius:var(--r);padding:9px 12px;margin-bottom:11px;display:none;font-size:12px">
        <span class="hint">Resumen: </span><span id="f_preview_txt"></span>
      </div>
      <button class="btn" onclick="registrarOp()">Guardar operación</button>
      <span id="f_msg" style="margin-left:12px;font-size:12px"></span>
    </div>
  </div>
  <div class="tw">
    <div class="tw-head"><span>Editar posición directa</span><span class="hint">Sin pasar por historial</span></div>
    <div class="form-grid">
      <div class="fg"><label>Ticker</label><input type="text" id="p_ticker"></div>
      <div class="fg"><label>Títulos totales</label><input type="number" id="p_titulos" step="0.01"></div>
      <div class="fg"><label>Costo promedio MXN</label><input type="number" id="p_cto" step="0.01"></div>
      <div class="fg"><label>Origen</label><select id="p_origen"><option value="USA">USA</option><option value="MX">México</option></select></div>
      <div class="fg"><label>Mercado</label><select id="p_mercado"><option value="SIC">SIC</option><option value="BMV">BMV</option></select></div>
    </div>
    <div style="padding:0 16px 16px">
      <button class="btn" onclick="guardarPosicion()">Guardar posición</button>
      <span id="p_msg" style="margin-left:12px;font-size:12px"></span>
    </div>
  </div>
</div>

<!-- ══ HISTORIAL ══ -->
<div id="tab-historial" class="tab">
  <div style="padding:20px 0 14px"><h2 style="font-size:20px;font-weight:600;letter-spacing:-.4px">Historial de operaciones</h2></div>
  <div class="kpis">
    <div class="kpi"><div class="lbl">Total invertido</div><div class="val">{fmt(res['inv'])}</div></div>
    <div class="kpi"><div class="lbl">Total vendido</div><div class="val">{fmt(res['vta'])}</div></div>
    <div class="kpi"><div class="lbl">P&L realizado</div><div class="val {pl_hist_cls}">{fmt(res['pl'])}</div></div>
    <div class="kpi"><div class="lbl">Operaciones</div><div class="val">{res['n']}</div></div>
    <div class="kpi"><div class="lbl">Ops cerradas</div><div class="val">{res.get('n_ops_cerradas',0)}</div></div>
  </div>
  <div class="tw" style="margin-bottom:18px">
    <div class="tw-head"><span>📊 Estadísticas de trading</span><span class="hint">Basado en operaciones cerradas</span></div>
    <div style="display:grid;grid-template-columns:repeat(auto-fit,minmax(280px,1fr));gap:0;border-top:1px solid var(--brd)">
      <div style="padding:16px 18px;border-right:1px solid var(--brd)">
        <div class="hint" style="margin-bottom:10px;text-transform:uppercase;font-size:10px;letter-spacing:.06em">Tasa de aciertos</div>
        <div style="display:flex;align-items:flex-end;gap:8px;margin-bottom:10px">
          <span style="font-size:36px;font-weight:600;font-family:var(--mono);color:{tasa_color}">{tasa_val}</span>
          <span class="hint" style="padding-bottom:6px">{res.get('ventas_ganadoras',0)}G / {res.get('ventas_perdedoras',0)}P de {res.get('n_ops_cerradas',0)} cerradas</span>
        </div>
        <div style="background:var(--brd);border-radius:4px;height:6px;overflow:hidden;margin-bottom:6px">
          <div style="background:{tasa_color};height:100%;width:{tasa_bar};border-radius:4px"></div>
        </div>
        <p class="hint" style="font-size:10px">{tasa_msg}</p>
      </div>
      <div style="padding:16px 18px;border-right:1px solid var(--brd)">
        <div class="hint" style="margin-bottom:10px;text-transform:uppercase;font-size:10px;letter-spacing:.06em">Ganancia / Pérdida promedio</div>
        <div class="pl-row"><span>Ganancia promedio por op</span><span class="num pos">{fmt(res.get('ganancia_promedio',0))}</span></div>
        <div class="pl-row"><span>Pérdida promedio por op</span><span class="num neg">{fmt(res.get('perdida_promedio',0))}</span></div>
        <div class="pl-row"><span>Mejor operación</span><span class="num pos">{fmt(res.get('mejor_op',0))}</span></div>
        <div class="pl-row"><span>Peor operación</span><span class="num neg">{fmt(res.get('peor_op',0))}</span></div>
        <div class="pl-row" style="border-top:2px solid var(--brd);margin-top:4px;padding-top:8px">
          <span style="font-weight:500">Expectativa por operación</span>
          <span class="num {exp_cls}" style="font-weight:600">{fmt(res.get('expectativa',0))}</span>
        </div>
        <p class="hint" style="font-size:10px;margin-top:6px">{exp_msg}</p>
      </div>
      <div style="padding:16px 18px">
        <div class="hint" style="margin-bottom:10px;text-transform:uppercase;font-size:10px;letter-spacing:.06em">P&L por ticker (ops cerradas)</div>
        {por_ticker_html}
      </div>
    </div>
    {por_mes_html}
  </div>
  <div class="tw">
    <div class="tw-head">
      <span>Operaciones</span>
      <div style="display:flex;gap:7px;align-items:center">
        <input type="text" id="fil_ticker" placeholder="Filtrar..." style="padding:4px 9px;border:1px solid var(--brd);border-radius:6px;font-size:12px;width:110px;background:var(--surface)" oninput="filtrar()">
        <select id="fil_tipo" style="padding:4px 8px;border:1px solid var(--brd);border-radius:6px;font-size:12px;background:var(--surface)" onchange="filtrar()">
          <option value="">Todos</option><option value="COMPRA">Compra</option><option value="VENTA">Venta</option>
        </select>
      </div>
    </div>
    <div style="overflow-x:auto"><table>
      <thead><tr><th>Fecha</th><th>Ticker</th><th>Tipo</th><th>Títulos</th>
        <th>Precio MXN</th><th>Total MXN</th><th>T/C</th><th>Origen</th><th>Notas</th><th>Acciones</th></tr></thead>
      <tbody id="hist_body">{hist_rows}</tbody>
    </table></div>
  </div>
  <div class="tw"><div class="tw-head"><span>Resumen mensual</span></div>
    <div style="padding:13px;overflow-x:auto">{res_mes_html}</div>
  </div>
</div>

<!-- ══ SCANNER ══ -->
<div id="tab-scanner" class="tab active">
  <div style="padding:20px 0 14px">
    <h2 style="font-size:20px;font-weight:600;letter-spacing:-.4px">Scanner de mercado</h2>
    <p class="hint">Muestra <strong>todas</strong> las acciones configuradas · 1D base · 1H+1W si score≥5 · TC ${tc:.4f} · {ts}</p>
  </div>

  <!-- Guía de acción -->
  <div style="background:var(--surface);border:1px solid var(--brd);border-radius:var(--r2);padding:14px 16px;margin-bottom:14px">
    <div style="font-size:12px;font-weight:600;color:var(--text);margin-bottom:10px">¿Qué hago con cada estado?</div>
    <div style="display:grid;grid-template-columns:repeat(auto-fit,minmax(200px,1fr));gap:8px;font-size:11px">

      <div style="background:#fff1f0;border:1px solid #fecaca;border-radius:var(--r);padding:9px 11px">
        <div style="font-weight:700;color:#dc2626;margin-bottom:4px">🚫 BLOQUEADO / LATERAL / RUPTURA</div>
        <div style="color:#7f1d1d;line-height:1.6">
          <strong>No compres.</strong> El mercado o el activo tiene señales en contra.<br>
          Si ya tienes posición: revisa tu stop. Si no: espera el siguiente análisis.
        </div>
      </div>

      <div style="background:#fff7e6;border:1px solid #fde68a;border-radius:var(--r);padding:9px 11px">
        <div style="font-weight:700;color:#b45309;margin-bottom:4px">👁 WATCH — Solo vigilar</div>
        <div style="color:#78350f;line-height:1.6">
          <strong>No entres todavía.</strong> Hay señales positivas pero faltan confirmaciones.<br>
          Ponlo en tu lista. Actualiza mañana pre-apertura (8-9 AM). Si mejora a BUY, actúas.
        </div>
      </div>

      <div style="background:#f0fdf4;border:1px solid #bbf7d0;border-radius:var(--r);padding:9px 11px">
        <div style="font-weight:700;color:#16a34a;margin-bottom:4px">↑ BUY — Entrada válida</div>
        <div style="color:#14532d;line-height:1.6">
          <strong>Puedes entrar.</strong> Usa el precio EMA9 como entrada ideal.<br>
          Pon el stop dinámico que indica la fila. No entres si el precio ya subió mucho del EMA9.
        </div>
      </div>

      <div style="background:#f5f3ff;border:1px solid #ddd6fe;border-radius:var(--r);padding:9px 11px">
        <div style="font-weight:700;color:#7c3aed;margin-bottom:4px">🚀 ROCKET — Alta convicción</div>
        <div style="color:#4c1d95;line-height:1.6">
          <strong>Señal fuerte.</strong> Todos los filtros alineados incluyendo OBV y sector.<br>
          Entra con tamaño completo sugerido. Stop obligatorio desde el primer momento.
        </div>
      </div>

      <div style="background:#fff1f0;border:2px solid #ffa39e;border-radius:var(--r);padding:9px 11px">
        <div style="font-weight:700;color:#cf1322;margin-bottom:4px">⚠️ EXIT — Salir ahora</div>
        <div style="color:#7f1d1d;line-height:1.6">
          <strong>Si tienes posición: sal.</strong> El sistema detectó deterioro.<br>
          No esperes "que se recupere". Ejecuta el stop. El capital protegido es capital disponible.
        </div>
      </div>

      <div style="background:#fef2f2;border:1px solid #fecaca;border-radius:var(--r);padding:9px 11px">
        <div style="font-weight:700;color:#dc2626;margin-bottom:4px">↓ BAJISTA — Evitar</div>
        <div style="color:#7f1d1d;line-height:1.6">
          <strong>No compres.</strong> Indicadores deteriorados.<br>
          Si tienes posición: evalúa cerrar. El rebote puede ser trampa.
        </div>
      </div>

    </div>
    <div style="margin-top:10px;padding-top:10px;border-top:1px solid var(--brd);font-size:10px;color:var(--muted);line-height:1.7">
      💡 <strong>Cuándo actualizar:</strong> Pre-apertura 8–9 AM · Cierre 2:30–3 PM · Domingo noche (vista semanal) — 
      <strong>No actualices entre 10 AM y 1 PM</strong> (movimientos intradía generan señales falsas) · 
      El plan DCA aparece dentro de cada fila al hacer clic.
    </div>
  </div>

  <!-- Agregar ticker — persiste en DB -->
  <div class="tw" style="margin-bottom:14px">
    <div class="tw-head">
      <span>➕ Agregar ticker al scanner</span>
      <span class="hint">Se guarda en la base de datos y persiste en cada actualización</span>
    </div>
    <div style="padding:12px 16px;display:flex;gap:8px;flex-wrap:wrap;align-items:center">
      <input type="text" id="add_ticker_input" placeholder="Ej: AMZN, COIN, MARA..."
        style="border:1px solid var(--brd);border-radius:var(--r);padding:7px 10px;font-size:13px;width:200px;background:var(--surface);color:var(--text);outline:none"
        onkeydown="if(event.key===\'Enter\')agregarTickerScanner()">
      <select id="add_exchange_input" style="border:1px solid var(--brd);border-radius:var(--r);padding:7px 10px;font-size:13px;background:var(--surface);color:var(--text)">
        <option value="">Exchange auto</option>
        <option value="NASDAQ">NASDAQ</option>
        <option value="NYSE">NYSE</option>
        <option value="NYSEARCA">NYSE Arca (ETFs)</option>
        <option value="BMV">BMV México</option>
      </select>
      <button class="btn" onclick="agregarTickerScanner()">Agregar</button>
      <span id="add_ticker_msg" style="font-size:12px"></span>
    </div>
    <div style="padding:0 16px 12px" id="custom_tickers_list">
      <span class="hint" style="font-size:11px">Cargando tickers...</span>
    </div>
  </div>

  <div class="tw">
    <div class="tw-head">
      <span>Todas las acciones ({len(scan_data)})</span>
      <div style="display:flex;gap:6px;flex-wrap:wrap;align-items:center">
        <input id="scan_search" placeholder="🔍 Buscar..." style="padding:4px 9px;border:1px solid var(--brd);border-radius:6px;font-size:12px;width:120px;background:var(--surface)" oninput="filtrarScanner()">
        <select id="scan_estado" onchange="filtrarScanner()" style="padding:4px 8px;border:1px solid var(--brd);border-radius:6px;font-size:12px;background:var(--surface)">
          <option value="">Todos los estados</option>
          <option value="Explosión">🚀 Explosión</option>
          <option value="Compra">↑ Compra</option>
          <option value="EXIT">⚠️ EXIT</option>
          <option value="Ganga">🏷️ Ganga</option>
          <option value="Vigilar">👁 Vigilar</option>
          <option value="Esperar">Esperar</option>
          <option value="Bajista">↓ Bajista</option>
          <option value="Bloqueado">🔒 Bloqueado</option>
        </select>
      </div>
    </div>
    <div style="overflow-x:auto"><table id="scan_table">
      <thead><tr><th>Ticker</th><th>Estado</th><th>Precio MXN</th>
        <th style="color:var(--green)">Entrada EMA9</th><th>R:R</th><th>RSI</th>
        <th>MACD</th><th>EMA200</th><th style="color:var(--green)">Orden GBM 🎯</th>
        <th class="sr-th" title="Soporte y Resistencia automáticos">📊 S/R</th><th>Score</th></tr></thead>
      <tbody id="scan_tbody">{scan_rows or '<tr><td colspan="10" style="text-align:center;color:var(--muted);padding:24px;font-size:12px">Sin datos — verifica tu API key en <a href="/api/debug" target="_blank" style="color:var(--blue)">/api/debug</a></td></tr>'}</tbody>
    </table></div>
  </div>
</div>

<!-- ══ RADAR ══ -->
<div id="tab-radar" class="tab">
  <div style="padding:20px 0 14px">
    <h2 style="font-size:20px;font-weight:600;letter-spacing:-.4px">Radar automático</h2>
    <p class="hint">{n_radar} de {total_univ} acciones analizadas · solo 1D · ideal pre-apertura · TC ${tc:.4f} · {ts}</p>
  </div>
  <div style="background:var(--surface);border:1px solid var(--brd);border-radius:var(--r2);padding:14px 16px;margin-bottom:16px;display:flex;align-items:center;gap:16px;flex-wrap:wrap">
    <div style="flex:1">
      <div style="font-weight:500;margin-bottom:3px">Análisis pre-apertura — {total_univ} acciones</div>
      <p class="hint">Analiza todo el universo en 1D. Ideal entre 8-9 AM antes de apertura. Muestra todas las acciones con su estado.</p>
    </div>
    <div style="display:flex;gap:8px;flex-wrap:wrap">
      <div class="kpi" style="padding:9px 13px"><div class="lbl">🚀 Explosión</div><div class="val" style="color:var(--purple);font-size:18px">{n_rocket}</div></div>
      <div class="kpi" style="padding:9px 13px"><div class="lbl">↑ Compra</div><div class="val pos" style="font-size:18px">{n_buy}</div></div>
      <div class="kpi" style="padding:9px 13px"><div class="lbl">👁 Vigilar</div><div class="val warn" style="font-size:18px">{n_watch}</div></div>
      <div class="kpi" style="padding:9px 13px"><div class="lbl">↓ Bajistas</div><div class="val neg" style="font-size:18px">{n_short}</div></div>
      <div class="kpi" style="padding:9px 13px"><div class="lbl">— Esperar</div><div class="val" style="font-size:18px;color:var(--muted)">{n_skip}</div></div>
    </div>
  </div>
  <div style="display:flex;gap:8px;margin-bottom:8px;flex-wrap:wrap;align-items:center">
    <span class="hint">Filtrar:</span>
    <button class="filter-btn active" onclick="filtrarRadar('ALL',this)">Todos</button>
    <button class="filter-btn" onclick="filtrarRadar('alza',this)">↑ Alza</button>
    <button class="filter-btn" onclick="filtrarRadar('ganga',this)">🏷️ Gangas</button>
    <button class="filter-btn" onclick="filtrarRadar('baja',this)">↓ Bajistas</button>
    <button class="filter-btn" onclick="filtrarRadar('10pct',this)">🔥 +10% potencial</button>
    <button class="filter-btn" onclick="filtrarRadar('rocket',this)">🚀 Explosiones</button>
    <button class="filter-btn" onclick="filtrarRadar('skip',this)">— Esperar</button>
    <input type="text" id="radar_search" placeholder="Buscar ticker..."
      style="padding:5px 10px;border:1px solid var(--brd);border-radius:6px;font-size:12px;background:var(--surface);margin-left:auto;width:130px"
      oninput="buscarRadar()">
  </div>

  <!-- Guía de acción radar -->
  <div style="background:var(--surface);border:1px solid var(--brd);border-radius:var(--r2);padding:12px 16px;margin-bottom:14px;font-size:11px">
    <div style="font-weight:600;margin-bottom:8px;color:var(--text)">¿Qué hago con cada resultado?</div>
    <div style="display:grid;grid-template-columns:repeat(auto-fit,minmax(170px,1fr));gap:6px">
      <div style="display:flex;gap:6px;align-items:flex-start">
        <span style="background:#f0fdf4;color:#14532d;border:2px solid #86efac;border-radius:4px;padding:2px 7px;font-weight:700;white-space:nowrap">🏷️ GANGA</span>
        <span style="color:var(--muted);line-height:1.5">Soporte fuerte + acumulación institucional. Entra escalonado con DCA. Stop bajo el soporte.</span>
      </div>
      <div style="display:flex;gap:6px;align-items:flex-start">
        <span style="background:#f5f3ff;color:#7c3aed;border-radius:4px;padding:2px 7px;font-weight:700;white-space:nowrap">🚀 ROCKET</span>
        <span style="color:var(--muted);line-height:1.5">Entra con tamaño completo. Stop desde el primer día.</span>
      </div>
      <div style="display:flex;gap:6px;align-items:flex-start">
        <span style="background:#f0fdf4;color:#16a34a;border-radius:4px;padding:2px 7px;font-weight:700;white-space:nowrap">↑ BUY</span>
        <span style="color:var(--muted);line-height:1.5">Entrada válida en EMA9. Usa el stop indicado.</span>
      </div>
      <div style="display:flex;gap:6px;align-items:flex-start">
        <span style="background:#fffbeb;color:#b45309;border-radius:4px;padding:2px 7px;font-weight:700;white-space:nowrap">👁 WATCH</span>
        <span style="color:var(--muted);line-height:1.5">No entres. Ponlo en lista y revisa mañana.</span>
      </div>
      <div style="display:flex;gap:6px;align-items:flex-start">
        <span style="background:#f0f0f0;color:#888;border-radius:4px;padding:2px 7px;font-weight:700;white-space:nowrap">— ESPERAR</span>
        <span style="color:var(--muted);line-height:1.5">Score insuficiente. Ignorar por ahora.</span>
      </div>
      <div style="display:flex;gap:6px;align-items:flex-start">
        <span style="background:#fef2f2;color:#dc2626;border-radius:4px;padding:2px 7px;font-weight:700;white-space:nowrap">↓ BAJISTA</span>
        <span style="color:var(--muted);line-height:1.5">No comprar. Si tienes posición, evalúa salir.</span>
      </div>
      <div style="display:flex;gap:6px;align-items:flex-start">
        <span style="background:#fff7e6;color:#d46b08;border-radius:4px;padding:2px 7px;font-weight:700;white-space:nowrap">〰️ LATERAL</span>
        <span style="color:var(--muted);line-height:1.5">Mercado sin tendencia. Las señales no son fiables.</span>
      </div>
    </div>
    <div style="margin-top:8px;padding-top:8px;border-top:1px solid var(--brd);color:var(--muted)">
      💡 Clic en cualquier fila para ver el análisis completo, zonas S/R y plan DCA de ese ticker.
    </div>
  </div>
  <div class="tw">
    <div class="tw-head"><span id="radar_count">Mostrando {n_radar} acciones</span><span class="hint">↓ Clic en fila para análisis completo</span></div>
    <div style="overflow-x:auto"><table id="radar_table">
      <thead><tr><th>Ticker</th><th>Estado</th><th>Precio MXN</th>
        <th style="color:var(--green)">Entrada EMA9</th><th style="color:var(--green)">Potencial</th>
        <th>R:R</th><th>RSI</th><th>MACD</th><th>EMA200</th><th>Score</th>
        <th class="sr-th" title="Soporte y Resistencia automáticos">📊 S/R</th>
        <th style="color:var(--green)">Orden GBM 🎯</th></tr></thead>
      <tbody id="radar_body">{radar_rows}</tbody>
    </table></div>
  </div>
  <p class="hint" style="margin-top:8px">💡 Potencial = distancia al objetivo dinámico (máx 20 velas). Solo fines educativos.</p>
</div>


<div id="tab-buscador" class="tab">
  <div style="padding:20px 0 14px">
    <h2 style="font-size:20px;font-weight:600;letter-spacing:-.4px">Buscador de acciones</h2>
    <p class="hint">Hasta 5 tickers a la vez. Se guardan y aparecen en el scanner en la proxima corrida.</p>
  </div>
  <div class="tw">
    <div class="tw-head"><span>Agregar tickers al scanner</span><span class="hint">Simbolo exacto: NVDA, AAPL, SOXL, TLEVISACPO...</span></div>
    <div class="nota-box">Los tickers guardados se analizan junto con los defaults. Descarga finbit_tickers.json y ponlo junto al script.</div>
    <div style="padding:16px">
      <div style="display:flex;gap:8px;flex-wrap:wrap;margin-bottom:14px">
        <input type="text" id="b_t1" placeholder="Ticker 1 (NVDA)" class="b-input">
        <input type="text" id="b_t2" placeholder="Ticker 2" class="b-input">
        <input type="text" id="b_t3" placeholder="Ticker 3" class="b-input">
        <input type="text" id="b_t4" placeholder="Ticker 4" class="b-input">
        <input type="text" id="b_t5" placeholder="Ticker 5" class="b-input">
      </div>
      <div style="display:flex;gap:8px;flex-wrap:wrap;align-items:center">
        <select id="b_exchange" style="padding:7px 10px;border:1px solid var(--brd);border-radius:var(--r);font-size:13px;background:var(--surface)">
          <option value="">Exchange auto</option>
          <option value="NASDAQ">NASDAQ</option>
          <option value="NYSE">NYSE</option>
          <option value="NYSEARCA">NYSE Arca (ETFs)</option>
          <option value="BMV">BMV Mexico</option>
        </select>
        <button class="btn" onclick="guardarTickers()">Guardar al scanner</button>
        <span id="b_msg" style="font-size:12px"></span>
      </div>
    </div>
  </div>
  <div class="tw">
    <div class="tw-head"><span>Tickers en el scanner personalizado</span></div>
    <div style="padding:14px">
      <div id="b_saved_list" style="display:flex;flex-wrap:wrap;gap:8px;min-height:32px">
        <span class="hint">Sin tickers guardados</span>
      </div>
      <p class="hint" style="margin-top:10px;font-size:11px">Despues de guardar, corre python finbit.py para analizarlos en el scanner.</p>
    </div>
  </div>
</div>

<footer>Solo fines educativos · No es asesoría financiera · Usa siempre stop loss<br>
TC: Banxico/Frankfurter · Precios: API financiera · DB: SQLite · finbit pro v3.2</footer>
</div>

<script>
const TC = {tc:.4f};
const PORT_BASE = {port_json};

// ── Tabs ─────────────────────────────────────────────────
function showTab(name,btn){{
  document.querySelectorAll('.tab').forEach(t=>t.classList.remove('active'));
  document.querySelectorAll('.nb').forEach(b=>b.classList.remove('active'));
  document.getElementById('tab-'+name).classList.add('active');
  btn.classList.add('active');
}}
function toggle(id){{
  const el=document.getElementById(id); if(!el)return;
  const open=el.classList.contains('open');
  document.querySelectorAll('.detail.open').forEach(d=>d.classList.remove('open'));
  if(!open){{el.classList.add('open');el.scrollIntoView({{behavior:'smooth',block:'nearest'}});}}
}}

// ── Portafolio live desde localStorage ───────────────────
function recalcPortafolio(ops){{
  const map={{}};
  // Partir de la base del script
  PORT_BASE.forEach(p=>{{
    map[p.ticker]={{titulos:p.titulos,costoTotal:p.cto_prom_mxn*p.titulos,
      origen:p.origen,mercado:p.mercado,precio_actual_mxn:p.precio_actual_mxn,
      pl_mxn:p.pl_mxn,pl_pct:p.pl_pct}};
  }});
  // Aplicar operaciones de localStorage encima
  ops.forEach(op=>{{
    const t=op.ticker;
    if(!map[t]) map[t]={{titulos:0,costoTotal:0,origen:op.origen||'USA',mercado:op.mercado||'SIC',precio_actual_mxn:null,pl_mxn:0,pl_pct:0}};
    if(op.tipo==='COMPRA'){{
      map[t].costoTotal+=op.titulos*op.precio_mxn;
      map[t].titulos+=op.titulos;
    }} else if(op.tipo==='VENTA'){{
      if(map[t].titulos>0){{
        const cto=map[t].costoTotal/map[t].titulos;
        map[t].costoTotal-=op.titulos*cto;
        map[t].titulos-=op.titulos;
      }}
    }}
  }});
  return map;
}}

function actualizarTablaPortafolio(){{
  const ops=JSON.parse(localStorage.getItem('finbit_ops')||'[]');
  const map=recalcPortafolio(ops);
  const tbody=document.getElementById('port_tbody');
  if(!tbody) return;

  // Actualizar filas existentes
  tbody.querySelectorAll('tr.datarow').forEach(row=>{{
    const tkEl=row.querySelector('td strong');
    if(!tkEl) return;
    const tk=tkEl.textContent.trim();
    if(!map[tk]) return;
    const d=map[tk];
    if(d.titulos<=0) return;
    const cto=d.titulos>0? d.costoTotal/d.titulos : 0;
    const cells=row.querySelectorAll('td');
    if(cells.length<7) return;
    cells[1].textContent=parseFloat(d.titulos.toFixed(6));
    cells[2].innerHTML='<span class="num">$'+cto.toLocaleString('es-MX',{{minimumFractionDigits:2}})+'</span>';
    const costo=cto*d.titulos;
    cells[4].innerHTML='<span class="num">$'+( (d.precio_actual_mxn||cto)*d.titulos ).toLocaleString('es-MX',{{minimumFractionDigits:2}})+'</span>';
    if(d.precio_actual_mxn){{
      const pl=(d.precio_actual_mxn-cto)*d.titulos;
      const plPct=(pl/costo*100);
      const col=pl>=0?'var(--green)':'var(--red)';
      cells[5].innerHTML='<span class="num" style="color:'+col+'">'+( pl<0?'-':'')+'$'+Math.abs(pl).toLocaleString('es-MX',{{minimumFractionDigits:2}})+'</span>';
      cells[6].innerHTML='<span class="num" style="color:'+col+'">'+(plPct>=0?'+':'')+plPct.toFixed(1)+'%</span>';
    }}
  }});

  // Agregar filas nuevas (posiciones que no estaban en el script)
  Object.entries(map).forEach(([tk,d])=>{{
    if(d.titulos<=0) return;
    const exists=[...tbody.querySelectorAll('tr.datarow')].some(r=>r.querySelector('td strong')?.textContent.trim()===tk);
    if(exists) return;
    const cto=d.costoTotal/d.titulos;
    const rid='pr_'+tk.replace(/[ .]/g,'_');
    const newRow=document.createElement('tr');
    newRow.className='datarow';
    newRow.setAttribute('onclick',`toggle('${{rid}}')`);
    newRow.innerHTML=`<td><strong>${{tk}}</strong><br><span class="hint">${{d.origen}} · ${{d.mercado}}</span></td>`
      +`<td class="num">${{parseFloat(d.titulos.toFixed(6))}}</td>`
      +`<td class="num">$${{cto.toLocaleString('es-MX',{{minimumFractionDigits:2}})}}</td>`
      +`<td class="num">—</td>`
      +`<td class="num">$${{(cto*d.titulos).toLocaleString('es-MX',{{minimumFractionDigits:2}})}}</td>`
      +`<td class="num">—</td><td class="num">—</td>`
      +`<td><span class="badge b-none">Sin análisis</span></td><td>—</td>`;
    tbody.appendChild(newRow);
    const detRow=document.createElement('tr');
    detRow.className='detail'; detRow.id=rid;
    detRow.innerHTML='<td colspan="9" style="padding:0"><div class="detail-panel"><p class="hint">Análisis disponible al correr el script de nuevo.</p></div></td>';
    tbody.appendChild(detRow);
  }});
}}

// ── Filtro tabla portafolio ───────────────────────────────
function filtrarPortafolio(){{
  const q=(document.getElementById('port_search').value||'').toUpperCase();
  const origen=document.getElementById('port_origen').value;
  const senal=document.getElementById('port_senal').value;
  let visible=0;
  document.querySelectorAll('#port_tbody tr.datarow').forEach(tr=>{{
    const tk=tr.querySelector('td strong')?.textContent||'';
    const origCell=tr.querySelectorAll('td')[0]?.textContent||'';
    const badgeTxt=tr.querySelector('.badge')?.textContent||'';
    const mQ=!q||tk.includes(q);
    const mO=!origen||origCell.includes(origen);
    const mS=!senal||badgeTxt.includes(senal);
    const show=mQ&&mO&&mS;
    tr.style.display=show?'':'none';
    const next=tr.nextElementSibling;
    if(next&&next.classList.contains('detail'))next.style.display=show?'':'none';
    if(show) visible++;
  }});
}}

// ── Filtro scanner ────────────────────────────────────────
function filtrarScanner(){{
  const q=(document.getElementById('scan_search').value||'').toUpperCase();
  const estado=document.getElementById('scan_estado').value;
  document.querySelectorAll('#scan_tbody tr.datarow').forEach(tr=>{{
    const tk=tr.querySelector('td strong')?.textContent||'';
    const badge=tr.querySelector('.badge')?.textContent||'';
    const mQ=!q||tk.includes(q);
    const mE=!estado||badge.includes(estado);
    const show=mQ&&mE;
    tr.style.display=show?'':'none';
    const next=tr.nextElementSibling;
    if(next&&next.classList.contains('detail'))next.style.display=show?'':'none';
  }});
}}

// ── Registrar operación ──────────────────────────────────
['f_ticker','f_tipo','f_titulos','f_precio'].forEach(id=>{{
  const el=document.getElementById(id); if(el) el.addEventListener('input',updatePreview);
}});
function updatePreview(){{
  const t=(document.getElementById('f_ticker').value||'').toUpperCase();
  const tipo=document.getElementById('f_tipo').value;
  const n=parseFloat(document.getElementById('f_titulos').value)||0;
  const p=parseFloat(document.getElementById('f_precio').value)||0;
  if(t&&n&&p){{
    const total=(n*p).toLocaleString('es-MX',{{minimumFractionDigits:2}});
    const color=tipo==='COMPRA'?'#16a34a':'#dc2626';
    document.getElementById('f_preview_txt').innerHTML='<strong style="color:'+color+'">'+tipo+'</strong> '+n+' tít de <strong>'+t+'</strong> a <strong>$'+p.toFixed(2)+' MXN</strong> = <strong>$'+total+' MXN</strong>';
    document.getElementById('f_preview').style.display='block';
  }}
}}
function registrarOp(){{
  const op={{fecha:document.getElementById('f_fecha').value,
    ticker:(document.getElementById('f_ticker').value||'').toUpperCase().trim(),
    tipo:document.getElementById('f_tipo').value,
    titulos:parseFloat(document.getElementById('f_titulos').value),
    precio_mxn:parseFloat(document.getElementById('f_precio').value),
    origen:document.getElementById('f_origen').value,
    mercado:document.getElementById('f_mercado').value,
    notas:document.getElementById('f_notas').value,
    tc_dia:TC}};
  if(!op.ticker||!op.titulos||!op.precio_mxn||!op.fecha){{
    document.getElementById('f_msg').innerHTML='<span style="color:var(--red)">⚠ Completa todos los campos</span>';return;
  }}
  op.total_mxn=op.titulos*op.precio_mxn;
  let ops=JSON.parse(localStorage.getItem('finbit_ops')||'[]');
  ops.unshift(op);
  localStorage.setItem('finbit_ops',JSON.stringify(ops));
  renderOpsTable(ops);
  actualizarTablaPortafolio();   // ← actualiza portafolio en tiempo real
  document.getElementById('f_msg').innerHTML='<span style="color:var(--green)">✅ Guardado — portafolio actualizado</span>';
  setTimeout(()=>document.getElementById('f_msg').innerHTML='',5000);
  ['f_ticker','f_titulos','f_precio','f_notas'].forEach(id=>document.getElementById(id).value='');
  document.getElementById('f_preview').style.display='none';
}}
function guardarPosicion(){{
  const pos={{ticker:(document.getElementById('p_ticker').value||'').toUpperCase().trim(),
    titulos:parseFloat(document.getElementById('p_titulos').value),
    cto_prom_mxn:parseFloat(document.getElementById('p_cto').value),
    origen:document.getElementById('p_origen').value,
    mercado:document.getElementById('p_mercado').value}};
  if(!pos.ticker||!pos.titulos||!pos.cto_prom_mxn){{
    document.getElementById('p_msg').innerHTML='<span style="color:var(--red)">⚠ Completa los campos</span>';return;
  }}
  let port=JSON.parse(localStorage.getItem('finbit_port')||'[]');
  const idx=port.findIndex(p=>p.ticker===pos.ticker);
  if(idx>=0)port[idx]=pos;else port.push(pos);
  localStorage.setItem('finbit_port',JSON.stringify(port));
  actualizarTablaPortafolio();
  document.getElementById('p_msg').innerHTML='<span style="color:var(--green)">✅ Guardado</span>';
  setTimeout(()=>document.getElementById('p_msg').innerHTML='',5000);
}}

// ── Historial ────────────────────────────────────────────
function renderOpsTable(ops){{
  const body=document.getElementById('hist_body');
  if(!ops.length){{body.innerHTML='<tr><td colspan="10" style="text-align:center;color:var(--muted);padding:24px">Sin operaciones</td></tr>';return;}}
  body.innerHTML=ops.map((op,i)=>{{
    const color=op.tipo==='COMPRA'?'#16a34a':'#dc2626';
    const total=(op.total_mxn||0).toLocaleString('es-MX',{{minimumFractionDigits:2}});
    const precio=(op.precio_mxn||0).toLocaleString('es-MX',{{minimumFractionDigits:2}});
    return '<tr><td>'+(op.fecha||'').slice(0,10)+'</td><td><strong>'+(op.ticker||'')+'</strong></td>'
      +'<td style="color:'+color+';font-weight:600">'+(op.tipo||'')+'</td><td class="num">'+(op.titulos||0)+'</td>'
      +'<td class="num">$'+precio+'</td><td class="num">$'+total+'</td>'
      +'<td style="font-family:monospace;font-size:11px">'+(op.tc_dia||'—')+'</td>'
      +'<td>'+(op.origen||'—')+'</td><td style="color:var(--muted);font-size:11px">'+(op.notas||'—')+'</td>'
      +'<td><button class="btn-sm btn-edit" onclick="editOpLS('+i+')">Editar</button> '
      +'<button class="btn-sm btn-del" onclick="delOpLS('+i+')">Borrar</button></td></tr>';
  }}).join('');
}}
function editOpLS(idx){{
  let ops=JSON.parse(localStorage.getItem('finbit_ops')||'[]');const op=ops[idx];if(!op)return;
  document.getElementById('edit_id').value='ls_'+idx;
  document.getElementById('edit_ticker').value=op.ticker;
  document.getElementById('edit_tipo').value=op.tipo;
  document.getElementById('edit_titulos').value=op.titulos;
  document.getElementById('edit_precio').value=op.precio_mxn;
  document.getElementById('edit_fecha').value=(op.fecha||'').slice(0,10);
  document.getElementById('edit_notas').value=op.notas||'';
  document.getElementById('editModal').classList.add('open');
}}
function delOpLS(idx){{
  if(!confirm('¿Borrar esta operación?'))return;
  let ops=JSON.parse(localStorage.getItem('finbit_ops')||'[]');
  ops.splice(idx,1);localStorage.setItem('finbit_ops',JSON.stringify(ops));renderOpsTable(ops);
  actualizarTablaPortafolio();
}}
function saveEdit(){{
  const idVal=document.getElementById('edit_id').value;
  if(idVal.startsWith('ls_')){{
    const idx=parseInt(idVal.replace('ls_',''));
    let ops=JSON.parse(localStorage.getItem('finbit_ops')||'[]');
    if(ops[idx]){{
      ops[idx].ticker=(document.getElementById('edit_ticker').value||'').toUpperCase().trim();
      ops[idx].tipo=document.getElementById('edit_tipo').value;
      ops[idx].titulos=parseFloat(document.getElementById('edit_titulos').value);
      ops[idx].precio_mxn=parseFloat(document.getElementById('edit_precio').value);
      ops[idx].total_mxn=ops[idx].titulos*ops[idx].precio_mxn;
      ops[idx].fecha=document.getElementById('edit_fecha').value;
      ops[idx].notas=document.getElementById('edit_notas').value;
      localStorage.setItem('finbit_ops',JSON.stringify(ops));renderOpsTable(ops);actualizarTablaPortafolio();
    }}
  }}
  closeModal();
}}
function editOp(id,ticker,tipo,titulos,precio,fecha,notas){{
  document.getElementById('edit_id').value=id;
  document.getElementById('edit_ticker').value=ticker;
  document.getElementById('edit_tipo').value=tipo;
  document.getElementById('edit_titulos').value=titulos;
  document.getElementById('edit_precio').value=precio;
  document.getElementById('edit_fecha').value=fecha;
  document.getElementById('edit_notas').value=notas;
  document.getElementById('editModal').classList.add('open');
}}
function delOp(id,ticker){{
  if(!confirm('¿Borrar operación #'+id+' de '+ticker+'?'))return;
  let del=JSON.parse(localStorage.getItem('finbit_del_ids')||'[]');del.push(id);
  localStorage.setItem('finbit_del_ids',JSON.stringify(del));
  alert('Marcada para borrar. Al correr el script se eliminará de la DB.');
}}
function closeModal(){{document.getElementById('editModal').classList.remove('open');}}
document.getElementById('editModal').addEventListener('click',function(e){{if(e.target===this)closeModal();}});

function filtrar(){{
  const t=(document.getElementById('fil_ticker').value||'').toUpperCase();
  const tipo=document.getElementById('fil_tipo').value;
  document.querySelectorAll('#hist_body tr').forEach(tr=>{{
    const cells=tr.querySelectorAll('td');if(!cells.length)return;
    const mT=!t||cells[1]?.textContent.includes(t);
    const mTp=!tipo||cells[2]?.textContent.trim()===tipo;
    tr.style.display=(mT&&mTp)?'':'none';
  }});
}}

// ── Config ───────────────────────────────────────────────
function saveConfig(){{
  const cap=parseFloat(document.getElementById('cfg_capital').value);
  const rie=parseFloat(document.getElementById('cfg_riesgo').value)/100;
  const rr=parseFloat(document.getElementById('cfg_rr').value);
  if(!cap||!rie||!rr)return;
  localStorage.setItem('cfg_capital',cap);localStorage.setItem('cfg_riesgo',rie);localStorage.setItem('cfg_rr',rr);
  const blob=new Blob([JSON.stringify({{capital:cap,riesgo:rie,rr_min:rr}})],{{type:'application/json'}});
  const a=document.createElement('a');a.href=URL.createObjectURL(blob);a.download='finbit_config.json';a.click();
  URL.revokeObjectURL(a.href);
  alert('Config guardada. Descargó finbit_config.json — ponlo junto al script y vuelve a correr.');
}}

// ── Radar ────────────────────────────────────────────────
function filtrarRadar(tipo,btn){{
  document.querySelectorAll('.filter-btn').forEach(b=>b.classList.remove('active'));btn.classList.add('active');
  let count=0;
  document.querySelectorAll('#radar_body tr.datarow').forEach(tr=>{{
    const estado=tr.querySelector('.badge')?.textContent?.trim()||'';
    const potEl=tr.querySelectorAll('td')[4]?.textContent?.trim()||'0';
    const pot=parseFloat(potEl.replace('%','').replace('+',''))||0;
    let show=true;
    if(tipo==='alza') show=estado.includes('↑')||estado.includes('🚀')||estado.includes('👁');
    if(tipo==='ganga') show=estado.includes('Ganga');
    if(tipo==='baja') show=estado.includes('↓');
    if(tipo==='10pct') show=pot>=10;
    if(tipo==='rocket') show=estado.includes('🚀');
    if(tipo==='skip') show=estado.includes('Esperar');
    tr.style.display=show?'':'none';
    const next=tr.nextElementSibling;
    if(next&&next.classList.contains('detail'))next.style.display=show?'':'none';
    if(show) count++;
  }});
  const el=document.getElementById('radar_count');
  if(el) el.textContent='Mostrando '+count+' acciones';
}}
function buscarRadar(){{
  const q=(document.getElementById('radar_search').value||'').toUpperCase();
  let count=0;
  document.querySelectorAll('#radar_body tr.datarow').forEach(tr=>{{
    const ticker=tr.querySelector('strong')?.textContent?.toUpperCase()||'';
    const show=!q||ticker.includes(q);tr.style.display=show?'':'none';
    const next=tr.nextElementSibling;
    if(next&&next.classList.contains('detail'))next.style.display=show?'':'none';
    if(show) count++;
  }});
  const el=document.getElementById('radar_count');
  if(el) el.textContent='Mostrando '+count+' acciones';
}}


// ── Buscador de tickers ───────────────────────────────────
function getTickers() {{
  const ids = ['b_t1','b_t2','b_t3','b_t4','b_t5'];
  return ids.map(id => document.getElementById(id).value.toUpperCase().trim()).filter(Boolean);
}}

function buscarTickers() {{
  const tickers = getTickers();
  if (!tickers.length) {{
    document.getElementById('b_msg').innerHTML = '<span style="color:var(--red)">Escribe al menos un ticker</span>';
    return;
  }}
  const exchange = document.getElementById('b_exchange').value;
  document.getElementById('b_loading').style.display = 'block';
  document.getElementById('b_results').innerHTML = '';
  document.getElementById('b_msg').innerHTML = '';

  // Guardar para que el script los lea en la próxima corrida
  let saved = JSON.parse(localStorage.getItem('finbit_custom_tickers') || '{{}}');
  tickers.forEach(t => {{ saved[t] = exchange; }});
  localStorage.setItem('finbit_custom_tickers', JSON.stringify(saved));

  // Mostrar resultado visual inmediato (el análisis real ocurre al correr el script)
  setTimeout(() => {{
    document.getElementById('b_loading').style.display = 'none';
    let html = '<div style="background:var(--green-l);border:1px solid var(--green-b);border-radius:var(--r);padding:13px 16px;font-size:12px;color:#14532d">';
    html += '<strong>✅ Tickers registrados:</strong> ' + tickers.join(', ') + '<br>';
    html += 'Se analizarán en la próxima corrida del script. ';
    html += 'Para ver el análisis ahora mismo, cierra el browser y corre <code>python finbit.py</code>';
    html += '</div>';
    document.getElementById('b_results').innerHTML = html;
    actualizarListaGuardados();
  }}, 500);
}}

function guardarTickers() {{
  const tickers = getTickers();
  if (!tickers.length) return;
  const exchange = document.getElementById('b_exchange').value;
  let saved = JSON.parse(localStorage.getItem('finbit_custom_tickers') || '{{}}');
  tickers.forEach(t => {{ saved[t] = exchange; }});
  localStorage.setItem('finbit_custom_tickers', JSON.stringify(saved));

  // También exportar como JSON para que el script lo lea
  const blob = new Blob([JSON.stringify(saved, null, 2)], {{type:'application/json'}});
  const a = document.createElement('a'); a.href = URL.createObjectURL(blob);
  a.download = 'finbit_tickers.json'; a.click(); URL.revokeObjectURL(a.href);

  document.getElementById('b_msg').innerHTML = '<span style="color:var(--green)">✅ Guardado. Descargó finbit_tickers.json — ponlo junto al script y vuelve a correr</span>';
  actualizarListaGuardados();
}}

function actualizarListaGuardados() {{
  const saved = JSON.parse(localStorage.getItem('finbit_custom_tickers') || '{{}}');
  const el = document.getElementById('b_saved_list');
  if (!Object.keys(saved).length) {{
    el.innerHTML = '<span class="hint">Sin tickers guardados aún</span>';
    return;
  }}
  el.innerHTML = Object.entries(saved).map(([t, ex]) =>
    `<span class="ticker-chip"><strong>${{t}}</strong><span class="hint">${{ex||'auto'}}</span>`+
    `<button onclick="quitarTicker('${{t}}')">×</button></span>`
  ).join('');
}}

function quitarTicker(ticker) {{
  let saved = JSON.parse(localStorage.getItem('finbit_custom_tickers') || '{{}}');
  delete saved[ticker];
  localStorage.setItem('finbit_custom_tickers', JSON.stringify(saved));
  actualizarListaGuardados();
}}
// ── Init ─────────────────────────────────────────────────
window.addEventListener('load',()=>{{
  cargarTickersPersonalizados();
  const ops=JSON.parse(localStorage.getItem('finbit_ops')||'[]');
  if(ops.length) renderOpsTable(ops);
  actualizarTablaPortafolio();
  actualizarListaGuardados();
  const cap=localStorage.getItem('cfg_capital');
  const rie=localStorage.getItem('cfg_riesgo');
  const rr=localStorage.getItem('cfg_rr');
  if(cap) document.getElementById('cfg_capital').value=cap;
  if(rie) document.getElementById('cfg_riesgo').value=(parseFloat(rie)*100).toFixed(0);
  if(rr) document.getElementById('cfg_rr').value=rr;
}});

// ── Agregar ticker al scanner (guarda en DB via API) ─────────
function agregarTickerScanner() {{
  const ticker = (document.getElementById('add_ticker_input').value||'').toUpperCase().trim();
  const exchange = document.getElementById('add_exchange_input').value;
  const msg = document.getElementById('add_ticker_msg');
  if (!ticker) {{ msg.innerHTML = '<span style="color:var(--red)">Escribe un ticker</span>'; return; }}
  msg.innerHTML = '<span style="color:var(--muted)">Guardando...</span>';

  fetch('/api/tickers/add', {{
    method: 'POST',
    headers: {{'Content-Type': 'application/json'}},
    body: JSON.stringify({{ticker, exchange}})
  }})
  .then(r => r.json())
  .then(d => {{
    if (d.status === 'ok') {{
      const srcBadge = d.source === 'serpapi'
        ? '<span style="background:#f5f3ff;border:1px solid #ddd6fe;color:#7c3aed;border-radius:4px;padding:1px 6px;font-size:10px;margin-left:4px">SerpApi</span>'
        : '<span style="background:#f0fdf4;border:1px solid #bbf7d0;color:#16a34a;border-radius:4px;padding:1px 6px;font-size:10px;margin-left:4px">TwelveData</span>';
      msg.innerHTML = '<span style="color:var(--green)">✅ ' + ticker + ' guardado' + (srcBadge||'') + ' — actualiza para analizarlo.</span>';
      document.getElementById('add_ticker_input').value = '';
      cargarTickersPersonalizados();
    }} else {{
      msg.innerHTML = '<span style="color:var(--red)">Error: ' + (d.error||'desconocido') + '</span>';
    }}
  }})
  .catch(() => {{
    // Fallback: guardar en localStorage si no hay servidor
    let saved = JSON.parse(localStorage.getItem('finbit_custom_tickers')||'{{}}');
    saved[ticker] = exchange;
    localStorage.setItem('finbit_custom_tickers', JSON.stringify(saved));
    msg.innerHTML = '<span style="color:var(--yellow)">⚠️ Guardado localmente (sin servidor).</span>';
    cargarTickersPersonalizados();
  }});
}}

function quitarTickerScanner(ticker) {{
  fetch('/api/tickers/remove', {{
    method: 'POST',
    headers: {{'Content-Type': 'application/json'}},
    body: JSON.stringify({{ticker}})
  }})
  .then(() => cargarTickersPersonalizados())
  .catch(() => {{
    let saved = JSON.parse(localStorage.getItem('finbit_custom_tickers')||'{{}}');
    delete saved[ticker];
    localStorage.setItem('finbit_custom_tickers', JSON.stringify(saved));
    cargarTickersPersonalizados();
  }});
}}

function cargarTickersPersonalizados() {{
  fetch('/api/tickers')
    .then(r => r.json())
    .then(d => {{
      const el = document.getElementById('custom_tickers_list');
      if (!el) return;
      const defaults   = d.defaults   || [];
      const custom     = d.custom     || [];
      const eliminados = d.eliminados || [];
      const total      = d.total      || (defaults.length + custom.length);
      let html = `<span class="hint" style="font-size:11px">${{total}} ticker(s) activos en el scanner</span><br><br>`;

      // Todos los activos (defaults + custom) — con botón × para todos
      const todos = [...defaults, ...custom];
      html += todos.map(t => {{
        const isCustom = !d.defaults.includes(t);
        const style = isCustom
          ? 'border-color:var(--red-b)'
          : 'border-color:var(--brd)';
        return `<span class="ticker-chip" style="${{style}};margin-bottom:4px">` +
          `<strong style="color:${{isCustom?'var(--red)':'var(--text)'}}">${{t}}</strong>` +
          `<button onclick="quitarTickerScanner('${{t}}')" title="Quitar ${{t}} del scanner" ` +
          `style="border:none;background:none;color:var(--red);cursor:pointer;font-size:14px;padding:0 0 0 4px;line-height:1">×</button></span>`;
      }}).join(' ');

      // Tickers eliminados por el usuario (aparecen tachados con opción de restaurar)
      if (eliminados.length) {{
        html += `<div style="margin-top:10px;border-top:1px solid var(--brd);padding-top:8px">`;
        html += `<span class="hint" style="font-size:10px">Eliminados (solo de tu scanner):</span><br>`;
        html += eliminados.map(t =>
          `<span class="ticker-chip" style="opacity:.5;margin-top:4px">` +
          `<s style="font-size:11px">${{t}}</s>` +
          `<button onclick="restaurarTickerScanner('${{t}}')" title="Restaurar ${{t}}" ` +
          `style="border:none;background:none;color:var(--green);cursor:pointer;font-size:12px;padding:0 0 0 4px">↩</button></span>`
        ).join(' ');
        html += '</div>';
      }}

      el.innerHTML = html;
    }})
    .catch(() => {{
      const saved = JSON.parse(localStorage.getItem('finbit_custom_tickers')||'{{}}');
      const el = document.getElementById('custom_tickers_list');
      if (!el) return;
      const keys = Object.keys(saved);
      el.innerHTML = '<span class="hint" style="font-size:11px;margin-right:6px">Modo local:</span>' +
        (keys.length
          ? keys.map(t => `<span class="ticker-chip"><strong>${{t}}</strong></span>`).join('')
          : '<span class="hint" style="font-size:11px">Sin tickers guardados</span>');
    }});
}}

function restaurarTickerScanner(ticker) {{
  fetch('/api/tickers/add', {{
    method: 'POST',
    headers: {{'Content-Type': 'application/json'}},
    body: JSON.stringify({{ticker}})
  }})
  .then(() => cargarTickersPersonalizados())
  .catch(() => cargarTickersPersonalizados());
}}


function actualizarDashboard() {{
  const btn = document.getElementById('btn_update');
  if (btn) {{ btn.disabled = true; btn.textContent = '↺ Actualizando...'; }}
  fetch('/refresh', {{method:'POST'}})
    .then(r => r.json())
    .then(d => {{
      if (d.status === 'busy') {{
        if (btn) {{ btn.disabled = false; btn.textContent = '↺ Actualizar'; }}
        alert('Ya hay una actualización en curso, espera un momento.');
        return;
      }}
      // Poll until ready
      const poll = setInterval(() => {{
        fetch('/status').then(r=>r.json()).then(s => {{
          if (s.ready) {{
            clearInterval(poll);
            location.reload();
          }}
        }});
      }}, 3000);
    }})
    .catch(() => {{ if(btn){{btn.disabled=false;btn.textContent='↺ Actualizar';}} }});
}}

// ── Backup / Restore DB ──────────────────────────────────
function backupDB() {{
  window.location.href = '/api/backup';
}}

function restaurarDB(input) {{
  if (!input.files || !input.files[0]) return;
  const file = input.files[0];
  if (!file.name.endsWith('.db')) {{
    alert('El archivo debe tener extensión .db');
    input.value = '';
    return;
  }}
  if (!confirm('¿Restaurar la base de datos con "' + file.name + '"?\n\nSe hará un backup automático de la DB actual antes de reemplazarla.')) {{
    input.value = '';
    return;
  }}
  const formData = new FormData();
  formData.append('file', file);
  fetch('/api/restore', {{method:'POST', body: formData}})
    .then(r => r.json())
    .then(d => {{
      if (d.status === 'ok') {{
        alert('✅ ' + d.msg);
        actualizarDashboard();
      }} else {{
        alert('❌ Error: ' + (d.error || 'desconocido'));
      }}
      input.value = '';
    }})
    .catch(() => {{ alert('❌ Error de red al restaurar'); input.value = ''; }});
}}
</script></body></html>"""


# ── IMPORTAR OPS ──────────────────────────────────────────
def importar_ops_json(path: str, tc: float):
    if not os.path.exists(path): return
    with open(path) as f: ops=json.load(f)
    con=sqlite3.connect(DB_FILE)
    for op in ops:
        try:
            con.execute("INSERT OR IGNORE INTO operaciones (fecha,ticker,tipo,titulos,precio_mxn,total_mxn,tc_dia,origen,mercado,notas) VALUES(?,?,?,?,?,?,?,?,?,?)",
                (op.get("fecha"),op.get("ticker"),op.get("tipo"),op.get("titulos"),
                 op.get("precio_mxn"),op.get("total_mxn"),op.get("tc_dia",tc),
                 op.get("origen","USA"),op.get("mercado","SIC"),op.get("notas","")))
            upsert_portafolio_from_op(op)   # ← actualiza SQLite también
        except Exception as e: print(f"  op skip: {e}")
    con.commit(); con.close()
    print(f"  {len(ops)} operaciones importadas → portafolio SQLite actualizado")

def procesar_borrados():
    path="finbit_del_ids.json"
    if not os.path.exists(path): return
    with open(path) as f: ids=json.load(f)
    if not ids: return
    con=sqlite3.connect(DB_FILE)
    for oid in ids: con.execute("DELETE FROM operaciones WHERE id=?", (oid,))
    con.commit(); con.close(); os.remove(path)
    print(f"  {len(ids)} operaciones borradas")

def cargar_config() -> dict:
    defaults={"capital":CAPITAL_TOTAL,"riesgo":RIESGO_POR_TRADE,"rr_min":RR_MINIMO}
    if not os.path.exists("finbit_config.json"): return defaults
    try:
        with open("finbit_config.json") as f: return {**defaults,**json.load(f)}
    except Exception: return defaults


# ═══════════════════════════════════════════════════════════
#   CONSTRUCCIÓN DEL DASHBOARD (reutilizable)
# ═══════════════════════════════════════════════════════════
def construir_dashboard() -> str:
    """Genera dashboard.html con datos frescos. Devuelve HTML como string."""
    global _MACRO_CACHE
    _MACRO_CACHE = {}   # limpiar cache macro en cada corrida

    cfg = cargar_config()
    capital = cfg["capital"]; riesgo_pct = cfg["riesgo"]; rr_min = cfg["rr_min"]

    init_db()
    init_score_history()
    tc = get_tipo_cambio(API_KEY)
    seed_portafolio(tc)

    if os.path.exists("finbit_ops.json"):
        importar_ops_json("finbit_ops.json", tc)
    procesar_borrados()

    tickers_extra = {}
    if os.path.exists("finbit_tickers.json"):
        try:
            with open("finbit_tickers.json") as f:
                raw = json.load(f)
            for t, ex in raw.items():
                tickers_extra[t.upper()] = (t.upper(), ex or "")
            print(f"  Tickers extra cargados: {list(tickers_extra.keys())}")
        except Exception as e:
            print(f"  finbit_tickers.json error: {e}")

    if API_KEY not in ("TU_KEY_AQUI", ""):
        # Obtener macro PRIMERO (se reutiliza en todo el análisis)
        print("[MACRO] Obteniendo VIX y SPY...")
        vix = get_vix()
        spy = get_spy_macro()
        regimen = regimen_mercado(vix, spy)
        print(f"  VIX={vix:.1f} | SPY EMA200={'✅' if spy.get('sobre_ema200') else '❌'} | {regimen['label']}")

        port_data  = analizar_portafolio(tc, capital, riesgo_pct, rr_min)
        scan_data  = correr_scanner(tc, capital, riesgo_pct, rr_min, tickers_extra,
                                     vix=vix, spy=spy)
        radar_data = radar_masivo(tc, capital, riesgo_pct, rr_min, scan_results=scan_data,
                                   vix=vix, spy=spy)
    else:
        vix = 20.0
        spy = {"sobre_ema200": True, "precio": None, "ema200": None}
        regimen = regimen_mercado(vix, spy)
        port_data = []
        for p in get_portafolio():
            port_data.append({**p, "analisis": None, "precio_actual_usd": None,
                "precio_actual_mxn": None,
                "valor_mxn": p["cto_prom_mxn"] * p["titulos"],
                "costo_total": p["cto_prom_mxn"] * p["titulos"],
                "pl_mxn": 0, "pl_pct": 0, "alertas": [],
                "entrada_mxn": None, "stop_mxn": None, "obj_mxn": None})
        scan_data = []; radar_data = []

    ops  = get_operaciones()
    html = generar_html(port_data, scan_data, radar_data, ops, tc, capital,
                        riesgo_pct, rr_min, vix=vix, spy=spy, regimen=regimen)
    with open(OUTPUT_FILE, "w", encoding="utf-8") as f:
        f.write(html)
    print(f"  Dashboard listo — {len(port_data)} pos · {len(scan_data)} scan · {len(radar_data)} radar · TC={tc:.4f} · VIX={vix:.1f}")
    return html


# ═══════════════════════════════════════════════════════════
#   SERVIDOR FLASK  — arquitectura non-blocking
#   El dashboard se construye en un hilo separado.
#   Mientras no está listo, / devuelve una pantalla de
#   loading elegante que hace polling cada 3 s a /status.
#   Nunca se bloquea el worker de Flask/Gunicorn.
# ═══════════════════════════════════════════════════════════
app = Flask(__name__)
_dash_html: str = ""
_dash_lock  = threading.Lock()
_refresh_in_progress = False
_build_start_time: float = 0.0   # para mostrar tiempo transcurrido
_build_error: str = ""           # captura último error de build


# ── Pantalla de loading profesional ──────────────────────
_LOADING_HTML = """<!DOCTYPE html>
<html lang="es">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>finbit pro — cargando</title>
<link href="https://fonts.googleapis.com/css2?family=DM+Sans:wght@300;400;500;600&family=DM+Mono:wght@400;500&display=swap" rel="stylesheet">
<style>
  :root{--red:#dc2626;--green:#16a34a;--bg:#f5f5f3;--surface:#fff;--brd:#e5e5e3;--text:#111;--muted:#666}
  *{margin:0;padding:0;box-sizing:border-box}
  body{background:var(--bg);font-family:'DM Sans',sans-serif;min-height:100vh;display:flex;flex-direction:column;align-items:center;justify-content:center;gap:0}
  .card{background:var(--surface);border:1px solid var(--brd);border-radius:16px;padding:40px 48px;text-align:center;max-width:480px;width:90%;box-shadow:0 4px 24px rgba(0,0,0,.06)}
  .logo{font-size:22px;font-weight:600;letter-spacing:-.4px;margin-bottom:32px}
  .logo em{color:var(--red);font-style:normal}
  .spinner-wrap{margin:0 auto 28px;width:52px;height:52px;position:relative}
  .spinner{width:52px;height:52px;border-radius:50%;border:3px solid var(--brd);border-top-color:var(--red);animation:spin 1s linear infinite}
  @keyframes spin{to{transform:rotate(360deg)}}
  .status-msg{font-size:15px;font-weight:500;color:var(--text);margin-bottom:8px}
  .status-sub{font-size:12px;color:var(--muted);margin-bottom:24px;line-height:1.6}
  .steps{text-align:left;font-size:12px;color:var(--muted);border-top:1px solid var(--brd);padding-top:18px;display:flex;flex-direction:column;gap:6px}
  .step{display:flex;align-items:center;gap:8px;padding:4px 0}
  .step-dot{width:8px;height:8px;border-radius:50%;flex-shrink:0}
  .dot-done{background:var(--green)}
  .dot-active{background:var(--red);animation:pulse .8s ease-in-out infinite}
  .dot-pending{background:var(--brd)}
  @keyframes pulse{0%,100%{opacity:1}50%{opacity:.3}}
  .time-chip{font-size:11px;color:var(--red);font-family:'DM Mono',monospace;margin-top:16px;border:1px solid #fecaca;background:#fef2f2;border-radius:20px;padding:3px 10px;display:inline-block}
  .reload-btn{margin-top:22px;padding:8px 22px;background:var(--red);color:#fff;border:none;border-radius:8px;font-size:13px;font-family:'DM Sans',sans-serif;cursor:pointer;display:none}
  .reload-btn:hover{opacity:.88}
</style>
</head>
<body>
<div class="card">
  <div class="logo">fin<em>bit</em> <span style="font-size:12px;color:var(--muted);font-weight:400">pro</span></div>
  <div class="spinner-wrap"><div class="spinner"></div></div>
  <div class="status-msg" id="smsg">Analizando mercados...</div>
  <div class="status-sub" id="ssub">Obteniendo datos históricos, calculando indicadores<br>y construyendo el dashboard. Un momento.</div>
  <div class="steps">
    <div class="step"><div class="step-dot dot-done"></div><span>Servidor iniciado</span></div>
    <div class="step"><div class="step-dot dot-done"></div><span>Base de datos lista</span></div>
    <div class="step" id="step-tc"><div class="step-dot dot-active"></div><span>Tipo de cambio USD/MXN</span></div>
    <div class="step" id="step-macro"><div class="step-dot dot-pending"></div><span>Macro: VIX + SPY EMA200</span></div>
    <div class="step" id="step-port"><div class="step-dot dot-pending"></div><span>Análisis de portafolio</span></div>
    <div class="step" id="step-scan"><div class="step-dot dot-pending"></div><span>Scanner técnico</span></div>
    <div class="step" id="step-radar"><div class="step-dot dot-pending"></div><span>Radar automático</span></div>
    <div class="step" id="step-html"><div class="step-dot dot-pending"></div><span>Generando dashboard HTML</span></div>
  </div>
  <div class="time-chip" id="tchip">0s transcurridos</div>
  <button class="reload-btn" id="rbtn" onclick="window.location.reload()">Ver dashboard →</button>
</div>
<script>
  let elapsed = 0;
  const startTs = Date.now();
  const chip = document.getElementById('tchip');
  const rbtn = document.getElementById('rbtn');
  const smsg = document.getElementById('smsg');
  const ssub = document.getElementById('ssub');

  function markStep(id){
    const el = document.getElementById(id);
    if(!el) return;
    el.querySelector('.step-dot').className = 'step-dot dot-done';
    // Avanzar el spinner al siguiente
    const all = ['step-tc','step-macro','step-port','step-scan','step-radar','step-html'];
    const idx = all.indexOf(id);
    if(idx >= 0 && idx < all.length-1){
      const nxt = document.getElementById(all[idx+1]);
      if(nxt) nxt.querySelector('.step-dot').className = 'step-dot dot-active';
    }
  }

  function poll(){
    fetch('/status')
      .then(r=>r.json())
      .then(d=>{
        if(d.ready){
          smsg.textContent = '¡Dashboard listo!';
          ssub.textContent = 'Cargando en un momento...';
          chip.textContent = 'Listo en ' + d.elapsed + 's';
          rbtn.style.display = 'inline-block';
          // Redirigir automáticamente
          window.location.reload();
        } else {
          // Marcar pasos según etapa reportada
          const stage = d.stage || '';
          if(stage === 'tc_ok') markStep('step-tc');
          if(stage === 'macro_ok'){ markStep('step-tc'); markStep('step-macro'); }
          if(stage === 'port_ok'){ markStep('step-tc'); markStep('step-macro'); markStep('step-port'); }
          if(stage === 'scan_ok'){ markStep('step-tc'); markStep('step-macro'); markStep('step-port'); markStep('step-scan'); }
          if(stage === 'radar_ok'){ markStep('step-tc'); markStep('step-macro'); markStep('step-port'); markStep('step-scan'); markStep('step-radar'); }
          elapsed = d.elapsed || 0;
          chip.textContent = elapsed + 's transcurridos';
          smsg.textContent = d.msg || 'Analizando mercados...';
          setTimeout(poll, 2500);
        }
      })
      .catch(()=>setTimeout(poll, 3000));
  }

  // Actualizar el chip de tiempo localmente también
  setInterval(()=>{
    const s = Math.round((Date.now()-startTs)/1000);
    chip.textContent = s + 's transcurridos';
  }, 1000);

  // Primer poll en 1.5s
  setTimeout(poll, 1500);
</script>
</body></html>"""

# Estado del build en progreso (para reportar al /status)
_build_stage: str = ""
_build_elapsed: float = 0.0


def _get_html() -> str:
    """Retorna el dashboard si ya está listo, o "" si todavía está construyéndose."""
    global _dash_html
    return _dash_html


# ── Página principal — NUNCA bloquea ─────────────────────
@app.route("/")
def index():
    html = _get_html()
    if html:
        return Response(html, mimetype="text/html")
    # Dashboard aún no listo → devolver loading screen instantáneamente
    return Response(_LOADING_HTML, mimetype="text/html")


# ── Status endpoint (polling desde la loading screen) ────
@app.route("/status")
def status():
    global _dash_html, _build_stage, _build_start_time, _build_error
    ready = bool(_dash_html)
    elapsed = round(time.time() - _build_start_time, 1) if _build_start_time else 0

    stage_msg = {
        "":          "Iniciando análisis...",
        "tc_ok":     "Tipo de cambio obtenido. Cargando macro...",
        "macro_ok":  "VIX y SPY listos. Analizando portafolio...",
        "port_ok":   "Portafolio analizado. Corriendo scanner...",
        "scan_ok":   "Scanner completo. Ejecutando radar...",
        "radar_ok":  "Radar listo. Generando HTML...",
        "html_ok":   "¡Dashboard generado!",
        "error":     f"Error: {_build_error[:80]}" if _build_error else "Error desconocido",
    }.get(_build_stage, "Procesando...")

    return jsonify({
        "ready":   ready,
        "stage":   _build_stage,
        "msg":     stage_msg,
        "elapsed": elapsed,
        "error":   _build_error if _build_stage == "error" else "",
    })


# ── Función de build con reporte de etapas ───────────────
_BUILD_TIMEOUT = 480   # segundos — 8 min, suficiente para scanner + radar completos

def _construir_con_etapas():
    """Wrapper de construir_dashboard() que va reportando el stage."""
    global _dash_html, _build_stage, _build_start_time, _build_error, _refresh_in_progress
    global _MACRO_CACHE, _TD_CACHE

    _refresh_in_progress = True
    _build_start_time    = time.time()
    _build_stage         = ""
    _build_error         = ""
    _MACRO_CACHE         = {}
    _TD_CACHE            = {}

    def _timeout_exceeded():
        return (time.time() - _build_start_time) > _BUILD_TIMEOUT

    try:
        cfg        = cargar_config()
        capital    = cfg["capital"]
        riesgo_pct = cfg["riesgo"]
        rr_min     = cfg["rr_min"]

        init_db()
        init_score_history()

        tc = get_tipo_cambio(API_KEY)
        seed_portafolio(tc)
        _build_stage = "tc_ok"

        if os.path.exists("finbit_ops.json"):
            importar_ops_json("finbit_ops.json", tc)
        procesar_borrados()

        tickers_extra = {}
        if os.path.exists("finbit_tickers.json"):
            try:
                with open("finbit_tickers.json") as f:
                    raw = json.load(f)
                for t, ex in raw.items():
                    tickers_extra[t.upper()] = (t.upper(), ex or "")
            except Exception as e:
                print(f"  finbit_tickers.json error: {e}")

        vix     = 20.0
        spy     = {"sobre_ema200": True, "precio": None, "ema200": None}
        port_data  = []
        scan_data  = []
        radar_data = []

        if API_KEY not in ("TU_KEY_AQUI", ""):
            # ── DIAGNÓSTICO DE API ANTES DE CONSTRUIR ───────────────────────
            print("[build] 🔍 Verificando conexión a TwelveData...")
            try:
                _test_r = requests.get(f"{API_BASE}/time_series",
                    params={"symbol":"AAPL","interval":"1day","outputsize":"5","apikey":API_KEY},
                    timeout=10)
                _test_d = _test_r.json()
                if "values" in _test_d:
                    print(f"[build] ✅ TwelveData OK — {len(_test_d['values'])} velas de prueba")
                elif _test_r.status_code == 429:
                    print("[build] ⚠️  TwelveData: RATE LIMIT 429 — esperando 15s antes de continuar...")
                    time.sleep(15)
                else:
                    _msg = _test_d.get("message", _test_d.get("code", str(_test_d)[:120]))
                    print(f"[build] ❌ TwelveData error: {_msg}")
                    print(f"[build]    status HTTP: {_test_r.status_code}")
            except Exception as _e:
                print(f"[build] ❌ TwelveData sin conexión: {_e}")
            print("[build] Obteniendo VIX y SPY...")
            try:
                vix = get_vix()
                spy = get_spy_macro()
            except Exception as e:
                print(f"[build] Macro error (continuando): {e}")
            regimen = regimen_mercado(vix, spy)
            _build_stage = "macro_ok"

            if not _timeout_exceeded():
                try:
                    port_data = analizar_portafolio(tc, capital, riesgo_pct, rr_min)
                except Exception as e:
                    print(f"[build] Portafolio error (continuando): {e}")
                    port_data = []
            _build_stage = "port_ok"

            if not _timeout_exceeded():
                try:
                    scan_data = correr_scanner(tc, capital, riesgo_pct, rr_min, tickers_extra,
                                               vix=vix, spy=spy)
                    print(f"[build] Scanner: {len(scan_data)} tickers procesados")
                except Exception as e:
                    import traceback
                    print(f"[build] Scanner error: {e}")
                    traceback.print_exc()
                    scan_data = []
            else:
                print(f"[build] ⚠️  Timeout antes del scanner — omitiendo")
            _build_stage = "scan_ok"

            if not _timeout_exceeded():
                try:
                    radar_data = radar_masivo(tc, capital, riesgo_pct, rr_min,
                                              scan_results=scan_data, vix=vix, spy=spy)
                    print(f"[build] Radar: {len(radar_data)} tickers procesados")
                except Exception as e:
                    import traceback
                    print(f"[build] Radar error: {e}")
                    traceback.print_exc()
                    radar_data = []
            else:
                print(f"[build] ⚠️  Timeout antes del radar — omitiendo")
            _build_stage = "radar_ok"
        else:
            regimen = regimen_mercado(vix, spy)
            _build_stage = "macro_ok"
            for p in get_portafolio():
                port_data.append({**p, "analisis": None, "precio_actual_usd": None,
                    "precio_actual_mxn": None,
                    "valor_mxn": p["cto_prom_mxn"] * p["titulos"],
                    "costo_total": p["cto_prom_mxn"] * p["titulos"],
                    "pl_mxn": 0, "pl_pct": 0, "alertas": [],
                    "entrada_mxn": None, "stop_mxn": None, "obj_mxn": None})
            _build_stage = "port_ok"
            _build_stage = "scan_ok"
            _build_stage = "radar_ok"

        ops  = get_operaciones()
        html = generar_html(port_data, scan_data, radar_data, ops, tc, capital,
                            riesgo_pct, rr_min, vix=vix, spy=spy, regimen=regimen)
        _build_stage = "html_ok"

        # Escribir al disco también (por si se reinicia)
        try:
            with open(OUTPUT_FILE, "w", encoding="utf-8") as f:
                f.write(html)
        except Exception:
            pass

        with _dash_lock:
            _dash_html = html

        elapsed = round(time.time() - _build_start_time, 1)
        print(f"[build] Dashboard listo en {elapsed}s — {len(port_data)} pos · {len(scan_data)} scan · {len(radar_data)} radar")

    except Exception as e:
        _build_stage = "error"
        _build_error = str(e)
        print(f"[build] ERROR: {e}")
        import traceback; traceback.print_exc()

        # ── Si el build falla (sin créditos API, timeout, etc.)
        #    servir el dashboard anterior en lugar de pantalla de error
        if not _dash_html and os.path.exists(OUTPUT_FILE):
            try:
                with open(OUTPUT_FILE, "r", encoding="utf-8") as f:
                    cached = f.read()
                if len(cached) > 500:
                    with _dash_lock:
                        _dash_html = cached
                    _build_stage = "html_ok"
                    print(f"[build] ⚠️  Build falló — sirviendo dashboard anterior desde disco")
            except Exception:
                pass
        # Intentar servir un dashboard mínimo con lo que tengamos
        try:
            tc_fallback = 17.5
            ops = get_operaciones()
            port_fallback = []
            for p in get_portafolio():
                port_fallback.append({**p, "analisis": None, "precio_actual_usd": None,
                    "precio_actual_mxn": None,
                    "valor_mxn": p["cto_prom_mxn"] * p["titulos"],
                    "costo_total": p["cto_prom_mxn"] * p["titulos"],
                    "pl_mxn": 0, "pl_pct": 0, "alertas": [],
                    "entrada_mxn": None, "stop_mxn": None, "obj_mxn": None})
            html_fallback = generar_html(port_fallback, [], [], ops, tc_fallback, 15000,
                                         0.01, 3.0)
            with _dash_lock:
                _dash_html = html_fallback
            print("[build] Dashboard fallback servido (sin datos API)")
        except Exception as e2:
            print(f"[build] Fallback también falló: {e2}")
    finally:
        _refresh_in_progress = False


# ── Actualizar dashboard (botón en el HTML) ───────────────
@app.route("/update")
@app.route("/refresh", methods=["GET", "POST"])
def refresh():
    global _dash_html, _refresh_in_progress
    if _refresh_in_progress:
        return jsonify({"status": "busy", "msg": "Ya hay una actualización en curso"}), 202
    # Invalidar cache para que / muestre loading screen mientras reconstruye
    with _dash_lock:
        _dash_html = ""
    threading.Thread(target=_construir_con_etapas, daemon=True).start()
    return jsonify({"status": "ok", "msg": "Actualización iniciada"})


# ── Timeframe (selector en el HTML — guardado en config) ──
@app.route("/set_tf/<tf>")
def set_tf(tf: str):
    """El HTML manda /set_tf/intra|swing|largo — lo guardamos y regeneramos."""
    tf_map = {"intra": "1h", "swing": "1day", "largo": "1week"}
    tf_code = tf_map.get(tf, "1day")
    try:
        cfg = cargar_config()
        cfg["timeframe"] = tf_code
        with open("finbit_config.json", "w") as f:
            json.dump(cfg, f, indent=2)
    except Exception as e:
        print(f"[server] set_tf error: {e}")
    return jsonify({"status": "ok", "tf": tf_code})


# ── API: tickers del scanner ──────────────────────────────
@app.route("/api/tickers")
def api_tickers():
    try:
        todos_activos = get_all_scanner_tickers()
        # Separar: los que vienen del hardcode vs los que son solo custom DB
        defaults_activos = [t for t in todos_activos if t in SCANNER_TICKERS]
        custom_activos   = [t for t in todos_activos if t not in SCANNER_TICKERS]
        # Tickers hardcoded que el usuario eliminó (en DB con activo=0)
        con = sqlite3.connect(DB_FILE)
        con.row_factory = sqlite3.Row
        try:
            deleted_rows = con.execute(
                "SELECT ticker FROM tickers WHERE activo=0"
            ).fetchall()
        except Exception:
            deleted_rows = []
        con.close()
        eliminados = [r["ticker"] for r in deleted_rows if r["ticker"] in SCANNER_TICKERS]
        return jsonify({
            "defaults": defaults_activos,
            "custom":   custom_activos,
            "eliminados": eliminados,
            "total": len(todos_activos)
        })
    except Exception as e:
        return jsonify({"status": "error", "error": str(e)}), 500


@app.route("/api/tickers/add", methods=["POST"])
def api_tickers_add():
    try:
        data     = flask_req.get_json(force=True) or {}
        ticker   = (data.get("ticker") or "").upper().strip()
        exchange = (data.get("exchange") or "").upper().strip()
        origen   = data.get("origen", "USA")
        if not ticker:
            return jsonify({"status": "error", "error": "ticker vacío"}), 400
        # Validar formato
        import re as _re
        if not _re.match(r'^[A-Z0-9.]{1,15}$', ticker):
            return jsonify({"status": "error", "error": f"Ticker inválido: {ticker}"}), 400
        # Si el ticker ya existe en la DB (puede estar activo=0), lo reactiva
        con = sqlite3.connect(DB_FILE)
        con.execute(
            "INSERT OR REPLACE INTO tickers (ticker, exchange, origen, activo) VALUES (?,?,?,1)",
            (ticker, exchange or (SCANNER_TICKERS.get(ticker, ("", ""))[1] or
                                   UNIVERSO.get(ticker, ("", ""))[1] or ""),
             origen)
        )
        con.commit(); con.close()
        # NO invalidamos el cache aquí — el ticker aparecerá en la próxima actualización
        return jsonify({"status": "ok", "ticker": ticker,
                        "msg": f"{ticker} guardado. Aparecerá en la próxima actualización (↺)."})
    except Exception as e:
        return jsonify({"status": "error", "error": str(e)}), 500


@app.route("/api/tickers/remove", methods=["POST"])
def api_tickers_remove():
    global _dash_html
    try:
        data   = flask_req.get_json(force=True) or {}
        ticker = (data.get("ticker") or "").upper().strip()
        if not ticker:
            return jsonify({"status": "error", "error": "ticker vacío"}), 400
        remove_ticker_db(ticker)
        _dash_html = ""
        return jsonify({"status": "ok", "ticker": ticker})
    except Exception as e:
        return jsonify({"status": "error", "error": str(e)}), 500


# ── API: operaciones ──────────────────────────────────────
@app.route("/api/operaciones")
def api_operaciones():
    try:
        return jsonify(get_operaciones())
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/operaciones/import", methods=["POST"])
def api_ops_import():
    global _dash_html
    try:
        ops  = flask_req.get_json(force=True) or []
        tc   = get_tipo_cambio(API_KEY)
        path = "_tmp_ops_import.json"
        with open(path, "w") as f:
            json.dump(ops, f)
        importar_ops_json(path, tc)
        os.remove(path)
        _dash_html = ""
        return jsonify({"status": "ok", "importadas": len(ops)})
    except Exception as e:
        return jsonify({"status": "error", "error": str(e)}), 500


# ── API: config ───────────────────────────────────────────
@app.route("/api/config", methods=["POST"])
def api_config():
    global _dash_html
    try:
        data = flask_req.get_json(force=True) or {}
        with open("finbit_config.json", "w") as f:
            json.dump(data, f, indent=2)
        _dash_html = ""
        return jsonify({"status": "ok"})
    except Exception as e:
        return jsonify({"status": "error", "error": str(e)}), 500


# ── Backup / Restore de la base de datos ─────────────────
@app.route("/api/backup")
def api_backup():
    """Descarga el archivo finbit.db completo como binario."""
    if not os.path.exists(DB_FILE):
        return jsonify({"status": "error", "error": "DB no encontrada"}), 404
    from flask import send_file
    from datetime import timezone, timedelta
    tz_mx  = timezone(timedelta(hours=-6))
    ts     = datetime.now(tz_mx).strftime("%Y%m%d_%H%M")
    nombre = f"finbit_backup_{ts}.db"
    return send_file(
        DB_FILE,
        as_attachment=True,
        download_name=nombre,
        mimetype="application/octet-stream"
    )

@app.route("/api/restore", methods=["POST"])
def api_restore():
    """Restaura la DB desde un archivo .db subido por el usuario."""
    global _dash_html
    if "file" not in flask_req.files:
        return jsonify({"status": "error", "error": "No se envió ningún archivo"}), 400
    f = flask_req.files["file"]
    if not f.filename.endswith(".db"):
        return jsonify({"status": "error", "error": "El archivo debe tener extensión .db"}), 400
    try:
        # Guardar backup de la DB actual antes de reemplazarla
        backup_path = DB_FILE + ".prev"
        if os.path.exists(DB_FILE):
            import shutil
            shutil.copy2(DB_FILE, backup_path)
        f.save(DB_FILE)
        # Invalidar cache del dashboard
        with _dash_lock:
            _dash_html = ""
        return jsonify({"status": "ok", "msg": "DB restaurada. Haz clic en ↺ Actualizar para recargar el dashboard."})
    except Exception as e:
        return jsonify({"status": "error", "error": str(e)}), 500


# ── Health check (Render lo necesita) ────────────────────
@app.route("/health")
def health():
    return jsonify({"status": "ok", "version": "3.2"})


# ── API Debug — diagnóstico rápido desde el browser ──────
@app.route("/api/debug")
def api_debug():
    """Prueba ambas keys de TwelveData y muestra estado del sistema."""
    resultado = {
        "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "n_keys":    len(_TD_KEYS),
        "keys":      [f"...{k[-4:]}" for k in _TD_KEYS],
        "twelvedata_k1": {},
        "twelvedata_k2": {},
    }
    for i, test_key in enumerate(_TD_KEYS[:2]):
        label = f"twelvedata_k{i+1}"
        try:
            r = requests.get(f"{API_BASE}/time_series",
                params={"symbol":"AAPL","interval":"1day","outputsize":"3","apikey":test_key},
                timeout=12)
            d = r.json()
            if "values" in d and d["values"]:
                resultado[label] = {"ok": True, "http": r.status_code,
                                    "velas": len(d["values"]),
                                    "ultimo_close": d["values"][-1].get("close")}
            else:
                resultado[label] = {"ok": False, "http": r.status_code, "respuesta": d}
        except Exception as e:
            resultado[label] = {"ok": False, "excepcion": str(e)}

    return jsonify(resultado)


# ── API Test — prueba todos los tickers del scanner ──────
@app.route("/api/test")
def api_test():
    """Prueba TwelveData para cada ticker del scanner y reporta cuáles funcionan."""
    tickers = list(get_all_scanner_tickers().keys())
    resultados = {}
    for sym in tickers:
        try:
            r = requests.get(f"{API_BASE}/time_series",
                params={"symbol":sym,"interval":"1day","outputsize":"5","apikey":API_KEY},
                timeout=10)
            d = r.json()
            if "values" in d and d["values"]:
                resultados[sym] = {"ok": True, "velas": len(d["values"]),
                                   "precio": d["values"][-1]["close"]}
            else:
                resultados[sym] = {"ok": False, "http": r.status_code,
                                   "error": str(d)[:300]}
        except Exception as e:
            resultados[sym] = {"ok": False, "error": str(e)}
        time.sleep(0.3)  # no saturar
    ok  = sum(1 for v in resultados.values() if v["ok"])
    return jsonify({
        "resumen": f"{ok}/{len(tickers)} tickers OK",
        "tickers": resultados,
        "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
    })



# ═══════════════════════════════════════════════════════════
#   MAIN
# ═══════════════════════════════════════════════════════════
if __name__ == "__main__":
    print("\n" + "="*56)
    print("   FINBIT PRO  v3.2  — servidor web (non-blocking)")
    print("="*56)

    init_db()

    # ── Cargar dashboard anterior si existe (sin créditos de API,
    #    mostramos el último dashboard guardado en lugar de pantalla vacía)
    if os.path.exists(OUTPUT_FILE):
        try:
            with open(OUTPUT_FILE, "r", encoding="utf-8") as f:
                _cached = f.read()
            if len(_cached) > 500:   # archivo válido, no vacío
                with _dash_lock:
                    _dash_html = _cached
                print(f"[server] 📂 Dashboard anterior cargado desde disco ({len(_cached)//1024}KB)")
                print(f"[server]    Puedes usar Finbit mientras se actualiza en background.")
        except Exception as _e:
            print(f"[server] No se pudo cargar dashboard anterior: {_e}")

    # Lanzar build en segundo plano — Flask responde de inmediato
    threading.Thread(target=_construir_con_etapas, daemon=True).start()

    port = int(os.environ.get("PORT", 5000))
    print(f"[server] Puerto: {port}  |  http://localhost:{port}")
    app.run(host="0.0.0.0", port=port, debug=False, use_reloader=False)

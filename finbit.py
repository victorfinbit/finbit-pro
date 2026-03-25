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

import sqlite3, requests, json, os, webbrowser, time
import pandas as pd
from datetime import datetime, date

# ═══════════════════════════════════════════════════════════
#   ⚙️  CONFIGURACIÓN
# ═══════════════════════════════════════════════════════════
API_KEY          = "2431ce60befa48bebfdaa7fcf3c864e4"   # ← twelvedata.com gratis

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

# Tickers por defecto del scanner (se complementan con los de la DB)
SCANNER_TICKERS = {
    "SOXL":("SOXL","NYSE"), "TQQQ":("TQQQ","NASDAQ"),
    "NVDA":("NVDA","NASDAQ"),   
    "TSLA":("TSLA","NASDAQ"),   "AAPL":("AAPL","NASDAQ"),
    "META":("META","NASDAQ"),   "PLTR":("PLTR","NYSE"),
    "PYPL":("PYPL","NASDAQ"),   "NFLX":("NFLX","NASDAQ"),
    "NKE":("NKE","NYSE"),      
}

# Universo para el radar automático (~120 acciones, análisis 1D)
UNIVERSO = {
    "NVDA":("NVDA","NASDAQ"), "SOXL":("SOXL","NYSE"),"MSFT":("MSFT","NASDAQ"),"GOOGL":("GOOGL","NASDAQ"),
    "META":("META","NASDAQ"),"AAPL":("AAPL","NASDAQ"),"AMZN":("AMZN","NASDAQ"),
    "PLTR":("PLTR","NYSE"),"TQQQ":("TQQQ","NASDAQ"),"SPXL":("SPXL","NYSEARCA"),
    "PYPL":("PYPL","NASDAQ"),"UBER":("UBER","NYSE"),"ABNB":("ABNB","NASDAQ"),
    "NFLX":("NFLX","NASDAQ"),"DIS":("DIS","NYSE"),"NKE":("NKE","NYSE"),
}
# ═══════════════════════════════════════════════════════════


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
    con.commit(); con.close()

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
    """Agrega un ticker al scanner personalizado."""
    con = sqlite3.connect(DB_FILE)
    try:
        con.execute("INSERT OR REPLACE INTO tickers (ticker, exchange, origen, activo) VALUES(?,?,?,1)",
                    (ticker.upper().strip(), exchange.upper().strip(), origen))
        con.commit()
    except Exception as e:
        print(f"  Error agregando ticker: {e}")
    con.close()

def remove_ticker_db(ticker: str):
    """Desactiva un ticker del scanner."""
    con = sqlite3.connect(DB_FILE)
    con.execute("UPDATE tickers SET activo=0 WHERE ticker=?", (ticker.upper(),))
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


# ── API DE DATOS ──────────────────────────────────────────
API_BASE = "https://api.twelvedata.com"

def api_timeseries(symbol: str, interval: str, outputsize: int = 200,
                   exchange: str = "") -> list | None:
    if API_KEY in ("TU_KEY_AQUI", ""): return None
    params = {"symbol":symbol,"interval":interval,"outputsize":outputsize,
              "apikey":API_KEY,"order":"ASC"}
    if exchange: params["exchange"] = exchange
    try:
        r = requests.get(f"{API_BASE}/time_series", params=params, timeout=20)
        d = r.json()
        if d.get("status") == "error" or "values" not in d:
            print(f"    API error ({symbol} {interval}): {d.get('message','')}")
            return None
        return d["values"]
    except Exception as e:
        print(f"    API exception ({symbol}): {e}"); return None

def ohlcv_to_close(v): return [float(x["close"]) for x in v]
def ohlcv_to_volume(v): return [float(x.get("volume",0)) for x in v]


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

def analizar_tf(closes, volumes, tf_label, capital, riesgo_pct, rr_min,
                titulos_en_cartera=0.0, tc=17.5, origen="USA") -> dict:
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

    vol_now = float(v.iloc[-1])
    vol_avg = float(v.rolling(min(20,n)).mean().iloc[-1])

    precio   = float(c.iloc[-1])
    soporte  = float(c.rolling(min(20,n)).min().iloc[-1])
    objetivo = float(c.rolling(min(20,n)).max().iloc[-1])
    stop     = soporte * 0.97
    riesgo_acc = precio - stop
    rr_val = (objetivo-precio)/riesgo_acc if riesgo_acc>0 else 0

    emas_ok  = precio>e9>e21>e50
    e200_ok  = precio>e200
    macd_ok  = ml_v>ms_v
    macdh_ok = mh_v>0
    rsi_ok   = 40<=rv<=72
    vol_ok   = vol_now>vol_avg
    rr_ok    = rr_val>=rr_min
    sop_ok   = precio>soporte

    criterios = {
        "emas":   {"ok":emas_ok,  "label":"EMAs 9>21>50",   "val":f"{e9:.2f}/{e21:.2f}/{e50:.2f}",
                   "razon":f"Precio {precio:.2f} {'>' if emas_ok else '<'} EMA9 {e9:.2f}"},
        "ema200": {"ok":e200_ok,  "label":"Precio > EMA200","val":f"{e200:.2f}",
                   "razon":f"EMA200 en {e200:.2f}. Precio {((precio-e200)/e200*100):+.1f}% {'encima' if e200_ok else 'DEBAJO'}."},
        "macd":   {"ok":macd_ok,  "label":"MACD alcista",   "val":f"{ml_v:.3f}",
                   "razon":f"MACD {ml_v:.3f} vs señal {ms_v:.3f}. {'Momentum comprador.' if macd_ok else 'Sin momentum.'}"},
        "macd_h": {"ok":macdh_ok, "label":"Histograma >0",  "val":f"{mh_v:+.3f}",
                   "razon":f"Histograma {'positivo.' if macdh_ok else 'negativo.'}"},
        "rsi":    {"ok":rsi_ok,   "label":"RSI 40-72",      "val":f"{rv:.0f}",
                   "razon":("Sobrecomprado >75." if rv>=75 else f"RSI {rv:.0f}: {'alcista.' if rv>=55 else 'neutral.' if rv>=40 else 'debil.'}")},
        "volumen":{"ok":vol_ok,   "label":"Volumen>media",  "val":f"{vol_now/vol_avg:.1f}x" if vol_avg else "—",
                   "razon":f"Vol {vol_now:,.0f} vs media {vol_avg:,.0f}. {'Confirmado.' if vol_ok else 'Sin conviccion.'}"},
        "rr":     {"ok":rr_ok,    "label":f"R:R>={rr_min:.0f}x", "val":f"{rr_val:.1f}x",
                   "razon":f"Stop {stop:.2f} Obj {objetivo:.2f}. R:R {rr_val:.1f}x {'valido.' if rr_ok else 'insuficiente.'}"},
        "soporte":{"ok":sop_ok,   "label":"Sobre soporte",  "val":f"{soporte:.2f}",
                   "razon":f"Soporte en {soporte:.2f}. {'OK.' if sop_ok else 'Roto — señal bajista.'}"},
    }
    score = sum(1 for x in criterios.values() if x["ok"])
    total_criterios = len(criterios)   # 8
    explosion = emas_ok and e200_ok and macd_ok and macdh_ok and 55<=rv<=72 and vol_ok and rr_val>=4.0

    if score>=6 and emas_ok and e200_ok: senal="COMPRAR"
    elif score>=4:                        senal="MANTENER"
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


def analizar_ticker_1d(nombre, symbol, exchange, capital, riesgo_pct, rr_min,
                        titulos_en_cartera=0.0, tc=17.5, origen="USA") -> dict:
    print(f"  {nombre}...", end=" ", flush=True)

    values_1d = api_timeseries(symbol, "1day", 200, exchange)
    time.sleep(0.2)

    if not values_1d:
        print("sin datos")
        return {"nombre":nombre,"symbol":symbol,
                "tf":{"1D":{"tf":"1D","valido":False},"1H":{"tf":"1H","valido":False},"1W":{"tf":"1W","valido":False}},
                "senal":"SIN DATOS","precio_actual":None,"score_global":0,"confluencia":{}}

    closes_1d  = ohlcv_to_close(values_1d)
    volumes_1d = ohlcv_to_volume(values_1d)
    tf_1d = analizar_tf(closes_1d, volumes_1d, "1D", capital, riesgo_pct, rr_min,
                         titulos_en_cartera, tc=tc, origen=origen)

    score_1d = tf_1d.get("score", 0)
    tfs = {"1D": tf_1d, "1H": {"tf":"1H","valido":False}, "1W": {"tf":"1W","valido":False}}

    if score_1d >= 5:
        vals_1h = api_timeseries(symbol, "1h", 168, exchange)
        time.sleep(0.2)
        if vals_1h:
            tfs["1H"] = analizar_tf(ohlcv_to_close(vals_1h), ohlcv_to_volume(vals_1h), "1H",
                                     capital, riesgo_pct, rr_min, titulos_en_cartera, tc=tc, origen=origen)
        vals_1w = api_timeseries(symbol, "1week", 52, exchange)
        time.sleep(0.2)
        if vals_1w:
            tfs["1W"] = analizar_tf(ohlcv_to_close(vals_1w), ohlcv_to_volume(vals_1w), "1W",
                                     capital, riesgo_pct, rr_min, titulos_en_cartera, tc=tc, origen=origen)

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
            "precio_actual":precio_actual,"score_global":score_global,"confluencia":confluencia}


# ── ANÁLISIS PORTAFOLIO ───────────────────────────────────
def analizar_portafolio(tc, capital, riesgo_pct, rr_min):

    posiciones = get_portafolio()

    resultados = []

    for pos in posiciones:

        ticker = pos["ticker"]

        tickers_db = get_tickers_db()

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


def correr_scanner(tc, capital, riesgo_pct, rr_min, tickers_extra: dict | None = None):
    """
    Scanner: analiza SCANNER_TICKERS + tickers de la DB + hasta 5 tickers extra.
    tickers_extra: dict {nombre: (symbol, exchange)} pasado desde la UI
    """
    port_map = {p["ticker"]:p["titulos"] for p in get_portafolio()}
    # Combinar: defaults + DB + extra (máx 5 del extra para no gastar créditos)
    tickers_db = get_tickers_db()
    combinados = {**SCANNER_TICKERS, **tickers_db}
    if tickers_extra:
        for k,v in list(tickers_extra.items())[:5]:
            combinados[k] = v
    resultados = []
    for nombre,(symbol,exchange) in combinados.items():
        tit = port_map.get(nombre, 0.0)
        an  = analizar_ticker_1d(nombre, symbol, exchange, capital, riesgo_pct, rr_min,
                                  tit, tc=tc, origen="USA")
        tf_1d = an["tf"].get("1D",{})
        if not tf_1d.get("valido"): continue

        c     = {k:v["ok"] for k,v in tf_1d["criterios"].items()}
        score = tf_1d["score"]
        explosion = tf_1d.get("explosion",False)

        # Estado: TODAS aparecen, no se filtra ninguna
        if explosion:
            estado = "ROCKET"
        elif c.get("emas") and c.get("ema200") and c.get("macd") and c.get("rr") and c.get("soporte"):
            estado = "BUY"
        elif score >= 5:
            estado = "WATCH"
        elif score <= 2:
            estado = "SHORT"
        else:
            estado = "SKIP"

        p_usd = tf_1d["precio"]
        resultados.append({
            "nombre":nombre,"estado":estado,
            "precio_usd":p_usd,"precio_mxn":p_usd*tc,
            "entrada_mxn":tf_1d.get("entrada_sugerida",p_usd)*tc,
            "stop_mxn":tf_1d.get("stop",0)*tc,"obj_mxn":tf_1d.get("objetivo",0)*tc,
            "rsi":tf_1d["rsi"],"rr":tf_1d["rr"],"macd_ok":tf_1d["macd_alcista"],
            "ema200_ok":c.get("ema200",False),"score":score,
            "total_criterios":tf_1d.get("total_criterios",8),
            "criterios":tf_1d["criterios"],"sizing":tf_1d.get("sizing",{}),
            "tfs":an["tf"],"confluencia":an["confluencia"],"titulos_cartera":tit,
        })

    orden = {"ROCKET":0,"BUY":1,"WATCH":2,"SKIP":3,"SHORT":4}
    resultados.sort(key=lambda x:(orden.get(x["estado"],9),-x["rr"]))
    return resultados


def radar_masivo(tc, capital, riesgo_pct, rr_min):
    """Radar: analiza UNIVERSO + tickers de la DB en modo rápido 1D."""
    port_map  = {p["ticker"]:p["titulos"] for p in get_portafolio()}
    tickers_db = get_tickers_db()
    universo_completo = {**UNIVERSO, **tickers_db}  # DB tiene prioridad
    resultados= []
    total     = len(universo_completo)
    print(f"  Radar: analizando {total} acciones en 1D...")

    for i,(nombre,(symbol,exchange)) in enumerate(universo_completo.items()):
        if i%10==0: print(f"  [{i}/{total}]...", end="\r", flush=True)
        values = api_timeseries(symbol, "1day", 200, exchange)
        time.sleep(0.15)
        if not values or len(values)<30: continue

        closes  = ohlcv_to_close(values)
        volumes = ohlcv_to_volume(values)
        tit     = port_map.get(nombre, 0.0)
        tf = analizar_tf(closes, volumes, "1D", capital, riesgo_pct, rr_min,
                          tit, tc=tc, origen="USA")
        if not tf["valido"]: continue

        score = tf["score"]
        total_c = tf.get("total_criterios", 8)
        c     = {k:v["ok"] for k,v in tf["criterios"].items()}
        exp   = tf.get("explosion",False)
        precio= tf["precio"]

        # Estado para TODOS los activos, ninguno se omite
        if exp:
            estado = "ROCKET"
        elif c.get("emas") and c.get("ema200") and c.get("macd") and c.get("rr"):
            estado = "BUY"
        elif score >= 5:
            estado = "WATCH"
        elif score <= 2:
            estado = "SHORT"
        else:
            estado = "SKIP"

        objetivo = tf["objetivo"]; stop = tf["stop"]
        pot_alza = ((objetivo-precio)/precio*100) if precio else 0

        resultados.append({
            "nombre":nombre,"estado":estado,
            "precio_usd":precio,"precio_mxn":precio*tc,
            "entrada_mxn":tf.get("entrada_sugerida",precio)*tc,
            "stop_mxn":stop*tc,"obj_mxn":objetivo*tc,
            "rsi":tf["rsi"],"rr":tf["rr"],"macd_ok":tf["macd_alcista"],
            "ema200_ok":c.get("ema200",False),"score":score,"total_criterios":total_c,
            "pot_alza":pot_alza,"criterios":tf["criterios"],"sizing":tf.get("sizing",{}),
            "titulos_cartera":tit,
        })

    print(f"  Radar completo: {len(resultados)} acciones relevantes de {total} analizadas")
    orden = {"ROCKET":0,"BUY":1,"WATCH":2,"SKIP":3,"SHORT":4}
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
    m={"ROCKET":("b-rocket","🚀 Explosión"),"BUY":("b-buy","↑ Compra"),
       "WATCH":("b-hold","👁 Vigilar"),"SKIP":("b-none","Esperar"),"SHORT":("b-sell","↓ Bajista")}
    c,t=m.get(s,("b-none",s)); return badge(c,t)
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

        detail=(f'<div class="detail-panel">'
                f'{render_conf(conf) if conf else ""}'
                f'{alertas_h}'
                f'<div class="dp-grid">'
                # Col 1: semáforo
                f'<div class="dp-sec"><div class="dp-sec-t">Semáforo indicadores (1D)</div>'
                f'{render_criterios(criterios) if criterios else "<p class=hint>Sin datos API</p>"}</div>'
                # Col 2: TFs + score con señal + sizing
                f'<div style="display:flex;flex-direction:column;gap:8px">'
                f'<div class="dp-sec"><div class="dp-sec-t">Timeframes</div>{render_tf_chips(tfs)}</div>'
                f'<div class="dp-sec"><div class="dp-sec-t">Score técnico</div>{score_block}</div>'
                f'<div class="dp-sec"><div class="dp-sec-t">Tamaño de posición adicional</div>'
                f'{render_sizing(sz,pos["titulos"],tc,pos["origen"])}</div></div>'
                # Col 3: niveles + rec
                f'<div style="display:flex;flex-direction:column;gap:8px">'
                f'<div class="dp-sec"><div class="dp-sec-t">Niveles clave MXN</div>'
                f'{render_niveles(tf_1d if tf_1d.get("valido") else {},tc,pos["origen"])}</div>'
                f'{render_rec(senal,tf_1d,pos.get("entrada_mxn"),pos.get("stop_mxn"),pos.get("obj_mxn"))}'
                f'</div></div></div>')

        h+=(f'<tr class="datarow" onclick="toggle(\'{rid}\')">'
            f'<td><strong>{pos["ticker"]}</strong><br><span class="hint">{pos["origen"]} · {pos["mercado"]}</span></td>'
            f'<td class="num">{pos["titulos"]}</td>'
            f'<td class="num">{fmt(pos["cto_prom_mxn"])}</td>'
            f'<td class="num">{precio_cell}</td>'
            f'<td class="num">{fmt(pos["costo_total"])}</td>'
            f'<td class="num {pl_cls}">{fmt(pos["pl_mxn"])}</td>'
            f'<td class="num {pl_cls}">{pos["pl_pct"]:+.1f}%</td>'
            f'<td>{badge_senal(senal)}</td>'
            f'<td>{gbm_cell(pos.get("entrada_mxn"),pos.get("stop_mxn"),pos.get("obj_mxn"))}</td>'
            f'</tr>'
            f'<tr class="detail" id="{rid}"><td colspan="9" style="padding:0">{detail}</td></tr>')
    return h


def render_scan_rows(scanner, tc):
    h=""
    for r in scanner:
        rid=f"sc_{r['nombre']}"
        rr_col=("var(--green)" if r["rr"]>=3 else "var(--yellow)" if r["rr"]>=2 else "var(--red)")
        rr_pct=min(r["rr"]/6,1)*100
        crit=r.get("criterios",{}); sz=r.get("sizing",{}); conf=r.get("confluencia",{})
        en_cartera=r.get("titulos_cartera",0)
        score=r.get("score",0); total_c=r.get("total_criterios",8)
        senal_1d = r.get("tfs",{}).get("1D",{}).get("senal","SIN DATOS")
        cartera_badge=(f'<br><span class="badge b-hold" style="font-size:9px">★ {en_cartera} en cartera</span>'
                       if en_cartera>0 else "")

        score_block = render_score_badge(score, total_c, senal_1d)

        detail=(f'<div class="detail-panel">'
                f'{render_conf(conf) if conf else ""}'
                f'<div class="dp-grid">'
                f'<div class="dp-sec"><div class="dp-sec-t">Desglose indicadores 1D</div>'
                f'{render_criterios(crit) if crit else "<p class=hint>Sin datos</p>"}</div>'
                f'<div style="display:flex;flex-direction:column;gap:8px">'
                f'<div class="dp-sec"><div class="dp-sec-t">Timeframes (1H/1W solo si score≥5)</div>'
                f'{render_tf_chips(r.get("tfs",{}))}</div>'
                f'<div class="dp-sec"><div class="dp-sec-t">Score técnico</div>{score_block}</div>'
                f'<div class="dp-sec"><div class="dp-sec-t">Tamaño de posición sugerido</div>'
                f'{render_sizing(sz,en_cartera,tc,"USA")}</div></div>'
                f'<div class="dp-sec"><div class="dp-sec-t">Niveles clave MXN</div>'
                f'<div class="sz-grid" style="font-size:12px">'
                f'<div class="pl-row"><span>Precio actual</span><span class="num">{fmt(r["precio_mxn"])}</span></div>'
                f'<div class="pl-row"><span>Entrada EMA9</span><span class="num" style="color:var(--green)">{fmt(r["entrada_mxn"])}</span></div>'
                f'<div class="pl-row"><span>Stop</span><span class="num" style="color:var(--red)">{fmt(r["stop_mxn"])}</span></div>'
                f'<div class="pl-row"><span>Objetivo</span><span class="num" style="color:var(--green)">{fmt(r["obj_mxn"])}</span></div>'
                f'<div class="pl-row"><span>RSI</span><span class="num" style="color:{rsi_col(r["rsi"])}">{r["rsi"]:.0f}</span></div>'
                f'<div class="pl-row"><span>R:R</span><span class="num" style="color:{rr_col}">{r["rr"]:.1f}x</span></div>'
                f'</div></div></div></div>')

        h+=(f'<tr class="datarow" onclick="toggle(\'{rid}\')">'
            f'<td><strong>{r["nombre"]}</strong>{cartera_badge}</td>'
            f'<td>{badge_estado(r["estado"])}</td>'
            f'<td class="num">{fmt(r["precio_mxn"])}<br><span class="hint">USD {r["precio_usd"]:.2f}</span></td>'
            f'<td class="num" style="color:var(--green)">{fmt(r["entrada_mxn"])}</td>'
            f'<td><div class="rrw"><div class="rrb"><div class="rrf" style="width:{rr_pct:.0f}%;background:{rr_col}"></div></div>'
            f'<span style="color:{rr_col};font-family:var(--mono);font-size:11px">{r["rr"]:.1f}x</span></div></td>'
            f'<td style="color:{rsi_col(r["rsi"])};font-weight:600;font-family:var(--mono)">{r["rsi"]:.0f}</td>'
            f'<td>{"<span style=color:var(--green)>▲</span>" if r["macd_ok"] else "<span style=color:var(--red)>▼</span>"}</td>'
            f'<td>{"<span style=color:var(--green)>↑</span>" if r["ema200_ok"] else "<span style=color:var(--red)>↓</span>"}</td>'
            f'<td>{gbm_cell(r["entrada_mxn"],r["stop_mxn"],r["obj_mxn"])}</td>'
            f'<td><span style="font-family:var(--mono);font-size:12px;color:{"var(--green)" if score>=6 else "var(--yellow)" if score>=4 else "var(--red)"};font-weight:600">{score}/{total_c}</span></td>'
            f'</tr>'
            f'<tr class="detail" id="{rid}"><td colspan="10" style="padding:0">{detail}</td></tr>')
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
        return '<tr><td colspan="11" style="text-align:center;color:var(--muted);padding:28px;font-size:12px">Sin datos — activa el radar desde el botón de arriba</td></tr>'
    h=""
    for r in radar:
        rid=f"rd_{r['nombre']}"
        rr_col=("var(--green)" if r["rr"]>=3 else "var(--yellow)" if r["rr"]>=2 else "var(--red)")
        rr_pct=min(r["rr"]/6,1)*100
        estado=r["estado"]
        badge_map={"ROCKET":("b-rocket","🚀 Explosión"),"BUY":("b-buy","↑ Compra"),
                   "WATCH":("b-hold","👁 Vigilar"),"SHORT":("b-sell","↓ Bajista"),
                   "SKIP":("b-none","— Esperar")}
        bcls,btxt=badge_map.get(estado,("b-none",estado))
        pot_col="var(--green)" if r["pot_alza"]>=10 else "var(--muted)"
        en_cartera=r.get("titulos_cartera",0)
        score=r.get("score",0); total_c=r.get("total_criterios",8)
        cartera_tag=(f'<br><span class="badge b-hold" style="font-size:9px">★ {en_cartera} tít</span>'
                     if en_cartera>0 else "")
        crit=r.get("criterios",{}); sz=r.get("sizing",{})
        senal_est = ("COMPRAR" if estado in ("ROCKET","BUY") else
                     "VENDER" if estado=="SHORT" else "MANTENER")
        score_block = render_score_badge(score, total_c, senal_est)

        detail=(f'<div class="detail-panel">'
                f'<div class="dp-grid">'
                f'<div class="dp-sec"><div class="dp-sec-t">Semáforo indicadores 1D</div>'
                f'{render_criterios(crit) if crit else "<p class=hint>Sin datos</p>"}</div>'
                f'<div style="display:flex;flex-direction:column;gap:8px">'
                f'<div class="dp-sec"><div class="dp-sec-t">Score técnico</div>{score_block}</div>'
                f'<div class="dp-sec"><div class="dp-sec-t">Tamaño de posición sugerido</div>'
                f'{render_sizing(sz,en_cartera,tc,"USA")}</div></div>'
                f'<div class="dp-sec"><div class="dp-sec-t">Niveles clave MXN</div>'
                f'<div class="sz-grid" style="font-size:12px">'
                f'<div class="pl-row"><span>Precio actual</span><span class="num">{fmt(r["precio_mxn"])}</span></div>'
                f'<div class="pl-row"><span>Entrada EMA9</span><span class="num" style="color:var(--green)">{fmt(r["entrada_mxn"])}</span></div>'
                f'<div class="pl-row"><span>Stop</span><span class="num" style="color:var(--red)">{fmt(r["stop_mxn"])}</span></div>'
                f'<div class="pl-row"><span>Objetivo</span><span class="num" style="color:var(--green)">{fmt(r["obj_mxn"])}</span></div>'
                f'<div class="pl-row"><span>Potencial alza</span><span class="num" style="color:var(--green)">{r["pot_alza"]:.1f}%</span></div>'
                f'<div class="pl-row"><span>RSI</span><span class="num" style="color:{rsi_col(r["rsi"])}">{r["rsi"]:.0f}</span></div>'
                f'<div class="pl-row"><span>R:R</span><span class="num" style="color:{rr_col}">{r["rr"]:.1f}x</span></div>'
                f'</div></div></div></div>')

        h+=(f'<tr class="datarow" onclick="toggle(\'{rid}\')">'
            f'<td><strong>{r["nombre"]}</strong>{cartera_tag}</td>'
            f'<td><span class="badge {bcls}">{btxt}</span></td>'
            f'<td class="num">{fmt(r["precio_mxn"])}<br><span class="hint">USD {r["precio_usd"]:.2f}</span></td>'
            f'<td class="num" style="color:var(--green)">{fmt(r["entrada_mxn"])}</td>'
            f'<td class="num" style="color:{pot_col};font-weight:{"600" if r["pot_alza"]>=10 else "400"}">{r["pot_alza"]:+.1f}%</td>'
            f'<td><div class="rrw"><div class="rrb"><div class="rrf" style="width:{rr_pct:.0f}%;background:{rr_col}"></div></div>'
            f'<span style="color:{rr_col};font-family:var(--mono);font-size:11px">{r["rr"]:.1f}x</span></div></td>'
            f'<td style="color:{rsi_col(r["rsi"])};font-weight:600;font-family:var(--mono)">{r["rsi"]:.0f}</td>'
            f'<td>{"<span style=color:var(--green)>▲</span>" if r["macd_ok"] else "<span style=color:var(--red)>▼</span>"}</td>'
            f'<td>{"<span style=color:var(--green)>↑</span>" if r["ema200_ok"] else "<span style=color:var(--red)>↓</span>"}</td>'
            f'<td><span style="font-family:var(--mono);font-size:12px;color:{"var(--green)" if score>=6 else "var(--yellow)" if score>=4 else "var(--red)"};font-weight:600">{score}/{total_c}</span></td>'
            f'<td>{gbm_cell(r["entrada_mxn"],r["stop_mxn"],r["obj_mxn"])}</td>'
            f'</tr>'
            f'<tr class="detail" id="{rid}"><td colspan="11" style="padding:0">{detail}</td></tr>')
    return h


def resumen_hist(ops):
    if not ops:
        return {"inv":0,"vta":0,"pl":0,"n":0,"n_compras":0,"n_ventas":0,
                "n_ops_cerradas":0,"ventas_ganadoras":0,"ventas_perdedoras":0,
                "tasa_acierto":0,"ganancia_promedio":0,"perdida_promedio":0,
                "expectativa":0,"mejor_op":0,"peor_op":0,"por_ticker":{},"por_mes":{}}
    from collections import defaultdict
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
def generar_html(port_data, scan_data, radar_data, ops, tc, capital, riesgo_pct, rr_min):
    ts  = datetime.now().strftime("%d/%m/%Y %H:%M")
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
    } for p in port_data], ensure_ascii=False)

    port_rows  = render_port_rows(port_data, tc)
    scan_rows  = render_scan_rows(scan_data, tc)
    radar_rows = render_radar_rows(radar_data, tc)
    hist_rows  = render_hist_rows(ops)

    n_radar=len(radar_data)
    n_rocket=sum(1 for r in radar_data if r["estado"]=="ROCKET")
    n_buy   =sum(1 for r in radar_data if r["estado"]=="BUY")
    n_watch =sum(1 for r in radar_data if r["estado"]=="WATCH")
    n_short =sum(1 for r in radar_data if r["estado"]=="SHORT")
    n_skip  =sum(1 for r in radar_data if r["estado"]=="SKIP")
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
footer{{color:var(--hint);font-size:10px;text-align:center;padding:18px 0;border-top:1px solid var(--brd);line-height:2}}
@media(max-width:720px){{thead th:nth-child(n+5){{display:none}}td:nth-child(n+5){{display:none}}}}
</style></head><body>

<div class="topbar"><div class="topbar-inner">
  <div class="logo">fin<em>bit</em> <span style="font-size:11px;color:var(--muted);font-weight:400">pro v3.2</span></div>
  <div class="topbar-right">
    <span class="tc-chip">USD/MXN <strong>${tc:.4f}</strong></span>
    <div class="cfg-row">
      <span class="cfg-lbl">Capital</span>
      <input id="cfg_capital" class="cfg-input" type="number" value="{capital}" min="100" style="width:90px">
      <span class="cfg-lbl">Riesgo%</span>
      <input id="cfg_riesgo" class="cfg-input" type="number" value="{riesgo_pct*100:.0f}" min="0.5" max="10" step="0.5" style="width:60px">
      <span class="cfg-lbl">R:R mín</span>
      <input id="cfg_rr" class="cfg-input" type="number" value="{rr_min}" min="1" max="10" step="0.5" style="width:55px">
      <button class="cfg-btn" onclick="saveConfig()">Guardar</button>
    </div>
    <span style="font-size:11px;color:var(--muted)">{ts}</span>
  </div>
</div></div>

<div class="nav"><div class="nav-inner">
  <button class="nb active" onclick="showTab('portafolio',this)">Mi portafolio</button>
  <button class="nb" onclick="showTab('registrar',this)">Registrar operación</button>
  <button class="nb" onclick="showTab('historial',this)">Historial</button>
  <button class="nb" onclick="showTab('scanner',this)">Scanner</button>
  <button class="nb" onclick="showTab('radar',this)">🔭 Radar automático</button>
  <button class="nb" onclick="showTab('buscador',this)">🔍 Buscador</button>
</div></div>

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
<div id="tab-portafolio" class="tab active">
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
        <th>Valor MXN</th><th>P&L MXN</th><th>% Var</th><th>Señal</th><th style="color:var(--green)">Orden GBM 🎯</th></tr></thead>
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
<div id="tab-scanner" class="tab">
  <div style="padding:20px 0 14px">
    <h2 style="font-size:20px;font-weight:600;letter-spacing:-.4px">Scanner de mercado</h2>
    <p class="hint">Muestra <strong>todas</strong> las acciones configuradas · base 1D · abre 1H+1W si score≥5 · TC ${tc:.4f} · {ts}</p>
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
          <option value="Vigilar">👁 Vigilar</option>
          <option value="Esperar">Esperar</option>
          <option value="Bajista">↓ Bajista</option>
        </select>
      </div>
    </div>
    <div style="overflow-x:auto"><table id="scan_table">
      <thead><tr><th>Ticker</th><th>Estado</th><th>Precio MXN</th>
        <th style="color:var(--green)">Entrada EMA9</th><th>R:R</th><th>RSI</th>
        <th>MACD</th><th>EMA200</th><th style="color:var(--green)">Orden GBM 🎯</th><th>Score</th></tr></thead>
      <tbody id="scan_tbody">{scan_rows or '<tr><td colspan="10" style="text-align:center;color:var(--muted);padding:24px;font-size:12px">Sin datos — agrega tu API key</td></tr>'}</tbody>
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
  <div style="display:flex;gap:8px;margin-bottom:14px;flex-wrap:wrap;align-items:center">
    <span class="hint">Filtrar:</span>
    <button class="filter-btn active" onclick="filtrarRadar('ALL',this)">Todos</button>
    <button class="filter-btn" onclick="filtrarRadar('alza',this)">↑ Alza</button>
    <button class="filter-btn" onclick="filtrarRadar('baja',this)">↓ Bajistas</button>
    <button class="filter-btn" onclick="filtrarRadar('10pct',this)">🔥 +10% potencial</button>
    <button class="filter-btn" onclick="filtrarRadar('rocket',this)">🚀 Explosiones</button>
    <button class="filter-btn" onclick="filtrarRadar('skip',this)">— Esperar</button>
    <input type="text" id="radar_search" placeholder="Buscar ticker..."
      style="padding:5px 10px;border:1px solid var(--brd);border-radius:6px;font-size:12px;background:var(--surface);margin-left:auto;width:130px"
      oninput="buscarRadar()">
  </div>
  <div class="tw">
    <div class="tw-head"><span id="radar_count">Mostrando {n_radar} acciones</span><span class="hint">↓ Clic en fila para análisis completo</span></div>
    <div style="overflow-x:auto"><table id="radar_table">
      <thead><tr><th>Ticker</th><th>Estado</th><th>Precio MXN</th>
        <th style="color:var(--green)">Entrada EMA9</th><th style="color:var(--green)">Potencial</th>
        <th>R:R</th><th>RSI</th><th>MACD</th><th>EMA200</th><th>Score</th>
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
#   MAIN
# ═══════════════════════════════════════════════════════════
if __name__ == "__main__":
    print("\n" + "="*56)
    print("   FINBIT PRO  v3.2")
    print("="*56)

    print("\n[1/6] Cargando configuración...")
    cfg=cargar_config()
    capital=cfg["capital"]; riesgo_pct=cfg["riesgo"]; rr_min=cfg["rr_min"]
    print(f"      Capital: ${capital:,.0f} MXN · Riesgo: {riesgo_pct*100:.1f}% · R:R mín: {rr_min}")

    print("[2/6] Base de datos..."); init_db()

    print("[3/6] Tipo de cambio...")
    tc=get_tipo_cambio(API_KEY)
    print(f"      USD/MXN = ${tc:.4f}")

    seed_portafolio(tc)

    if os.path.exists("finbit_ops.json"):
        print("[4/6] Importando operaciones y actualizando portafolio...")
        importar_ops_json("finbit_ops.json",tc)
    else:
        print("[4/6] (finbit_ops.json no encontrado)")

    procesar_borrados()

    # Cargar tickers personalizados del buscador
    tickers_extra = {}
    if os.path.exists("finbit_tickers.json"):
        try:
            with open("finbit_tickers.json") as f:
                raw = json.load(f)
            for t, ex in raw.items():
                tickers_extra[t.upper()] = (t.upper(), ex or "")
            print(f"[4b] Tickers personalizados cargados: {list(tickers_extra.keys())}")
        except Exception as e:
            print(f"  finbit_tickers.json error: {e}")

    if API_KEY not in ("TU_KEY_AQUI", ""):
        print("[5/6] Analizando portafolio...")
        port_data=analizar_portafolio(tc,capital,riesgo_pct,rr_min)
        print("[6/6] Scanner (todas las acciones configuradas)...")
        scan_data=correr_scanner(tc,capital,riesgo_pct,rr_min,tickers_extra)
        print("[7/7] Radar automático (universo completo)...")
        radar_data=radar_masivo(tc,capital,riesgo_pct,rr_min)
    else:
        print("[5/6] Sin API key — sin análisis técnico")
        print("      Regístrate gratis en twelvedata.com")
        port_data=[]
        for p in get_portafolio():
            port_data.append({**p,"analisis":None,"precio_actual_usd":None,"precio_actual_mxn":None,
                "valor_mxn":p["cto_prom_mxn"]*p["titulos"],"costo_total":p["cto_prom_mxn"]*p["titulos"],
                "pl_mxn":0,"pl_pct":0,"alertas":[],"entrada_mxn":None,"stop_mxn":None,"obj_mxn":None})
        scan_data=[]; radar_data=[]

    ops=get_operaciones()
    html=generar_html(port_data,scan_data,radar_data,ops,tc,capital,riesgo_pct,rr_min)
    with open(OUTPUT_FILE,"w",encoding="utf-8") as f: f.write(html)

    print(f"\n{'='*56}")
    print(f"  Dashboard:    {OUTPUT_FILE}")
    print(f"  Base datos:   {DB_FILE}")
    print(f"  TC:           ${tc:.4f}")
    print(f"  Posiciones:   {len(port_data)}")
    print(f"  Scanner:      {len(scan_data)} acciones")
    print(f"  Radar:        {len(radar_data)} señales relevantes")
    print(f"  Operaciones:  {len(ops)}")
    print(f"{'='*56}\n")
    print("  TIPS:")
    print("  · Exportar ops del browser → F12 → copy(localStorage.getItem('finbit_ops'))")
    print("    → pega en finbit_ops.json → corre el script")
    print("  · Scanner y radar muestran TODAS las acciones, filtra desde la UI")
    print("  · Ideal correr entre 8-9 AM antes de apertura\n")

    # webbrowser.open("file://" + os.path.abspath(OUTPUT_FILE))
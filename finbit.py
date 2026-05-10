"""
╔══════════════════════════════════════════════════════════════╗
║          FINBIT PRO  v3.2  — build 15/04/2026               ║
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
from datetime import datetime, date, timedelta
from collections import defaultdict
from flask import Flask, Response, request as flask_req, jsonify

# ═══════════════════════════════════════════════════════════
# ═══════════════════════════════════════════════════════════
API_KEY     = os.environ.get("TWELVEDATA_API_KEY",  "2431ce60befa48bebfdaa7fcf3c864e4")
API_KEY_2   = os.environ.get("TWELVEDATA_API_KEY_2","3c4971fd74eb4363bcbf877edb1616b4")
API_KEY_3   = os.environ.get("TWELVEDATA_API_KEY_3","0ce51f56198e4184841be0c52565b847")
API_KEY_4   = os.environ.get("TWELVEDATA_API_KEY_4","cca9055d9d654e479dd68b14a2bacd34")

_TD_KEYS    = [k for k in [API_KEY_4, API_KEY_3, API_KEY, API_KEY_2] if k not in ("","TU_KEY_AQUI")]

TELEGRAM_TOKEN   = os.environ.get("TELEGRAM_TOKEN", "")
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID", "")
TELEGRAM_ACTIVO  = True

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

_BASE_DIR   = os.path.dirname(os.path.abspath(__file__))
DB_FILE     = os.path.join(_BASE_DIR, "finbit.db")
OUTPUT_FILE = os.path.join(_BASE_DIR, "dashboard.html")

# ── Sync automático de DB con GitHub ─────────────────────
GITHUB_TOKEN = os.environ.get("GITHUB_TOKEN", "")
GITHUB_REPO  = "victorfinbit/finbit-pro"   # tu repo
GITHUB_PATH  = "finbit.db"                  # ruta del archivo en el repo
GITHUB_BRANCH= "main"
GITHUB_BRANCH_DB = "db-backup"  # rama separada para DB — Render no la monitorea

PORTAFOLIO_INICIAL = [
   
]

# ── Exchanges vacíos "" = auto-detect TwelveData (más estable) ──
SCANNER_TICKERS = {
    "SOXL":("SOXL",""),
    "TSLA":("TSLA",""),
    "PYPL":("PYPL",""),
}

_SEMIS_CACHE_TICKERS = {
    "SMH":  ("SMH",  ""),
    "SOXX": ("SOXX", ""),
    "QQQ":  ("QQQ",  ""),
    "AMD":  ("AMD",  ""),
    "ASML": ("ASML", ""),
    "AVGO": ("AVGO", ""),
    "MU":   ("MU",   ""),
    "QCOM": ("QCOM", ""),
    "ARM":  ("ARM",  ""),
    "INTC": ("INTC", ""),
}

_UNIVERSO_EXTRA = {}
UNIVERSO = {**SCANNER_TICKERS, **_UNIVERSO_EXTRA}

# ── BASE DE DATOS ─────────────────────────────────────────
# ── SYNC DB ↔ GITHUB ─────────────────────────────────────
def _gh_headers():
    return {"Authorization": f"token {GITHUB_TOKEN}",
            "Accept": "application/vnd.github.v3+json"}

def db_restore_from_github():
    """Al arrancar: descarga finbit.db desde GitHub si existe."""
    if not GITHUB_TOKEN:
        print("[github] Sin GITHUB_TOKEN — sync desactivado")
        return
    try:
        url = f"https://api.github.com/repos/{GITHUB_REPO}/contents/{GITHUB_PATH}"
        r = requests.get(url + f"?ref={GITHUB_BRANCH_DB}", headers=_gh_headers(), timeout=15)
        if r.status_code == 404:
            print("[github] DB no encontrada en db-backup — intentando main...")
            r = requests.get(url, headers=_gh_headers(), timeout=15)
        if r.status_code == 200:
            import base64
            data = r.json()
            content = base64.b64decode(data["content"])
            with open(DB_FILE, "wb") as f:
                f.write(content)
            print(f"[github] ✅ DB restaurada desde GitHub ({len(content)//1024}KB)")
        elif r.status_code == 404:
            print("[github] DB no encontrada en GitHub — se usará la DB local (primera vez)")
        else:
            print(f"[github] ⚠️ Error al restaurar: {r.status_code}")
    except Exception as e:
        print(f"[github] ⚠️ Error de red al restaurar: {e}")

_backup_lock = threading.Lock()

def db_backup_to_github():
    """Sube finbit.db a GitHub (crea o actualiza el archivo).
    Usa lock para evitar backups simultáneos que causan error 409.
    Reintenta una vez si hay conflicto de SHA."""
    if not GITHUB_TOKEN:
        return
    if not os.path.exists(DB_FILE):
        return
    if not _backup_lock.acquire(blocking=False):
        print("[github] ⏭️  Backup ya en curso — omitiendo")
        return
    try:
        import base64
        with open(DB_FILE, "rb") as f:
            content_b64 = base64.b64encode(f.read()).decode()

        url = f"https://api.github.com/repos/{GITHUB_REPO}/contents/{GITHUB_PATH}"

        for intento in range(2):  # máximo 2 intentos si hay conflicto de SHA
            sha = None
            r = requests.get(url + f"?ref={GITHUB_BRANCH_DB}", headers=_gh_headers(), timeout=10)
            if r.status_code == 200:
                sha = r.json().get("sha")

            payload = {
                "message": f"finbit.db auto-backup {datetime.now().strftime('%Y-%m-%d %H:%M')} [skip render]",
                "content": content_b64,
                "branch":  GITHUB_BRANCH_DB,
            }
            if sha:
                payload["sha"] = sha

            r = requests.put(url, headers=_gh_headers(), json=payload, timeout=20)
            if r.status_code in (200, 201):
                print(f"[github] ✅ DB respaldada en GitHub")
                return
            elif r.status_code == 409:
                print(f"[github] ⚠️ Conflicto de SHA (intento {intento+1}/2) — reintentando...")
                time.sleep(2)
                continue
            else:
                print(f"[github] ⚠️ Error al respaldar: {r.status_code} {r.text[:100]}")
                return
    except Exception as e:
        print(f"[github] ⚠️ Error de red al respaldar: {e}")
    finally:
        _backup_lock.release()

def _loop_backup_github():
    """Hilo que respalda la DB en GitHub cada 60 minutos y resetea keys a medianoche."""
    time.sleep(300)   # esperar 5 min después de arrancar
    ultimo_dia = datetime.now().day
    ultimo_reset_top    = -1   # hora del último reset del top diario
    ultima_semana_reset = ""   # semana del último reset semanal
    while True:
        db_backup_to_github()
        dia_actual = datetime.now().day
        if dia_actual != ultimo_dia:
            global _KEYS_AGOTADAS
            _KEYS_AGOTADAS = set()
            ultimo_dia = dia_actual
            print("[keys] ✅ Nuevo día — créditos de TwelveData renovados, keys reseteadas")
        hora_cdmx = _hora_cdmx()
        time.sleep(3600)  # cada hora

# ── ALERTAS TELEGRAM — monitoreo automático ────────────────
_alertas_enviadas: dict = {}  # {ticker: {"ganga": timestamp, "pre4": timestamp, "pre5": timestamp}}

def _en_horario_mercado() -> bool:
    """Verifica si estamos en horario de mercado USA (9:30-16:00 ET, lunes-viernes).
    Usa UTC-4 (EDT activo abril-noviembre)."""
    ahora_utc = datetime.utcnow()
    minutos_utc = ahora_utc.hour * 60 + ahora_utc.minute
    apertura_utc = 13 * 60 + 30
    cierre_utc   = 20 * 60
    dia = ahora_utc.weekday()  # 0=lunes, 6=domingo
    if dia >= 5:
        return False
    en_horario = apertura_utc <= minutos_utc <= cierre_utc
    print(f"[alertas] ⏰ UTC {ahora_utc.strftime('%H:%M')} | dia={dia} | en_mercado={en_horario}")
    return en_horario

def _puede_enviar_alerta(ticker: str, tipo: str, minutos: int = 120) -> bool:
    """Evita spam — solo manda la misma alerta cada X minutos."""
    global _alertas_enviadas
    key = f"{ticker}:{tipo}"
    ahora = time.time()
    ultimo = _alertas_enviadas.get(key, 0)
    if ahora - ultimo > minutos * 60:
        _alertas_enviadas[key] = ahora
        return True
    return False

def _formatear_alerta_ganga(r: dict) -> str:
    """Formatea mensaje Telegram para badge Ganga."""
    nombre = r.get("nombre", "")
    precio = r.get("precio_mxn", 0)
    rr     = r.get("rr", 0)
    rsi    = r.get("rsi", 0)
    score  = r.get("score_ajustado", r.get("score", 0))
    total  = r.get("total_criterios", 11)
    stop   = r.get("stop_mxn", 0)
    obj    = r.get("obj_mxn", 0)
    fuente = r.get("objetivo_fuente", "ATR")
    return (
        f"🟡 <b>GANGA DETECTADA — {nombre}</b>\n"
        f"━━━━━━━━━━━━━━━\n"
        f"💰 Precio:  <b>${precio:,.2f} MXN</b>\n"
        f"📊 RSI:     {rsi:.0f}\n"
        f"⭐ Score:   {score}/{total}\n"
        f"🛑 Stop:    ${stop:,.2f} MXN\n"
        f"🎯 Obj:     ${obj:,.2f} MXN ({fuente})\n"
        f"📈 R:R:     <b>{rr:.1f}x</b>\n"
        f"━━━━━━━━━━━━━━━\n"
        f"⏰ {datetime.now().strftime('%H:%M')} ET"
    )

def _formatear_alerta_prebreakout(r: dict, nivel: str) -> str:
    """Formatea mensaje Telegram para Pre-breakout 4/5."""
    nombre = r.get("nombre", "")
    precio = r.get("precio_mxn", 0)
    rr     = r.get("rr", 0)
    rsi    = r.get("rsi", 0)
    score  = r.get("score_ajustado", r.get("score", 0))
    total  = r.get("total_criterios", 11)
    stop   = r.get("stop_mxn", 0)
    obj    = r.get("obj_mxn", 0)
    fuente = r.get("objetivo_fuente", "ATR")
    emoji  = "🟠" if nivel == "4/5" else "🟢"
    return (
        f"{emoji} <b>PRE-BREAKOUT {nivel} — {nombre}</b>\n"
        f"━━━━━━━━━━━━━━━\n"
        f"💰 Precio:  <b>${precio:,.2f} MXN</b>\n"
        f"📊 RSI:     {rsi:.0f}\n"
        f"⭐ Score:   {score}/{total}\n"
        f"🛑 Stop:    ${stop:,.2f} MXN\n"
        f"🎯 Obj:     ${obj:,.2f} MXN ({fuente})\n"
        f"📈 R:R:     <b>{rr:.1f}x</b>\n"
        f"━━━━━━━━━━━━━━━\n"
        f"⏰ {datetime.now().strftime('%H:%M')} ET"
    )

def _loop_alertas_telegram():
    """Hilo autónomo — analiza tickers directamente cada 30 min en horario de mercado.
    NO depende del ↺ Actualizar manual."""
    global _scan_resultados
    print("[alertas] 🔔 Hilo de alertas Telegram iniciado — modo autónomo")
    time.sleep(30)  # esperar 30 seg al arrancar para que la DB esté lista

    while True:
        try:
            if not _en_horario_mercado():
                print("[alertas] 💤 Fuera de horario de mercado — durmiendo 5 min")
                time.sleep(300)
                continue

            print("[alertas] 🔄 Iniciando análisis autónomo...")

            try:
                tc = obtener_tipo_cambio()
            except Exception:
                tc = 17.5

            capital    = CAPITAL_TOTAL
            riesgo_pct = RIESGO_POR_TRADE
            rr_min     = RR_MINIMO

            vix = 20.0
            spy = {}
            try:
                vix = get_vix()
            except Exception:
                pass

            try:
                resultados = correr_scanner(tc, capital, riesgo_pct, rr_min, vix=vix, spy=spy)
                _scan_resultados = resultados or []
                print(f"[alertas] ✅ Scanner autónomo: {len(_scan_resultados)} tickers analizados")
            except Exception as e:
                print(f"[alertas] ❌ Error en scanner autónomo: {e}")
                time.sleep(1800)
                continue

            alertas = 0
            port_tickers = {p["ticker"].upper() for p in get_portafolio()}

            for r in _scan_resultados:
                nombre   = r.get("nombre", "")
                rr       = r.get("rr", 0)
                ganga_d  = r.get("ganga", {})
                inicio_d = r.get("inicio", {})
                rsi      = r.get("rsi", 0)
                precio   = r.get("precio_mxn", 0)
                stop     = r.get("stop_mxn", 0)
                score    = r.get("score_ajustado", r.get("score", 0))
                ema200   = r.get("ema200_mxn", 0)
                en_cartera = nombre.upper() in port_tickers

                print(f"[alertas] {nombre} | rr={rr:.1f} | ganga={ganga_d.get('es_ganga',False)} | inicio={inicio_d.get('nivel','')} | cartera={en_cartera}")

                # ── ALERTAS DE ENTRADA ──────────────────────────────────
                if rr >= rr_min:
                    es_ganga = isinstance(ganga_d, dict) and ganga_d.get("es_ganga", False)
                    if es_ganga and _puede_enviar_alerta(nombre, "ganga", 120):
                        msg = _formatear_alerta_ganga(r)
                        if tg_send(msg):
                            print(f"[alertas] ✅ Ganga enviada — {nombre}")
                            alertas += 1

                    es_inicio = isinstance(inicio_d, dict) and inicio_d.get("es_inicio", False)
                    nivel_str = inicio_d.get("nivel", "") if es_inicio else ""
                    if nivel_str in ("pre_breakout", "listo") and _puede_enviar_alerta(nombre, "pre4", 120):
                        label = "4/5" if nivel_str == "pre_breakout" else "5/5"
                        msg = _formatear_alerta_prebreakout(r, label)
                        if tg_send(msg):
                            print(f"[alertas] ✅ Pre-breakout {label} enviada — {nombre}")
                            alertas += 1

                # ── ALERTAS DE SALIDA (solo posiciones en cartera) ──────
                if not en_cartera:
                    continue

                try:
                    sd = analizar_score_drop(nombre, score)
                    if sd["severidad"] in ("alert", "critical") and _puede_enviar_alerta(nombre, "score_drop", 240):
                        emoji = "🚨" if sd["severidad"] == "critical" else "⚠️"
                        msg = (f"{emoji} <b>SCORE DROP — {nombre}</b>\n"
                               f"━━━━━━━━━━━━━━━\n"
                               f"📉 {sd['desc']}\n"
                               f"⭐ Score actual: {score}/11\n"
                               f"💰 Precio: ${precio:,.2f} MXN\n"
                               f"🛑 Stop: ${stop:,.2f} MXN\n"
                               f"━━━━━━━━━━━━━━━\n"
                               f"👀 Revisar posición\n"
                               f"⏰ {datetime.now().strftime('%H:%M')} ET")
                        if tg_send(msg):
                            print(f"[alertas] ✅ Score Drop enviada — {nombre}")
                            alertas += 1
                except Exception:
                    pass

                señal_d = r.get("señal", {})
                es_exit = r.get("nivel_señal", "") == "exit" or (isinstance(señal_d, dict) and señal_d.get("nivel") == "exit")
                if es_exit and _puede_enviar_alerta(nombre, "exit_ya", 120):
                    msg = (f"🔴 <b>EXIT YA — {nombre}</b>\n"
                           f"━━━━━━━━━━━━━━━\n"
                           f"⚠️ Múltiples señales de deterioro\n"
                           f"💰 Precio: ${precio:,.2f} MXN\n"
                           f"🛑 Stop dinámico: ${stop:,.2f} MXN\n"
                           f"⭐ Score: {score}/11\n"
                           f"━━━━━━━━━━━━━━━\n"
                           f"❗ Considera salir de la posición\n"
                           f"⏰ {datetime.now().strftime('%H:%M')} ET")
                    if tg_send(msg):
                        print(f"[alertas] ✅ EXIT YA enviada — {nombre}")
                        alertas += 1

                if stop > 0 and precio > 0 and precio <= stop * 1.005 and _puede_enviar_alerta(nombre, "stop", 60):
                    msg = (f"🚨 <b>STOP TOCADO — {nombre}</b>\n"
                           f"━━━━━━━━━━━━━━━\n"
                           f"💰 Precio actual: ${precio:,.2f} MXN\n"
                           f"🛑 Stop dinámico: ${stop:,.2f} MXN\n"
                           f"━━━━━━━━━━━━━━━\n"
                           f"❗ VENDER — respetar el stop\n"
                           f"⏰ {datetime.now().strftime('%H:%M')} ET")
                    if tg_send(msg):
                        print(f"[alertas] ✅ Stop tocado enviada — {nombre}")
                        alertas += 1

                if rsi >= 72 and _puede_enviar_alerta(nombre, "rsi_alto", 240):
                    msg = (f"📈 <b>RSI SOBRECOMPRADO — {nombre}</b>\n"
                           f"━━━━━━━━━━━━━━━\n"
                           f"📊 RSI: {rsi:.0f} — zona de sobrecompra\n"
                           f"💰 Precio: ${precio:,.2f} MXN\n"
                           f"🎯 Objetivo EMA200: ${ema200:,.2f} MXN\n"
                           f"━━━━━━━━━━━━━━━\n"
                           f"💡 Considera tomar parciales\n"
                           f"⏰ {datetime.now().strftime('%H:%M')} ET")
                    if tg_send(msg):
                        print(f"[alertas] ✅ RSI alto enviada — {nombre}")
                        alertas += 1

                if ema200 > 0 and precio >= ema200 * 0.995 and _puede_enviar_alerta(nombre, "objetivo", 480):
                    msg = (f"🎯 <b>OBJETIVO EMA200 — {nombre}</b>\n"
                           f"━━━━━━━━━━━━━━━\n"
                           f"💰 Precio: ${precio:,.2f} MXN\n"
                           f"✅ EMA200: ${ema200:,.2f} MXN — llegaste\n"
                           f"━━━━━━━━━━━━━━━\n"
                           f"💡 Considera tomar 50% de la posición\n"
                           f"⏰ {datetime.now().strftime('%H:%M')} ET")
                    if tg_send(msg):
                        print(f"[alertas] ✅ Objetivo EMA200 enviada — {nombre}")
                        alertas += 1

            print(f"[alertas] 📊 Ciclo completo — {alertas} alertas enviadas")

            # ── ALERTAS SEMIS ETF — SOXL y SOXS ─────────────────────────
            try:
                for etf_nom, etf_sym in [("SOXL", "SOXL"), ("SOXS", "SOXS"), ("SMH", "SMH")]:
                    base = _analizar_base_semis(etf_sym, "", tc)
                    if not base.get("valido"):
                        continue
                    pasos = _detectar_4_pasos(base, tc)
                    pb_ok = pasos.get("pasos_bajista_ok", 0)
                    pa_ok = pasos.get("pasos_alcista_ok", 0)
                    precio_etf = base.get("precio_mxn", 0)
                    hora_txt   = datetime.now().strftime("%H:%M")

                    if etf_nom == "SOXS":
                        if pb_ok == 4 and _puede_enviar_alerta("SOXS", "entrada_4", 240):
                            msg = (f"🚨 <b>ENTRA A SOXS AHORA</b>\n"
                                   f"━━━━━━━━━━━━━━━\n"
                                   f"✅ Los 4 pasos bajistas completos\n"
                                   f"📉 SMH/Semis en corrección confirmada\n"
                                   f"💰 SOXS precio: ${precio_etf:,.2f} MXN\n"
                                   f"🛑 Pon stop loss al entrar — sin excepción\n"
                                   f"━━━━━━━━━━━━━━━\n"
                                   f"⏰ {hora_txt} ET")
                            if tg_send(msg):
                                print("[alertas] ✅ SOXS ENTRADA enviada")

                        elif pb_ok == 3 and _puede_enviar_alerta("SOXS", "prep_3", 240):
                            msg = (f"⚡ <b>PREPÁRATE PARA SOXS — 3/4</b>\n"
                                   f"━━━━━━━━━━━━━━━\n"
                                   f"3 de 4 pasos bajistas activos\n"
                                   f"💰 SOXS precio: ${precio_etf:,.2f} MXN\n"
                                   f"👀 Falta 1 paso para señal completa\n"
                                   f"━━━━━━━━━━━━━━━\n"
                                   f"⏰ {hora_txt} ET")
                            if tg_send(msg):
                                print("[alertas] ✅ SOXS PREP enviada")

                        elif pa_ok >= 2 and _puede_enviar_alerta("SOXS", "salida_giro", 240):
                            msg = (f"⚠️ <b>SOXS — MERCADO GIRANDO</b>\n"
                                   f"━━━━━━━━━━━━━━━\n"
                                   f"📈 {pa_ok}/4 señales alcistas activas\n"
                                   f"⚠️ El mercado puede estar revirtiendo\n"
                                   f"💰 SOXS precio: ${precio_etf:,.2f} MXN\n"
                                   f"━━━━━━━━━━━━━━━\n"
                                   f"💡 Considera reducir o salir de SOXS\n"
                                   f"⏰ {hora_txt} ET")
                            if tg_send(msg):
                                print("[alertas] ✅ SOXS SALIDA GIRO enviada")

                        elif pa_ok == 4 and _puede_enviar_alerta("SOXS", "salida_4", 240):
                            msg = (f"🔴 <b>SAL DE SOXS AHORA</b>\n"
                                   f"━━━━━━━━━━━━━━━\n"
                                   f"🚀 4/4 señales alcistas — mercado reviró\n"
                                   f"❌ SOXS va en contra del mercado ahora\n"
                                   f"💰 SOXS precio: ${precio_etf:,.2f} MXN\n"
                                   f"━━━━━━━━━━━━━━━\n"
                                   f"❗ VENDER SOXS — respetar la señal\n"
                                   f"⏰ {hora_txt} ET")
                            if tg_send(msg):
                                print("[alertas] ✅ SOXS SAL AHORA enviada")

                    elif etf_nom == "SOXL":
                        if pa_ok == 4 and _puede_enviar_alerta("SOXL", "entrada_4", 240):
                            msg = (f"🚀 <b>ENTRA A SOXL AHORA</b>\n"
                                   f"━━━━━━━━━━━━━━━\n"
                                   f"✅ Los 4 pasos alcistas completos\n"
                                   f"📈 SMH/Semis en tendencia alcista confirmada\n"
                                   f"💰 SOXL precio: ${precio_etf:,.2f} MXN\n"
                                   f"🛑 Pon stop loss al entrar — sin excepción\n"
                                   f"━━━━━━━━━━━━━━━\n"
                                   f"⏰ {hora_txt} ET")
                            if tg_send(msg):
                                print("[alertas] ✅ SOXL ENTRADA enviada")

                        elif pa_ok == 3 and _puede_enviar_alerta("SOXL", "prep_3", 240):
                            msg = (f"⚡ <b>PREPÁRATE PARA SOXL — 3/4</b>\n"
                                   f"━━━━━━━━━━━━━━━\n"
                                   f"3 de 4 pasos alcistas activos\n"
                                   f"💰 SOXL precio: ${precio_etf:,.2f} MXN\n"
                                   f"👀 Falta 1 paso para señal completa\n"
                                   f"━━━━━━━━━━━━━━━\n"
                                   f"⏰ {hora_txt} ET")
                            if tg_send(msg):
                                print("[alertas] ✅ SOXL PREP enviada")

                        elif pb_ok >= 2 and _puede_enviar_alerta("SOXL", "salida_giro", 240):
                            msg = (f"⚠️ <b>SOXL — MERCADO GIRANDO</b>\n"
                                   f"━━━━━━━━━━━━━━━\n"
                                   f"📉 {pb_ok}/4 señales bajistas activas\n"
                                   f"⚠️ El mercado puede estar revirtiendo\n"
                                   f"💰 SOXL precio: ${precio_etf:,.2f} MXN\n"
                                   f"━━━━━━━━━━━━━━━\n"
                                   f"💡 Considera reducir o salir de SOXL\n"
                                   f"⏰ {hora_txt} ET")
                            if tg_send(msg):
                                print("[alertas] ✅ SOXL SALIDA GIRO enviada")

                        elif pb_ok == 4 and _puede_enviar_alerta("SOXL", "salida_4", 240):
                            msg = (f"🔴 <b>SAL DE SOXL AHORA</b>\n"
                                   f"━━━━━━━━━━━━━━━\n"
                                   f"📉 4/4 señales bajistas — mercado reviró\n"
                                   f"❌ SOXL va en contra del mercado ahora\n"
                                   f"💰 SOXL precio: ${precio_etf:,.2f} MXN\n"
                                   f"━━━━━━━━━━━━━━━\n"
                                   f"❗ VENDER SOXL — respetar la señal\n"
                                   f"⏰ {hora_txt} ET")
                            if tg_send(msg):
                                print("[alertas] ✅ SOXL SAL AHORA enviada")

            except Exception as e:
                print(f"[alertas] ⚠️ Error alertas semis: {e}")

            time.sleep(1800)  # revisar cada 30 minutos

        except Exception as e:
            import traceback
            print(f"[alertas] ❌ Error general: {e}")
            traceback.print_exc()
            time.sleep(600)

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
    CREATE TABLE IF NOT EXISTS top_diario_acumulado (
        ticker      TEXT PRIMARY KEY,
        fecha       TEXT NOT NULL,
        puntuacion  REAL NOT NULL,
        datos_json  TEXT NOT NULL
    );
    CREATE TABLE IF NOT EXISTS semis_senales (
        id          INTEGER PRIMARY KEY AUTOINCREMENT,
        fecha       TEXT NOT NULL,
        simbolo     TEXT NOT NULL,
        senal       TEXT NOT NULL,
        precio_mxn  REAL NOT NULL,
        pasos_ok    INTEGER DEFAULT 0,
        tipo        TEXT DEFAULT 'ETF'
    );
    CREATE TABLE IF NOT EXISTS top_semanal_acumulado (
        ticker      TEXT PRIMARY KEY,
        semana      TEXT NOT NULL,
        puntuacion  REAL NOT NULL,
        datos_json  TEXT NOT NULL
    );
    CREATE TABLE IF NOT EXISTS watchlist (
        ticker      TEXT PRIMARY KEY,
        notas       TEXT DEFAULT '',
        e1_manual   REAL DEFAULT 0,
        fecha_add   TEXT DEFAULT ''
    );
    CREATE TABLE IF NOT EXISTS diario_trading (
        id              INTEGER PRIMARY KEY AUTOINCREMENT,
        op_id           INTEGER,
        ticker          TEXT NOT NULL,
        fecha           TEXT NOT NULL,
        tipo            TEXT NOT NULL,
        precio_mxn      REAL NOT NULL,
        titulos         REAL NOT NULL,
        score_entrada   INTEGER DEFAULT 0,
        total_criterios INTEGER DEFAULT 13,
        razon_entrada   TEXT NOT NULL,
        setup_tipo      TEXT DEFAULT '',
        rr_esperado     REAL DEFAULT 0,
        resultado       TEXT DEFAULT 'abierta',
        pnl_mxn         REAL DEFAULT 0,
        pnl_pct         REAL DEFAULT 0,
        aprendizaje     TEXT DEFAULT '',
        fecha_cierre    TEXT DEFAULT ''
    );
    CREATE TABLE IF NOT EXISTS pnl_historico (
        id          INTEGER PRIMARY KEY AUTOINCREMENT,
        fecha       TEXT NOT NULL,
        capital     REAL NOT NULL,
        pnl_dia_mxn REAL DEFAULT 0,
        pnl_acum_pct REAL DEFAULT 0,
        spy_precio  REAL DEFAULT 0
    );
    CREATE TABLE IF NOT EXISTS tickers (
        id       INTEGER PRIMARY KEY AUTOINCREMENT,
        ticker   TEXT UNIQUE NOT NULL,
        exchange TEXT DEFAULT '',
        origen   TEXT DEFAULT 'USA',
        activo   INTEGER DEFAULT 1
    );
    """)
    con.commit()
    migrations = [
        "ALTER TABLE tickers ADD COLUMN origen TEXT DEFAULT 'USA'",
        "ALTER TABLE portafolio ADD COLUMN mercado TEXT DEFAULT 'SIC'",
        "ALTER TABLE operaciones ADD COLUMN diario_id INTEGER DEFAULT 0",
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
    La DB tiene prioridad: si un ticker está en DB con activo=0,
    NO aparece aunque esté hardcoded en SCANNER_TICKERS.
    """
    con = sqlite3.connect(DB_FILE)
    con.row_factory = sqlite3.Row
    try:
        rows = con.execute("SELECT ticker, exchange, activo FROM tickers").fetchall()
    except Exception:
        rows = []
    con.close()

    db_map = {r["ticker"]: (r["activo"], r["exchange"] or "") for r in rows}

    combinados = {}
    for t, val in SCANNER_TICKERS.items():
        estado = db_map.get(t)
        if estado is None or estado[0] == 1:
            combinados[t] = val
    for t, (activo, exchange) in db_map.items():
        if activo == 1:
            combinados[t] = (t, exchange)
    return combinados

def remove_ticker_db(ticker: str):
    """
    Desactiva un ticker del scanner.
    Funciona para cualquier ticker — incluyendo los hardcoded en SCANNER_TICKERS.
    Hace INSERT OR REPLACE con activo=0 para que la DB prevalezca sobre el código.
    """
    t = ticker.upper()
    exchange = ""
    for src in (SCANNER_TICKERS, UNIVERSO):
        if t in src:
            exchange = src[t][1]
            break
    con = sqlite3.connect(DB_FILE)
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
    try:
        r = requests.get("https://api.frankfurter.app/latest?from=USD&to=MXN", timeout=4)
        return float(r.json()["rates"]["MXN"])
    except Exception: pass
    try:
        url = "https://www.banxico.org.mx/SieAPIRest/service/v1/series/SF43718/datos/oportuno"
        r = requests.get(url, headers={"Bmx-Token":"adec2b6a30609a9e4f696c3b44f32d16b8a6ab3b0e83da2e18b0c2e24f892abc"}, timeout=4)
        return float(r.json()["bmx"]["series"][0]["datos"][0]["dato"].replace(",",""))
    except Exception: pass
    if key and key not in ("TU_KEY_AQUI", ""):
        try:
            r = requests.get("https://api.twelvedata.com/exchange_rate",
                params={"symbol":"USD/MXN","apikey":key}, timeout=4)
            d = r.json()
            if "rate" in d: return float(d["rate"])
        except Exception: pass
    print("[TC] ⚠️ Todas las fuentes fallaron — usando TC fijo 17.50")
    return 17.50

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

_KEY_IDX: int = 0
_KEYS_AGOTADAS: set = set()

def _es_error_creditos(d: dict) -> bool:
    """Detecta si TwelveData respondió con error de créditos/límite dentro del JSON."""
    if not isinstance(d, dict): return False
    code = d.get("code", 0)
    msg  = str(d.get("message", "")).lower()
    return code in (429, 402) or "run out" in msg or "credits" in msg or "limit" in msg

def _next_key() -> str:
    """Devuelve la siguiente key disponible del pool, saltando las agotadas."""
    global _KEY_IDX
    if not _TD_KEYS:
        return ""
    for _ in range(len(_TD_KEYS)):
        key = _TD_KEYS[_KEY_IDX % len(_TD_KEYS)]
        _KEY_IDX += 1
        if key not in _KEYS_AGOTADAS:
            return key
    print("  ⚠️  Todas las API keys agotadas por hoy — espera a mañana o agrega más keys")
    return ""

def api_timeseries(symbol: str, interval: str, outputsize: int = 200,
                   exchange: str = "", key: str = "") -> list | None:
    """
    Petición individual a TwelveData.
    Si no se pasa key, toma la siguiente del pool dual-key.
    Si la key está agotada (créditos), la marca y prueba la siguiente automáticamente.
    """
    global _KEYS_AGOTADAS
    out = min(outputsize, 5000)

    keys_a_probar = [key] if key else [_next_key()]
    if not key and len(_TD_KEYS) > 1:
        for k in _TD_KEYS:
            if k not in keys_a_probar:
                keys_a_probar.append(k)

    for use_key in keys_a_probar:
        if not use_key or use_key in _KEYS_AGOTADAS:
            continue
        params = {"symbol": symbol, "interval": interval, "outputsize": out,
                  "apikey": use_key, "order": "ASC"}
        if exchange:
            params["exchange"] = exchange
        for intento in range(2):
            try:
                r = requests.get(f"{API_BASE}/time_series", params=params, timeout=15)
                if r.status_code == 429:
                    print(f"    ⏳ Rate limit HTTP ({symbol}) key …{use_key[-4:]} — esperando 8s...")
                    time.sleep(8)
                    continue
                d = r.json()
                if _es_error_creditos(d):
                    print(f"    ⚠️  Key …{use_key[-4:]} agotada — cambiando a la siguiente key")
                    _KEYS_AGOTADAS.add(use_key)
                    break  # salir del loop de intentos, probar siguiente key
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

    print(f"    ❌ TD ({symbol}): todas las keys fallaron o agotadas")
    return None

def api_timeseries_batch(symbols: list, interval: str,
                          outputsize: int = 100, key: str = "") -> dict:
    """
    Llama TwelveData con hasta 8 símbolos en UNA sola request.
    Si la key está agotada, cambia a la siguiente automáticamente.
    """
    global _KEYS_AGOTADAS
    if not symbols:
        return {}
    out = min(outputsize, 5000)
    sym_str = ",".join(s.upper() for s in symbols)

    keys_a_probar = [key] if key else [_next_key()]
    if len(_TD_KEYS) > 1:
        for k in _TD_KEYS:
            if k not in keys_a_probar:
                keys_a_probar.append(k)

    for use_key in keys_a_probar:
        if not use_key or use_key in _KEYS_AGOTADAS:
            continue
        params = {"symbol": sym_str, "interval": interval, "outputsize": out,
                  "apikey": use_key, "order": "ASC"}
        try:
            r = requests.get(f"{API_BASE}/time_series", params=params, timeout=30)
            if r.status_code == 429:
                print(f"    ⏳ Rate limit batch HTTP k=…{use_key[-4:]} — esperando 8s...")
                time.sleep(8)
                r = requests.get(f"{API_BASE}/time_series", params=params, timeout=30)
            d = r.json()
            if _es_error_creditos(d):
                print(f"    ⚠️  Key …{use_key[-4:]} agotada en batch — cambiando a la siguiente key")
                _KEYS_AGOTADAS.add(use_key)
                continue  # probar siguiente key
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

    ventana = 10
    obv_reciente = obv_vals[-ventana:]
    precio_reciente = c[-ventana:]

    obv_sube   = obv_reciente[-1] > obv_reciente[0]
    precio_sube= precio_reciente[-1] > precio_reciente[0]

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
_SECTOR_MAP = {
    "NVDA":"SMH","SOXL":"SMH","AMD":"SMH",
    "AAPL":"QQQ","META":"QQQ","GOOGL":"QQQ","MSFT":"QQQ","AMZN":"QQQ",
    "TQQQ":"QQQ","NFLX":"QQQ","PYPL":"QQQ",
    "PLTR":"XLF",
    "TSLA":"XLY","ABNB":"XLY","UBER":"XLY","NKE":"XLY","DIS":"XLY",
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
    Estrategia de salida escalonada para swing trading:
      Nivel 1 (+8-10%)  → vender 25% (1 titulo si tienes 4, etc.)
      Nivel 2 (+15%)    → vender 25% adicional
      Nivel 3 (EMA200)  → vender 25% adicional
      Nivel 4           → dejar correr con trailing stop al 97%
    Stop loss siempre — sin excepciones.
    """
    if not precio_entrada_mxn or precio_entrada_mxn <= 0:
        return {}

    ganancia_pct = (precio_actual_mxn - precio_entrada_mxn) / precio_entrada_mxn * 100
    riesgo_orig  = precio_entrada_mxn - stop_mxn if stop_mxn else 0
    objetivo_pct = (objetivo_mxn - precio_entrada_mxn) / precio_entrada_mxn * 100 if objetivo_mxn else 0

    breakeven = precio_entrada_mxn * 1.003

    nivel1_precio = precio_entrada_mxn * 1.09   # +9% — punto medio de 8-10%
    nivel2_precio = precio_entrada_mxn * 1.15   # +15%
    nivel3_precio = objetivo_mxn or (precio_entrada_mxn * 1.25)  # EMA200 u objetivo

    tit_vender_n1 = max(1, round(titulos * 0.25))
    tit_vender_n2 = max(1, round(titulos * 0.25))
    tit_vender_n3 = max(1, round(titulos * 0.25))

    trailing_stop = precio_actual_mxn * 0.97

    if ganancia_pct < -8:
        nivel_actual = "stop"
        estado_op    = "🔴 Stop loss — SALIR YA"
        accion       = f"Precio {ganancia_pct:.1f}% abajo de tu entrada. SALIR en {fmt(stop_mxn or precio_actual_mxn)} MXN. El stop existe para proteger tu capital."
        color        = "var(--red)"
        urgente      = True

    elif ganancia_pct < -3:
        nivel_actual = "vigilar"
        estado_op    = "⚠️ En pérdida — vigilar stop"
        accion       = f"Pérdida de {ganancia_pct:.1f}%. Stop en {fmt(stop_mxn)} MXN. NO promediar a la baja. Si toca el stop, salir sin dudar."
        color        = "var(--yellow)"
        urgente      = False

    elif ganancia_pct < 0:
        nivel_actual = "breakeven"
        estado_op    = "〰️ Cerca de entrada"
        accion       = f"Pérdida pequeña ({ganancia_pct:.1f}%). Mantener stop en {fmt(stop_mxn)}. Esperar que el setup se active."
        color        = "var(--yellow)"
        urgente      = False

    elif ganancia_pct < 8:
        nivel_actual = "0"
        estado_op    = "📈 En ganancia — aún no vender"
        accion       = (f"Ganancia de +{ganancia_pct:.1f}%. Mueve el stop a breakeven ({fmt(breakeven)}) "
                        f"para operar sin riesgo. Espera +9% para vender el primer 25%.")
        color        = "var(--green)"
        urgente      = False

    elif ganancia_pct < 15:
        nivel_actual = "1"
        estado_op    = "🎯 NIVEL 1 — Vender 25%"
        accion       = (f"Ganancia +{ganancia_pct:.1f}%. ✅ Vende {tit_vender_n1} título(s) ({fmt(precio_actual_mxn)} MXN c/u). "
                        f"Mueve stop a breakeven ({fmt(breakeven)}). Espera +15% para el siguiente 25%.")
        color        = "var(--green)"
        urgente      = False

    elif ganancia_pct < (objetivo_pct * 0.85 if objetivo_pct > 15 else 25):
        nivel_actual = "2"
        estado_op    = "🎯 NIVEL 2 — Vender otro 25%"
        accion       = (f"Ganancia +{ganancia_pct:.1f}%. ✅ Vende {tit_vender_n2} título(s) más ({fmt(precio_actual_mxn)} MXN c/u). "
                        f"Stop ya en breakeven. Espera EMA200 ({fmt(nivel3_precio)}) para el 25% siguiente.")
        color        = "var(--green)"
        urgente      = False

    elif objetivo_mxn and precio_actual_mxn >= objetivo_mxn * 0.90:
        nivel_actual = "3"
        estado_op    = "🏁 NIVEL 3 — Vender otro 25% (objetivo/EMA200)"
        accion       = (f"Precio cerca del objetivo ({fmt(objetivo_mxn)}). ✅ Vende {tit_vender_n3} título(s) más. "
                        f"El último {25}% lo dejas correr con trailing stop al 97% ({fmt(trailing_stop)}).")
        color        = "#d4a017"
        urgente      = False

    else:
        nivel_actual = "4"
        estado_op    = "🚀 NIVEL 4 — Dejar correr"
        accion       = (f"Ganancia +{ganancia_pct:.1f}%. Ya vendiste 75%. El último lote corre libre. "
                        f"Trailing stop en {fmt(trailing_stop)} (97% del precio actual). Muévelo arriba cada semana.")
        color        = "#7c3aed"
        urgente      = False

    return {
        "ganancia_pct":   round(ganancia_pct, 2),
        "estado_op":      estado_op,
        "accion":         accion,
        "color":          color,
        "urgente":        urgente,
        "nivel_actual":   nivel_actual,
        "breakeven":      round(breakeven, 2),
        "nivel1_precio":  round(nivel1_precio, 2),
        "nivel2_precio":  round(nivel2_precio, 2),
        "nivel3_precio":  round(nivel3_precio, 2),
        "trailing_stop":  round(trailing_stop, 2),
        "tit_vender_n1":  tit_vender_n1,
        "tit_vender_n2":  tit_vender_n2,
        "tit_vender_n3":  tit_vender_n3,
        "objetivo_pct":   round(objetivo_pct, 2),
        "riesgo_orig":    round(riesgo_orig, 2),
        "parciales_50":   round(nivel1_precio, 2),  # compatibilidad legacy
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

# ── MODO GANGA (señal adicional de precio) ───────────────
def detectar_ganga(tf_1d: dict, sr: dict, objetivo_mxn: float, precio_mxn: float) -> dict:
    """
    Detecta oportunidad de acumulacion cuando el precio esta lejos de su objetivo.
    Todo en MXN usando el TC del día. 3 criterios obligatorios:
      1. Precio >= 15% por debajo del objetivo calculado (ambos en MXN)
      2. RSI entre 30-55 (deprimido/neutral, no sobrecomprado)
      3. Al menos 1 soporte con >= 2 toques debajo del precio actual
    Solo si los 3 se cumplen aparece el badge.
    """
    if not tf_1d or not tf_1d.get("valido") or not objetivo_mxn or not precio_mxn:
        return {"es_ganga": False}

    rsi = tf_1d.get("rsi", 50)

    if objetivo_mxn <= precio_mxn:
        return {"es_ganga": False}

    margen_pct = (objetivo_mxn - precio_mxn) / precio_mxn * 100
    c1 = margen_pct >= 15.0

    c2 = 30 <= rsi <= 55

    precio_usd = tf_1d.get("precio", 0)
    soportes = [z for z in sr.get("soportes", [])
                if z.get("fuerza", 0) >= 2 and z.get("precio", precio_usd + 1) < precio_usd]
    c3 = len(soportes) > 0

    es_ganga    = c1 and c2 and c3
    soporte_ref = soportes[0] if soportes else None

    return {
        "es_ganga":    es_ganga,
        "margen_pct":  round(margen_pct, 1),
        "rsi":         round(rsi, 1),
        "soporte":     soporte_ref,
        "objetivo_mxn": round(objetivo_mxn, 2),
        "precio_mxn":  round(precio_mxn, 2),
    }

def detectar_inicio_movimiento(tf_1d: dict) -> dict:
    """
    Detector de ACUMULACIÓN — 3 niveles de señal:
    - "acumulacion"  : 3/5 señales — zona de acumulación temprana
    - "pre_breakout" : 4/5 señales — pre-breakout inminente
    - "listo"        : 5/5 señales — listo para entrar

    5 condiciones (sin importar EMA200 — detecta ANTES de recuperarla):
      1. RSI saliendo de sobreventa — entre 25-50 Y subiendo
      2. MACD histograma girando al alza (aunque negativo)
      3. Precio sobre soporte clave (no rompió el piso)
      4. Volumen empezando — >= 0.7x media
      5. Estructura HH/HL — mínimos más altos (precio acumulando)
    """
    if not tf_1d or not tf_1d.get("valido"):
        return {"es_inicio": False}

    rsi      = tf_1d.get("rsi", 50)
    rsi_ant  = tf_1d.get("rsi_anterior", rsi)
    mh_v     = tf_1d.get("macd_hist", 0)
    mh_ant   = tf_1d.get("macd_hist_ant", mh_v)
    vol_rel  = tf_1d.get("vol_rel", 0)
    precio   = tf_1d.get("precio", 0)
    soporte  = tf_1d.get("soporte", 0)
    struct   = tf_1d.get("estructura", {})

    if not precio:
        return {"es_inicio": False}

    c1 = 25 <= rsi <= 50 and rsi > rsi_ant

    c2 = mh_v > mh_ant

    c3 = precio > soporte if soporte else False

    c4 = vol_rel >= 0.7 if vol_rel > 0 else False

    estructura = struct.get("estructura", "") if isinstance(struct, dict) else ""
    c5 = estructura == "alcista" or struct.get("hl", False) if isinstance(struct, dict) else False

    condiciones  = [c1, c2, c3, c4, c5]
    n_cumplidas  = sum(condiciones)

    if n_cumplidas < 3:
        return {"es_inicio": False}

    if n_cumplidas >= 5:
        nivel = "listo"
    elif n_cumplidas >= 4:
        nivel = "pre_breakout"
    else:
        nivel = "acumulacion"

    return {
        "es_inicio":    True,
        "nivel":        nivel,
        "n_cumplidas":  n_cumplidas,
        "rsi":          round(rsi, 1),
        "c1": c1, "c2": c2, "c3": c3, "c4": c4, "c5": c5,
    }

def badge_inicio_movimiento(inicio: dict) -> str:
    """Badge con 3 niveles de acumulación."""
    if not inicio or not inicio.get("es_inicio"):
        return ""
    nivel = inicio.get("nivel", "acumulacion")
    n     = inicio.get("n_cumplidas", 0)
    cfg = {
        "acumulacion":  ("🟡 Acumulación 3/5",  "#fff7e6", "#d46b08", "#ffd591"),
        "pre_breakout": ("🟠 Pre-breakout 4/5",  "#fff3e0", "#b45309", "#fdba74"),
        "listo":        ("🟢 Listo entrar 5/5",  "#f0fdf4", "#15803d", "#86efac"),
    }
    label, bg, color, brd = cfg.get(nivel, cfg["acumulacion"])
    return (f'<span style="display:inline-flex;padding:2px 7px;border-radius:20px;'
            f'font-size:9px;font-weight:700;white-space:nowrap;'
            f'background:{bg};color:{color};border:2px solid {brd};margin-left:4px">'
            f'{label}</span>')

def render_inicio_movimiento_panel(inicio: dict) -> str:
    """Panel en el detail — explica por qué detectó inicio de movimiento."""
    if not inicio or not inicio.get("es_inicio"):
        return ""
    n    = inicio.get("n_cumplidas", 0)
    d200 = inicio.get("dist_e200_pct", 0)
    rsi  = inicio.get("rsi", 0)
    rows = [
        ("📈 RSI saliendo zona débil",  inicio.get("c1"), f"RSI {rsi:.0f} subiendo"),
        ("🔄 MACD histograma girando",  inicio.get("c2"), "Histograma recuperándose"),
        ("🛡️ Precio sobre soporte",     inicio.get("c3"), "Piso no roto"),
        ("📊 Volumen despertando",       inicio.get("c4"), "Vol ≥ 0.7x media"),
        ("📐 Estructura HH/HL",         inicio.get("c5"), "Mínimos más altos"),
    ]
    filas_html = ""
    for label, ok, val in rows:
        color = "#14532d" if ok else "#6b7280"
        bg    = "#f0fdf4" if ok else "#f9fafb"
        icon  = "✅" if ok else "⬜"
        filas_html += (f'<div style="display:flex;align-items:center;gap:8px;'
                       f'padding:6px 8px;border-radius:6px;background:{bg};margin-bottom:4px">'
                       f'<span>{icon}</span>'
                       f'<span style="flex:1;font-size:11px;color:{color}">{label}</span>'
                       f'<span style="font-size:10px;color:var(--muted)">{val}</span>'
                       f'</div>')
    nivel   = inicio.get("nivel", "acumulacion")
    cfg_n = {
        "acumulacion":  ("#fff7e6","#ffd591","#d46b08",
                         "🟡 ACUMULACIÓN — zona de entrada temprana (3/5)",
                         "Señales tempranas de recuperación. Entra con 30-50% del tamaño normal. Stop ajustado."),
        "pre_breakout": ("#fff3e0","#fdba74","#b45309",
                         "🟠 PRE-BREAKOUT — movimiento inminente (4/5)",
                         "Alta probabilidad de movimiento. Puedes entrar con 50-75% del tamaño. Stop dinámico."),
        "listo":        ("#f0fdf4","#86efac","#15803d",
                         "🟢 LISTO PARA ENTRAR — todas las señales alineadas (5/5)",
                         "Entrada completa válida. Usa el tamaño sugerido por Finbit con stop en EMA9."),
    }
    bg, brd, col, titulo, msg = cfg_n.get(nivel, cfg_n["acumulacion"])
    return (f'<div style="background:{bg};border:2px solid {brd};border-radius:8px;'
            f'padding:12px 14px;margin-bottom:10px">'
            f'<div style="font-weight:700;font-size:13px;color:{col};margin-bottom:6px">'
            f'{titulo}</div>'
            f'<div style="font-size:11px;color:{col};opacity:.85;margin-bottom:10px">{msg}</div>'
            f'{filas_html}</div>')

def detectar_capitulacion(tf_1d: dict, values_raw: list) -> dict:
    """
    Detecta capitulación — el momento exacto donde el cuchillo se detiene.
    Requiere los 3 elementos juntos:
      1. Volumen de capitulación: último día >= 3x el promedio de 20 días
      2. Patrón de vela de reversión: martillo, envolvente alcista o estrella mañana
      3. Divergencia RSI alcista: precio baja más pero RSI no confirma
    Los 3 juntos = señal de alta convicción. 2/3 = señal moderada.
    """
    if not tf_1d or not tf_1d.get("valido") or not values_raw:
        return {"es_capitulacion": False, "nivel": 0, "desc": "", "criterios": []}

    vol_rel   = tf_1d.get("vol_rel", 0)
    div_rsi   = tf_1d.get("div_rsi", {}) or {}
    patron    = tf_1d.get("patron_velas", {}) or {}
    rsi       = tf_1d.get("rsi", 50)
    obv_info  = tf_1d.get("obv", {}) or {}

    criterios = []

    # ── Criterio 1: Volumen de capitulación ───────────────────────────────
    vol_cap = vol_rel >= 3.0
    vol_mod = vol_rel >= 2.0 and not vol_cap
    if vol_cap:
        criterios.append(f"🔥 Volumen {vol_rel:.1f}x — capitulación extrema, vendedores agotados")
    elif vol_mod:
        criterios.append(f"⚡ Volumen {vol_rel:.1f}x — actividad inusual significativa")

    # ── Criterio 2: Patrón de vela de reversión ───────────────────────────
    tiene_patron = patron.get("ok", False)
    if tiene_patron:
        criterios.append(patron.get("desc", "Patrón de reversión alcista"))

    # ── Criterio 3: Divergencia RSI alcista ───────────────────────────────
    tiene_div_rsi = div_rsi.get("alcista", False)
    if tiene_div_rsi:
        criterios.append("📈 Divergencia RSI alcista — precio baja pero RSI sube")

    # ── Criterio 4 (bonus): OBV divergencia alcista ───────────────────────
    tiene_div_obv = obv_info.get("div_alcista", False)
    if tiene_div_obv:
        criterios.append("💡 Divergencia OBV — institucionales acumulando en silencio")

    # ── Criterio 5 (bonus): RSI en zona de sobreventa ─────────────────────
    rsi_sobreventa = rsi <= 35
    if rsi_sobreventa:
        criterios.append(f"📉 RSI {rsi:.0f} — sobreventa, presión vendedora casi agotada")

    principales = sum([vol_cap or vol_mod, tiene_patron, tiene_div_rsi])

    if principales == 3 and vol_cap:
        nivel = 3
        desc  = "🔔 Capitulación confirmada — los 3 elementos presentes con volumen extremo"
    elif principales == 3:
        nivel = 2
        desc  = "🔔 Capitulación probable — volumen + patrón + divergencia RSI"
    elif principales == 2 and vol_cap:
        nivel = 2
        desc  = "⚠️ Posible capitulación — volumen extremo + señal técnica"
    elif principales == 2:
        nivel = 1
        desc  = "👁 Señal moderada — 2 de 3 elementos de capitulación presentes"
    else:
        return {"es_capitulacion": False, "nivel": 0, "desc": "", "criterios": criterios}

    return {
        "es_capitulacion": True,
        "nivel":           nivel,
        "desc":            desc,
        "criterios":       criterios,
        "vol_rel":         vol_rel,
        "tiene_patron":    tiene_patron,
        "tiene_div_rsi":   tiene_div_rsi,
        "tiene_div_obv":   tiene_div_obv,
        "rsi_sobreventa":  rsi_sobreventa,
    }

def badge_capitulacion(cap: dict) -> str:
    """Badge compacto para la tabla del scanner."""
    if not cap or not cap.get("es_capitulacion"):
        return ""
    nivel = cap.get("nivel", 0)
    if nivel == 3:
        return ('<span style="display:inline-flex;padding:2px 8px;border-radius:20px;'
                'font-size:9px;font-weight:700;white-space:nowrap;'
                'background:#1e1b4b;color:#a5b4fc;border:2px solid #6366f1;margin-left:4px">'
                '🔔 Capitulación</span>')
    elif nivel == 2:
        return ('<span style="display:inline-flex;padding:2px 8px;border-radius:20px;'
                'font-size:9px;font-weight:700;white-space:nowrap;'
                'background:#fdf4ff;color:#7e22ce;border:2px solid #d8b4fe;margin-left:4px">'
                '🔔 Cap. probable</span>')
    else:
        return ('<span style="display:inline-flex;padding:2px 8px;border-radius:20px;'
                'font-size:9px;font-weight:700;white-space:nowrap;'
                'background:#f5f3ff;color:#6d28d9;border:1px solid #ddd6fe;margin-left:4px">'
                '👁 Cap. moderada</span>')

def render_capitulacion_panel(cap: dict) -> str:
    """Panel detallado en el detail del scanner."""
    if not cap or not cap.get("es_capitulacion"):
        return ""
    nivel   = cap.get("nivel", 0)
    desc    = cap.get("desc", "")
    crits   = cap.get("criterios", [])
    color_bg  = "#1e1b4b" if nivel == 3 else "#fdf4ff" if nivel == 2 else "#f5f3ff"
    color_brd = "#6366f1" if nivel == 3 else "#d8b4fe" if nivel == 2 else "#ddd6fe"
    color_txt = "#a5b4fc" if nivel == 3 else "#7e22ce" if nivel == 2 else "#6d28d9"

    crits_html = "".join(f'<div style="margin-top:4px;font-size:11px">• {c}</div>' for c in crits)

    return (f'<div style="background:{color_bg};border:2px solid {color_brd};border-radius:10px;'
            f'padding:14px 16px;margin-bottom:10px">'
            f'<div style="font-weight:700;font-size:13px;color:{color_txt};margin-bottom:6px">'
            f'🔔 {desc}</div>'
            f'<div style="color:{color_txt}">{crits_html}</div>'
            f'<div style="margin-top:10px;font-size:11px;color:{color_txt};opacity:0.8">'
            f'⚠️ Usa siempre stop loss. La capitulación puede ser falsa si hay problemas fundamentales. '
            f'Confirma con el precio del día siguiente antes de entrar.</div>'
            f'</div>')

def badge_ganga(ganga: dict) -> str:
    """Badge compacto para la tabla — solo aparece si es_ganga=True."""
    if not ganga or not ganga.get("es_ganga"):
        return ""
    margen = ganga.get("margen_pct", 0)
    return (f'<span style="display:inline-flex;padding:2px 7px;border-radius:20px;'
            f'font-size:9px;font-weight:700;white-space:nowrap;'
            f'background:#f0fdf4;color:#14532d;border:2px solid #86efac;margin-left:4px">'
            f'🏷️ Ganga +{margen:.0f}%</span>')

def render_ganga_panel(ganga: dict) -> str:
    """Panel en el detail — explica por qué es ganga y cómo acumular."""
    if not ganga or not ganga.get("es_ganga"):
        return ""
    margen  = ganga.get("margen_pct", 0)
    rsi     = ganga.get("rsi", 0)
    sop     = ganga.get("soporte")
    obj     = ganga.get("objetivo_mxn", 0)
    precio  = ganga.get("precio_mxn", 0)
    sop_txt = (f'Piso en <strong>${sop["precio"]:.2f} USD</strong> ({sop["fuerza"]}× toques)'
               if sop else "Sin soporte definido")
    return (f'<div style="background:#f0fdf4;border:2px solid #86efac;border-radius:8px;'
            f'padding:12px 14px;margin-bottom:10px">'
            f'<div style="font-weight:700;font-size:13px;color:#14532d;margin-bottom:4px">'
            f'🏷️ GANGA DE PRECIO — oportunidad de acumulación</div>'
            f'<div style="font-size:11px;color:#166534;margin-bottom:10px">'
            f'El precio (${precio:,.2f} MXN) está <strong>{margen:.1f}%</strong> por debajo '
            f'del objetivo calculado (${obj:,.2f} MXN) con RSI neutral ({rsi:.0f}) y piso confirmado. '
            f'Zona ideal para acumular en escalones.</div>'
            f'<div style="display:grid;grid-template-columns:1fr 1fr 1fr;gap:8px;margin-bottom:10px">'
            f'<div style="background:#dcfce7;border-radius:6px;padding:8px;text-align:center">'
            f'<div style="font-size:10px;color:#166534">Margen al objetivo</div>'
            f'<div style="font-size:16px;font-weight:700;color:#14532d">+{margen:.1f}%</div></div>'
            f'<div style="background:#dcfce7;border-radius:6px;padding:8px;text-align:center">'
            f'<div style="font-size:10px;color:#166534">RSI actual</div>'
            f'<div style="font-size:16px;font-weight:700;color:#14532d">{rsi:.0f}</div></div>'
            f'<div style="background:#dcfce7;border-radius:6px;padding:8px;text-align:center">'
            f'<div style="font-size:10px;color:#166534">Piso</div>'
            f'<div style="font-size:13px;font-weight:700;color:#14532d">{sop_txt}</div></div></div>'
            f'<div style="font-size:10px;color:#166534;background:#dcfce7;border-radius:5px;padding:6px 9px">'
            f'💡 Usa el plan DCA de abajo para entrar en 3 escalones. '
            f'Stop obligatorio bajo el piso ({sop_txt.replace("<strong>","").replace("</strong>","")}).'
            f'</div></div>')

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
_MACRO_CACHE: dict = {}

def get_vix() -> float:
    """Obtiene el VIX via Yahoo Finance (TwelveData no soporta VIX)."""
    global _MACRO_CACHE
    if "vix" in _MACRO_CACHE:
        return _MACRO_CACHE["vix"]
    try:
        r = requests.get(
            "https://query1.finance.yahoo.com/v8/finance/chart/%5EVIX",
            params={"interval": "1d", "range": "5d"},
            headers={"User-Agent": "Mozilla/5.0"},
            timeout=8
        )
        data = r.json()
        closes = data["chart"]["result"][0]["indicators"]["quote"][0]["close"]
        closes = [c for c in closes if c is not None]
        if closes:
            v = round(float(closes[-1]), 2)
            if 5 < v < 90:
                _MACRO_CACHE["vix"] = v
                print(f"[vix] OK Yahoo ^VIX -> {v:.1f}")
                return v
    except Exception as e:
        print(f"[vix] Error Yahoo: {e}")
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

_NO_DISPONIBLE_GBM = {
    "FUJIY",  # No disponible en GBM/SIC
}

def es_etf_apalancado(ticker: str) -> bool:
    return ticker.upper() in _ETFS_APALANCADOS

def no_disponible_gbm(ticker: str) -> bool:
    return ticker.upper() in _NO_DISPONIBLE_GBM

def score_minimo_entrada(ticker: str, vix: float) -> int:
    """Score mínimo para señal BUY según tipo de activo y condición macro.
    Con 13 criterios — 7/13 = 54% es razonable para acción normal."""
    if es_etf_apalancado(ticker):
        return 9 if vix > 20 else 8   # ETF 3x: exige casi perfección (69%+)
    return 7 if vix > 20 else 6       # Acción normal: 6/13 en calma (46%), 7/13 con miedo

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
    Guarda score en historial. Inserta siempre una nueva fila — acumula histórico.
    Solo actualiza si ya hay una entrada en los últimos 30 minutos (evita duplicados
    por múltiples corridas seguidas en el mismo día).
    """
    from datetime import timezone, timedelta
    tz_mx  = timezone(timedelta(hours=-6))
    ahora  = datetime.now(tz_mx).strftime("%Y-%m-%d %H:%M")
    hace30 = (datetime.now(tz_mx) - timedelta(minutes=30)).strftime("%Y-%m-%d %H:%M")
    con    = sqlite3.connect(DB_FILE)
    existe = con.execute(
        "SELECT id FROM score_history WHERE ticker=? AND fecha >= ?",
        (ticker.upper(), hace30)
    ).fetchone()
    if existe:
        con.execute(
            "UPDATE score_history SET fecha=?,score=?,senal=?,vix=?,precio=?,estado=? WHERE id=?",
            (ahora, score, senal, round(vix,2), round(precio,4), estado, existe[0])
        )
    else:
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

def detectar_patrones_velas(opens: list, highs: list, lows: list, closes: list) -> dict:
    """
    Detecta los 3 patrones de velas más confiables en soporte:
    - Martillo: mecha inferior larga, cuerpo pequeño arriba — reversión alcista
    - Envolvente alcista: vela verde envuelve completamente la roja anterior
    - Estrella de la mañana: 3 velas — bajista, doji/pequeña, alcista fuerte
    """
    if not opens or len(closes) < 3:
        return {"patron": None, "desc": "", "ok": False}

    o, h, l, c = opens, highs, lows, closes

    o1, h1, l1, c1 = float(o[-1]), float(h[-1]), float(l[-1]), float(c[-1])
    o2, h2, l2, c2 = float(o[-2]), float(h[-2]), float(l[-2]), float(c[-2])
    o3, h3, l3, c3 = float(o[-3]), float(h[-3]), float(l[-3]), float(c[-3])

    cuerpo1 = abs(c1 - o1)
    rango1  = h1 - l1
    mecha_inf1 = min(o1, c1) - l1
    mecha_sup1 = h1 - max(o1, c1)

    cuerpo2 = abs(c2 - o2)
    rango2  = h2 - l2

    # ── Martillo ────────────────────────────────────────────────────────
    es_martillo = (
        rango1 > 0 and
        cuerpo1 > 0 and
        mecha_inf1 >= 2 * cuerpo1 and
        mecha_sup1 <= cuerpo1 * 0.3 and
        cuerpo1 <= rango1 * 0.35
    )

    # ── Envolvente alcista ───────────────────────────────────────────────
    vela2_bajista = c2 < o2
    vela1_alcista = c1 > o1
    es_envolvente = (
        vela2_bajista and
        vela1_alcista and
        o1 <= c2 and   # abre por debajo del cierre anterior
        c1 >= o2       # cierra por encima del open anterior
    )

    # ── Estrella de la mañana ────────────────────────────────────────────
    vela3_bajista = c3 < o3 and abs(c3 - o3) > rango2 * 0.5 if rango2 > 0 else False
    vela2_pequena = cuerpo2 <= (h2 - l2) * 0.3 if (h2 - l2) > 0 else False
    vela1_alcista_fuerte = c1 > o1 and cuerpo1 >= abs(c3 - o3) * 0.5
    es_estrella = vela3_bajista and vela2_pequena and vela1_alcista_fuerte and c1 > (o3 + c3) / 2

    if es_estrella:
        return {"patron": "estrella_manana", "desc": "⭐ Estrella de la mañana — reversión alcista fuerte de 3 velas", "ok": True}
    elif es_envolvente:
        return {"patron": "envolvente_alcista", "desc": "🕯️ Envolvente alcista — compradores tomaron control total", "ok": True}
    elif es_martillo:
        return {"patron": "martillo", "desc": "🔨 Martillo — rechazo de mínimos, presión compradora", "ok": True}

    return {"patron": None, "desc": "", "ok": False}

def detectar_divergencia_rsi(closes: list, rsi_series: list, ventana: int = 14) -> dict:
    """
    Divergencia RSI:
    - Alcista: precio hace mínimo más bajo, RSI hace mínimo más alto → reversión probable
    - Bajista: precio hace máximo más alto, RSI hace máximo más bajo → agotamiento
    """
    if not rsi_series or len(rsi_series) < ventana or len(closes) < ventana:
        return {"divergencia": False, "tipo": "", "desc": "", "alcista": False, "bajista": False}

    p  = closes[-ventana:]
    rs = rsi_series[-ventana:]

    precio_min_reciente = min(p[-ventana//2:])
    precio_min_anterior = min(p[:ventana//2])
    rsi_min_reciente    = min(rs[-ventana//2:])
    rsi_min_anterior    = min(rs[:ventana//2])

    precio_max_reciente = max(p[-ventana//2:])
    precio_max_anterior = max(p[:ventana//2])
    rsi_max_reciente    = max(rs[-ventana//2:])
    rsi_max_anterior    = max(rs[:ventana//2])

    div_alcista = (
        precio_min_reciente < precio_min_anterior * 0.99 and
        rsi_min_reciente > rsi_min_anterior + 2
    )
    div_bajista = (
        precio_max_reciente > precio_max_anterior * 1.01 and
        rsi_max_reciente < rsi_max_anterior - 2
    )

    if div_alcista:
        return {"divergencia": True, "tipo": "alcista",
                "desc": "📈 Divergencia RSI alcista — precio baja pero RSI sube, institucionales acumulando",
                "alcista": True, "bajista": False}
    elif div_bajista:
        return {"divergencia": True, "tipo": "bajista",
                "desc": "⚠️ Divergencia RSI bajista — precio sube pero RSI baja, momentum se agota",
                "alcista": False, "bajista": True}

    return {"divergencia": False, "tipo": "", "desc": "", "alcista": False, "bajista": False}

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

    soportes     = [z for z in zonas_b if z["distancia_pct"] <= 0]
    resistencias = [z for z in zonas_a if z["distancia_pct"] >= 0]

    soportes     = sorted(soportes,     key=lambda x: -x["distancia_pct"])
    resistencias = sorted(resistencias, key=lambda x:  x["distancia_pct"])

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

    ctx_color = "#0958d9" if en_zona else "var(--muted)"
    ctx_bg    = "#e6f4ff" if en_zona else "var(--surface2)"
    h = (f'<div style="background:{ctx_bg};border-radius:5px;padding:7px 10px;'
         f'margin-bottom:8px;font-size:11px;color:{ctx_color}">{contexto}</div>')

    h += '<div style="font-size:11px">'

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

    h += (f'<div style="display:flex;align-items:center;gap:8px;padding:5px 0;'
          f'border-bottom:1px solid var(--brd);border-top:2px solid var(--text)">'
          f'<span style="width:20px;font-size:10px;color:var(--text)">◆</span>'
          f'<span style="flex:1;font-family:var(--mono);font-weight:600">'
          f'${precio_actual_mxn:,.2f} <span style="font-size:10px;font-weight:400;color:var(--muted)">precio actual</span></span>'
          f'</div>')

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

    if len(hist_scores) >= 2:
        delta = hist_scores[0] - hist_scores[1]
        if delta > 0:   tend_icon, tend_col, tend_txt = "↑", "var(--green)", f"+{delta} pts"
        elif delta < 0: tend_icon, tend_col, tend_txt = "↓", "var(--red)",   f"{delta} pts"
        else:           tend_icon, tend_col, tend_txt = "→", "var(--muted)", "sin cambio"
    else:
        tend_icon, tend_col, tend_txt = "—", "var(--muted)", "primera sesión"

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
    """Panel de estrategia de salida escalonada — 4 niveles claros."""
    if not gestion:
        return '<p class="hint" style="font-size:11px">Registra tu precio de entrada para ver la gestión</p>'

    color      = gestion.get("color", "var(--muted)")
    estado     = gestion.get("estado_op", "—")
    accion     = gestion.get("accion", "—")
    pct        = gestion.get("ganancia_pct", 0)
    urgente    = gestion.get("urgente", False)
    nivel      = gestion.get("nivel_actual", "0")
    be         = gestion.get("breakeven")
    n1         = gestion.get("nivel1_precio")
    n2         = gestion.get("nivel2_precio")
    n3         = gestion.get("nivel3_precio")
    trail      = gestion.get("trailing_stop")
    pct_col    = "var(--green)" if pct >= 0 else "var(--red)"
    borde      = "2px solid var(--red)" if urgente else f"2px solid {color}"

    def _nivel_dot(n_id, label, precio, activo, completado):
        if completado:
            bg, txt, ring = "#14532d", "#86efac", "var(--green)"
        elif activo:
            bg, txt, ring = "#1e3a5f", "#93c5fd", "#3b82f6"
        else:
            bg, txt, ring = "var(--surface)", "var(--muted)", "var(--brd)"
        icon = "✅" if completado else ("▶" if activo else "○")
        precio_txt = fmt(precio) if precio else "—"
        return (
            f'<div style="display:flex;flex-direction:column;align-items:center;gap:3px;flex:1">'
            f'<div style="width:28px;height:28px;border-radius:50%;background:{bg};border:2px solid {ring};'
            f'display:flex;align-items:center;justify-content:center;font-size:12px">{icon}</div>'
            f'<div style="font-size:9px;font-weight:700;color:{txt};text-align:center">{label}</div>'
            f'<div style="font-size:9px;color:var(--muted);font-family:var(--mono)">{precio_txt}</div>'
            f'</div>'
        )

    en_stop    = nivel == "stop"
    en_n0      = nivel == "0"
    en_n1      = nivel == "1"
    en_n2      = nivel == "2"
    en_n3      = nivel == "3"
    en_n4      = nivel == "4"

    dot1 = _nivel_dot("1", "+9%\n25%", n1,
                       activo=en_n1, completado=en_n2 or en_n3 or en_n4)
    dot2 = _nivel_dot("2", "+15%\n25%", n2,
                       activo=en_n2, completado=en_n3 or en_n4)
    dot3 = _nivel_dot("3", "EMA200\n25%", n3,
                       activo=en_n3, completado=en_n4)
    dot4 = _nivel_dot("4", "Trail\nCorrer", trail,
                       activo=en_n4, completado=False)

    linea = f'<div style="flex:0 0 20px;height:2px;background:var(--brd);margin-top:13px"></div>'
    mapa = (
        f'<div style="display:flex;align-items:flex-start;gap:0;margin:10px 0 4px">'
        f'{dot1}{linea}{dot2}{linea}{dot3}{linea}{dot4}'
        f'</div>'
    )

    stats = (
        f'<div style="display:flex;gap:10px;flex-wrap:wrap;font-size:11px;margin-top:8px">'
        f'<div><span style="color:var(--muted)">P&L: </span>'
        f'<span style="color:{pct_col};font-weight:700;font-family:var(--mono)">{pct:+.1f}%</span></div>'
    )
    if be:
        stats += f'<div><span style="color:var(--muted)">Breakeven: </span><span style="font-family:var(--mono)">{fmt(be)}</span></div>'
    if stop_mxn:
        stats += f'<div><span style="color:var(--muted)">Stop: </span><span style="font-family:var(--mono);color:var(--red)">{fmt(stop_mxn)}</span></div>'
    if trail and (en_n4):
        stats += f'<div><span style="color:var(--muted)">Trailing: </span><span style="font-family:var(--mono);color:#7c3aed">{fmt(trail)}</span></div>'
    stats += '</div>'

    return (
        f'<div style="border-left:3px solid {color};padding:10px 14px;background:var(--surface2);border-radius:0 8px 8px 0">'
        f'<div style="font-weight:700;font-size:12px;color:{color};margin-bottom:4px">{estado}</div>'
        f'<div style="font-size:11px;line-height:1.5;margin-bottom:6px">{accion}</div>'
        f'{mapa}'
        f'{stats}'
        f'</div>'
    )

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

    if precio < ema9:
        razones.append(f"Precio ${precio:.2f} < EMA9 ${ema9:.2f} — soporte perdido")
        nivel = "warning"

    if rsi_val < 50:
        razones.append(f"RSI {rsi_val:.0f} < 50 — momentum muerto")
        nivel = "warning"

    if not criterios.get("macd", {}).get("ok", True):
        razones.append("MACD bajista — vendedores en control")

    score_drop = analizar_score_drop(ticker, score_actual)
    caida_score = score_drop["caida_pts"]
    if score_drop["severidad"] in ("alert", "critical"):
        razones.append(f"Score Drop {score_drop['severidad'].upper()}: {score_drop['desc']}")
        nivel = "exit" if score_drop["severidad"] == "critical" else "warning"

    precio_bajo = precio < ema9
    rsi_bajo    = rsi_val < 50
    if len(razones) >= 3 or score_drop["severidad"] == "critical" or (precio_bajo and rsi_bajo):
        nivel = "exit"

    return {"exit": nivel == "exit", "nivel": nivel, "razones": razones,
            "score_drop": score_drop}

# ══════════════════════════════════════════════════════════
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

    if adx_v < 20:
        bloqueadores.append(f"ADX {adx_v:.0f} < 20 — mercado lateral, señales técnicas no son fiables")
        pasa_filtros = False

    distancia_ema9_pct = (ema9 - precio) / ema9 * 100 if ema9 > 0 else 0
    if precio < ema9 * 0.97:  # más de 3% bajo EMA9
        bloqueadores.append(f"Precio ${precio:.2f} muy bajo EMA9 ${ema9:.2f} ({distancia_ema9_pct:.1f}% abajo) — entrada en debilidad prohibida")
        pasa_filtros = False

    if not c.get("soporte", True):
        bloqueadores.append(f"Soporte ${soporte:.2f} roto — señal de ruptura bajista")
        pasa_filtros = False

    if not vol_ok:
        bloqueadores.append("Volumen < 1.5x media — movimiento sin confirmación institucional")
        pasa_filtros = False

    if not spy_ok:
        bloqueadores.append("S&P500 bajo EMA200 — mercado bajista macro, no comprar")
        pasa_filtros = False

    if etf_3x and vix > 20:
        bloqueadores.append(f"ETF 3x con VIX {vix:.1f} > 20 — volatility decay activo, prohibido")
        pasa_filtros = False

    # ── ADVERTENCIAS (no bloquean pero reducen confianza) ─────────────────

    tf_1w = tfs.get("1W", {})
    w1_senal = tf_1w.get("senal") if tf_1w.get("valido") else None
    w1_score = tf_1w.get("score", 0) if tf_1w.get("valido") else None

    if w1_senal == "VENDER":
        bloqueadores.append(f"1W en VENDER (score {w1_score}/10) — tendencia semanal bajista, NO entrar")
        pasa_filtros = False

    score_drop = analizar_score_drop(nombre, score)
    if score_drop["severidad"] == "critical":
        bloqueadores.append(f"SCORE DROP CRITICO: {score_drop['desc']}")
        pasa_filtros = False
    elif score_drop["severidad"] == "alert":
        advertencias.append(f"Score Drop ALERTA: {score_drop['desc']}")
    elif score_drop["severidad"] == "warning":
        advertencias.append(f"Score Drop: {score_drop['desc']}")

    estructura_1d = tf_1d.get("estructura", {})
    if estructura_1d.get("estructura") == "bajista":
        bloqueadores.append(f"Estructura LH+LL en 1D — price action bajista confirmado, NO entrar")
        pasa_filtros = False

    if rsi_v > 72:
        advertencias.append(f"RSI {rsi_v:.0f} > 72 — sobrecomprado, riesgo de pullback inmediato")

    if rr_v < 3:
        advertencias.append(f"R:R {rr_v:.1f}x < 3x — recompensa insuficiente para el riesgo")

    # ── DETECTAR ESTADO BASE ──────────────────────────────────────────────

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

    precio_cerca_ema9 = abs(precio - ema9) / ema9 < 0.015   # dentro del 1.5% de EMA9
    nuevo_maximo_20   = precio >= obj * 0.97                 # cerca del máximo de 20 velas
    adx_acelerando    = adx_v >= 25

    vol_rel = tf_1d.get("vol_rel", 0)
    vol_ok_breakout = vol_ok and vol_rel >= 1.5
    if nuevo_maximo_20 and adx_acelerando and vol_ok_breakout:
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

    pivot_highs = []
    pivot_lows  = []
    for i in range(2, n - 2):
        if h[i] > h[i-1] and h[i] > h[i-2] and h[i] > h[i+1] and h[i] > h[i+2]:
            pivot_highs.append(h[i])
        if l[i] < l[i-1] and l[i] < l[i-2] and l[i] < l[i+1] and l[i] < l[i+2]:
            pivot_lows.append(l[i])

    ph = pivot_highs[-n_pivotes:] if len(pivot_highs) >= 2 else []
    pl = pivot_lows[-n_pivotes:]  if len(pivot_lows)  >= 2 else []

    if len(ph) < 2 or len(pl) < 2:
        return {"estructura":"indefinida","hh":False,"hl":False,
                "lh":False,"ll":False,"desc":"Pocos pivotes detectados","score_extra":0}

    hh = all(ph[i] > ph[i-1] for i in range(1, len(ph)))   # cada maximo mayor al anterior
    hl = all(pl[i] > pl[i-1] for i in range(1, len(pl)))   # cada minimo mayor al anterior
    lh = all(ph[i] < ph[i-1] for i in range(1, len(ph)))   # cada maximo menor al anterior
    ll = all(pl[i] < pl[i-1] for i in range(1, len(pl)))   # cada minimo menor al anterior

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
                highs=None, lows=None, opens=None) -> dict:
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
    mh_ant = float(mh.iloc[-2]) if len(mh) >= 2 else mh_v   # histograma anterior
    rv     = float(rsi(c).iloc[-1])
    rv_ant = float(rsi(c).iloc[-2]) if n >= 2 else rv         # RSI anterior

    adx_val = 0.0
    if highs and lows and len(highs) == len(closes):
        try: adx_val = adx(highs, lows, closes)
        except Exception: adx_val = 0.0

    estructura_info = {"estructura":"indefinida","hh":False,"hl":False,
                       "lh":False,"ll":False,"desc":"","score_extra":0}
    if highs and lows and len(highs) == len(closes):
        try: estructura_info = detectar_estructura_hhhl(highs, lows, n_pivotes=5)
        except Exception: pass

    vol_now = float(v.iloc[-1])
    vol_avg = float(v.rolling(min(20,n)).mean().iloc[-1])
    _sin_volumen = (vol_avg < 1.0)
    vol_ok   = _sin_volumen or (vol_now >= vol_avg * 1.5)

    obv_info = obv(closes, volumes) if not _sin_volumen else {"tendencia":"sin datos","divergencia":False,"div_tipo":"","ok":True}

    if obv_info.get("div_alcista"):
        obv_info["ok"] = True   # acumulación institucional — es buena señal
    elif obv_info.get("div_bajista"):
        obv_info["ok"] = False  # distribución institucional — señal negativa

    rsi_series = list(rsi(c, 14))
    div_rsi = detectar_divergencia_rsi(closes, rsi_series)

    opens_list = opens if opens and len(opens) >= 3 else None
    patron_velas = detectar_patrones_velas(
        opens_list or closes, highs or closes, lows or closes, closes
    ) if opens_list else {"patron": None, "desc": "", "ok": False}

    sector_ok = True   # se sobreescribe en analizar_ticker si aplica

    precio   = float(c.iloc[-1])
    soporte  = float(c.rolling(min(20,n)).min().iloc[-1])
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

    # ── OBJETIVO REALISTA POR ATR + EMA50/EMA200 ─────────────────────────
    if atr_val > 0:
        objetivo_atr = precio + (atr_val * 3.0)
        objetivo = min(objetivo_atr, max_20) if max_20 < objetivo_atr else objetivo_atr
    else:
        objetivo = max_20   # fallback si no hay ATR

    _objetivo_fuente = "ATR"  # fuente por defecto

    if objetivo <= precio * 1.005 and e50 > precio:
        objetivo = e50
        _objetivo_fuente = "EMA50"
    elif objetivo <= precio * 1.005 and e50 <= precio and e200 > precio:
        objetivo = e200
        _objetivo_fuente = "EMA200"
    elif e50 > precio and e50 > objetivo * 1.05:
        objetivo = e50
        _objetivo_fuente = "EMA50"

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

    mult = tc if origen == "USA" else 1.0

    criterios = {
        "emas":   {"ok":emas_ok,  "label":"EMAs 9>21>50",
                   "val":f"${e9*mult:,.2f}/${e21*mult:,.2f}/${e50*mult:,.2f}",
                   "razon":f"Precio ${precio*mult:,.2f} {'>' if precio > e9 else '<'} EMA9 ${e9*mult:,.2f}"},
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
                   "val":f"{rr_val:.1f}x ({_objetivo_fuente})",
                   "razon":(f"Stop ${stop*mult:,.2f} MXN · Objetivo ${objetivo*mult:,.2f} MXN [{_objetivo_fuente}] · "
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
        "obv":    {"ok":obv_info["ok"],"label":"OBV alcista/acumulación",
                   "val":obv_info["tendencia"],
                   "razon":(obv_info["div_tipo"] if obv_info["divergencia"]
                            else f"OBV {obv_info['tendencia']} — flujo institucional consistente con precio.")},
        "div_rsi":{"ok": div_rsi["alcista"] or not div_rsi["bajista"],
                   "label":"Divergencia RSI",
                   "val": div_rsi["tipo"] if div_rsi["divergencia"] else "sin divergencia",
                   "razon": div_rsi["desc"] if div_rsi["divergencia"] else "RSI confirma movimiento del precio — sin señales de agotamiento."},
        "patron_velas": {"ok": patron_velas["ok"],
                   "label":"Patrón de vela",
                   "val": patron_velas["patron"] or "ninguno",
                   "razon": patron_velas["desc"] if patron_velas["ok"] else "Sin patrón de reversión alcista en las últimas 3 velas."},
    }
    score = sum(1 for x in criterios.values() if x["ok"])
    total_criterios = len(criterios)   # 13 criterios ahora
    explosion = (emas_ok and e200_ok and macd_ok and macdh_ok and 55<=rv<=72
                 and vol_ok and rr_val>=4.0 and adx_val>=25 and hhhl_ok
                 and obv_info["ok"] and (div_rsi["alcista"] or not div_rsi["bajista"]))

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
        "rsi":rv,"rsi_anterior":rv_ant,"macd_alcista":macd_ok,"ml":ml_v,"ms":ms_v,"mh":mh_v,
        "macd_hist":mh_v,"macd_hist_ant":mh_ant,
        "vol_rel":(vol_now/vol_avg if vol_avg > 0 else 0),
        "adx":adx_val,"atr":round(atr_val,4),"trailing_stop":round(stop,4),
        "estructura":estructura_info,
        "obv":obv_info,
        "div_rsi":div_rsi,
        "patron_velas":patron_velas,
        "rr":rr_val,"stop":stop,"objetivo":objetivo,"objetivo_fuente":_objetivo_fuente,"soporte":soporte,"vol_ok":vol_ok,
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
            result = api_timeseries(symbol, interval, 200, exchange, key=k)
            if result:
                break
        _TD_CACHE[key] = result

    elif _TD_CACHE[key] is None:
        print(f"    [retry] {symbol} {interval} — esperando 3s y reintentando...")
        time.sleep(3)
        for k in (_TD_KEYS or [""]):
            if k in _KEYS_AGOTADAS:
                continue
            result = api_timeseries(symbol, interval, 200, exchange, key=k)
            if result:
                _TD_CACHE[key] = result
                break

    return _TD_CACHE[key]

def _precargar_cache_batch(symbols: list, intervals: list = None):
    """
    Precarga el cache con batches de hasta 8 símbolos.
    Usa KEY_1 para TODO. Solo cambia a KEY_2 si KEY_1 se agota.
    No alterna por chunk — eso gastaba ambas keys al mismo tiempo.
    """
    global _TD_CACHE, _KEY_IDX, _KEYS_AGOTADAS
    if intervals is None:
        intervals = ["1day", "1week"]

    syms = [s.upper() for s in symbols if s]
    seen = set(); syms = [s for s in syms if not (s in seen or seen.add(s))]
    CHUNK  = 12   # plan Grow permite 55+ calls/min — chunks más grandes
    n_keys = len(_TD_KEYS)

    print(f"  [batch] {len(syms)} tickers × {len(intervals)} intervalos | "
          f"{n_keys} key(s) disponibles | chunks de {CHUNK}")

    def _key_activa() -> str:
        """Devuelve la primera key no agotada."""
        for k in _TD_KEYS:
            if k not in _KEYS_AGOTADAS:
                return k
        return _TD_KEYS[0] if _TD_KEYS else ""

    for interval in intervals:
        chunks = [syms[i:i+CHUNK] for i in range(0, len(syms), CHUNK)]
        for idx, chunk in enumerate(chunks):
            key_use = _key_activa()
            if not key_use:
                print(f"  [batch] ⚠️ Sin keys disponibles — abortando batch")
                break

            print(f"  [batch] {interval} chunk {idx+1}/{len(chunks)} "
                  f"k=…{key_use[-4:]} → {', '.join(chunk)}")

            batch = api_timeseries_batch(chunk, interval, outputsize=200, key=key_use)

            faltantes = [s for s in chunk if s.upper() not in batch]
            if faltantes:
                print(f"  [batch] {len(faltantes)} faltantes — reintentando individualmente...")
                time.sleep(3)
                for sym_f in faltantes:
                    key2 = _key_activa()
                    if not key2:
                        break
                    vals = api_timeseries(sym_f, interval, 200, key=key2)
                    if vals:
                        batch[sym_f.upper()] = vals
                        print(f"  [batch] ✅ Recuperado {sym_f}")
                    time.sleep(1)

            for sym, vals in batch.items():
                _TD_CACHE[f"{sym}:{interval}"] = vals

            for sym in chunk:
                if f"{sym}:{interval}" not in _TD_CACHE:
                    _TD_CACHE[f"{sym}:{interval}"] = None

            if idx < len(chunks) - 1:
                time.sleep(1)

        time.sleep(1)   # pausa entre intervalos — plan Grow aguanta

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
    opens_1d   = [float(x.get("open", x["close"])) for x in values_1d]

    tf_1d = analizar_tf(closes_1d, volumes_1d, "1D", capital, riesgo_pct, rr_min,
                         titulos_en_cartera, tc=tc, origen=origen,
                         highs=highs_1d, lows=lows_1d, opens=opens_1d)

    # ── ZONAS DE SOPORTE / RESISTENCIA ────────────────────────────────────
    sr = calcular_zonas_sr(highs_1d, lows_1d, closes_1d, volumes_1d, tc=tc, origen=origen)

    score_1d = tf_1d.get("score", 0)
    tfs = {"1D": tf_1d, "1H": {"tf":"1H","valido":False}, "1W": {"tf":"1W","valido":False}}

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
    if not posiciones:
        return []

    tickers_db = get_tickers_db()

    syms_port = []
    for pos in posiciones:
        ticker = pos["ticker"]
        symbol, exchange = tickers_db.get(
            ticker,
            (ticker.replace(" CPO","CPO").replace(" ",""),
             "BMV" if pos["origen"] == "MX" else "")
        )
        key_1d = f"{symbol.upper()}:1day"
        key_1w = f"{symbol.upper()}:1week"
        if key_1d not in _TD_CACHE or _TD_CACHE[key_1d] is None:
            syms_port.append(symbol)

    if syms_port:
        print(f"  [portafolio] Precargando {len(syms_port)} ticker(s) del portafolio...")
        _precargar_cache_batch(list(set(syms_port)), ["1day", "1week"])

    resultados = []

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

        # ── RECOMENDACIÓN CLARA para el portafolio ──────────────────────
        recomendacion = _calcular_recomendacion_port(
            pos, precio_mxn, cto_mxn, tf_1d, mult, pl_pct
        )

        resultados.append({**pos, "analisis":an,
                "precio_actual_usd":precio_usd,"precio_actual_mxn":precio_mxn,
                "valor_mxn":valor_mxn,"costo_total":costo_total,"pl_mxn":pl_mxn,"pl_pct":pl_pct,
                "alertas":alertas,"entrada_mxn":entrada_mxn,"stop_mxn":stop_mxn,"obj_mxn":obj_mxn,
                "recomendacion":recomendacion})
    return resultados

def _calcular_recomendacion_port(pos, precio_mxn, cto_mxn, tf_1d, mult, pl_pct) -> dict:
    """
    Genera una recomendación clara y accionable para cada posición del portafolio.
    SALIR / MANTENER / AGREGAR — con precio objetivo y stop.
    """
    if not precio_mxn or not tf_1d.get("valido"):
        return {"accion": "SIN DATOS", "color": "var(--muted)", "icono": "—",
                "mensaje": "Sin precio actual — verifica tu API key", "stop": None, "objetivo": None}

    rsi     = tf_1d.get("rsi", 50)
    score   = tf_1d.get("score", 0)
    total_c = tf_1d.get("total_criterios", 11)
    stop    = tf_1d.get("stop", 0) * mult
    obj     = tf_1d.get("objetivo", 0) * mult
    senal   = tf_1d.get("senal", "MANTENER")
    adx     = tf_1d.get("adx", 0)
    macd_ok = tf_1d.get("macd_alcista", False)

    dist_stop_pct = ((precio_mxn - stop) / precio_mxn * 100) if stop else 0
    dist_obj_pct  = ((obj - precio_mxn) / precio_mxn * 100) if obj else 0

    if senal == "VENDER" or (score <= 3) or (rsi < 35 and pl_pct < -5):
        return {
            "accion":   "SALIR",
            "color":    "var(--red)",
            "icono":    "🚨",
            "mensaje":  f"Indicadores deteriorados (score {score}/{total_c}, RSI {rsi:.0f}). Stop en {fmt(stop)}.",
            "stop":     stop,
            "objetivo": obj,
        }

    if obj and precio_mxn >= obj * 0.92 and pl_pct > 3:
        return {
            "accion":   "TOMAR GANANCIAS",
            "color":    "#d46b08",
            "icono":    "💰",
            "mensaje":  f"Precio cerca del objetivo {fmt(obj)} con +{pl_pct:.1f}% de ganancia. Considera cerrar 50-75% de la posición.",
            "stop":     stop,
            "objetivo": obj,
        }

    if senal == "COMPRAR" and score >= 7 and macd_ok and adx >= 20 and pl_pct > -3:
        return {
            "accion":   "AGREGAR",
            "color":    "var(--green)",
            "icono":    "✅",
            "mensaje":  f"Señal alcista confirmada. Entrada en {fmt(precio_mxn)}. Objetivo {fmt(obj)} (+{dist_obj_pct:.1f}%).",
            "stop":     stop,
            "objetivo": obj,
        }

    if pl_pct < -3 and pl_pct >= -8:
        return {
            "accion":   "VIGILAR",
            "color":    "var(--yellow)",
            "icono":    "⚠️",
            "mensaje":  f"Pérdida de {pl_pct:.1f}%. Stop en {fmt(stop)} — si lo toca, salir sin dudar.",
            "stop":     stop,
            "objetivo": obj,
        }

    if pl_pct < -8:
        return {
            "accion":   "SALIR",
            "color":    "var(--red)",
            "icono":    "🚨",
            "mensaje":  f"Pérdida de {pl_pct:.1f}%. Stop en {fmt(stop)}. Salir para proteger capital.",
            "stop":     stop,
            "objetivo": obj,
        }

    estado_pl = f"+{pl_pct:.1f}% ganancia" if pl_pct > 0 else f"{pl_pct:.1f}% pérdida pequeña"
    return {
        "accion":   "MANTENER",
        "color":    "var(--yellow)",
        "icono":    "→",
        "mensaje":  f"{estado_pl}. Stop en {fmt(stop)} ({dist_stop_pct:.1f}% abajo). Objetivo {fmt(obj)} (+{dist_obj_pct:.1f}%).",
        "stop":     stop,
        "objetivo": obj,
    }

def correr_scanner(tc, capital, riesgo_pct, rr_min, tickers_extra: dict | None = None,
                   vix: float = 20.0, spy: dict | None = None):
    global _TD_CACHE, _KEYS_AGOTADAS, _KEY_IDX
    _TD_CACHE      = {}
    _KEY_IDX       = 0

    if spy is None: spy = {}
    regimen = regimen_mercado(vix, spy)

    port_map   = {p["ticker"]: p["titulos"] for p in get_portafolio()}
    tickers_db = get_tickers_db()
    combinados: dict = get_all_scanner_tickers()   # lee DB fresh en cada build
    if tickers_extra:
        combinados.update(tickers_extra)

    n_db  = len([k for k in tickers_db if k not in SCANNER_TICKERS])
    n_ext = len([k for k in (tickers_extra or {}) if k not in combinados])
    print(f"  Scanner: {len(combinados)} tickers ({len(SCANNER_TICKERS)} base + {n_db} DB + {n_ext} extra)")
    print(f"  Régimen: {regimen['label']} | VIX={vix:.1f} | SPY {'✅' if spy.get('sobre_ema200') else '❌'} EMA200")

    # ── PRECARGAR CACHE CON BATCH ─────────────────────────────────────────
    _sector_etfs = []  # No precargar sector ETFs — ahorrar créditos de API
    syms_usa = list(set([v[0] for v in combinados.values() if v[1] != "BMV"]))
    syms_bmv = [(v[0], v[1]) for v in combinados.values() if v[1] == "BMV"]

    _precargar_cache_batch(syms_usa, ["1day", "1week"])

    syms_semis = [v[0] for v in _SEMIS_CACHE_TICKERS.values()
                  if v[0] not in syms_usa]  # solo los que no están ya en scanner
    if syms_semis:
        _precargar_cache_batch(syms_semis, ["1day"])
    if syms_bmv:
        print(f"  [batch] {len(syms_bmv)} tickers BMV — cargando individualmente con exchange=BMV")
        for sym_bmv, exch_bmv in syms_bmv:
            for interval in ["1day", "1week"]:
                cache_key = f"{sym_bmv.upper()}:{interval}"
                if cache_key not in _TD_CACHE or _TD_CACHE[cache_key] is None:
                    vals = api_timeseries(sym_bmv, interval, 200, exchange=exch_bmv)
                    _TD_CACHE[cache_key] = vals
                    if vals:
                        print(f"  [batch] ✅ BMV {sym_bmv} {interval} — {len(vals)} velas")
                    else:
                        print(f"  [batch] ❌ BMV {sym_bmv} {interval} — sin datos")
                    time.sleep(1)

    resultados = []
    for nombre, (symbol, exchange) in combinados.items():
        try:
            tit = port_map.get(nombre, 0.0)
            origen_ticker = "MX" if exchange == "BMV" else "USA"
            an  = analizar_ticker_1d(nombre, symbol, exchange, capital, riesgo_pct, rr_min,
                                     tit, tc=tc, origen=origen_ticker)
            tf_1d = an["tf"].get("1D", {})
            if not tf_1d.get("valido"):
                resultados.append({
                    "nombre": nombre, "estado": "SIN DATOS",
                    "precio_usd": None, "precio_mxn": None,
                    "entrada_mxn": None, "stop_mxn": None, "obj_mxn": None,
                    "rsi": 0, "rr": 0, "macd_ok": False, "ema200_ok": False,
                    "score": 0, "score_ajustado": 0, "total_criterios": 11,
                    "criterios": {}, "sizing": {}, "tfs": an["tf"],
                    "confluencia": {}, "titulos_cartera": port_map.get(nombre, 0),
                    "etf_apalancado": False, "exit_info": {}, "vix": vix,
                    "regimen": regimen, "adx": 0, "obv": {}, "sector": {},
                    "setup": {"estado": "SIN DATOS", "decision_final": "Sin datos de API",
                              "bloqueadores": [], "advertencias": [], "confianza": 0,
                              "tipo_setup": "—"},
                    "sr": {}, "dca": {}, "ganga": {}, "inicio": {},
                })
                continue

            precio_usd    = tf_1d["precio"]
            etf_peligroso = es_etf_apalancado(nombre)
            exit_info     = detectar_exit(nombre, tf_1d, tf_1d.get("score", 0))

            # ── SECTOR ETF ────────────────────────────────────────────────
            sector_info   = get_sector_estado(nombre)

            setup         = evaluar_setup(nombre, tf_1d, an["tf"], vix, spy, tit, exit_info)
            estado        = setup["estado"]
            score         = tf_1d["score"]
            score_ajustado= setup.get("score_ajustado", max(0, score - regimen["penalizacion"]))

            etf_sector = sector_info.get("etf")
            if etf_sector:
                if sector_info["alcista"]:
                    score_ajustado = min(tf_1d["total_criterios"], score_ajustado + 1)
                    setup["advertencias"] = [a for a in setup.get("advertencias", []) if etf_sector not in a]
                else:
                    score_ajustado = max(0, score_ajustado - 1)
                    if estado == "BUY":
                        estado = "WATCH"
                    setup["advertencias"].append(
                        f"Sector {etf_sector} bajista — esperar recuperación del sector")

            # ── GANGA: señal adicional de precio (no cambia el estado) ──
            ganga_info = {}
            if not etf_peligroso:
                ganga_info = detectar_ganga(
                    tf_1d,
                    an.get("sr", {}),
                    tf_1d.get("objetivo", 0) * tc,
                    precio_usd * tc
                )

            # ── INICIO DE MOVIMIENTO: anticipación al breakout ──
            inicio_info = detectar_inicio_movimiento(tf_1d)

            # ── CAPITULACIÓN: cuchillo que se detiene ──────────────────────
            values_raw = _get_cached(symbol, "1day", exchange) or []
            cap_info = detectar_capitulacion(tf_1d, values_raw)

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
                "ema200_mxn": round(tf_1d.get("ema200", 0) * tc, 2),
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
                "inicio": inicio_info,
                "capitulacion": cap_info,
            })
        except Exception as e:
            print(f"  [scanner] ❌ Error procesando {nombre}: {e}")
            continue

    orden = {"ROCKET":0,"BUY":1,"EXIT":2,"WATCH":3,"LATERAL":4,"SKIP":5,"SHORT":6,"RUPTURA":7,"BLOQUEADO":8}
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

    _radar_start  = time.time()
    _RADAR_TIMEOUT = 540  # 9 minutos — suficiente para 17 tickers con pausas batch

    # Precargar TODO el universo del radar + sector ETFs en batch desde el inicio.
    _sector_etfs_r = []  # No precargar sector ETFs en radar — ahorrar créditos
    todos_radar    = list(set([v[0] for v in universo_completo.values()]))
    syms_sin_cache = [s for s in todos_radar if f"{s.upper()}:1day" not in _TD_CACHE]
    syms_sin_cache_usa = [s for s in syms_sin_cache
                          if universo_completo.get(s, ("", ""))[1] != "BMV"]
    syms_sin_cache_bmv = [(s, universo_completo[s][1]) for s in syms_sin_cache
                          if universo_completo.get(s, ("", ""))[1] == "BMV"]
    if syms_sin_cache_usa:
        print(f"  [Radar batch] Precargando {len(syms_sin_cache_usa)} tickers USA...")
        _precargar_cache_batch(syms_sin_cache_usa, ["1day"])
    if syms_sin_cache_bmv:
        print(f"  [Radar batch] {len(syms_sin_cache_bmv)} tickers BMV — individualmente...")
        for sym_b, exch_b in syms_sin_cache_bmv:
            cache_key = f"{sym_b.upper()}:1day"
            if cache_key not in _TD_CACHE or _TD_CACHE[cache_key] is None:
                vals = api_timeseries(sym_b, "1day", 200, exchange=exch_b)
                _TD_CACHE[cache_key] = vals
                time.sleep(1)

    resultados = []
    for i, (nombre, (symbol, exchange)) in enumerate(universo_completo.items()):
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

        sr_radar = calcular_zonas_sr(highs, lows, closes, volumes, tc=tc, origen="USA")

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

        if not sector_info["alcista"] and estado == "BUY":
            estado = "WATCH"
            setup["advertencias"].append(
                f"Sector {sector_info['etf']} bajista — esperar recuperación del sector")

        # ── GANGA: señal adicional de precio (no cambia el estado) ──
        ganga_info_r = {}
        if not es_etf_apalancado(nombre):
            ganga_info_r = detectar_ganga(
                tf,
                sr_radar,
                tf.get("objetivo", 0) * tc,
                precio * tc
            )

        # ── INICIO DE MOVIMIENTO en radar ──
        inicio_info_r = detectar_inicio_movimiento(tf)

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
            "ema200_mxn":round(tf.get("ema200",0)*tc,2),
            "objetivo_fuente":tf.get("objetivo_fuente","ATR"),
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
            "inicio":inicio_info_r,
            "vix":vix,"regimen":regimen_mercado(vix,spy),
        })

    print(f"  Radar completo: {len(resultados)} de {total} analizadas")
    orden = {"ROCKET":0,"BUY":1,"EXIT":2,"WATCH":3,"LATERAL":4,"SKIP":5,"SHORT":6,"RUPTURA":7,"BLOQUEADO":8}
    resultados.sort(key=lambda x:(orden.get(x["estado"],9),-x["pot_alza"]))
    return resultados

# ══════════════════════════════════════════════════════════
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

    conf_color = "#52c41a" if confianza>=70 else "#faad14" if confianza>=40 else "#ff4d4f"
    conf_bar   = (f'<div style="margin:8px 0 4px"><div style="font-size:10px;color:var(--muted);margin-bottom:3px">'
                  f'Confianza del setup: {confianza}%</div>'
                  f'<div style="height:5px;background:var(--brd2);border-radius:3px;overflow:hidden">'
                  f'<div style="height:100%;width:{confianza}%;background:{conf_color};border-radius:3px"></div></div></div>')

    bloqueos_html = ""
    if bloqueos:
        items = "".join(f'<li style="margin:2px 0">{b}</li>' for b in bloqueos)
        bloqueos_html = f'<ul style="margin:6px 0 0 14px;font-size:10px;color:#7f1d1d">{items}</ul>'

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

def _render_rec_badge(rec: dict) -> str:
    """Badge compacto de recomendación para la tabla del portafolio."""
    if not rec or rec.get("accion") == "SIN DATOS":
        return '<span class="hint" style="font-size:10px">Sin análisis</span>'
    accion  = rec.get("accion", "—")
    color   = rec.get("color", "var(--muted)")
    icono   = rec.get("icono", "—")
    stop    = rec.get("stop")
    objetivo= rec.get("objetivo")
    bg_map  = {
        "SALIR":          "#fff1f0",
        "AGREGAR":        "#f0fdf4",
        "TOMAR GANANCIAS":"#fffbeb",
        "MANTENER":       "#fffbeb",
    }
    brd_map = {
        "SALIR":          "#fca5a5",
        "AGREGAR":        "#86efac",
        "TOMAR GANANCIAS":"#fde68a",
        "MANTENER":       "#fde68a",
    }
    bg  = bg_map.get(accion, "var(--surface2)")
    brd = brd_map.get(accion, "var(--brd)")
    lines = [f'<div style="font-weight:700;font-size:11px;color:{color}">{icono} {accion}</div>']
    if stop:
        lines.append(f'<div style="font-size:10px;color:var(--muted)">Stop: <span style="color:var(--red);font-family:var(--mono)">{fmt(stop)}</span></div>')
    if objetivo:
        lines.append(f'<div style="font-size:10px;color:var(--muted)">Meta: <span style="color:var(--green);font-family:var(--mono)">{fmt(objetivo)}</span></div>')
    return (f'<div style="background:{bg};border:1px solid {brd};border-radius:6px;'
            f'padding:5px 8px;min-width:110px">{"".join(lines)}</div>')

def _render_rec_panel(rec: dict) -> str:
    """Panel completo de recomendación en el detail del portafolio."""
    if not rec or rec.get("accion") == "SIN DATOS":
        return ""
    accion  = rec.get("accion", "—")
    color   = rec.get("color", "var(--muted)")
    icono   = rec.get("icono", "—")
    mensaje = rec.get("mensaje", "")
    bg_map  = {"SALIR":"#fff1f0","AGREGAR":"#f0fdf4","TOMAR GANANCIAS":"#fffbeb","MANTENER":"#fffbeb"}
    brd_map = {"SALIR":"#fca5a5","AGREGAR":"#86efac","TOMAR GANANCIAS":"#fde68a","MANTENER":"#fde68a"}
    bg  = bg_map.get(accion, "var(--surface2)")
    brd = brd_map.get(accion, "var(--brd)")
    return (f'<div style="background:{bg};border:2px solid {brd};border-radius:8px;'
            f'padding:12px 14px;margin-bottom:10px">'
            f'<div style="font-weight:700;font-size:14px;color:{color};margin-bottom:4px">'
            f'{icono} {accion}</div>'
            f'<div style="font-size:12px;color:{color};opacity:.9">{mensaje}</div>'
            f'</div>')

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

        score_block = render_score_badge(score, total_c, senal) if tf_1d.get("valido") else ""

        # ── S/R del portafolio ───────────────────────────────────────
        sr_port = an.get("sr", {})
        sr_html_port = render_zonas_sr(sr_port, pos.get("precio_actual_mxn") or pos["cto_prom_mxn"], tc)
        stop_sr_port = sr_port.get("stop_sr")
        obj_sr_port  = sr_port.get("objetivo_sr")
        precio_ref   = pos.get("precio_actual_mxn") or pos["cto_prom_mxn"]
        ema200_port  = round(tf_1d.get("ema200", 0) * tc, 2) if tf_1d.get("valido") else 0
        lines_port = []
        if stop_sr_port:
            lines_port.append(f'<div style="white-space:nowrap"><span style="color:var(--green);font-size:9px;font-weight:600">S</span> '
                     f'<span style="color:var(--green);font-family:var(--mono);font-size:11px">{fmt(stop_sr_port)}</span>'
                     f'<span style="color:var(--muted);font-size:9px"> ({(stop_sr_port-precio_ref)/precio_ref*100:.1f}%)</span></div>')
        if obj_sr_port:
            lines_port.append(f'<div style="white-space:nowrap"><span style="color:var(--red);font-size:9px;font-weight:600">R</span> '
                     f'<span style="color:var(--red);font-family:var(--mono);font-size:11px">{fmt(obj_sr_port)}</span>'
                     f'<span style="color:var(--muted);font-size:9px"> (+{(obj_sr_port-precio_ref)/precio_ref*100:.1f}%)</span></div>')
        if ema200_port and ema200_port > 0 and precio_ref > 0:
            e200_pct_p = (ema200_port - precio_ref) / precio_ref * 100
            if e200_pct_p >= 0:
                lines_port.append(f'<div style="white-space:nowrap"><span style="color:#ff7875;font-size:9px;font-style:italic">R EMA200</span> '
                         f'<span style="color:#ff7875;font-family:var(--mono);font-size:11px">{fmt(ema200_port)}</span>'
                         f'<span style="color:var(--muted);font-size:9px"> (+{e200_pct_p:.1f}%)</span></div>')
            else:
                lines_port.append(f'<div style="white-space:nowrap"><span style="color:#73d13d;font-size:9px;font-style:italic">S EMA200</span> '
                         f'<span style="color:#73d13d;font-family:var(--mono);font-size:11px">{fmt(ema200_port)}</span>'
                         f'<span style="color:var(--muted);font-size:9px"> ({e200_pct_p:.1f}%)</span></div>')
        sr_inline_port = f'<div style="font-size:10px;line-height:1.9">{"".join(lines_port)}</div>' if lines_port else '<span class="hint" style="font-size:10px">—</span>'

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

        obv_port  = tf_1d.get("obv", {}) if tf_1d.get("valido") else {}
        obv_html_port = render_obv_panel(obv_port)

        _tnombre = pos['ticker']
        wl_btn = ('<div style="margin-top:10px">'
                  '<button onclick="event.stopPropagation();wlToggle(this,\''+_tnombre+'\')"'
                  ' id="wl-btn-'+_tnombre+'"'
                  ' style="font-size:11px;padding:6px 14px;border-radius:8px;border:1px solid #3b82f6;'
                  'background:var(--surface2);color:#3b82f6;cursor:pointer;font-weight:600">'
                  '👁 Watchlist</button></div>')
        detail=(f'<div class="detail-panel">'
                f'{render_conf(conf) if conf else ""}'
                f'{alertas_h}'
                f'{_render_rec_panel(pos.get("recomendacion",{}))}'
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
            f'<td>{_render_rec_badge(pos.get("recomendacion",{}))}</td>'
            f'<td>{sr_inline_port}</td>'
            f'<td>{gbm_cell(pos.get("entrada_mxn"),pos.get("stop_mxn"),pos.get("obj_mxn"))}</td>'
            f'</tr>'
            f'<tr class="detail" id="{rid}"><td colspan="11" style="padding:0">{detail}</td></tr>')
    return h

def calcular_etapa(r: dict) -> tuple[str, str, str]:
    """
    Determina en qué etapa del ciclo está el ticker para swing trading.
    🟡 Preparándose — RSI bajo, ADX tranquilo, sobre soporte. Aún no arranca.
    🟠 A punto      — RSI 48-65, MACD alcista, score >=6, RR >=1.5. Vigilar.
    🟢 Entrar       — Score >=7, Señal 1D=COMPRAR, Volumen >=1.5x, RR >=3.0, no bloqueado.
    🚀 Ya arrancó   — precio >5% sobre EMA9, RSI >65. Tren salió.
    Retorna (emoji, label, tooltip)
    """
    rsi      = r.get("rsi", 50)
    adx      = r.get("adx", 0)
    macd_ok  = r.get("macd_ok", False)
    ema200   = r.get("ema200_ok", False)
    score    = r.get("score_ajustado", r.get("score", 0))
    rr       = r.get("rr", 0)
    precio   = r.get("precio_mxn", 0)
    entrada  = r.get("entrada_mxn", 0)  # EMA9
    sr       = r.get("sr", {})
    soportes = sr.get("soportes", [])
    senal_1d = r.get("tfs", {}).get("1D", {}).get("senal", "")
    bloqueado= r.get("estado", "") == "Bloqueado"
    vol_rel  = r.get("tfs", {}).get("1D", {}).get("vol_rel", 0)

    dist_ema9 = ((precio - entrada) / entrada * 100) if entrada else 0
    if dist_ema9 > 5 and rsi > 65 and score >= 7:
        return ("🚀", "Ya arrancó", "Ya arrancó — tren salió, no persigas")

    if (score >= 7 and senal_1d == "COMPRAR" and vol_rel >= 1.5
            and rr >= 3.0 and not bloqueado):
        return ("🟢", "Entrar", "✅ Entrar — Score≥7 | Señal=COMPRAR | Vol≥1.5x | RR≥3.0 | No bloqueado")

    if 48 <= rsi <= 65 and macd_ok and ema200 and score >= 6 and rr >= 1.5:
        return ("🟠", "A punto", "A punto — RSI 48-65 | MACD alcista | Score≥6 | RR≥1.5. Vigilar.")

    hay_soporte = any(z.get("fuerza", 0) >= 2 for z in soportes)
    if rsi < 52 and adx < 30 and hay_soporte and ema200:
        return ("🟡", "Preparándose", "Preparándose — RSI bajo | ADX tranquilo | Sobre soporte. Aún no.")

    return ("⬜", "Sin etapa", "Sin etapa clara definida")

def _puntuacion_top(r: dict) -> float:
    """
    Fórmula de puntuación para Top Diario y Semanal.
    Orden de prioridad:
    1. ROCKET
    2. Ganga
    3. BUY
    4. Listo 5/5
    5. Pre-breakout 4/5
    6. Acumulación / Capitulación
    """
    score  = r.get("score_ajustado", r.get("score", 0))
    total  = r.get("total_criterios", 13)
    rr     = r.get("rr", 0)
    estado = r.get("estado", "")
    ganga  = r.get("ganga", {})
    inicio = r.get("inicio", {})
    cap    = r.get("capitulacion", {})

    es_ganga  = isinstance(ganga, dict) and ganga.get("es_ganga", False)
    nivel_str = inicio.get("nivel", "") if isinstance(inicio, dict) and inicio.get("es_inicio") else ""
    es_cap    = isinstance(cap, dict) and cap.get("es_capitulacion", False)
    nivel_cap = cap.get("nivel", 0) if es_cap else 0
    es_buy    = estado in ("BUY", "ROCKET")

    if estado == "ROCKET":
        bonus = 5.0    # #1 — máxima convicción
    elif es_ganga:
        bonus = 4.0    # #2 — precio castigado, entrada segura
    elif es_buy:
        bonus = 3.5    # #3 — BUY confirmado
    elif nivel_str == "listo":
        bonus = 3.0    # #4 — 5/5 criterios
    elif nivel_str == "pre_breakout":
        bonus = 2.0    # #5 — 4/5 criterios
    elif es_cap and nivel_cap == 3:
        bonus = 2.5    # Capitulación nivel 3 (muy fuerte)
    elif es_cap and nivel_cap == 2:
        bonus = 1.8    # Capitulación nivel 2
    elif nivel_str == "acumulacion":
        bonus = 1.5    # #6 — acumulación
    else:
        bonus = 0

    score_pct = (score / total) if total > 0 else 0
    rr_norm   = min(rr / 10.0, 1.0)
    bonus_pct = bonus / 6.0  # normalizado sobre el máximo (5.0)

    return (bonus_pct * 0.35) + (rr_norm * 0.35) + (score_pct * 0.30)

def _fecha_hoy_cdmx() -> str:
    """Fecha actual en formato YYYY-MM-DD para horario CDMX (UTC-6)."""
    ahora_cdmx = datetime.utcnow() - timedelta(hours=6)
    return ahora_cdmx.strftime("%Y-%m-%d")

def _hora_cdmx() -> int:
    """Hora actual en CDMX (UTC-6) como entero."""
    return (datetime.utcnow() - timedelta(hours=6)).hour

def guardar_senal_semis(simbolo: str, senal: str, precio_mxn: float,
                         pasos_ok: int, tipo: str = "ETF") -> None:
    """Guarda señal detectada en historial para análisis futuro."""
    if senal == "NEUTRAL":
        return
    hoy = _fecha_hoy_cdmx()
    try:
        con = sqlite3.connect(DB_FILE)
        existe = con.execute(
            "SELECT id FROM semis_senales WHERE fecha=? AND simbolo=? AND senal=?",
            (hoy, simbolo, senal)
        ).fetchone()
        if not existe:
            con.execute(
                "INSERT INTO semis_senales (fecha, simbolo, senal, precio_mxn, pasos_ok, tipo) VALUES (?,?,?,?,?,?)",
                (hoy, simbolo, senal, precio_mxn, pasos_ok, tipo)
            )
            con.commit()
        con.close()
    except Exception as e:
        print(f"[semis] Error guardando señal: {e}")

def get_historial_señales(limite: int = 30) -> list:
    """Devuelve historial de señales de semis."""
    try:
        con = sqlite3.connect(DB_FILE)
        con.row_factory = sqlite3.Row
        rows = con.execute(
            "SELECT * FROM semis_senales ORDER BY fecha DESC, id DESC LIMIT ?",
            (limite,)
        ).fetchall()
        con.close()
        return [dict(r) for r in rows]
    except Exception:
        return []

def _semana_actual_cdmx() -> str:
    """Devuelve la semana actual como 'YYYY-WXX' basado en el lunes de la semana CDMX."""
    ahora_cdmx = datetime.utcnow() - timedelta(hours=6)
    return ahora_cdmx.strftime("%G-W%V")

def guardar_entrada_diario(ticker: str, tipo: str, precio_mxn: float, titulos: float,
                            score_entrada: int, total_criterios: int, razon_entrada: str,
                            setup_tipo: str = "", rr_esperado: float = 0,
                            op_id: int = 0) -> int:
    """Guarda una entrada en el diario. Devuelve el id generado."""
    try:
        con = sqlite3.connect(DB_FILE)
        cur = con.execute(
            """INSERT INTO diario_trading
               (ticker, fecha, tipo, precio_mxn, titulos, score_entrada, total_criterios,
                razon_entrada, setup_tipo, rr_esperado, resultado, op_id)
               VALUES (?,?,?,?,?,?,?,?,?,?,'abierta',?)""",
            (ticker.upper(), _fecha_hoy_cdmx(), tipo, precio_mxn, titulos,
             score_entrada, total_criterios, razon_entrada, setup_tipo, rr_esperado, op_id)
        )
        diario_id = cur.lastrowid
        con.commit(); con.close()
        return diario_id
    except Exception as e:
        print(f"[diario] Error al guardar: {e}")
        return 0

def cerrar_entrada_diario(diario_id: int, precio_cierre_mxn: float,
                           aprendizaje: str = "") -> bool:
    """Cierra una entrada del diario calculando P&L real."""
    try:
        con = sqlite3.connect(DB_FILE)
        con.row_factory = sqlite3.Row
        row = con.execute("SELECT * FROM diario_trading WHERE id=?", (diario_id,)).fetchone()
        if not row:
            con.close(); return False
        row = dict(row)
        pnl_mxn = (precio_cierre_mxn - row["precio_mxn"]) * row["titulos"]
        if row["tipo"] == "VENTA":
            pnl_mxn = -pnl_mxn
        pnl_pct = ((precio_cierre_mxn - row["precio_mxn"]) / row["precio_mxn"] * 100) if row["precio_mxn"] else 0
        resultado = "ganancia" if pnl_mxn > 0 else "perdida" if pnl_mxn < 0 else "break_even"
        con.execute(
            """UPDATE diario_trading SET resultado=?, pnl_mxn=?, pnl_pct=?,
               aprendizaje=?, fecha_cierre=? WHERE id=?""",
            (resultado, round(pnl_mxn, 2), round(pnl_pct, 2),
             aprendizaje, _fecha_hoy_cdmx(), diario_id)
        )
        con.commit(); con.close()
        return True
    except Exception as e:
        print(f"[diario] Error al cerrar: {e}")
        return False

def get_diario(limite: int = 50) -> list:
    """Devuelve entradas del diario más recientes."""
    try:
        con = sqlite3.connect(DB_FILE)
        con.row_factory = sqlite3.Row
        rows = con.execute(
            "SELECT * FROM diario_trading ORDER BY fecha DESC, id DESC LIMIT ?",
            (limite,)
        ).fetchall()
        con.close()
        return [dict(r) for r in rows]
    except Exception:
        return []

def get_estadisticas_diario() -> dict:
    """Calcula estadísticas del diario para mostrar patrones."""
    try:
        con = sqlite3.connect(DB_FILE)
        con.row_factory = sqlite3.Row
        rows = [dict(r) for r in con.execute(
            "SELECT * FROM diario_trading WHERE resultado != 'abierta'"
        ).fetchall()]
        con.close()
    except Exception:
        return {}

    if not rows:
        return {}

    total    = len(rows)
    ganadoras= [r for r in rows if r["resultado"] == "ganancia"]
    perdedoras=[r for r in rows if r["resultado"] == "perdida"]
    win_rate = len(ganadoras) / total * 100 if total else 0
    pnl_total= sum(r["pnl_mxn"] for r in rows)
    avg_gan  = sum(r["pnl_mxn"] for r in ganadoras) / len(ganadoras) if ganadoras else 0
    avg_per  = sum(r["pnl_mxn"] for r in perdedoras) / len(perdedoras) if perdedoras else 0

    score_stats = {}
    for r in rows:
        s = r.get("score_entrada", 0)
        tc = r.get("total_criterios", 13) or 13
        rango = f"{s}/{tc}"
        if rango not in score_stats:
            score_stats[rango] = {"total": 0, "ganadoras": 0, "pnl": 0.0}
        score_stats[rango]["total"] += 1
        score_stats[rango]["pnl"] += r["pnl_mxn"]
        if r["resultado"] == "ganancia":
            score_stats[rango]["ganadoras"] += 1

    setup_stats = {}
    for r in rows:
        s = r.get("setup_tipo", "—") or "—"
        if s not in setup_stats:
            setup_stats[s] = {"total": 0, "ganadoras": 0, "pnl": 0.0}
        setup_stats[s]["total"] += 1
        setup_stats[s]["pnl"] += r["pnl_mxn"]
        if r["resultado"] == "ganancia":
            setup_stats[s]["ganadoras"] += 1

    return {
        "total": total, "ganadoras": len(ganadoras), "perdedoras": len(perdedoras),
        "win_rate": round(win_rate, 1), "pnl_total": round(pnl_total, 2),
        "avg_ganadora": round(avg_gan, 2), "avg_perdedora": round(avg_per, 2),
        "score_stats": score_stats, "setup_stats": setup_stats,
    }

# ═══════════════════════════════════════════════════════════
# ═══════════════════════════════════════════════════════════

def registrar_snapshot_pnl(capital_actual: float, spy_precio: float = 0) -> None:
    """Guarda o actualiza el snapshot diario del capital."""
    hoy = _fecha_hoy_cdmx()
    try:
        con = sqlite3.connect(DB_FILE)
        existe = con.execute(
            "SELECT id, capital FROM pnl_historico WHERE fecha=?", (hoy,)
        ).fetchone()

        primer = con.execute(
            "SELECT capital FROM pnl_historico ORDER BY fecha ASC LIMIT 1"
        ).fetchone()
        ultimo_ant = con.execute(
            "SELECT capital FROM pnl_historico WHERE fecha != ? ORDER BY fecha DESC LIMIT 1", (hoy,)
        ).fetchone()

        pnl_dia  = capital_actual - ultimo_ant[0] if ultimo_ant else 0
        cap_ini  = primer[0] if primer else capital_actual
        pnl_acum = (capital_actual - cap_ini) / cap_ini * 100 if cap_ini else 0

        if not existe:
            con.execute(
                "INSERT INTO pnl_historico (fecha, capital, pnl_dia_mxn, pnl_acum_pct, spy_precio) VALUES (?,?,?,?,?)",
                (hoy, capital_actual, round(pnl_dia, 2), round(pnl_acum, 2), spy_precio)
            )
        else:
            con.execute(
                "UPDATE pnl_historico SET capital=?, pnl_dia_mxn=?, pnl_acum_pct=?, spy_precio=? WHERE fecha=?",
                (capital_actual, round(pnl_dia, 2), round(pnl_acum, 2), spy_precio, hoy)
            )
        con.commit()
        con.close()
    except Exception as e:
        print(f"[pnl] Error: {e}")

def get_pnl_historico(dias: int = 90) -> list:
    """Devuelve el historial de P&L de los últimos N días."""
    try:
        con = sqlite3.connect(DB_FILE)
        con.row_factory = sqlite3.Row
        rows = con.execute(
            "SELECT * FROM pnl_historico ORDER BY fecha DESC LIMIT ?", (dias,)
        ).fetchall()
        con.close()
        return [dict(r) for r in reversed(rows)]
    except Exception:
        return []

def get_spy_precio_actual() -> float:
    """Obtiene precio actual de SPY para comparación."""
    try:
        vals = _get_cached("SPY", "1day", "")
        if vals:
            return float(vals[-1]["close"])
    except Exception:
        pass
    return 0.0

def calcular_rendimiento_vs_spy(pnl_hist: list, spy_actual: float) -> dict:
    """Compara rendimiento del portafolio vs SPY en el mismo periodo."""
    if not pnl_hist or len(pnl_hist) < 2:
        return {}
    primer = pnl_hist[0]
    ultimo = pnl_hist[-1]
    rend_port = ultimo.get("pnl_acum_pct", 0)
    spy_ini   = primer.get("spy_precio", 0)
    rend_spy  = ((spy_actual - spy_ini) / spy_ini * 100) if spy_ini and spy_actual else 0
    alfa      = rend_port - rend_spy
    return {
        "rend_port": round(rend_port, 2),
        "rend_spy":  round(rend_spy, 2),
        "alfa":      round(alfa, 2),
        "periodo_dias": len(pnl_hist),
        "ganando_al_mercado": alfa > 0,
    }

# ═══════════════════════════════════════════════════════════
# ═══════════════════════════════════════════════════════════

def es_volumen_inusual(vol_rel: float, umbral: float = 2.5) -> bool:
    """Volumen inusual: 2.5x o más del promedio de 20 días."""
    return vol_rel >= umbral

def badge_volumen_inusual(vol_rel: float) -> str:
    """Badge visual para volumen inusual."""
    if vol_rel >= 4.0:
        return f'<span style="background:#fde68a;color:#92400e;border:1px solid #fbbf24;border-radius:6px;padding:1px 6px;font-size:9px;font-weight:700" title="Volumen {vol_rel:.1f}x — actividad institucional extrema">🔥 {vol_rel:.1f}x VOL</span>'
    elif vol_rel >= 2.5:
        return f'<span style="background:#fef3c7;color:#d97706;border:1px solid #fde68a;border-radius:6px;padding:1px 6px;font-size:9px;font-weight:700" title="Volumen {vol_rel:.1f}x — actividad inusual">⚡ {vol_rel:.1f}x VOL</span>'
    return ""

def distancia_soporte_pct(precio_mxn: float, sr: dict) -> float:
    """Calcula distancia % al soporte más cercano por debajo del precio."""
    soportes = sr.get("soportes", [])
    if not soportes or not precio_mxn:
        return 0.0
    cercano = None
    for z in soportes:
        p = z.get("precio_mxn", z.get("precio", 0))
        if p and p < precio_mxn:
            if cercano is None or p > cercano:
                cercano = p
    if not cercano:
        return 0.0
    return round((precio_mxn - cercano) / precio_mxn * 100, 1)

def render_tab_diario(entradas: list, stats: dict) -> str:
    """Tab del diario de trading con análisis de patrones."""
    medals_score = {"ganancia": "🟢", "perdida": "🔴", "break_even": "🟡", "abierta": "🔵"}

    # ── Estadísticas generales ────────────────────────────
    stats_html = ""
    if stats:
        wr_col = "var(--green)" if stats["win_rate"] >= 55 else "var(--yellow)" if stats["win_rate"] >= 40 else "var(--red)"
        pnl_col = "var(--green)" if stats["pnl_total"] >= 0 else "var(--red)"
        stats_html = f'''
    <div style="display:grid;grid-template-columns:repeat(auto-fill,minmax(140px,1fr));gap:10px;margin-bottom:20px">
      <div style="background:var(--surface);border:1px solid var(--brd);border-radius:10px;padding:12px;text-align:center">
        <div style="font-size:22px;font-weight:800;color:{wr_col}">{stats["win_rate"]:.0f}%</div>
        <div style="font-size:10px;color:var(--muted)">Win rate</div>
        <div style="font-size:10px;color:var(--muted)">{stats["ganadoras"]}G / {stats["perdedoras"]}P</div>
      </div>
      <div style="background:var(--surface);border:1px solid var(--brd);border-radius:10px;padding:12px;text-align:center">
        <div style="font-size:22px;font-weight:800;color:{pnl_col}">{fmt(stats["pnl_total"])}</div>
        <div style="font-size:10px;color:var(--muted)">P&L total MXN</div>
      </div>
      <div style="background:var(--surface);border:1px solid var(--brd);border-radius:10px;padding:12px;text-align:center">
        <div style="font-size:22px;font-weight:800;color:var(--green)">{fmt(stats["avg_ganadora"])}</div>
        <div style="font-size:10px;color:var(--muted)">Promedio ganadora</div>
      </div>
      <div style="background:var(--surface);border:1px solid var(--brd);border-radius:10px;padding:12px;text-align:center">
        <div style="font-size:22px;font-weight:800;color:var(--red)">{fmt(stats["avg_perdedora"])}</div>
        <div style="font-size:10px;color:var(--muted)">Promedio perdedora</div>
      </div>
    </div>'''

        if stats.get("score_stats"):
            score_rows = ""
            for rango, s in sorted(stats["score_stats"].items()):
                wr = s["ganadoras"] / s["total"] * 100 if s["total"] else 0
                wr_c = "var(--green)" if wr >= 55 else "var(--yellow)" if wr >= 40 else "var(--red)"
                pnl_c = "var(--green)" if s["pnl"] >= 0 else "var(--red)"
                score_rows += (f'<tr><td style="font-weight:700">{rango}</td>'
                               f'<td class="num">{s["total"]}</td>'
                               f'<td class="num" style="color:{wr_c}">{wr:.0f}%</td>'
                               f'<td class="num" style="color:{pnl_c}">{fmt(s["pnl"])}</td></tr>')
            stats_html += f'''
    <div style="background:var(--surface);border:1px solid var(--brd);border-radius:10px;padding:16px;margin-bottom:16px">
      <div style="font-size:13px;font-weight:700;margin-bottom:10px">📊 Rendimiento por score de entrada</div>
      <p class="hint" style="margin-bottom:10px">Aquí verás si tus mejores trades vienen de score 7/13 o 10/13 — eso te dice en qué nivel de convicción operas mejor.</p>
      <table style="width:100%;font-size:12px;border-collapse:collapse">
        <thead><tr style="color:var(--muted);font-size:10px">
          <th style="text-align:left;padding:4px 0">Score</th>
          <th style="text-align:right">Ops</th><th style="text-align:right">Win%</th><th style="text-align:right">P&L</th>
        </tr></thead>
        <tbody>{score_rows}</tbody>
      </table>
    </div>'''

        if stats.get("setup_stats"):
            setup_rows = ""
            for setup, s in sorted(stats["setup_stats"].items(), key=lambda x: -x[1]["total"]):
                wr = s["ganadoras"] / s["total"] * 100 if s["total"] else 0
                wr_c = "var(--green)" if wr >= 55 else "var(--yellow)" if wr >= 40 else "var(--red)"
                pnl_c = "var(--green)" if s["pnl"] >= 0 else "var(--red)"
                setup_rows += (f'<tr><td style="font-weight:600">{setup}</td>'
                               f'<td class="num">{s["total"]}</td>'
                               f'<td class="num" style="color:{wr_c}">{wr:.0f}%</td>'
                               f'<td class="num" style="color:{pnl_c}">{fmt(s["pnl"])}</td></tr>')
            stats_html += f'''
    <div style="background:var(--surface);border:1px solid var(--brd);border-radius:10px;padding:16px;margin-bottom:16px">
      <div style="font-size:13px;font-weight:700;margin-bottom:10px">🎯 Rendimiento por tipo de setup</div>
      <table style="width:100%;font-size:12px;border-collapse:collapse">
        <thead><tr style="color:var(--muted);font-size:10px">
          <th style="text-align:left;padding:4px 0">Setup</th>
          <th style="text-align:right">Ops</th><th style="text-align:right">Win%</th><th style="text-align:right">P&L</th>
        </tr></thead>
        <tbody>{setup_rows}</tbody>
      </table>
    </div>'''

    # ── Lista de entradas del diario ──────────────────────
    entradas_html = ""
    if not entradas:
        entradas_html = '<div style="padding:30px;text-align:center;color:var(--muted)">Sin entradas aún. Cada operación que registres aparecerá aquí.</div>'
    else:
        for e in entradas:
            dot    = medals_score.get(e.get("resultado", "abierta"), "⚪")
            r_col  = ("var(--green)" if e["resultado"] == "ganancia"
                      else "var(--red)" if e["resultado"] == "perdida"
                      else "var(--muted)")
            pnl_txt = (f'<span style="color:{r_col};font-weight:700">{fmt(e["pnl_mxn"])} ({e["pnl_pct"]:+.1f}%)</span>'
                       if e["resultado"] != "abierta" else
                       '<span style="color:#3b82f6;font-size:10px">● Abierta</span>')
            aprend_html = (f'<div style="margin-top:6px;font-size:11px;color:var(--muted);'
                           f'border-top:1px solid var(--brd);padding-top:6px">'
                           f'💡 <em>{e["aprendizaje"]}</em></div>'
                           if e.get("aprendizaje") else "")
            entradas_html += f'''
    <div style="background:var(--surface);border:1px solid var(--brd);border-radius:12px;padding:16px;margin-bottom:10px">
      <div style="display:flex;align-items:flex-start;justify-content:space-between;margin-bottom:8px">
        <div>
          <span style="font-size:16px">{dot}</span>
          <strong style="font-size:15px;margin-left:6px">{e["ticker"]}</strong>
          <span style="color:var(--muted);font-size:11px;margin-left:8px">{e["tipo"]} · {e["fecha"][:10]}</span>
          {f'<span style="background:var(--surface2);border-radius:6px;padding:1px 8px;font-size:10px;margin-left:6px">{e["setup_tipo"]}</span>' if e.get("setup_tipo") else ""}
        </div>
        <div style="text-align:right">
          {pnl_txt}
          <div style="font-size:10px;color:var(--muted)">Score: {e["score_entrada"]}/{e["total_criterios"]} · R:R {e["rr_esperado"]:.1f}x</div>
        </div>
      </div>
      <div style="font-size:12px;line-height:1.5;background:var(--surface2);border-radius:8px;padding:8px 10px">
        📝 <strong>Por qué entré:</strong> {e["razon_entrada"]}
      </div>
      {aprend_html}
      {f'<div style="font-size:10px;color:var(--muted);margin-top:4px;text-align:right">Cerrada: {e["fecha_cierre"]}</div>' if e.get("fecha_cierre") else ""}
    </div>'''

    return f'''<div id="tab-diario" class="tab">
  <div style="padding:20px 0 14px">
    <h2 style="font-size:20px;font-weight:600;letter-spacing:-.4px">📓 Diario de Trading</h2>
    <p class="hint">Cada operación que registres queda aquí con tu razonamiento. Con el tiempo verás qué setups y qué scores te funcionan mejor.</p>
  </div>
  {stats_html}
  <div>{entradas_html}</div>
</div>'''

def render_tab_rendimiento(pnl_hist: list, vs_spy: dict, stats_diario: dict) -> str:
    """Tab de rendimiento acumulado vs SPY con opción de exportar."""
    if not pnl_hist:
        return '''<div id="tab-rendimiento" class="tab">
          <div style="padding:40px;text-align:center;color:var(--muted)">
            <div style="font-size:48px;margin-bottom:16px">📊</div>
            <div style="font-size:16px;font-weight:600">Sin historial de rendimiento todavía</div>
            <div style="font-size:13px;margin-top:8px">El sistema registrará tu P&L cada vez que actualices el dashboard.</div>
          </div>
        </div>'''

    ultimo   = pnl_hist[-1]
    cap_act  = ultimo.get("capital", 0)
    pnl_acum = ultimo.get("pnl_acum_pct", 0)
    pnl_col  = "var(--green)" if pnl_acum >= 0 else "var(--red)"

    alfa_html = ""
    if vs_spy:
        alfa_col = "var(--green)" if vs_spy["ganando_al_mercado"] else "var(--red)"
        alfa_html = f'''
    <div style="display:grid;grid-template-columns:1fr 1fr 1fr;gap:10px;margin-bottom:16px">
      <div style="background:var(--surface);border:1px solid var(--brd);border-radius:10px;padding:14px;text-align:center">
        <div style="font-size:20px;font-weight:800;color:{pnl_col}">{pnl_acum:+.1f}%</div>
        <div style="font-size:10px;color:var(--muted)">Tu portafolio</div>
      </div>
      <div style="background:var(--surface);border:1px solid var(--brd);border-radius:10px;padding:14px;text-align:center">
        <div style="font-size:20px;font-weight:800;color:var(--muted)">{vs_spy["rend_spy"]:+.1f}%</div>
        <div style="font-size:10px;color:var(--muted)">SPY (S&P500)</div>
      </div>
      <div style="background:var(--surface);border:1px solid var(--brd);border-radius:10px;padding:14px;text-align:center">
        <div style="font-size:20px;font-weight:800;color:{alfa_col}">{vs_spy["alfa"]:+.1f}%</div>
        <div style="font-size:10px;color:var(--muted)">Alfa vs mercado</div>
        <div style="font-size:9px;color:{alfa_col}">{"✅ Ganando al mercado" if vs_spy["ganando_al_mercado"] else "⚠️ Perdiendo vs índice"}</div>
      </div>
    </div>'''

    hist_rows = ""
    for h in reversed(pnl_hist[-30:]):
        pnl_d_col = "var(--green)" if h.get("pnl_dia_mxn", 0) >= 0 else "var(--red)"
        pnl_a_col = "var(--green)" if h.get("pnl_acum_pct", 0) >= 0 else "var(--red)"
        hist_rows += (f'<tr>'
                      f'<td>{h["fecha"]}</td>'
                      f'<td class="num">{fmt(h["capital"])}</td>'
                      f'<td class="num" style="color:{pnl_d_col}">{fmt(h["pnl_dia_mxn"])}</td>'
                      f'<td class="num" style="color:{pnl_a_col}">{h["pnl_acum_pct"]:+.1f}%</td>'
                      f'<td class="num" style="color:var(--muted)">{fmt(h["spy_precio"]) if h["spy_precio"] else "—"}</td>'
                      f'</tr>')

    return f'''<div id="tab-rendimiento" class="tab">
  <div style="padding:20px 0 14px;display:flex;align-items:center;justify-content:space-between;flex-wrap:wrap;gap:10px">
    <div>
      <h2 style="font-size:20px;font-weight:600;letter-spacing:-.4px">📊 Rendimiento</h2>
      <p class="hint">Capital actual: <strong>{fmt(cap_act)} MXN</strong> · {len(pnl_hist)} días de historial</p>
    </div>
    <div style="display:flex;gap:8px">
      <button onclick="exportarExcel()" style="font-size:11px;padding:6px 14px;border-radius:8px;border:1px solid var(--brd);background:var(--surface2);color:var(--text);cursor:pointer">📥 Excel</button>
      <button onclick="exportarPDF()" style="font-size:11px;padding:6px 14px;border-radius:8px;border:1px solid var(--brd);background:var(--surface2);color:var(--text);cursor:pointer">📄 PDF</button>
    </div>
  </div>
  {alfa_html}
  <div style="background:var(--surface);border:1px solid var(--brd);border-radius:10px;overflow:hidden">
    <table style="width:100%;font-size:12px;border-collapse:collapse">
      <thead style="background:var(--surface2)">
        <tr style="color:var(--muted);font-size:10px">
          <th style="text-align:left;padding:8px 12px">Fecha</th>
          <th style="text-align:right;padding:8px 12px">Capital</th>
          <th style="text-align:right;padding:8px 12px">P&L día</th>
          <th style="text-align:right;padding:8px 12px">P&L acum%</th>
          <th style="text-align:right;padding:8px 12px">SPY</th>
        </tr>
      </thead>
      <tbody>{hist_rows}</tbody>
    </table>
  </div>
</div>'''

def get_watchlist() -> list:
    """Devuelve los tickers de la watchlist."""
    try:
        con = sqlite3.connect(DB_FILE)
        con.row_factory = sqlite3.Row
        rows = con.execute("SELECT * FROM watchlist ORDER BY fecha_add DESC").fetchall()
        con.close()
        return [dict(r) for r in rows]
    except Exception:
        return []

def agregar_watchlist(ticker: str, notas: str = "", e1_manual: float = 0) -> bool:
    try:
        con = sqlite3.connect(DB_FILE)
        con.execute(
            "INSERT OR IGNORE INTO watchlist (ticker, notas, e1_manual, fecha_add) VALUES (?,?,?,?)",
            (ticker.upper().strip(), notas, e1_manual, _fecha_hoy_cdmx())
        )
        con.commit(); con.close()
        return True
    except Exception:
        return False

def quitar_watchlist(ticker: str) -> bool:
    try:
        con = sqlite3.connect(DB_FILE)
        con.execute("DELETE FROM watchlist WHERE ticker=?", (ticker.upper().strip(),))
        con.commit(); con.close()
        return True
    except Exception:
        return False

def render_tab_watchlist(scan_data: list, radar_data: list, tc: float) -> str:
    """
    Tab dedicado de Watchlist — solo tus candidatas activas con sus niveles.
    Cruza la watchlist de DB con los datos frescos del scanner/radar.
    """
    wl = get_watchlist()
    if not wl:
        return '''<div id="tab-wl" class="tab">
          <div style="padding:40px;text-align:center;color:var(--muted)">
            <div style="font-size:48px;margin-bottom:16px">👁</div>
            <div style="font-size:16px;font-weight:600">Watchlist vacía</div>
            <div style="font-size:13px;margin-top:8px">Agrega tickers con el buscador del scanner y márcalos como Watchlist.</div>
          </div>
        </div>'''

    datos_frescos = {}
    for r in (scan_data or []):
        t = r.get("nombre", "")
        if t:
            datos_frescos[t.upper()] = r
    for r in (radar_data or []):
        t = r.get("nombre", "")
        if t and t.upper() not in datos_frescos:
            datos_frescos[t.upper()] = r

    estado_color = {
        "ROCKET": "#7c3aed", "BUY": "var(--green)", "WATCH": "#d97706",
        "SKIP": "var(--muted)", "BLOQUEADO": "var(--red)", "EXIT": "var(--red)",
        "SHORT": "var(--red)", "LATERAL": "#d97706", "RUPTURA": "var(--red)",
    }
    cards = []
    for w in wl:
        ticker = w["ticker"]
        notas  = w.get("notas", "")
        fecha  = w.get("fecha_add", "")
        r = datos_frescos.get(ticker.upper(), {})

        if r:
            precio   = r.get("precio_mxn", 0)
            rr       = r.get("rr", 0)
            rsi_v    = r.get("rsi", 0)
            score    = r.get("score_ajustado", r.get("score", 0))
            total_c  = r.get("total_criterios", 13)
            estado   = r.get("estado", "—")
            ecolor   = estado_color.get(estado, "var(--muted)")
            dca      = r.get("dca", {}) or {}
            e1       = dca.get("e1_precio", 0)
            e2       = dca.get("e2_precio", 0)
            e3       = dca.get("e3_precio", 0)
            sl       = r.get("sizing", {}).get("sl_mxn", 0) if r.get("sizing") else 0
            obj      = r.get("sizing", {}).get("objetivo_mxn", 0) if r.get("sizing") else 0
            ganga    = r.get("ganga", {}) or {}
            es_ganga = isinstance(ganga, dict) and ganga.get("es_ganga", False)
            inicio   = r.get("inicio", {}) or {}
            nivel    = inicio.get("nivel", "") if inicio.get("es_inicio") else ""
            div_rsi  = r.get("div_rsi", {}) or {}
            patron   = r.get("patron_velas", {}) or {}

            badge = ""
            if es_ganga:
                badge = '<span style="background:#fef3c7;color:#d97706;padding:2px 8px;border-radius:99px;font-size:10px;font-weight:700;margin-left:6px">🏷️ Ganga</span>'
            elif nivel == "pre_breakout":
                badge = '<span style="background:#fef3c7;color:#b45309;padding:2px 8px;border-radius:99px;font-size:10px;font-weight:700;margin-left:6px">⚡ 4/5</span>'
            elif nivel == "listo":
                badge = '<span style="background:#dcfce7;color:#15803d;padding:2px 8px;border-radius:99px;font-size:10px;font-weight:700;margin-left:6px">✅ 5/5</span>'

            div_html = ""
            if div_rsi.get("alcista"):
                div_html = f'<div style="font-size:10px;color:#3b82f6;margin-top:4px">{div_rsi["desc"]}</div>'
            elif div_rsi.get("bajista"):
                div_html = f'<div style="font-size:10px;color:var(--red);margin-top:4px">{div_rsi["desc"]}</div>'

            patron_html = ""
            if patron.get("ok"):
                patron_html = f'<div style="font-size:10px;color:var(--green);margin-top:4px">{patron["desc"]}</div>'

            entradas_html = ""
            if e1 or e2 or e3:
                entradas_html = (
                    f'<div style="display:flex;gap:6px;flex-wrap:wrap;margin-top:8px;font-size:11px">'
                    f'{"<span style=background:var(--bg);border-radius:6px;padding:4px 8px><span style=color:var(--muted)>E1 </span><strong>"+fmt(e1)+"</strong></span>" if e1 else ""}'
                    f'{"<span style=background:var(--bg);border-radius:6px;padding:4px 8px><span style=color:var(--muted)>E2 </span><strong>"+fmt(e2)+"</strong></span>" if e2 else ""}'
                    f'{"<span style=background:var(--bg);border-radius:6px;padding:4px 8px><span style=color:var(--muted)>E3 </span><strong>"+fmt(e3)+"</strong></span>" if e3 else ""}'
                    f'</div>'
                )

            card_body = f'''
        <div style="display:flex;align-items:flex-start;justify-content:space-between;margin-bottom:10px">
          <div>
            <span style="font-size:20px;font-weight:800">{ticker}</span>{badge}
            <div style="font-size:11px;color:var(--muted);margin-top:2px">${precio:,.2f} MXN · RSI {rsi_v:.0f} · Score {score}/{total_c}</div>
          </div>
          <div style="text-align:right">
            <div style="font-size:16px;font-weight:700;color:{ecolor}">{estado}</div>
            <div style="font-size:12px;color:var(--green);font-weight:600">{rr:.1f}x R:R</div>
          </div>
        </div>
        {div_html}{patron_html}{entradas_html}
        <div style="display:flex;gap:10px;margin-top:8px;font-size:11px">
          {"<div><span style=color:var(--muted)>SL </span><span style=color:var(--red);font-family:var(--mono)>"+fmt(sl)+"</span></div>" if sl else ""}
          {"<div><span style=color:var(--muted)>Obj </span><span style=color:var(--green);font-family:var(--mono)>"+fmt(obj)+"</span></div>" if obj else ""}
        </div>'''
        else:
            card_body = f'''
        <div style="display:flex;align-items:center;justify-content:space-between">
          <span style="font-size:20px;font-weight:800">{ticker}</span>
          <span style="font-size:11px;color:var(--muted)">Sin datos frescos — escanear primero</span>
        </div>'''

        notas_html = f'<div style="font-size:11px;color:var(--muted);margin-top:8px;padding-top:8px;border-top:1px solid var(--brd)">📝 {notas}</div>' if notas else ""
        fecha_html = f'<div style="font-size:9px;color:var(--muted);text-align:right;margin-top:6px">Agregado {fecha}</div>'

        cards.append(f'''
    <div style="background:var(--surface);border:1px solid var(--brd);border-radius:14px;padding:18px;position:relative">
      <div style="position:absolute;top:0;left:0;right:0;height:3px;background:linear-gradient(90deg,#3b82f6,#8b5cf6)"></div>
      <button onclick="fetch('/api/watchlist/quitar/{ticker}',{{method:'POST'}}).then(()=>location.reload())"
        style="position:absolute;top:12px;right:12px;background:none;border:none;color:var(--muted);font-size:16px;cursor:pointer" title="Quitar de watchlist">✕</button>
      {card_body}{notas_html}{fecha_html}
    </div>''')

    cards_html = "\n".join(cards)
    n = len(wl)
    return f'''<div id="tab-wl" class="tab">
  <div style="padding:20px 0 14px;display:flex;align-items:center;justify-content:space-between">
    <div>
      <h2 style="font-size:20px;font-weight:600;letter-spacing:-.4px">👁 Watchlist <span style="font-size:14px;color:var(--muted);font-weight:400">({n} candidatas)</span></h2>
      <p class="hint">Tus candidatas activas con datos frescos del último scan · Datos se actualizan al correr el scanner</p>
    </div>
  </div>
  <div class="top-grid" style="display:grid;grid-template-columns:repeat(auto-fill,minmax(340px,1fr));gap:16px">
    {cards_html}
  </div>
  <div style="margin-top:20px;padding:14px 16px;background:var(--surface);border:1px solid var(--brd);border-radius:10px;font-size:11px;color:var(--muted)">
    💡 Para agregar tickers a la Watchlist: búscalos en el scanner y usa el botón "👁 Watchlist". Para quitarlos usa la ✕ en cada card.
  </div>
</div>'''

def render_mini_chart(nombre: str, tc: float, width: int = 560, height: int = 160) -> str:
    """
    Mini gráfico de velas japonesas con EMAs 9, 21, 50 para los últimos 60 días.
    Usa datos del cache — cero llamadas API.
    """
    try:
        vals = None
        for key in (_TD_CACHE or {}):
            if nombre.upper() in key.upper() and "1day" in key.lower():
                vals = _TD_CACHE[key]
                break
        if not vals or len(vals) < 10:
            return ""

        data = vals[-60:]
        n    = len(data)

        closes = [float(x["close"]) for x in data]
        opens  = [float(x.get("open",  x["close"])) for x in data]
        highs  = [float(x.get("high",  x["close"])) for x in data]
        lows   = [float(x.get("low",   x["close"])) for x in data]

        c_s  = pd.Series(closes)
        e9_s  = list(ema(c_s, 9))
        e21_s = list(ema(c_s, 21))
        e50_s = list(ema(c_s, 50))

        all_prices = highs + lows + [v for v in e9_s + e21_s + e50_s if v and v == v]
        p_min = min(all_prices) * 0.998
        p_max = max(all_prices) * 1.002
        p_rng = p_max - p_min if p_max != p_min else 1

        pad_l, pad_r, pad_t, pad_b = 8, 8, 8, 20
        w = width - pad_l - pad_r
        h = height - pad_t - pad_b

        def px(price):
            return pad_t + h - (price - p_min) / p_rng * h

        def py(i):
            return pad_l + (i / (n - 1)) * w if n > 1 else pad_l

        candle_w = max(2, int(w / n * 0.7))
        velas_svg = ""
        for i, (o, c, hi, lo) in enumerate(zip(opens, closes, highs, lows)):
            x     = py(i)
            color = "#22c55e" if c >= o else "#ef4444"
            y_hi  = px(hi)
            y_lo  = px(lo)
            y_o   = px(max(o, c))
            y_c   = px(min(o, c))
            body_h = max(1, y_lo - y_hi if c == o else abs(px(o) - px(c)))

            velas_svg += f'<line x1="{x:.1f}" y1="{y_hi:.1f}" x2="{x:.1f}" y2="{y_lo:.1f}" stroke="{color}" stroke-width="1" opacity="0.7"/>'
            velas_svg += f'<rect x="{x - candle_w/2:.1f}" y="{y_o:.1f}" width="{candle_w}" height="{max(1, abs(px(o)-px(c))):.1f}" fill="{color}" opacity="0.9"/>'

        def make_ema_line(ema_vals, color, dash=""):
            pts = " ".join(
                f"{py(i):.1f},{px(v):.1f}"
                for i, v in enumerate(ema_vals)
                if v and v == v
            )
            dash_attr = f'stroke-dasharray="{dash}"' if dash else ""
            return f'<polyline points="{pts}" fill="none" stroke="{color}" stroke-width="1.5" {dash_attr} opacity="0.9"/>'

        ema9_line  = make_ema_line(e9_s,  "#60a5fa")       # azul — EMA9
        ema21_line = make_ema_line(e21_s, "#f59e0b")       # naranja — EMA21
        ema50_line = make_ema_line(e50_s, "#a78bfa", "4,2") # morado punteado — EMA50

        precio_actual = closes[-1] * tc
        y_actual = px(closes[-1])
        precio_line = (f'<line x1="{pad_l}" y1="{y_actual:.1f}" x2="{pad_l+w}" y2="{y_actual:.1f}" '
                       f'stroke="#94a3b8" stroke-width="0.5" stroke-dasharray="3,3"/>')

        leyenda = (f'<text x="{pad_l+2}" y="{height-6}" font-size="8" fill="#60a5fa">EMA9</text>'
                   f'<text x="{pad_l+30}" y="{height-6}" font-size="8" fill="#f59e0b">EMA21</text>'
                   f'<text x="{pad_l+58}" y="{height-6}" font-size="8" fill="#a78bfa">EMA50</text>'
                   f'<text x="{pad_l+w-2}" y="{height-6}" font-size="8" fill="#94a3b8" text-anchor="end">${precio_actual:,.0f}</text>')

        svg = (f'<svg viewBox="0 0 {width} {height}" xmlns="http://www.w3.org/2000/svg" '
               f'style="width:100%;height:{height}px;background:#0d1117;border-radius:8px;display:block">'
               f'{velas_svg}{ema9_line}{ema21_line}{ema50_line}{precio_line}{leyenda}'
               f'</svg>')

        return (f'<div style="margin-bottom:12px">'
                f'<div style="font-size:10px;color:var(--muted);margin-bottom:4px;font-weight:600">'
                f'📈 Gráfico 60 días — Velas diarias + EMA9/21/50</div>'
                f'{svg}</div>')

    except Exception as e:
        return f'<div style="font-size:10px;color:var(--muted)">Gráfico no disponible: {e}</div>'

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
        ganga_badge  = badge_ganga(r.get("ganga", {}))
        inicio_badge = badge_inicio_movimiento(r.get("inicio", {}))
        cap_badge    = badge_capitulacion(r.get("capitulacion", {}))

        vol_rel_val  = r.get("tfs", {}).get("1D", {}).get("vol_rel", 0) or 0
        vol_badge    = badge_volumen_inusual(vol_rel_val)

        dist_sop = distancia_soporte_pct(r.get("precio_mxn", 0), r.get("sr", {}))
        dist_badge = ""
        if 0 < dist_sop <= 3:
            dist_badge = f'<span style="font-size:9px;background:#dcfce7;color:#15803d;border:1px solid #86efac;border-radius:6px;padding:1px 5px;margin-left:3px" title="A {dist_sop:.1f}% del soporte">🎯 {dist_sop:.1f}% sop</span>'
        elif 0 < dist_sop <= 6:
            dist_badge = f'<span style="font-size:9px;background:#fef9c3;color:#854d0e;border:1px solid #fde047;border-radius:6px;padding:1px 5px;margin-left:3px" title="A {dist_sop:.1f}% del soporte">📍 {dist_sop:.1f}% sop</span>'

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
        ema200_mxn   = r.get("ema200_mxn", 0)
        precio_ref   = r["precio_mxn"]
        lines = []
        if stop_sr_mxn:
            down_pct = (stop_sr_mxn - precio_ref) / precio_ref * 100
            lines.append(f'<div><span style="color:var(--green);font-size:9px">S</span> '
                         f'<span style="color:var(--green);font-family:var(--mono)">{fmt(stop_sr_mxn)}</span>'
                         f'<span style="color:var(--muted);font-size:9px"> ({down_pct:.1f}%)</span></div>')
        if obj_sr_mxn:
            up_pct = (obj_sr_mxn - precio_ref) / precio_ref * 100
            lines.append(f'<div><span style="color:var(--red);font-size:9px">R</span> '
                         f'<span style="color:var(--red);font-family:var(--mono)">{fmt(obj_sr_mxn)}</span>'
                         f'<span style="color:var(--muted);font-size:9px"> (+{up_pct:.1f}%)</span></div>')
        if ema200_mxn and ema200_mxn > 0:
            e200_pct = (ema200_mxn - precio_ref) / precio_ref * 100
            if e200_pct >= 0:
                lines.append(f'<div><span style="color:#ff7875;font-size:9px;font-style:italic">R EMA200</span> '
                             f'<span style="color:#ff7875;font-family:var(--mono)">{fmt(ema200_mxn)}</span>'
                             f'<span style="color:var(--muted);font-size:9px"> (+{e200_pct:.1f}%)</span></div>')
            else:
                lines.append(f'<div><span style="color:#73d13d;font-size:9px;font-style:italic">S EMA200</span> '
                             f'<span style="color:#73d13d;font-family:var(--mono)">{fmt(ema200_mxn)}</span>'
                             f'<span style="color:var(--muted);font-size:9px"> ({e200_pct:.1f}%)</span></div>')
        if lines:
            sr_cell_html = f'<div style="font-size:10px;line-height:1.9;white-space:nowrap">{"".join(lines)}</div>'
        else:
            sr_cell_html = '<span class="hint" style="font-size:10px">—</span>'

        # ── OBV + SECTOR + HISTORIAL ─────────────────────────────────────
        obv_html    = render_obv_panel(r.get("obv", {}))
        sector_html = render_sector_panel(r.get("sector", {}))
        hist_html   = render_score_history(r["nombre"], score_aj)
        dca_html    = render_dca_panel(r.get("dca", {}), r["precio_mxn"])

        etf_warning = ""
        if etf_peligroso:
            min_s = 8 if r.get("vix",20)>20 else 7
            etf_warning = (f'<div style="background:#fff7e6;border:1px solid #ffd591;border-radius:var(--r);'
                           f'padding:9px 13px;margin-bottom:9px;font-size:11px;color:#d46b08">'
                           f'⚡ <strong>ETF 3x</strong> — Score mín: {min_s}/11. '
                           f'Solo con VIX &lt; 20 y SPY alcista.</div>')

        adx_color = "var(--green)" if adx_val>=25 else "var(--yellow)" if adx_val>=20 else "var(--red)"
        adx_label = "✅ Tendencia" if adx_val>=25 else "⚠️ Débil" if adx_val>=20 else "❌ Lateral"

        _n = r['nombre']
        mini_chart = render_mini_chart(r['nombre'], tc)
        detail=(f'<div class="detail-panel">'
                f'{mini_chart}'
                f'{exit_html}'
                f'{render_ganga_panel(r.get("ganga", {}))}'
                f'{render_capitulacion_panel(r.get("capitulacion", {}))}'
                f'{render_inicio_movimiento_panel(r.get("inicio", {}))}'
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
                + ('<div style="margin-top:10px">'
                   '<button onclick="event.stopPropagation();wlToggle(this,\''+_n+'\')"'
                   ' id="wl-btn-'+_n+'"'
                   ' style="font-size:11px;padding:6px 14px;border-radius:8px;'
                   'border:1px solid #3b82f6;background:var(--surface2);'
                   'color:#3b82f6;cursor:pointer;font-weight:600">'
                   '👁 Agregar a Watchlist</button></div>')
                + '</div>')

        score_color = "var(--green)" if score_aj>=7 else "var(--yellow)" if score_aj>=5 else "var(--red)"
        etapa_emoji, etapa_label, etapa_tooltip = calcular_etapa(r)
        etapa_badge = (f'<span title="{etapa_tooltip}" style="display:inline-block;font-size:13px;'
                       f'margin-left:3px;cursor:help">{etapa_emoji}</span>')
        conf_bar_mini = (f'<div style="width:36px;height:4px;background:var(--brd2);border-radius:2px;margin-top:2px">'
                         f'<div style="height:100%;width:{confianza}%;background:{"#52c41a" if confianza>=70 else "#faad14" if confianza>=40 else "#ff4d4f"};border-radius:2px"></div></div>'
                         if confianza>0 else "")
        h+=(f'<tr class="datarow" onclick="toggle(\'{rid}\')">'
            f'<td><strong>{r["nombre"]}</strong>{etf_badge}{setup_badge}{ganga_badge}{inicio_badge}{sr_badge}{vol_badge}{dist_badge}{cap_badge}{cartera_badge}</td>'
            f'<td>{badge_estado(r["estado"])}</td>'
            f'<td class="num">{fmt(r["precio_mxn"])}<br><span class="hint">USD {(r["precio_usd"] or 0):.2f}</span></td>'
            f'<td class="num" style="color:var(--green)">{fmt(r["entrada_mxn"])}</td>'
            f'<td><div class="rrw"><div class="rrb"><div class="rrf" style="width:{rr_pct:.0f}%;background:{rr_col}"></div></div>'
            f'<span style="color:{rr_col};font-family:var(--mono);font-size:11px">{r["rr"]:.1f}x</span></div>'
            f'<div style="font-size:8px;color:var(--muted);margin-top:1px">{r.get("objetivo_fuente","ATR")}</div></td>'
            f'<td style="color:{rsi_col(r["rsi"])};font-weight:600;font-family:var(--mono)">{r["rsi"]:.0f}</td>'
            f'<td>{"<span style=color:var(--green)>▲</span>" if r["macd_ok"] else "<span style=color:var(--red)>▼</span>"}</td>'
            f'<td>{"<span style=color:var(--green)>↑</span>" if r["ema200_ok"] else "<span style=color:var(--red)>↓</span>"}</td>'
            f'<td>{gbm_cell(r["entrada_mxn"],r["stop_mxn"],r["obj_mxn"])}</td>'
            f'<td>{sr_cell_html}</td>'
            f'<td><span style="font-family:var(--mono);font-size:12px;color:{score_color};font-weight:600">'
            f'{score_aj}/{total_c}</span>{conf_bar_mini}'
            f'{etapa_badge}'
            f'{"<br><span style=font-size:9px;color:var(--muted)>adj VIX</span>" if penaliz>0 else ""}'
            f'</td>'
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
        ganga_badge_r  = badge_ganga(r.get("ganga", {}))
        inicio_badge_r = badge_inicio_movimiento(r.get("inicio", {}))

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

        detail=(f'<div class="detail-panel">'
                f'{exit_html}'
                f'{render_ganga_panel(r.get("ganga", {}))}'
                f'{render_inicio_movimiento_panel(r.get("inicio", {}))}'
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
        ema200_mxn_r   = r.get("ema200_mxn", 0)
        precio_ref_r   = r["precio_mxn"]
        lines_r = []
        if stop_sr_mxn_r:
            d_pct = (stop_sr_mxn_r - precio_ref_r) / precio_ref_r * 100
            lines_r.append(f'<div style="white-space:nowrap"><span style="color:var(--green);font-size:9px">S</span> '
                           f'<span style="color:var(--green);font-family:var(--mono)">{fmt(stop_sr_mxn_r)}</span>'
                           f'<span style="color:var(--muted);font-size:9px"> ({d_pct:.1f}%)</span></div>')
        if obj_sr_mxn_r:
            u_pct = (obj_sr_mxn_r - precio_ref_r) / precio_ref_r * 100
            lines_r.append(f'<div style="white-space:nowrap"><span style="color:var(--red);font-size:9px">R</span> '
                           f'<span style="color:var(--red);font-family:var(--mono)">{fmt(obj_sr_mxn_r)}</span>'
                           f'<span style="color:var(--muted);font-size:9px"> (+{u_pct:.1f}%)</span></div>')
        if ema200_mxn_r and ema200_mxn_r > 0:
            e200_pct_r = (ema200_mxn_r - precio_ref_r) / precio_ref_r * 100
            if e200_pct_r >= 0:
                lines_r.append(f'<div style="white-space:nowrap"><span style="color:#ff7875;font-size:9px;font-style:italic">R EMA200</span> '
                               f'<span style="color:#ff7875;font-family:var(--mono)">{fmt(ema200_mxn_r)}</span>'
                               f'<span style="color:var(--muted);font-size:9px"> (+{e200_pct_r:.1f}%)</span></div>')
            else:
                lines_r.append(f'<div style="white-space:nowrap"><span style="color:#73d13d;font-size:9px;font-style:italic">S EMA200</span> '
                               f'<span style="color:#73d13d;font-family:var(--mono)">{fmt(ema200_mxn_r)}</span>'
                               f'<span style="color:var(--muted);font-size:9px"> ({e200_pct_r:.1f}%)</span></div>')
        sr_cell_r = f'<div style="font-size:10px;line-height:1.9">{"".join(lines_r)}</div>' if lines_r else '<span class="hint" style="font-size:10px">—</span>'
        h+=(f'<tr class="datarow" onclick="toggle(\'{rid}\')">'
            f'<td><strong>{r["nombre"]}</strong>{etf_badge}{setup_badge}{ganga_badge_r}{inicio_badge_r}{sr_badge}{cartera_tag}</td>'
            f'<td>{badge_estado(estado)}</td>'
            f'<td class="num">{fmt(r["precio_mxn"])}<br><span class="hint">USD {(r["precio_usd"] or 0):.2f}</span></td>'
            f'<td class="num" style="color:var(--green)">{fmt(r["entrada_mxn"])}</td>'
            f'<td class="num" style="color:{pot_col};font-weight:{"600" if r["pot_alza"]>=10 else "400"}">{r["pot_alza"]:+.1f}%</td>'
            f'<td><div class="rrw"><div class="rrb"><div class="rrf" style="width:{rr_pct:.0f}%;background:{rr_col}"></div></div>'
            f'<span style="color:{rr_col};font-family:var(--mono);font-size:11px">{r["rr"]:.1f}x</span></div>'
            f'<div style="font-size:8px;color:var(--muted);margin-top:1px">{r.get("objetivo_fuente","ATR")}</div></td>'
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

def render_que_hago_hoy(scan_data: list, port_data: list) -> str:
    """
    Panel ultra simple — ¿Qué hago hoy?
    Muestra solo las acciones con decisión clara: COMPRAR, VENDER, ESPERAR.
    Sin tecnicismos. Solo ticker, acción, cuántas, precio entrada, stop, objetivo.
    """
    filas = []

    # ── COMPRAR: scanner con señal COMPRAR, no bloqueado, RR >= 3 ──
    for r in scan_data:
        estado  = r.get("estado", "")
        senal   = r.get("tfs", {}).get("1D", {}).get("senal", "")
        rr      = r.get("rr", 0)
        bloq    = estado == "Bloqueado"
        if senal == "COMPRAR" and not bloq and rr >= 3.0:
            sz      = r.get("sizing", {})
            titulos = sz.get("titulos_adicionales", 0) or sz.get("titulos", 0)
            filas.append({
                "accion":  "COMPRAR",
                "emoji":   "🟢",
                "ticker":  r["nombre"],
                "titulos": titulos,
                "precio":  r.get("precio_mxn", 0),
                "entrada": r.get("entrada_mxn", 0),
                "stop":    r.get("stop_mxn", 0),
                "obj":     r.get("obj_mxn", 0),
                "rr":      rr,
            })

    # ── VENDER: portafolio con recomendación SALIR o TOMAR GANANCIAS ──
    for p in port_data:
        rec = p.get("recomendacion", {})
        if not rec:
            continue
        accion = rec.get("accion", "")
        if accion in ("SALIR", "TOMAR GANANCIAS"):
            emoji  = "🔴" if accion == "SALIR" else "💰"
            filas.append({
                "accion":  accion,
                "emoji":   emoji,
                "ticker":  p["ticker"],
                "titulos": p.get("titulos", 0),
                "precio":  p.get("precio_actual_mxn", 0),
                "entrada": p.get("cto_prom_mxn", 0),
                "stop":    rec.get("stop", 0),
                "obj":     rec.get("objetivo", 0),
                "rr":      0,
            })

    # ── VIGILAR: scanner con etapa 🟠 A punto ──
    for r in scan_data:
        etapa_emoji, etapa_label, _ = calcular_etapa(r)
        if etapa_label == "A punto":
            filas.append({
                "accion":  "VIGILAR",
                "emoji":   "🟠",
                "ticker":  r["nombre"],
                "titulos": "—",
                "precio":  r.get("precio_mxn", 0),
                "entrada": r.get("entrada_mxn", 0),
                "stop":    r.get("stop_mxn", 0),
                "obj":     r.get("obj_mxn", 0),
                "rr":      r.get("rr", 0),
            })

    if not filas:
        return (
            '<div style="background:var(--surface);border:1px solid var(--brd);border-radius:var(--r2);'
            'padding:16px 18px;margin-bottom:16px">'
            '<div style="font-size:13px;font-weight:600;margin-bottom:6px">📋 ¿Qué hago hoy?</div>'
            '<div style="font-size:12px;color:var(--muted)">Sin acciones claras por ahora — espera mejores setups.</div>'
            '</div>'
        )

    orden = {"SALIR": 0, "TOMAR GANANCIAS": 1, "COMPRAR": 2, "VIGILAR": 3}
    filas.sort(key=lambda x: orden.get(x["accion"], 9))

    rows_html = ""
    for f in filas:
        titulos_txt = f"{f['titulos']:.2f} tít." if isinstance(f["titulos"], float) else str(f["titulos"])
        color_accion = {
            "COMPRAR": "#16a34a", "SALIR": "#dc2626",
            "TOMAR GANANCIAS": "#d46b08", "VIGILAR": "#b45309"
        }.get(f["accion"], "var(--text)")
        rows_html += (
            f'<tr style="border-bottom:1px solid var(--brd);font-size:12px">'
            f'<td style="padding:8px 10px;font-weight:700;color:{color_accion}">{f["emoji"]} {f["accion"]}</td>'
            f'<td style="padding:8px 10px;font-weight:700">{f["ticker"]}</td>'
            f'<td style="padding:8px 10px;font-family:var(--mono);text-align:right">{titulos_txt}</td>'
            f'<td style="padding:8px 10px;font-family:var(--mono);text-align:right">{fmt(f["precio"])}</td>'
            f'<td style="padding:8px 10px;font-family:var(--mono);text-align:right;color:var(--green)">{fmt(f["entrada"])}</td>'
            f'<td style="padding:8px 10px;font-family:var(--mono);text-align:right;color:var(--red)">{fmt(f["stop"])}</td>'
            f'<td style="padding:8px 10px;font-family:var(--mono);text-align:right;color:#16a34a">{fmt(f["obj"])}</td>'
            f'</tr>'
        )

    return (
        '<div style="background:var(--surface);border:2px solid var(--brd);border-radius:var(--r2);'
        'padding:16px 18px;margin-bottom:16px">'
        '<div style="font-size:14px;font-weight:700;margin-bottom:12px;letter-spacing:-.3px">📋 ¿Qué hago hoy?</div>'
        '<div style="overflow-x:auto">'
        '<table style="width:100%;border-collapse:collapse">'
        '<thead><tr style="border-bottom:2px solid var(--brd);font-size:10px;color:var(--muted);text-transform:uppercase;letter-spacing:.5px">'
        '<th style="padding:6px 10px;text-align:left">Acción</th>'
        '<th style="padding:6px 10px;text-align:left">Ticker</th>'
        '<th style="padding:6px 10px;text-align:right">Títulos</th>'
        '<th style="padding:6px 10px;text-align:right">Precio actual</th>'
        '<th style="padding:6px 10px;text-align:right">Entrada</th>'
        '<th style="padding:6px 10px;text-align:right">Stop</th>'
        '<th style="padding:6px 10px;text-align:right">Objetivo</th>'
        '</thead><tbody>'
        + rows_html +
        '</tbody></table></div>'
        '<div style="font-size:10px;color:var(--muted);margin-top:8px">'
        '⚠️ Solo fines educativos — no es asesoría financiera. Usa siempre stop loss.</div>'
        '</div>'
    )

# ═══════════════════════════════════════════════════════════
#   TOP DIARIO Y SEMANAL — v2 con ordenamiento correcto
# ═══════════════════════════════════════════════════════════

def _calcular_puntuacion_top(r: dict) -> tuple:
    """Devuelve (prioridad, puntuacion) para ordenar.
    Prioridad: 0=ROCKET/5/5, 1=BUY, 2=Ganga, 3=Pre-breakout 4/5, 4=resto
    """
    estado  = r.get("estado", "")
    score   = r.get("score_ajustado", r.get("score", 0))
    total_c = r.get("total_criterios", 9)
    rr      = r.get("rr", 0)
    es_ganga = r.get("ganga", {}).get("es_ganga", False) if r.get("ganga") else False
    nivel    = r.get("inicio", {}).get("nivel", "") if r.get("inicio") else ""
    es_pre   = nivel == "pre_breakout"

    if estado in ("ROCKET", "Explosión") or score >= total_c:
        prioridad = 0
    elif estado in ("BUY", "Compra") and not es_ganga and not es_pre:
        prioridad = 1
    elif es_ganga:
        prioridad = 2
    elif es_pre:
        prioridad = 3
    else:
        prioridad = 4

    puntuacion = (score / total_c * 0.5) + (min(rr, 10) / 10 * 0.5)
    return (prioridad, -puntuacion)


def actualizar_top_diario(scan_data: list) -> None:
    """Guarda el top del día en DB."""
    hoy = _fecha_hoy_cdmx()
    try:
        con = sqlite3.connect(DB_FILE)
        con.execute("DELETE FROM top_diario_acumulado WHERE fecha != ?", (hoy,))
        for r in scan_data:
            if r.get("rr", 0) < RR_MINIMO:
                continue
            prioridad, neg_punt = _calcular_puntuacion_top(r)
            puntuacion = -neg_punt
            ticker = r["nombre"]
            existing = con.execute(
                "SELECT puntuacion FROM top_diario_acumulado WHERE ticker=? AND fecha=?",
                (ticker, hoy)
            ).fetchone()
            datos = json.dumps(r, ensure_ascii=False, default=str)
            if existing is None:
                con.execute(
                    "INSERT INTO top_diario_acumulado (ticker, fecha, puntuacion, datos_json) VALUES (?,?,?,?)",
                    (ticker, hoy, puntuacion, datos)
                )
            elif puntuacion > existing[0]:
                con.execute(
                    "UPDATE top_diario_acumulado SET puntuacion=?, datos_json=? WHERE ticker=? AND fecha=?",
                    (puntuacion, datos, ticker, hoy)
                )
        con.commit()
        con.close()
    except Exception as e:
        print(f"[top_diario] ⚠️ Error: {e}")


def obtener_top_diario(n: int = 20) -> list:
    hoy = _fecha_hoy_cdmx()
    resultado = []
    try:
        con = sqlite3.connect(DB_FILE)
        cur = con.execute(
            "SELECT datos_json FROM top_diario_acumulado WHERE fecha=? ORDER BY puntuacion DESC LIMIT ?",
            (hoy, n)
        )
        for row in cur.fetchall():
            try: resultado.append(json.loads(row[0]))
            except: pass
        con.close()
    except Exception as e:
        print(f"[top_diario] ⚠️ Error: {e}")
    return sorted(resultado, key=_calcular_puntuacion_top)


def actualizar_top_semanal_acum(scan_data: list) -> None:
    """Guarda el top semanal en DB."""
    semana = _semana_actual_cdmx() if callable(globals().get("_semana_actual_cdmx")) else datetime.utcnow().strftime("%G-W%V")
    try:
        con = sqlite3.connect(DB_FILE)
        for r in scan_data:
            if r.get("rr", 0) < RR_MINIMO:
                continue
            prioridad, neg_punt = _calcular_puntuacion_top(r)
            puntuacion = -neg_punt
            ticker = r["nombre"]
            existing = con.execute(
                "SELECT puntuacion FROM top_semanal_acumulado WHERE ticker=? AND semana=?",
                (ticker, semana)
            ).fetchone()
            datos = json.dumps(r, ensure_ascii=False, default=str)
            if existing is None:
                con.execute(
                    "INSERT INTO top_semanal_acumulado (ticker, semana, puntuacion, datos_json) VALUES (?,?,?,?)",
                    (ticker, semana, puntuacion, datos)
                )
            elif puntuacion > existing[0]:
                con.execute(
                    "UPDATE top_semanal_acumulado SET puntuacion=?, datos_json=? WHERE ticker=? AND semana=?",
                    (puntuacion, datos, ticker, semana)
                )
        con.commit()
        con.close()
    except Exception as e:
        print(f"[top_semanal] ⚠️ Error: {e}")


def obtener_top_semanal_acum(n: int = 20) -> list:
    semana = _semana_actual_cdmx() if callable(globals().get("_semana_actual_cdmx")) else datetime.utcnow().strftime("%G-W%V")
    resultado = []
    try:
        con = sqlite3.connect(DB_FILE)
        cur = con.execute(
            "SELECT datos_json FROM top_semanal_acumulado WHERE semana=? ORDER BY puntuacion DESC LIMIT ?",
            (semana, n)
        )
        for row in cur.fetchall():
            try: resultado.append(json.loads(row[0]))
            except: pass
        con.close()
    except Exception as e:
        print(f"[top_semanal] ⚠️ Error: {e}")
    return sorted(resultado, key=_calcular_puntuacion_top)


def _render_top_cards(items: list, tab_id: str, titulo: str, subtitulo: str) -> str:
    """Renderiza las tarjetas del top con secciones por categoría."""
    if not items:
        return f'''<div id="{tab_id}" class="tab" style="display:none">
  <div style="padding:40px;text-align:center;color:var(--muted)">
    <div style="font-size:48px;margin-bottom:16px">📊</div>
    <div style="font-size:16px;font-weight:600">Sin candidatas todavía</div>
    <div style="font-size:13px;margin-top:8px">Corre el scanner para poblar el Top. Necesitas R:R ≥ {RR_MINIMO}x.</div>
  </div>
</div>'''

    # Agrupar por prioridad
    grupos = {0: [], 1: [], 2: [], 3: [], 4: []}
    for r in items:
        p, _ = _calcular_puntuacion_top(r)
        grupos[p].append(r)

    etiquetas = {
        0: ("🚀 ROCKET / Máximo Score", "#7c3aed", "#f5f3ff"),
        1: ("↑ BUY — Entrada válida", "#16a34a", "#f0fdf4"),
        2: ("🏷️ Gangas — Precio castigado", "#d97706", "#fffbeb"),
        3: ("⚡ Pre-breakout 4/5", "#b45309", "#fef3c7"),
        4: ("📋 Otros candidatos", "#6b7280", "#f9fafb"),
    }

    secciones_html = ""
    medal_counter = 0
    medals = ["🥇","🥈","🥉","4️⃣","5️⃣","6️⃣","7️⃣","8️⃣","9️⃣","🔟",
              "1️⃣1️⃣","1️⃣2️⃣","1️⃣3️⃣","1️⃣4️⃣","1️⃣5️⃣","1️⃣6️⃣","1️⃣7️⃣","1️⃣8️⃣","1️⃣9️⃣","2️⃣0️⃣"]

    for prioridad in range(5):
        grupo = grupos[prioridad]
        if not grupo:
            continue
        label, color, bg = etiquetas[prioridad]
        cards_html = ""
        for r in grupo:
            if medal_counter >= len(medals):
                break
            medal = medals[medal_counter]
            medal_counter += 1
            nombre  = r.get("nombre", "—")
            precio  = r.get("precio_mxn", 0)
            rr      = r.get("rr", 0)
            score   = r.get("score_ajustado", r.get("score", 0))
            total_c = r.get("total_criterios", 9)
            estado  = r.get("estado", "—")
            sl      = r.get("stop_mxn") or r.get("sizing", {}).get("sl_mxn") or 0
            obj     = r.get("obj_mxn") or r.get("sizing", {}).get("objetivo_mxn") or 0
            entrada = r.get("entrada_mxn") or precio
            rsi     = r.get("rsi", 0)

            cards_html += f'''
    <div style="background:var(--surface);border:1px solid var(--brd);border-radius:12px;padding:18px;position:relative;overflow:hidden">
      <div style="position:absolute;top:0;left:0;right:0;height:3px;background:{color}"></div>
      <div style="display:flex;justify-content:space-between;align-items:flex-start;margin-bottom:12px">
        <div>
          <div style="font-size:24px;font-weight:800;letter-spacing:-1px">{medal} {nombre}</div>
          <div style="font-size:11px;color:var(--muted);margin-top:2px">${precio:,.2f} MXN · RSI {rsi:.0f}</div>
        </div>
        <div style="text-align:right">
          <div style="font-size:20px;font-weight:700;color:var(--green)">{rr:.1f}x</div>
          <div style="font-size:10px;color:var(--muted)">R:R</div>
        </div>
      </div>
      <div style="display:grid;grid-template-columns:1fr 1fr 1fr;gap:8px;font-size:11px;margin-bottom:10px">
        <div style="background:var(--bg);border-radius:6px;padding:7px 9px">
          <div style="color:var(--muted);font-size:9px;margin-bottom:2px">SCORE</div>
          <div style="font-weight:700">{score}/{total_c}</div>
        </div>
        <div style="background:var(--bg);border-radius:6px;padding:7px 9px">
          <div style="color:var(--muted);font-size:9px;margin-bottom:2px">STOP</div>
          <div style="font-weight:600;color:var(--red)">${sl:,.2f}</div>
        </div>
        <div style="background:var(--bg);border-radius:6px;padding:7px 9px">
          <div style="color:var(--muted);font-size:9px;margin-bottom:2px">OBJETIVO</div>
          <div style="font-weight:600;color:var(--green)">${obj:,.2f}</div>
        </div>
      </div>
      <div style="font-size:10px;color:var(--muted)">Entrada EMA9: <strong style="color:var(--green)">${entrada:,.2f}</strong> · Estado: {estado}</div>
    </div>'''

        secciones_html += f'''
  <div style="margin-bottom:24px">
    <div style="display:flex;align-items:center;gap:8px;margin-bottom:12px;padding:8px 12px;background:{bg};border-radius:8px;border-left:3px solid {color}">
      <span style="font-weight:700;font-size:13px;color:{color}">{label}</span>
      <span style="font-size:11px;color:var(--muted)">{len(grupo)} acciones</span>
    </div>
    <div style="display:grid;grid-template-columns:repeat(auto-fill,minmax(300px,1fr));gap:12px">
      {cards_html}
    </div>
  </div>'''

    return f'''<div id="{tab_id}" class="tab" style="display:none">
  <div style="padding:20px 0 14px">
    <h2 style="font-size:20px;font-weight:600;letter-spacing:-.4px">{titulo}</h2>
    <p class="hint">{subtitulo}</p>
  </div>
  {secciones_html}
</div>'''


def render_tab_top_semanal(top: list, tc: float) -> str:
    hoy = _fecha_hoy_cdmx()
    return _render_top_cards(
        top,
        tab_id="tab-top",
        titulo="🏆 Top Semanal",
        subtitulo=f"Mejores candidatas de la semana · Ordenadas por prioridad · {len(top)} acciones · {hoy}"
    )


def render_tab_top_diario(top: list, tc: float) -> str:
    hoy = _fecha_hoy_cdmx()
    return _render_top_cards(
        top,
        tab_id="tab-topd",
        titulo="📅 Top Diario",
        subtitulo=f"Mejores candidatas de hoy · Ordenadas por prioridad · {len(top)} acciones · {hoy}"
    )

def generar_html(port_data, scan_data, radar_data, ops, tc, capital, riesgo_pct, rr_min,
                 vix: float = 20.0, spy: dict | None = None, regimen: dict | None = None):
    if spy     is None: spy     = {"sobre_ema200": True}
    if regimen is None: regimen = regimen_mercado(vix, spy)
    from datetime import timezone, timedelta
    tz_mx = timezone(timedelta(hours=-6))
    ts  = datetime.now(tz_mx).strftime("%d/%m/%Y %H:%M")
    res = resumen_hist(ops)

    total_valor = sum(p.get("valor_mxn",0) for p in port_data)
    total_costo = sum(p.get("costo_total",0) for p in port_data)
    total_pl    = total_valor-total_costo
    total_pl_pct= (total_pl/total_costo*100) if total_costo else 0
    n_alertas   = sum(len(p.get("alertas",[])) for p in port_data)

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
    } for p in port_data], ensure_ascii=False).replace("</", "<\\/")

    scan_nombres_json = json.dumps([{
        "nombre": r.get("nombre",""),
        "estado": r.get("estado",""),
    } for r in scan_data], ensure_ascii=False).replace("</", "<\\/")
    scan_data_json = json.dumps([{
        "nombre":        r.get("nombre",""),
        "estado":        r.get("estado",""),
        "precio_mxn":    r.get("precio_mxn",0),
        "entrada_mxn":   r.get("entrada_mxn",0),
        "stop_mxn":      r.get("stop_mxn",0),
        "obj_mxn":       r.get("obj_mxn",0),
        "rr":            r.get("rr",0),
        "rsi":           r.get("rsi",0),
        "macd_ok":       r.get("macd_ok",False),
        "ema200_ok":     r.get("ema200_ok",False),
        "score":         r.get("score",0),
        "score_ajustado":r.get("score_ajustado",0),
        "total_criterios":r.get("total_criterios",13),
        "setup":         r.get("setup",{}),
        "ganga":         r.get("ganga",{}),
        "sector":        r.get("sector",{}),
    } for r in scan_data], ensure_ascii=False)

    port_rows       = render_port_rows(port_data, tc)
    scan_rows       = render_scan_rows(scan_data, tc)
    que_hago_hoy    = render_que_hago_hoy(scan_data, port_data)
    radar_rows = render_radar_rows(radar_data, tc)
    hist_rows  = render_hist_rows(ops)



    # ── TOP SEMANAL Y DIARIO ────────────────────────────────
    actualizar_top_semanal_acum(scan_data)
    top_semanal         = obtener_top_semanal_acum(n=20)
    top_tab_html        = render_tab_top_semanal(top_semanal, tc)

    actualizar_top_diario(scan_data)
    top_diario          = obtener_top_diario(n=20)
    top_diario_tab_html = render_tab_top_diario(top_diario, tc)

    # ── SEMIS ETF ────────────────────────────────────────────
    semis_data     = analizar_todos_semis(tc)
    semis_tab_html = render_tab_semis(semis_data, tc)

    # ── WATCHLIST ────────────────────────────────────────────
    watchlist_tab_html = render_tab_watchlist(scan_data, radar_data, tc)

    # ── DIARIO DE TRADING ────────────────────────────────────
    diario_entradas   = get_diario(limite=50)
    diario_stats      = get_estadisticas_diario()
    diario_tab_html   = render_tab_diario(diario_entradas, diario_stats)

    # ── RENDIMIENTO VS SPY ───────────────────────────────────
    capital_actual = capital + total_valor - total_costo  # capital config + P&L abierto
    spy_precio     = get_spy_precio_actual()
    registrar_snapshot_pnl(capital_actual, spy_precio)
    pnl_hist       = get_pnl_historico(dias=90)
    vs_spy         = calcular_rendimiento_vs_spy(pnl_hist, spy_precio)
    rendimiento_tab_html = render_tab_rendimiento(pnl_hist, vs_spy, diario_stats)

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
.tab{{display:none!important}}.tab.active{{display:block!important}}
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
</style><style>.tab{{display:none!important}}.tab.active{{display:block!important}}</style></head><body>

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
    <span style="font-size:11px;color:var(--muted)">{ts}</span>
  </div>
</div></div>

<div class="nav"><div class="nav-inner">
  <button class="nb" onclick="showTab('portafolio',this)">Mi portafolio</button>
  <button class="nb" onclick="showTab('registrar',this)">Registrar operación</button>
  <button class="nb" onclick="showTab('historial',this)">Historial</button>
  <button class="nb active" onclick="showTab('scanner',this)">Scanner</button>
  <button class="nb" onclick="showTab('top',this)">🏆 Top Semanal</button>
  <button class="nb" onclick="showTab('topd',this)">📅 Top Diario</button>
  <button class="nb" onclick="showTab('wl',this)">👁 Watchlist</button>
  <button class="nb" onclick="showTab('diario',this)">📓 Diario</button>
  <button class="nb" onclick="showTab('rendimiento',this)">📊 Rendimiento</button>
  <button class="nb" onclick="showTab('semis',this)">📡 Semis ETF</button>
  <button class="nb" onclick="showTab('radar',this)">🔭 Radar automático</button>
  <button class="nb" onclick="showTab('ia',this)">🧠 IA</button>
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
    <div class="kpi"><div class="lbl">Valor total</div><div class="val" id="kpi-valor">{fmt(total_valor)}</div></div>
    <div class="kpi"><div class="lbl">Costo total</div><div class="val" id="kpi-costo">{fmt(total_costo)}</div></div>
    <div class="kpi"><div class="lbl">P&L total</div><div class="val" id="kpi-pl">{fmt(total_pl)}</div></div>
    <div class="kpi"><div class="lbl">Rendimiento</div><div class="val" id="kpi-rend">{total_pl_pct:+.1f}%</div></div>
    <div class="kpi"><div class="lbl">Posiciones</div><div class="val" id="kpi-pos">{len(port_data)}</div></div>
    <div class="kpi"><div class="lbl">Alertas</div><div class="val" id="kpi-alertas">{n_alertas}</div></div>
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
        <th style="color:var(--green)">¿Qué hago?</th>
        <th class="sr-th" title="Soporte / Resistencia automáticos">📊 S/R</th>
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
      <div class="fg"><label>R:R esperado</label><input type="number" id="f_rr" step="0.1" min="0" placeholder="3.0"></div>
    </div>
    <div style="padding:0 16px 12px">
      <div style="background:linear-gradient(135deg,#0f172a,#1e293b);border:1px solid #334155;border-radius:10px;padding:16px;margin-bottom:12px">
        <div style="font-size:13px;font-weight:700;color:#94a3b8;margin-bottom:12px">📓 Diario de trading — obligatorio</div>
        <div style="display:grid;grid-template-columns:1fr 1fr;gap:10px;margin-bottom:10px">
          <div class="fg" style="margin:0">
            <label style="color:#94a3b8;font-size:11px">Score al entrar (ej: 8)</label>
            <input type="number" id="f_score" min="0" max="13" step="1" placeholder="8" style="width:100%">
          </div>
          <div class="fg" style="margin:0">
            <label style="color:#94a3b8;font-size:11px">Tipo de setup</label>
            <select id="f_setup" style="width:100%">
              <option value="">— Selecciona —</option>
              <option value="Ganga">🏷️ Ganga +15%</option>
              <option value="Pre-breakout">⚡ Pre-breakout 4/5</option>
              <option value="Listo 5/5">✅ Listo 5/5</option>
              <option value="Acumulacion">🟡 Acumulación</option>
              <option value="DCA">📥 DCA escalonado</option>
              <option value="Pullback">↩️ Pullback a EMA</option>
              <option value="Breakout">🚀 Breakout</option>
              <option value="Otro">Otro</option>
            </select>
          </div>
        </div>
        <div class="fg" style="margin:0">
          <label style="color:#94a3b8;font-size:11px">¿Por qué entras? — sé específico <span style="color:#ef4444">*obligatorio*</span></label>
          <textarea id="f_razon" rows="3" placeholder="Ej: Score 8/13, RSI 32 en soporte $2,400, divergencia RSI alcista, Ganga activa con R:R 4.2x. EMA9 cruzando arriba de EMA21. Sector SMH alcista." style="width:100%;resize:vertical;font-family:var(--sans);font-size:12px;padding:8px;border-radius:8px;border:1px solid #334155;background:#0f172a;color:var(--text)"></textarea>
        </div>
      </div>
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
    <div class="kpi"><div class="lbl">P&L realizado</div><div class="val {pl_hist_cls}">{fmt(res['pl']) if res.get('n_ops_cerradas',0) > 0 else '$0.00'}</div></div>
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
  {que_hago_hoy}
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
          <option value="Vigilar">👁 Vigilar</option>
          <option value="Esperar">Esperar</option>
          <option value="Bajista">↓ Bajista</option>
          <option value="Bloqueado">🔒 Bloqueado</option>
        </select>
        <button onclick="exportarScannerCSV()" style="font-size:11px;padding:4px 10px;border-radius:6px;border:1px solid var(--brd);background:var(--surface2);color:var(--text);cursor:pointer" title="Exportar scanner a Excel/CSV">📥 Excel</button>
      </div>
    </div>
    <div style="overflow-x:auto"><table id="scan_table">
      <thead><tr>
        <th onclick="sortScanner(0,'str')" style="cursor:pointer;user-select:none" title="Ordenar A-Z">Ticker <span id="srt0">⇅</span></th>
        <th>Estado</th>
        <th onclick="sortScanner(2,'num')" style="cursor:pointer;user-select:none" title="Ordenar por precio">Precio MXN <span id="srt2">⇅</span></th>
        <th style="color:var(--green)">Entrada EMA9</th>
        <th onclick="sortScanner(4,'num')" style="cursor:pointer;user-select:none" title="Ordenar por R:R">R:R <span id="srt4">⇅</span></th>
        <th onclick="sortScanner(5,'num')" style="cursor:pointer;user-select:none" title="Ordenar por RSI">RSI <span id="srt5">⇅</span></th>
        <th>MACD</th><th>EMA200</th><th style="color:var(--green)">Orden GBM 🎯</th>
        <th class="sr-th" title="Soporte y Resistencia automáticos">📊 S/R</th>
        <th onclick="sortScanner(10,'num')" style="cursor:pointer;user-select:none" title="Ordenar por Score">Score <span id="srt10">⇅</span></th>
      </tr></thead>
      <tbody id="scan_tbody">{scan_rows or '<tr><td colspan="10" style="text-align:center;color:var(--muted);padding:24px;font-size:12px">Sin datos — verifica tu API key en <a href="/api/debug" target="_blank" style="color:var(--blue)">/api/debug</a></td></tr>'}</tbody>
    </table></div>
  </div>
</div>



{top_tab_html}

{top_diario_tab_html}

{watchlist_tab_html}

{diario_tab_html}

{rendimiento_tab_html}

{semis_tab_html}

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

<footer>Solo fines educativos · No es asesoría financiera · Usa siempre stop loss<br>
TC: Banxico/Frankfurter · Precios: API financiera · DB: SQLite · finbit pro v3.3-tabs</footer>
</div>

<!-- TAB IA -->
<div id="tab-ia" class="tab">
<div style="padding:20px 20px 48px;max-width:1360px;margin:0 auto">
  <div style="padding:20px 0 14px;display:flex;align-items:center;justify-content:space-between;flex-wrap:wrap;gap:10px">
    <div>
      <h2 style="font-size:20px;font-weight:600;letter-spacing:-.4px">🧠 Análisis IA</h2>
      <p style="font-size:11px;color:var(--muted)">Análisis narrativo de cada acción del scanner generado por Claude · Haz clic en Analizar para obtener el resumen</p>
    </div>
    <button onclick="analizarTodos()" id="btn-analizar-todos"
      style="background:#7c3aed;color:#fff;border:none;border-radius:8px;padding:8px 18px;font-size:13px;font-family:var(--sans);cursor:pointer;font-weight:500">
      🧠 Analizar todos
    </button>
  </div>
  <div id="ia-lista" style="display:flex;flex-direction:column;gap:12px">
    <div style="padding:30px;text-align:center;color:var(--muted)">Haz clic en "Analizar todos" o en el botón de cada acción para generar el análisis.</div>
  </div>
</div>
</div>

<script>
const TC = {tc:.4f};
let PORT_BASE = [];
function _initPort() {{
  actualizarTablaPortafolio();
}}
fetch('/api/port/json').then(r=>r.json()).then(d=>{{ PORT_BASE=d; _initPort(); }}).catch(()=>{{}});

// ── Tabs ─────────────────────────────────────────────────
// ── Fix: rescatar tabs atrapados dentro de otro tab ──────
(function fixTabs(){{
  const wrap = document.querySelector('.wrap') || document.body;
  const scanner = document.getElementById('tab-scanner');
  if(!scanner) return;
  const tabIds = ['tab-top','tab-topd','tab-wl','tab-diario','tab-rendimiento',
                  'tab-semis','tab-radar','tab-ia','tab-portafolio',
                  'tab-registrar','tab-historial'];
  tabIds.forEach(id=>{{
    const el = document.getElementById(id);
    if(el && scanner.contains(el)){{
      el.style.setProperty('display','none','important');
      wrap.appendChild(el);
    }}
  }});
}})();

function showTab(name,btn){{
  document.querySelectorAll('.tab').forEach(t=>{{
    t.classList.remove('active');
    t.style.removeProperty('display');
  }});
  document.querySelectorAll('.nb').forEach(b=>b.classList.remove('active'));
  const tab=document.getElementById('tab-'+name);
  if(tab){{
    tab.classList.add('active');
    tab.style.removeProperty('display');
  }}
  if(btn)btn.classList.add('active');
  if(name==='ia') initIaTab();
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
  if(ops.length > 0){{
    // Si hay operaciones registradas, calcular portafolio SOLO desde las ops
    // No usar PORT_BASE para evitar duplicados
    ops.forEach(op=>{{
      const t=op.ticker;
      if(!map[t]) map[t]={{titulos:0,costoTotal:0,origen:op.origen||'USA',mercado:op.mercado||'SIC',precio_actual_mxn:null,pl_mxn:0,pl_pct:0}};
      if(op.tipo==='COMPRA'){{
        map[t].costoTotal+=op.titulos*op.precio_mxn;
        map[t].titulos+=op.titulos;
      }} else if(op.tipo==='VENTA'){{
        if(!map[t] || (map[t].titulos<=0 && map[t].costoTotal<=0)){{
          const base=PORT_BASE.find(p=>p.ticker===t);
          if(base) map[t]={{titulos:base.titulos,costoTotal:base.cto_prom_mxn*base.titulos,origen:base.origen||'USA',mercado:base.mercado||'SIC',precio_actual_mxn:base.precio_actual_mxn,pl_mxn:0,pl_pct:0}};
          else if(!map[t]) map[t]={{titulos:0,costoTotal:0,origen:'USA',mercado:'SIC',precio_actual_mxn:null,pl_mxn:0,pl_pct:0}};
        }}
        if(map[t] && map[t].titulos>0){{
          const cto=map[t].costoTotal/map[t].titulos;
          map[t].costoTotal-=op.titulos*cto;
          map[t].titulos-=op.titulos;
        }}
      }}
    }});
    // Agregar precio actual desde PORT_BASE si está disponible
    PORT_BASE.forEach(p=>{{
      if(map[p.ticker]) map[p.ticker].precio_actual_mxn = p.precio_actual_mxn;
    }});
  }} else {{
    // Sin ops en localStorage, usar PORT_BASE como respaldo
    PORT_BASE.forEach(p=>{{
      map[p.ticker]={{titulos:p.titulos,costoTotal:p.cto_prom_mxn*p.titulos,
        origen:p.origen,mercado:p.mercado,precio_actual_mxn:p.precio_actual_mxn,
        pl_mxn:p.pl_mxn,pl_pct:p.pl_pct}};
    }});
  }}
  // Eliminar tickers con 0 títulos (posiciones cerradas)
  Object.keys(map).forEach(t=>{{ if(map[t].titulos<=0.0001) delete map[t]; }});
  return map;
}}

function actualizarTablaPortafolio(){{
  const ops=JSON.parse(localStorage.getItem('finbit_ops')||'[]');
  const map=recalcPortafolio(ops);
  const tbody=document.getElementById('port_tbody');

  // ── Actualizar tarjetas KPI desde localStorage ─────────────
  let totalCosto=0, totalValor=0, nPos=0;
  Object.entries(map).forEach(([tk,d])=>{{
    if(d.titulos<=0) return;
    const cto = d.titulos>0 ? d.costoTotal/d.titulos : 0;
    const costoPos = cto*d.titulos;
    const valorPos = (d.precio_actual_mxn||cto)*d.titulos;
    totalCosto += costoPos;
    totalValor += valorPos;
    nPos++;
  }});
  const totalPL    = totalValor - totalCosto;
  const rendPct    = totalCosto>0 ? (totalPL/totalCosto*100) : 0;
  const fmtMXN = v => (v<0?'-$':'$')+Math.abs(v).toLocaleString('es-MX',{{minimumFractionDigits:2,maximumFractionDigits:2}});

  const setKPI = (id, val, cls) => {{
    const el = document.getElementById(id);
    if(!el) return;
    el.textContent = val;
    if(cls) el.className = 'val '+cls;
  }};
  setKPI('kpi-valor', fmtMXN(totalValor));
  setKPI('kpi-costo', fmtMXN(totalCosto));
  setKPI('kpi-pl',   fmtMXN(totalPL),   totalPL>=0?'pos':'neg');
  setKPI('kpi-rend', (rendPct>=0?'+':'')+rendPct.toFixed(1)+'%', rendPct>=0?'pos':'neg');
  setKPI('kpi-pos',  nPos);

  if(!tbody) return;

  // ── Ocultar filas de posiciones que ya se cerraron (titulos=0) ──
  tbody.querySelectorAll('tr.datarow').forEach(row=>{{
    const tkEl=row.querySelector('td strong');
    if(!tkEl) return;
    const tk=tkEl.textContent.trim();
    const d=map[tk];
    const cerrada = !d || d.titulos<=0;
    row.style.display = cerrada ? 'none' : '';
    const next=row.nextElementSibling;
    if(next&&next.classList.contains('detail')) next.style.display = cerrada?'none':'';
  }});

  // ── Actualizar filas existentes con datos del localStorage ──
  tbody.querySelectorAll('tr.datarow').forEach(row=>{{
    const tkEl=row.querySelector('td strong');
    if(!tkEl) return;
    const tk=tkEl.textContent.trim();
    const d=map[tk];
    if(!d||d.titulos<=0) return;
    const cto=d.titulos>0? d.costoTotal/d.titulos : 0;
    const cells=row.querySelectorAll('td');
    if(cells.length<7) return;
    cells[1].textContent=parseFloat(d.titulos.toFixed(6));
    cells[2].innerHTML='<span class="num">$'+cto.toLocaleString('es-MX',{{minimumFractionDigits:2,maximumFractionDigits:2}})+'</span>';
    const costo=cto*d.titulos;
    cells[4].innerHTML='<span class="num">$'+( (d.precio_actual_mxn||cto)*d.titulos ).toLocaleString('es-MX',{{minimumFractionDigits:2,maximumFractionDigits:2}})+'</span>';
    if(d.precio_actual_mxn){{
      const pl=(d.precio_actual_mxn-cto)*d.titulos;
      const plPct=(pl/costo*100);
      const col=pl>=0?'var(--green)':'var(--red)';
      cells[5].innerHTML='<span class="num" style="color:'+col+'">'+( pl<0?'-':'')+'$'+Math.abs(pl).toLocaleString('es-MX',{{minimumFractionDigits:2,maximumFractionDigits:2}})+'</span>';
      cells[6].innerHTML='<span class="num" style="color:'+col+'">'+(plPct>=0?'+':'')+plPct.toFixed(1)+'%</span>';
    }}
  }});

  // ── Agregar filas nuevas (posiciones no renderizadas por el servidor) ──
  Object.entries(map).forEach(([tk,d])=>{{
    if(d.titulos<=0) return;
    const exists=[...tbody.querySelectorAll('tr.datarow')].some(r=>r.querySelector('td strong')?.textContent.trim()===tk);
    if(exists) return;
    const cto=d.costoTotal/d.titulos;
    const rid='pr_'+tk.replace(/[ .]/g,'_');
    const newRow=document.createElement('tr');
    newRow.className='datarow';
    newRow.setAttribute('onclick',`toggle('${{rid}}')`);
    newRow.innerHTML=
      `<td><strong>${{tk}}</strong><br><span class="hint">${{d.origen||'USA'}} · ${{d.mercado||'SIC'}}</span></td>`
      +`<td class="num">${{parseFloat(d.titulos.toFixed(6))}}</td>`
      +`<td class="num">$${{cto.toLocaleString('es-MX',{{minimumFractionDigits:2,maximumFractionDigits:2}})}}</td>`
      +`<td class="num">—</td>`
      +`<td class="num">$${{(cto*d.titulos).toLocaleString('es-MX',{{minimumFractionDigits:2,maximumFractionDigits:2}})}}</td>`
      +`<td class="num">—</td><td class="num">—</td>`
      +`<td><span class="badge b-none">Sin análisis</span></td>`
      +`<td><span style="font-size:10px;color:var(--muted)">Actualiza ↺ para ver recomendación</span></td>`
      +`<td>—</td><td>—</td>`;
    tbody.appendChild(newRow);
    const detRow=document.createElement('tr');
    detRow.className='detail'; detRow.id=rid;
    detRow.innerHTML='<td colspan="11" style="padding:0"><div class="detail-panel">'
      +'<p class="hint">Presiona ↺ Actualizar para ver el análisis completo de esta posición.</p>'
      +'</div></td>';
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

// ── Sorting de columnas del scanner ─────────────────────
let _scanSortCol=-1, _scanSortAsc=true;
function sortScanner(colIdx, tipo){{
  _scanSortAsc = (_scanSortCol===colIdx) ? !_scanSortAsc : true;
  _scanSortCol = colIdx;
  // Actualizar iconos
  [0,2,4,5,10].forEach(i=>{{
    const el=document.getElementById('srt'+i);
    if(el) el.textContent = i===colIdx ? (_scanSortAsc?'↑':'↓') : '⇅';
  }});
  const tbody = document.getElementById('scan_tbody');
  // Recopilar pares [datarow, detailrow]
  const pares = [];
  let rows = Array.from(tbody.querySelectorAll('tr.datarow'));
  rows.forEach(tr=>{{
    const detail = tr.nextElementSibling;
    pares.push({{data: tr, detail: (detail&&detail.classList.contains('detail'))?detail:null}});
  }});
  pares.sort((a,b)=>{{
    const tdA = a.data.querySelectorAll('td')[colIdx];
    const tdB = b.data.querySelectorAll('td')[colIdx];
    const rawA = tdA ? (tdA.querySelector('strong')?.textContent || tdA.textContent) : '';
    const rawB = tdB ? (tdB.querySelector('strong')?.textContent || tdB.textContent) : '';
    let valA, valB;
    if(tipo==='num'){{
      valA = parseFloat(rawA.replace(/[^0-9.-]/g,'')) || 0;
      valB = parseFloat(rawB.replace(/[^0-9.-]/g,'')) || 0;
    }} else {{
      valA = rawA.trim().toLowerCase();
      valB = rawB.trim().toLowerCase();
    }}
    if(valA < valB) return _scanSortAsc ? -1 : 1;
    if(valA > valB) return _scanSortAsc ? 1 : -1;
    return 0;
  }});
  // Reordenar DOM
  pares.forEach(p=>{{
    tbody.appendChild(p.data);
    if(p.detail) tbody.appendChild(p.detail);
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
    const total=(n*p).toLocaleString('es-MX',{{minimumFractionDigits:2,maximumFractionDigits:2}});
    const color=tipo==='COMPRA'?'#16a34a':'#dc2626';
    document.getElementById('f_preview_txt').innerHTML='<strong style="color:'+color+'">'+tipo+'</strong> '+n+' tít de <strong>'+t+'</strong> a <strong>$'+p.toFixed(2)+' MXN</strong> = <strong>$'+total+' MXN</strong>';
    document.getElementById('f_preview').style.display='block';
  }}
}}
function registrarOp(){{
  const razon = (document.getElementById('f_razon').value||'').trim();
  const score = parseInt(document.getElementById('f_score').value||'0');
  const setup = document.getElementById('f_setup').value;
  const rr_esp = parseFloat(document.getElementById('f_rr').value||'0');

  // Validación del diario — obligatorio
  if(!razon || razon.length < 20) {{
    document.getElementById('f_msg').innerHTML='<span style="color:var(--red)">⚠ Escribe por qué entras (mínimo 20 caracteres) — el diario es obligatorio para mejorar</span>';
    document.getElementById('f_razon').focus();
    return;
  }}
  if(!setup) {{
    document.getElementById('f_msg').innerHTML='<span style="color:var(--red)">⚠ Selecciona el tipo de setup</span>';
    return;
  }}

  const op={{fecha:document.getElementById('f_fecha').value,
    ticker:(document.getElementById('f_ticker').value||'').toUpperCase().trim(),
    tipo:document.getElementById('f_tipo').value,
    titulos:parseFloat(document.getElementById('f_titulos').value),
    precio_mxn:parseFloat(document.getElementById('f_precio').value),
    origen:document.getElementById('f_origen').value,
    mercado:document.getElementById('f_mercado').value,
    notas:setup + (razon ? ' | ' + razon.substring(0,80) : ''),
    tc_dia:TC,
    score_entrada: score,
    total_criterios: 13,
    razon_entrada: razon,
    setup_tipo: setup,
    rr_esperado: rr_esp
  }};
  if(!op.ticker||!op.titulos||!op.precio_mxn||!op.fecha){{
    document.getElementById('f_msg').innerHTML='<span style="color:var(--red)">⚠ Completa todos los campos</span>';return;
  }}
  op.total_mxn=op.titulos*op.precio_mxn;
  let ops=JSON.parse(localStorage.getItem('finbit_ops')||'[]');
  ops.unshift(op);
  localStorage.setItem('finbit_ops',JSON.stringify(ops));
  renderOpsTable(ops);
  actualizarTablaPortafolio();
  document.getElementById('f_msg').innerHTML='<span style="color:var(--green)">✅ Guardado — portafolio y diario actualizados</span>';
  setTimeout(()=>document.getElementById('f_msg').innerHTML='',5000);
  ['f_ticker','f_titulos','f_precio','f_razon','f_score','f_rr'].forEach(id=>{{
    const el=document.getElementById(id); if(el) el.value='';
  }});
  document.getElementById('f_setup').value='';
  document.getElementById('f_preview').style.display='none';
  // ── Sincronizar todas las ops al servidor para persistencia ──
  _sincronizarOpsServidor(ops);
}}

function _sincronizarOpsServidor(ops) {{
  if (!ops || !ops.length) return;
  const opsCompletas = ops.map(op => ({{
    ...op,
    total_mxn: op.total_mxn || (op.titulos * op.precio_mxn),
    tc_dia:    op.tc_dia    || TC,
    origen:    op.origen    || 'USA',
    mercado:   op.mercado   || 'SIC',
  }}));
  fetch('/api/operaciones/import', {{
    method: 'POST',
    headers: {{'Content-Type': 'application/json'}},
    body: JSON.stringify(opsCompletas)
  }}).catch(()=>{{}}); // silencioso — no interrumpir al usuario
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
    const total=(op.total_mxn||0).toLocaleString('es-MX',{{minimumFractionDigits:2,maximumFractionDigits:2}});
    const precio=(op.precio_mxn||0).toLocaleString('es-MX',{{minimumFractionDigits:2,maximumFractionDigits:2}});
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
  _sincronizarOpsServidor(ops);
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
      _sincronizarOpsServidor(ops);
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
  fetch('/api/operaciones/delete',{{
    method:'POST',
    headers:{{'Content-Type':'application/json'}},
    body:JSON.stringify({{id:id}})
  }}).then(r=>r.json()).then(d=>{{
    if(d.status==='ok'){{
      // Eliminar la fila directamente sin recargar la página
      const btn = document.querySelector('button[onclick*="delOp('+id+',"]');
      if(btn){{
        const row = btn.closest('tr');
        if(row) row.remove();
      }}
    }} else {{
      alert('Error al borrar: '+d.error);
    }}
  }}).catch(e=>alert('Error de red: '+e));
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
  localStorage.setItem('cfg_capital',cap);
  localStorage.setItem('cfg_riesgo',rie);
  localStorage.setItem('cfg_rr',rr);
  // Guardar en el servidor para que persista en cada actualización
  fetch('/api/config', {{
    method: 'POST',
    headers: {{'Content-Type': 'application/json'}},
    body: JSON.stringify({{capital:cap, riesgo:rie, rr_min:rr}})
  }})
  .then(r => r.json())
  .then(d => {{
    if(d.status === 'ok') {{
      const msg = document.createElement('span');
      msg.style.cssText = 'margin-left:10px;font-size:12px;color:var(--green)';
      msg.textContent = '✅ Guardado';
      const btn = document.querySelector('.cfg-btn');
      btn.parentNode.appendChild(msg);
      setTimeout(() => msg.remove(), 3000);
    }}
  }})
  .catch(() => alert('Error al guardar config'));
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

// ── Init ─────────────────────────────────────────────────
window.addEventListener('load',()=>{{
  cargarTickersPersonalizados();
  const ops=JSON.parse(localStorage.getItem('finbit_ops')||'[]');
  if(ops.length) {{ renderOpsTable(ops); _sincronizarOpsServidor(ops); }}
  actualizarTablaPortafolio();
  const cap=localStorage.getItem('cfg_capital');
  const rie=localStorage.getItem('cfg_riesgo');
  const rr=localStorage.getItem('cfg_rr');
  if(cap) document.getElementById('cfg_capital').value=cap;
  if(rie) document.getElementById('cfg_riesgo').value=(parseFloat(rie)*100).toFixed(0);
  if(rr) document.getElementById('cfg_rr').value=rr;
}});

function sincronizarOpsAlServidor() {{
  const ops = JSON.parse(localStorage.getItem('finbit_ops') || '[]');
  if (!ops.length) return;
  // Agregar total_mxn si no existe
  const opsCompletas = ops.map(op => ({{
    ...op,
    total_mxn: op.total_mxn || (op.titulos * op.precio_mxn),
    tc_dia: op.tc_dia || 17.5,
    origen: op.origen || 'USA',
    mercado: op.mercado || 'SIC',
  }}));
  fetch('/api/operaciones/import', {{
    method: 'POST',
    headers: {{'Content-Type': 'application/json'}},
    body: JSON.stringify(opsCompletas)
  }})
  .then(r => r.json())
  .then(d => {{
    if (d.status === 'ok') {{
      console.log('[finbit] Ops sincronizadas al servidor:', d.importadas);
    }}
  }})
  .catch(e => console.log('[finbit] Sin conexión al servidor para sincronizar ops'));
}}

// ── Exportar Scanner a CSV/Excel (via servidor) ──────────
function exportarScannerCSV() {{
  window.location.href = '/api/exportar/scanner';
}}
function _exportarScannerCSV_unused() {{
  const blob = new Blob([""], {{type: "text/csv;charset=utf-8"}});
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = "finbit_scanner_" + new Date().toISOString().slice(0,10) + ".csv";
  a.click();
  URL.revokeObjectURL(url);
}}

// ── Exportar Excel ────────────────────────────────────────
function exportarExcel() {{
  fetch('/api/exportar/excel')
    .then(r => r.blob())
    .then(blob => {{
      const url = URL.createObjectURL(blob);
      const a = document.createElement('a');
      a.href = url;
      a.download = 'finbit_rendimiento.csv';
      a.click();
      URL.revokeObjectURL(url);
    }})
    .catch(() => alert('Error al exportar'));
}}

// ── Exportar PDF (print) ──────────────────────────────────
function exportarPDF() {{
  showTab('rendimiento', document.querySelector('.nb[onclick*=rendimiento]'));
  setTimeout(() => window.print(), 300);
}}

// ── Filtrar Top por precio máximo — dinámico ─────────────
function filtrarTopPorPrecio(tabId, valorStr) {{
  const max = parseFloat(valorStr);
  const tab = document.getElementById('tab-' + tabId);
  if (!tab) return;

  const cards = Array.from(tab.querySelectorAll('.top-card'));
  let visibles = 0;

  cards.forEach((card, idx) => {{
    const precio = parseFloat(card.getAttribute('data-precio') || '0');
    const cumple = !valorStr || isNaN(max) || precio <= max;

    if (cumple && visibles < 5) {{
      card.style.display = '';
      // Actualizar número de posición visualmente
      const posEl = card.querySelector('.top-pos');
      if (posEl) posEl.textContent = (visibles + 1) + 'º';
      visibles++;
    }} else {{
      card.style.display = 'none';
    }}
  }});

  // Mensaje si no hay resultados
  let msg = document.getElementById('top-empty-' + tabId);
  if (!msg) {{
    msg = document.createElement('div');
    msg.id = 'top-empty-' + tabId;
    msg.style.cssText = 'padding:20px;text-align:center;color:var(--muted);font-size:13px;display:none';
    msg.innerHTML = '😅 Sin acciones en ese rango de precio. Prueba un monto mayor.';
    tab.querySelector('.top-grid') && tab.querySelector('.top-grid').after(msg);
  }}
  msg.style.display = (visibles === 0 && valorStr) ? 'block' : 'none';
}}

// ── Watchlist toggle ──────────────────────────────────────
function wlToggle(btn, ticker) {{
  if(!ticker) {{ ticker = btn; btn = null; }}
  fetch('/api/watchlist/agregar', {{
    method: 'POST',
    headers: {{'Content-Type': 'application/json'}},
    body: JSON.stringify({{ticker: ticker}})
  }})
  .then(r => r.json())
  .then(d => {{
    const b = btn || document.getElementById('wl-btn-' + ticker);
    if (b) {{
      b.textContent = '✅ Agregado';
      b.style.background = '#dcfce7';
      b.style.color = '#15803d';
      b.style.borderColor = '#15803d';
    }}
    setTimeout(() => {{ showTab('wl', document.querySelector('.nb[onclick*=wl]')); }}, 800);
  }})
  .catch(e => console.log('WL error:', e));
}}

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

      let html = `<div style="margin-bottom:6px"><span class="hint" style="font-size:11px">${{total}} ticker(s) activos — clic en × para quitar del scanner</span></div>`;

      // Todos los activos: defaults en gris, custom en rojo — todos con ×
      const todos = [...defaults, ...custom];
      html += todos.map(t => {{
        const esCustom = custom.includes(t);
        const col = esCustom ? 'var(--red)' : 'var(--text)';
        const brd = esCustom ? 'border-color:var(--red-b)' : '';
        return `<span class="ticker-chip" style="${{brd}};margin:2px">` +
          `<strong style="color:${{col}}">${{t}}</strong>` +
          `<button onclick="quitarTickerScanner('${{t}}')" title="Quitar ${{t}}" ` +
          `style="border:none;background:none;color:var(--red);cursor:pointer;font-size:14px;padding:0 0 0 4px;line-height:1">×</button></span>`;
      }}).join('');

      // Eliminados: tachados con botón ↩ para restaurar
      if (eliminados.length) {{
        html += `<div style="margin-top:10px;padding-top:8px;border-top:1px solid var(--brd)">`;
        html += `<span class="hint" style="font-size:10px">Quitados (clic ↩ para restaurar):</span><br style="margin-bottom:4px">`;
        html += eliminados.map(t =>
          `<span class="ticker-chip" style="opacity:.5;margin:2px">` +
          `<s style="font-size:11px;color:var(--muted)">${{t}}</s>` +
          `<button onclick="restaurarTickerScanner('${{t}}')" title="Restaurar ${{t}}" ` +
          `style="border:none;background:none;color:var(--green);cursor:pointer;font-size:13px;padding:0 0 0 4px">↩</button></span>`
        ).join('');
        html += `</div>`;
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
</script>
<script src="/static/ia.js"></script></body></html>"""

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

_CONFIG_FILE = os.path.join(_BASE_DIR, "finbit_config.json")

def cargar_config() -> dict:
    defaults={"capital":CAPITAL_TOTAL,"riesgo":RIESGO_POR_TRADE,"rr_min":RR_MINIMO}
    if not os.path.exists(_CONFIG_FILE): return defaults
    try:
        with open(_CONFIG_FILE) as f: return {**defaults,**json.load(f)}
    except Exception: return defaults

# ═══════════════════════════════════════════════════════════
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
        print("[MACRO] Obteniendo VIX y SPY...")
        vix = get_vix()
        spy = get_spy_macro()
        regimen = regimen_mercado(vix, spy)
        print(f"  VIX={vix:.1f} | SPY EMA200={'✅' if spy.get('sobre_ema200') else '❌'} | {regimen['label']}")

        scan_data  = correr_scanner(tc, capital, riesgo_pct, rr_min, tickers_extra,
                                     vix=vix, spy=spy)
        port_data  = analizar_portafolio(tc, capital, riesgo_pct, rr_min)
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
# ═══════════════════════════════════════════════════════════
app = Flask(__name__)
_dash_html: str = ""
_dash_lock  = threading.Lock()
_scan_resultados: list = []  # últimos resultados del scanner para alertas Telegram
_refresh_in_progress = False
_build_start_time: float = 0.0   # para mostrar tiempo transcurrido
_build_error: str = ""           # captura último error de build

# ── Corre siempre al arrancar (python finbit.py Y gunicorn) ──
db_restore_from_github()
init_db()
init_score_history()
threading.Thread(target=_loop_backup_github, daemon=True).start()

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

  let buildTriggered = false;

  function triggerBuild(){
    if(buildTriggered) return;
    buildTriggered = true;
    fetch('/refresh', {method:'POST'})
      .then(r=>r.json())
      .then(d=>{ console.log('[finbit] build iniciado:', d.status); })
      .catch(e=>{ console.warn('[finbit] no se pudo iniciar build:', e); });
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
          // Si no hay build corriendo, dispararlo automáticamente
          if(!d.building && !buildTriggered){
            triggerBuild();
          }
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
        "ready":    ready,
        "building": _refresh_in_progress,
        "stage":    _build_stage,
        "msg":      stage_msg,
        "elapsed":  elapsed,
        "error":    _build_error if _build_stage == "error" else "",
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

        if os.path.exists(os.path.join(_BASE_DIR,"finbit_ops.json")):
            importar_ops_json(os.path.join(_BASE_DIR,"finbit_ops.json"), tc)
        procesar_borrados()

        tickers_extra = {}
        if os.path.exists(os.path.join(_BASE_DIR,"finbit_tickers.json")):
            try:
                with open(os.path.join(_BASE_DIR,"finbit_tickers.json")) as f:
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
                    scan_data = correr_scanner(tc, capital, riesgo_pct, rr_min, tickers_extra,
                                               vix=vix, spy=spy)
                    print(f"[build] Scanner: {len(scan_data)} tickers procesados")
                    global _scan_resultados
                    _scan_resultados = scan_data or []
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
                    port_data = analizar_portafolio(tc, capital, riesgo_pct, rr_min)
                except Exception as e:
                    print(f"[build] Portafolio error (continuando): {e}")
                    port_data = []
            _build_stage = "port_ok"

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
        if not _dash_html and os.path.exists(OUTPUT_FILE):
            try:
                with open(OUTPUT_FILE, "r", encoding="utf-8") as f:
                    cached = f.read()
                if len(cached) > 500 and "removeProperty('display')" in cached:
                    with _dash_lock:
                        _dash_html = cached
                    _build_stage = "html_ok"
                    print(f"[build] ⚠️  Build falló — sirviendo dashboard anterior desde disco")
            except Exception:
                pass
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

print("[server] ⏸️  Build automático desactivado — presiona ↺ Actualizar para cargar datos")

# ── Actualizar dashboard (botón en el HTML) ───────────────
@app.route("/update")
@app.route("/refresh", methods=["GET", "POST"])
def refresh():
    global _dash_html, _refresh_in_progress
    if _refresh_in_progress:
        return jsonify({"status": "busy", "msg": "Ya hay una actualización en curso"}), 202
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
        with open(_CONFIG_FILE, "w") as f:
            json.dump(cfg, f, indent=2)
    except Exception as e:
        print(f"[server] set_tf error: {e}")
    return jsonify({"status": "ok", "tf": tf_code})

# ── API: tickers del scanner ──────────────────────────────
@app.route("/api/tickers")
def api_tickers():
    try:
        activos = get_all_scanner_tickers()
        defaults_activos = [t for t in activos if t in SCANNER_TICKERS]
        custom_activos   = [t for t in activos if t not in SCANNER_TICKERS]
        con = sqlite3.connect(DB_FILE)
        con.row_factory = sqlite3.Row
        try:
            del_rows = con.execute("SELECT ticker FROM tickers WHERE activo=0").fetchall()
        except Exception:
            del_rows = []
        con.close()
        eliminados = [r["ticker"] for r in del_rows if r["ticker"] in SCANNER_TICKERS]
        return jsonify({
            "defaults":  defaults_activos,
            "custom":    custom_activos,
            "eliminados": eliminados,
            "total":     len(activos)
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
        import re as _re
        if not _re.match(r'^[A-Z0-9.]{1,15}$', ticker):
            return jsonify({"status": "error", "error": f"Ticker inválido: {ticker}"}), 400
        add_ticker_db(ticker, exchange, origen)
        threading.Thread(target=db_backup_to_github, daemon=True).start()
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
        threading.Thread(target=db_backup_to_github, daemon=True).start()
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

@app.route("/api/operaciones/delete", methods=["POST"])
def api_ops_delete():
    """Borra una operación por ID y reconstruye el portafolio."""
    global _dash_html
    try:
        data = flask_req.get_json(force=True) or {}
        oid  = data.get("id")
        if not oid:
            return jsonify({"status": "error", "error": "id requerido"}), 400
        con = sqlite3.connect(DB_FILE)
        con.execute("DELETE FROM operaciones WHERE id=?", (int(oid),))
        con.commit()
        con.close()
        ops = get_operaciones()
        con2 = sqlite3.connect(DB_FILE)
        con2.execute("DELETE FROM portafolio")
        con2.commit()
        con2.close()
        tc = get_tipo_cambio(API_KEY)
        for op in sorted(ops, key=lambda x: x.get("fecha","")):
            try:
                upsert_portafolio_from_op({
                    "ticker":     op.get("ticker","").upper(),
                    "tipo":       op.get("tipo"),
                    "titulos":    op.get("titulos",0),
                    "precio_mxn": op.get("precio_mxn",0),
                    "origen":     op.get("origen","USA"),
                    "mercado":    op.get("mercado","SIC"),
                })
            except Exception: pass
        threading.Thread(target=db_backup_to_github, daemon=True).start()
        _dash_html = ""
        return jsonify({"status": "ok", "id": oid})
    except Exception as e:
        return jsonify({"status": "error", "error": str(e)}), 500

@app.route("/api/operaciones/import", methods=["POST"])
def api_ops_import():
    """
    Sincroniza las operaciones del localStorage al servidor.
    Reemplaza TODA la tabla de operaciones y reconstruye el portafolio desde cero.
    El localStorage es la fuente de verdad — el servidor solo es el motor de análisis.
    """
    global _dash_html
    try:
        ops = flask_req.get_json(force=True) or []
        tc  = get_tipo_cambio(API_KEY)
        con = sqlite3.connect(DB_FILE)

        con.execute("DELETE FROM operaciones")
        con.execute("DELETE FROM portafolio")
        con.commit()

        for op in sorted(ops, key=lambda x: x.get("fecha", "")):
            try:
                total_mxn = op.get("total_mxn") or (op.get("titulos",0) * op.get("precio_mxn",0))
                cur = con.execute(
                    "INSERT INTO operaciones (fecha,ticker,tipo,titulos,precio_mxn,total_mxn,tc_dia,origen,mercado,notas) "
                    "VALUES(?,?,?,?,?,?,?,?,?,?)",
                    (op.get("fecha"), op.get("ticker","").upper(), op.get("tipo"),
                     op.get("titulos"), op.get("precio_mxn"), total_mxn,
                     op.get("tc_dia", tc), op.get("origen","USA"),
                     op.get("mercado","SIC"), op.get("notas",""))
                )
                op_id = cur.lastrowid
                con.commit()
                razon = op.get("razon_entrada", "").strip()
                if razon:
                    con.execute(
                        """INSERT OR IGNORE INTO diario_trading
                           (ticker, fecha, tipo, precio_mxn, titulos, score_entrada,
                            total_criterios, razon_entrada, setup_tipo, rr_esperado, resultado, op_id)
                           VALUES (?,?,?,?,?,?,?,?,?,?,'abierta',?)""",
                        (op.get("ticker","").upper(), op.get("fecha",""),
                         op.get("tipo",""), op.get("precio_mxn",0), op.get("titulos",0),
                         op.get("score_entrada", 0), op.get("total_criterios", 13),
                         razon, op.get("setup_tipo",""), op.get("rr_esperado", 0), op_id)
                    )
                    con.commit()
                upsert_portafolio_from_op({
                    "ticker":     op.get("ticker","").upper(),
                    "tipo":       op.get("tipo"),
                    "titulos":    op.get("titulos",0),
                    "precio_mxn": op.get("precio_mxn",0),
                    "origen":     op.get("origen","USA"),
                    "mercado":    op.get("mercado","SIC"),
                })
            except Exception as e:
                print(f"  [import] op skip: {e}")

        con.close()
        return jsonify({"status": "ok", "importadas": len(ops)})
    except Exception as e:
        return jsonify({"status": "error", "error": str(e)}), 500

# ── API: config ───────────────────────────────────────────
@app.route("/api/config", methods=["POST"])
def api_config():
    global _dash_html
    try:
        data = flask_req.get_json(force=True) or {}
        with open(_CONFIG_FILE, "w") as f:
            json.dump(data, f, indent=2)
        _dash_html = ""
        return jsonify({"status": "ok"})
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
        "twelvedata_k1": {"nota": "prueba desactivada — ahorra créditos"},
        "twelvedata_k2": {"nota": "prueba desactivada — ahorra créditos"},
    }

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
# ═══════════════════════════════════════════════════════════
@app.route("/api/diario/cerrar", methods=["POST"])
def api_diario_cerrar():
    data         = flask_req.get_json(silent=True) or {}
    diario_id    = data.get("id", 0)
    precio_cierre= data.get("precio_cierre_mxn", 0)
    aprendizaje  = data.get("aprendizaje", "")
    if not diario_id or not precio_cierre:
        return jsonify({"ok": False, "error": "id y precio_cierre_mxn requeridos"}), 400
    ok = cerrar_entrada_diario(diario_id, precio_cierre, aprendizaje)
    return jsonify({"ok": ok})

@app.route("/api/diario")
def api_diario_lista():
    return jsonify(get_diario(limite=100))

@app.route("/api/exportar/scanner")
def api_exportar_scanner():
    """Exporta el último scanner como CSV descargable."""
    import io, csv
    global _dash_html
    try:
        tc  = get_tipo_cambio(API_KEY)
        cfg = cargar_config()
        con = sqlite3.connect(DB_FILE)
        con.row_factory = sqlite3.Row
        tickers = {r["ticker"]: (r["exchange"] or "", r.get("origen","USA"))
                   for r in con.execute("SELECT ticker,exchange,origen FROM tickers WHERE activo=1").fetchall()}
        con.close()
        todos = {**SCANNER_TICKERS, **tickers}

        output = io.StringIO()
        writer = csv.writer(output)
        writer.writerow(["Ticker","Precio MXN","Entrada EMA9","Stop","Objetivo","R:R","RSI","MACD","EMA200","Score","Estado"])

        for ticker in todos:
            try:
                sym, exch = todos[ticker] if isinstance(todos[ticker], tuple) else (ticker, "")
                vals = _get_cached(sym or ticker, "1day", exch)
                if not vals: continue
                closes = [float(x["close"]) for x in vals]
                highs  = [float(x.get("high", x["close"])) for x in vals]
                lows   = [float(x.get("low",  x["close"])) for x in vals]
                opens  = [float(x.get("open", x["close"])) for x in vals]
                tf = analizar_tf(closes, [float(x.get("volume",0)) for x in vals],
                                 "1D", cfg["capital"], cfg["riesgo"], cfg["rr_min"],
                                 tc=tc, highs=highs, lows=lows, opens=opens)
                if not tf.get("valido"): continue
                precio = tf["precio"] * tc
                writer.writerow([
                    ticker,
                    f"{precio:.2f}",
                    f"{tf['ema9']*tc:.2f}",
                    f"{tf['stop']*tc:.2f}",
                    f"{tf['objetivo']*tc:.2f}",
                    f"{tf['rr']:.1f}",
                    f"{tf['rsi']:.0f}",
                    "Alcista" if tf["macd_alcista"] else "Bajista",
                    "Sobre" if tf["precio"] > tf["ema200"] else "Bajo",
                    f"{tf['score']}/{tf['total_criterios']}",
                    tf["senal"],
                ])
            except Exception:
                pass

        output.seek(0)
        from flask import Response
        return Response(
            "\ufeff" + output.getvalue(),
            mimetype="text/csv",
            headers={"Content-Disposition": f"attachment; filename=finbit_scanner_{_fecha_hoy_cdmx()}.csv"}
        )
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route("/api/exportar/excel")
def api_exportar_excel():
    """Exporta historial de P&L y diario como CSV."""
    import io, csv
    output = io.StringIO()
    writer = csv.writer(output)

    writer.writerow(["=== RENDIMIENTO HISTÓRICO ==="])
    writer.writerow(["Fecha", "Capital MXN", "P&L día MXN", "P&L acum %", "SPY precio"])
    for h in get_pnl_historico(dias=365):
        writer.writerow([h["fecha"], h["capital"], h["pnl_dia_mxn"], h["pnl_acum_pct"], h.get("spy_precio","")])

    writer.writerow([])
    writer.writerow(["=== DIARIO DE TRADING ==="])
    writer.writerow(["Fecha","Ticker","Tipo","Precio MXN","Títulos","Score","Setup","R:R esp","Resultado","P&L MXN","P&L %","Por qué entré","Aprendizaje"])
    for e in get_diario(limite=500):
        writer.writerow([
            e["fecha"], e["ticker"], e["tipo"], e["precio_mxn"], e["titulos"],
            f'{e["score_entrada"]}/{e["total_criterios"]}', e["setup_tipo"],
            e["rr_esperado"], e["resultado"], e["pnl_mxn"], e["pnl_pct"],
            e["razon_entrada"], e.get("aprendizaje","")
        ])

    output.seek(0)
    from flask import Response
    return Response(
        output.getvalue(),
        mimetype="text/csv",
        headers={"Content-Disposition": "attachment; filename=finbit_rendimiento.csv"}
    )

# ═══════════════════════════════════════════════════════════
# ═══════════════════════════════════════════════════════════
@app.route("/api/watchlist/agregar", methods=["POST"])
def api_wl_agregar():
    data   = flask_req.get_json(silent=True) or {}
    ticker = data.get("ticker", "").upper().strip()
    notas  = data.get("notas", "")
    if not ticker:
        return jsonify({"ok": False, "error": "ticker requerido"}), 400
    ok = agregar_watchlist(ticker, notas)
    return jsonify({"ok": ok, "ticker": ticker})

@app.route("/api/watchlist/quitar/<ticker>", methods=["POST"])
def api_wl_quitar(ticker):
    ok = quitar_watchlist(ticker)
    return jsonify({"ok": ok, "ticker": ticker.upper()})

@app.route("/api/watchlist")
def api_wl_lista():
    return jsonify(get_watchlist())

@app.route("/api/port/json")
def api_port_json():
    posiciones = get_portafolio()
    result = [{
        "ticker":            p["ticker"],
        "titulos":           p["titulos"],
        "cto_prom_mxn":      p["cto_prom_mxn"],
        "origen":            p.get("origen", "USA"),
        "mercado":           p.get("mercado", "SIC"),
        "precio_actual_mxn": None,
        "pl_mxn":            0,
        "pl_pct":            0,
        "activo":            p.get("activo", 1),
    } for p in posiciones]
    return jsonify(result)


_IA_JS = r'''
// Tab IA — Finbit Pro v2
(function() {

  var ESTADO_BADGE = {
    'RUPTURA':           { emoji: '🚀', color: '#16a34a', bg: '#f0fdf4', border: '#86efac' },
    'PRE-BREAKOUT':      { emoji: '⚡', color: '#b45309', bg: '#fffbeb', border: '#fcd34d' },
    'TENDENCIA_ALCISTA': { emoji: '📈', color: '#2563eb', bg: '#eff6ff', border: '#93c5fd' },
    'ACUMULACION':       { emoji: '🟡', color: '#92400e', bg: '#fef3c7', border: '#fcd34d' },
    'LISTO_ENTRAR':      { emoji: '✅', color: '#16a34a', bg: '#f0fdf4', border: '#86efac' },
    'LATERAL':           { emoji: '↔️',  color: '#6b7280', bg: '#f9fafb', border: '#d1d5db' },
    'BAJISTA':           { emoji: '📉', color: '#dc2626', bg: '#fef2f2', border: '#fca5a5' },
    'BLOQUEADO':         { emoji: '🔒', color: '#9ca3af', bg: '#f3f4f6', border: '#e5e7eb' },
  };

  function getBadge(estado) {
    var s = (estado || '').toUpperCase().replace(/ /g,'_').replace(/\//g,'_');
    for (var k in ESTADO_BADGE) {
      if (s.indexOf(k) !== -1) return ESTADO_BADGE[k];
    }
    return { emoji: '⬜', color: '#6b7280', bg: '#f9fafb', border: '#e5e7eb' };
  }

  function renderAnalisis(texto) {
    var SECS = {
      'SITUACION':        { icon: '📊', color: '#2563eb' },
      'ENTRADA':          { icon: '🎯', color: '#16a34a' },
      'STOP Y OBJETIVO':  { icon: '📏', color: '#b45309' },
      'RIESGOS':          { icon: '⚠️',  color: '#dc2626' },
      'VEREDICTO':        { icon: '🏁', color: '#7c3aed' },
    };
    var lines = texto.split('\n').filter(function(l){ return l.trim(); });
    var html = '';
    var currentColor = null;

    lines.forEach(function(line) {
      var matched = false;
      var up = line.toUpperCase();
      for (var sec in SECS) {
        if (up.indexOf(sec + ':') === 0) {
          var s = SECS[sec];
          currentColor = s.color;
          var rest = line.substring(line.indexOf(':') + 1).trim();
          html += '<div style="margin-top:12px;padding:10px 14px;border-radius:8px;background:' + s.color + '15;border-left:3px solid ' + s.color + '">';
          html += '<div style="font-size:10px;font-weight:700;color:' + s.color + ';text-transform:uppercase;letter-spacing:.06em;margin-bottom:5px">' + s.icon + ' ' + sec + '</div>';
          if (rest) html += '<div style="font-size:13px;line-height:1.65;color:#1f2937">' + rest + '</div>';
          html += '</div>';
          matched = true;
          break;
        }
      }
      if (!matched && html) {
        // Línea de continuación — agregarla dentro del último bloque
        var lastClose = html.lastIndexOf('</div></div>');
        if (lastClose !== -1) {
          html = html.substring(0, lastClose) + '<br><span style="font-size:13px;line-height:1.65;color:#1f2937">' + line + '</span></div></div>';
        } else {
          html += '<p style="margin:4px 0 0;font-size:13px;line-height:1.65;color:#374151">' + line + '</p>';
        }
      }
    });
    return html || '<p style="color:#9ca3af;font-style:italic">Sin análisis recibido.</p>';
  }

  function _iaCard(ticker, estado) {
    var b = getBadge(estado);
    return '<div id="ia-card-' + ticker + '" style="background:var(--surface);border:1px solid ' + b.border + ';border-radius:14px;padding:18px 20px">'
      + '<div style="display:flex;align-items:center;justify-content:space-between;margin-bottom:6px">'
      + '<div style="display:flex;align-items:center;gap:10px">'
      + '<strong style="font-size:16px">' + ticker + '</strong>'
      + '<span style="font-size:11px;font-weight:600;padding:3px 9px;border-radius:20px;background:' + b.bg + ';color:' + b.color + ';border:1px solid ' + b.border + '">' + b.emoji + ' ' + estado + '</span>'
      + '</div>'
      + '<button onclick="window._ia_analizar(\'' + ticker + '\')" id="ia-btn-' + ticker + '" style="font-size:12px;padding:6px 14px;border-radius:8px;border:1px solid #7c3aed;background:var(--surface2);color:#7c3aed;cursor:pointer;font-weight:600">🧠 Analizar</button>'
      + '</div>'
      + '<div id="ia-txt-' + ticker + '" style="margin-top:6px;color:var(--muted)">—</div>'
      + '</div>';
  }

  window._ia_analizar = function(ticker) {
    var btn = document.getElementById('ia-btn-' + ticker);
    var txt = document.getElementById('ia-txt-' + ticker);
    if (!btn || !txt) return;
    btn.disabled = true;
    btn.textContent = '⏳ Analizando...';
    btn.style.opacity = '0.6';
    txt.innerHTML = '<div style="padding:12px 0;color:var(--muted);font-size:13px">🧠 Generando análisis con IA...</div>';
    fetch('/api/ia/' + ticker)
      .then(function(r){ return r.json(); })
      .then(function(d){
        btn.disabled = false;
        btn.style.opacity = '1';
        if (d.ok) {
          btn.textContent = '✅ Listo';
          btn.style.color = '#16a34a';
          btn.style.borderColor = '#16a34a';
          txt.innerHTML = renderAnalisis(d.analisis);
        } else {
          btn.textContent = '❌ Error';
          btn.style.color = '#dc2626';
          txt.innerHTML = '<p style="color:#dc2626;font-size:13px">⚠ ' + (d.error||'Sin respuesta') + '</p>';
        }
      })
      .catch(function(){
        btn.disabled = false;
        btn.style.opacity = '1';
        btn.textContent = '🧠 Analizar';
        txt.innerHTML = '<p style="color:#dc2626;font-size:13px">⚠ Error de conexión.</p>';
      });
  };

  function initIaTab() {
    var lista = document.getElementById('ia-lista');
    if (!lista) return;
    lista.innerHTML = '<div style="padding:40px;text-align:center;color:var(--muted)">🧠 Cargando tickers...</div>';
    fetch('/api/scan/nombres')
      .then(function(r){ return r.json(); })
      .then(function(tickers){
        if (!tickers || !tickers.length) {
          lista.innerHTML = '<div style="padding:40px;text-align:center;color:var(--muted)"><div style="font-size:32px;margin-bottom:12px">📭</div><strong>Sin datos del scanner</strong><br><span style="font-size:13px">Da clic en <strong>Actualizar</strong> primero.</span><br><br><button onclick="initIaTab()" style="padding:8px 18px;border-radius:8px;border:1px solid var(--brd2);background:var(--surface2);cursor:pointer;font-size:13px">↺ Reintentar</button></div>';
          return;
        }
        lista.innerHTML = tickers.map(function(t){ return _iaCard(t.nombre, t.estado); }).join('');
      })
      .catch(function(){
        lista.innerHTML = '<div style="padding:40px;text-align:center;color:#dc2626">⚠ Error cargando tickers.</div>';
      });
  }

  window.initIaTab = initIaTab;

  window.analizarTodos = function() {
    var btn = document.getElementById('btn-analizar-todos');
    if (btn) { btn.disabled = true; btn.textContent = '⏳ Analizando...'; }
    fetch('/api/scan/nombres')
      .then(function(r){ return r.json(); })
      .then(function(tickers){
        if (!tickers || !tickers.length) { if (btn) btn.disabled = false; return; }
        initIaTab();
        var i = 0;
        function siguiente() {
          if (i >= tickers.length) {
            if (btn) { btn.disabled = false; btn.textContent = '🧠 Analizar todos'; }
            return;
          }
          window._ia_analizar(tickers[i].nombre);
          i++;
          setTimeout(siguiente, 4500);
        }
        setTimeout(siguiente, 800);
      })
      .catch(function(){ if (btn) btn.disabled = false; });
  };

  var tab = document.getElementById('tab-ia');
  if (tab && tab.classList.contains('active')) { initIaTab(); }
})();
'''

@app.route("/static/ia.js")
def static_ia_js():
    return Response(_IA_JS, mimetype="application/javascript")

@app.route("/api/scan/nombres")
def api_scan_nombres():
    nombres = [{"nombre": r.get("nombre",""), "estado": r.get("estado","")}
               for r in (_scan_resultados or [])]
    return jsonify(nombres)


@app.route("/api/gemini-test")
def api_ia_test():
    import requests as _req
    gemini_key = os.environ.get("GEMINI_API_KEY", "")
    if not gemini_key:
        return jsonify({"error": "Sin key"})
    # Listar modelos disponibles
    r = _req.get(f"https://generativelanguage.googleapis.com/v1beta/models?key={gemini_key}", timeout=10)
    data = r.json()
    modelos = [m["name"] for m in data.get("models", []) if "generateContent" in m.get("supportedGenerationMethods", [])]
    return jsonify({"modelos_disponibles": modelos, "total": len(modelos)})

@app.route("/api/ia/<ticker>")
def api_ia_analisis(ticker):
    ticker = ticker.upper().strip()
    r = next((x for x in (_scan_resultados or []) if x.get("nombre","").upper() == ticker), {})
    precio = r.get("precio_mxn", 0)
    entrada = r.get("entrada_mxn", 0)
    stop = r.get("stop_mxn", 0)
    obj = r.get("obj_mxn", 0)
    rr = r.get("rr", 0)
    rsi_v = r.get("rsi", 0)
    score = r.get("score_ajustado", r.get("score", 0))
    total_c = r.get("total_criterios", 13)
    estado = r.get("estado", "N/A")
    macd_ok = r.get("macd_ok", False)
    ema200_ok = r.get("ema200_ok", False)
    obv = (r.get("obv") or {}).get("tendencia", "sin datos")
    adx_v = r.get("adx", 0)
    sector = (r.get("sector") or {})
    ganga = (r.get("ganga") or {})
    es_ganga = ganga.get("es_ganga", False)
    ganga_pct = ganga.get("margen_pct", 0)
    inicio = (r.get("inicio") or {})
    nivel_ini = inicio.get("nivel", "") if inicio.get("es_inicio") else ""
    cap = (r.get("capitulacion") or {})
    es_cap = cap.get("es_capitulacion", False)
    bloq = "; ".join((r.get("setup") or {}).get("bloqueadores", [])[:2])
    sr = (r.get("sr") or {})
    sr_ctx = sr.get("contexto", "")
    tc_actual = _MACRO_CACHE.get("tc", 17.2) or 17.2
    soportes = [{"precio": round(z.get("precio",0)*tc_actual,2), "fuerza": z.get("fuerza",0)} for z in sr.get("soportes",[])[:3]]
    resists = [{"precio": round(z.get("precio",0)*tc_actual,2), "fuerza": z.get("fuerza",0)} for z in sr.get("resistencias",[])[:3]]
    # ── Extraer campos adicionales del scanner ──
    tfs         = r.get("tfs", {})
    tf_1d       = tfs.get("1day", {})
    tf_1w       = tfs.get("1week", {})
    criterios   = r.get("criterios", {})
    sizing      = r.get("sizing", {})
    confluencia = r.get("confluencia", {})
    setup_d     = r.get("setup", {})
    exit_info   = r.get("exit_info", {})

    ema21_mxn   = round((tf_1d.get("ema21", 0) or 0) * tc_actual, 2)
    ema50_mxn   = round((tf_1d.get("ema50", 0) or 0) * tc_actual, 2)
    ema200_mxn  = r.get("ema200_mxn", 0) or 0
    rsi_1w      = tf_1w.get("rsi", 0) or 0
    macd_1w     = tf_1w.get("macd_alcista", None)

    titulos_sug  = sizing.get("titulos", 0) or 0
    costo_op     = sizing.get("costo_total_mxn", 0) or 0
    riesgo_op    = sizing.get("riesgo_mxn", 0) or 0
    ganancia_op  = sizing.get("ganancia_potencial_mxn", 0) or 0

    decision     = setup_d.get("decision_final", estado)
    tipo_setup   = setup_d.get("tipo_setup", "—")
    confianza    = setup_d.get("confianza", 0) or 0
    advertencias = "; ".join(setup_d.get("advertencias", [])[:3])
    bloq_lista   = setup_d.get("bloqueadores", [])

    criterios_ok = [k for k,v in criterios.items() if v is True]
    criterios_no = [k for k,v in criterios.items() if v is False]

    obv_div     = (r.get("obv") or {}).get("divergencia", "")
    conf_score  = confluencia.get("score", 0) or 0
    conf_desc   = confluencia.get("descripcion", "")
    exit_senal  = exit_info.get("senal", "")
    exit_razon  = exit_info.get("razon", "")

    specials = []
    if es_ganga:  specials.append(f"GANGA: precio {ganga_pct}% bajo objetivo")
    if nivel_ini: specials.append(f"INICIO DE MOVIMIENTO nivel {nivel_ini}")
    if es_cap:    specials.append("CAPITULACION detectada")

    prompt = (
        f"Eres un analista experto de swing trading. Analiza {ticker} para un trader mexicano en GBM/SIC.\n"
        f"Capital: $15,000 MXN | Riesgo por operación: 1% ($150 MXN) | R:R mínimo: 3x\n\n"
        f"=== PRECIO Y NIVELES (MXN) ===\n"
        f"Precio: ${precio:,.2f} | EMA9: ${entrada:,.2f} | EMA21: ${ema21_mxn:,.2f} | EMA50: ${ema50_mxn:,.2f} | EMA200: ${ema200_mxn:,.2f}\n"
        f"Stop: ${stop:,.2f} | Objetivo: ${obj:,.2f} | R:R: {rr:.1f}x\n\n"
        f"=== INDICADORES 1D ===\n"
        f"RSI: {rsi_v:.0f} {'(sobrecomprado)' if rsi_v>70 else '(sobrevendido)' if rsi_v<30 else '(neutro)'} | MACD: {'ALCISTA' if macd_ok else 'BAJISTA'} | EMA200: {'ENCIMA' if ema200_ok else 'DEBAJO'}\n"
        f"ADX: {adx_v:.0f} {'(tendencia fuerte)' if adx_v>25 else '(sin tendencia clara)'} | OBV: {obv}{' | Diverg: '+obv_div if obv_div else ''}\n\n"
        f"=== TIMEFRAME SEMANAL ===\n"
        f"RSI 1W: {rsi_1w:.0f} | MACD 1W: {'ALCISTA' if macd_1w else 'BAJISTA' if macd_1w is False else 'N/D'}\n\n"
        f"=== ESTADO Y SETUP ===\n"
        f"Estado: {estado} | Setup: {tipo_setup} | Confianza: {confianza}% | Score: {score}/{total_c} | Confluencia: {conf_score}/5\n"
        f"Decisión sistema: {decision}\n"
        f"Bloqueadores: {'; '.join(bloq_lista) if bloq_lista else 'ninguno'}\n"
        f"Advertencias: {advertencias or 'ninguna'}\n"
        f"Señales especiales: {' | '.join(specials) if specials else 'ninguna'}\n\n"
        f"=== CRITERIOS TÉCNICOS ===\n"
        f"Cumplen ({len(criterios_ok)}): {', '.join(criterios_ok[:10]) or 'ninguno'}\n"
        f"No cumplen ({len(criterios_no)}): {', '.join(criterios_no[:10]) or 'ninguno'}\n\n"
        f"=== SOPORTE / RESISTENCIA ===\n"
        f"Contexto: {sr_ctx} | Soportes: {soportes} | Resistencias: {resists}\n\n"
        f"=== SIZING ===\n"
        f"Títulos: {titulos_sug:.2f} | Costo: ${costo_op:,.0f} | Riesgo: ${riesgo_op:,.0f} | Ganancia potencial: ${ganancia_op:,.0f}\n\n"
        f"=== SECTOR ===\n"
        f"Sector: {sector.get('desc','N/A')} | Tendencia: {sector.get('tendencia','N/A')}\n\n"
        + (f"=== SEÑAL DE SALIDA ===\nSeñal: {exit_senal} | Razón: {exit_razon}\n\n" if exit_senal else "")
        + f"Responde EXACTAMENTE en este formato (sin asteriscos ni markdown):\n\n"
        f"SITUACION: [Qué está haciendo el precio con los indicadores. Números concretos en MXN.]\n\n"
        f"ENTRADA: [Precio exacto de entrada en MXN y condición para entrar. Si bloqueado: qué debe pasar para desbloquearse.]\n\n"
        f"STOP Y OBJETIVO: [Stop: $X MXN ({'{:.1f}'.format(abs(stop-precio)/precio*100 if precio else 0)}% abajo). Objetivo: $X MXN. Con el sizing: pérdida máx ${riesgo_op:,.0f} / ganancia potencial ${ganancia_op:,.0f}.]\n\n"
        f"RIESGOS: [3 riesgos concretos y específicos de {ticker} ahora mismo.]\n\n"
        f"VEREDICTO: [COMPRAR AHORA / ESPERAR CONFIRMACION / VIGILAR / NO ENTRAR / SALIR] — [2 oraciones explicando por qué.]"
    )
    gemini_key = os.environ.get("GEMINI_API_KEY", "")
    if not gemini_key:
        return jsonify({"ok": False, "error": "GEMINI_API_KEY no configurada en Render"}), 500
    try:
        import requests as _req
        url = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-2.5-flash:generateContent?key={gemini_key}"
        resp = _req.post(url,
            json={"contents": [{"parts": [{"text": prompt}]}],
                  "generationConfig": {"maxOutputTokens": 500, "temperature": 0.4}},
            headers={"Content-Type": "application/json"}, timeout=30)
        data = resp.json()
        # Extraer texto de la respuesta de Gemini
        candidates = data.get("candidates", [])
        if not candidates:
            error_msg = data.get("error", {}).get("message", "Sin candidatos en respuesta")
            print(f"[IA] Gemini sin candidatos para {ticker}: {data}")
            return jsonify({"ok": False, "error": error_msg}), 500
        content = candidates[0].get("content", {})
        parts = content.get("parts", [])
        texto = parts[0].get("text", "") if parts else ""
        if not texto:
            print(f"[IA] Gemini texto vacío para {ticker}: {data}")
            return jsonify({"ok": False, "error": "Gemini no generó texto"}), 500
        return jsonify({"ok": True, "analisis": texto, "ticker": ticker})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500

SEMIS_ETFS = {
    "SMH":  ("SMH",  "NASDAQ"),   # Referencia del sector sin apalancamiento
    "SOXL": ("SOXL", "NYSE"),     # 3x alcista
    "SOXS": ("SOXS", "NYSE"),     # 3x bajista
    "SOXX": ("SOXX", "NASDAQ"),   # Sin apalancamiento alternativo
}

SEMIS_EMPRESAS = {
    "NVDA": ("NVDA", "NASDAQ"),   # #1 — IA, GPUs, mueve todo el sector
    "AMD":  ("AMD",  "NASDAQ"),   # #2 — competidor NVDA, GPUs IA
    "ASML": ("ASML", "NASDAQ"),   # #3 — máquinas para fabricar chips, sin ellos no hay semis
    "AVGO": ("AVGO", "NASDAQ"),   # #4 — Broadcom, chips redes e IA
    "MU":   ("MU",   "NASDAQ"),   # #5 — Micron, memoria, ciclo de semis
    "QCOM": ("QCOM", "NASDAQ"),   # #6 — Qualcomm, móviles y autos
    "ARM":  ("ARM",  "NASDAQ"),   # #7 — arquitectura base de chips modernos
    "INTC": ("INTC", "NASDAQ"),   # #8 — Intel, en declive pero aún mueve
}

SEMIS_MACRO = {
    "QQQ":  ("QQQ",  "NASDAQ"),   # NASDAQ ETF — macro de tech
}

SEMIS_POSICION_ACTUAL = {
    "ticker": "SOXS",
    "titulos": 18,
    "precio_entrada_mxn": 198.0,
    "tipo": "BAJISTA",
    "notas": "Posición abierta — esperando corrección de semis"
}

def analizar_semis_etf(symbol: str, exchange: str, tc: float) -> dict:
    """
    Análisis especializado para ETFs de semiconductores.
    Detecta los 4 pasos de corrección bajista y los 4 alcistas.
    """
    vals = _get_cached(symbol, "1day", exchange)
    if not vals or len(vals) < 30:
        return {"valido": False, "simbolo": symbol}

    closes = [float(x["close"]) for x in vals]
    highs  = [float(x.get("high",  x["close"])) for x in vals]
    lows   = [float(x.get("low",   x["close"])) for x in vals]
    opens  = [float(x.get("open",  x["close"])) for x in vals]
    vols   = [float(x.get("volume", 0))          for x in vals]

    c = pd.Series(closes)
    v = pd.Series(vols)
    n = len(c)

    e9   = float(ema(c, 9).iloc[-1])
    e21  = float(ema(c, 21).iloc[-1])
    e50  = float(ema(c, 50).iloc[-1])
    e200 = float(ema(c, 200).iloc[-1]) if n >= 200 else float(ema(c, min(n-1, 50)).iloc[-1])

    precio   = closes[-1]
    precio_1 = closes[-2] if n > 1 else precio
    open_h   = opens[-1]
    high_h   = highs[-1]
    low_h    = lows[-1]

    vol_avg = float(v.rolling(20).mean().iloc[-1])
    vol_rel = float(v.iloc[-1]) / vol_avg if vol_avg > 0 else 1.0

    rsi_v = float(rsi(c, 14).iloc[-1])

    macd_line, signal_line, hist_v = macd(c)
    macd_val   = float(macd_line.iloc[-1])
    signal_val = float(signal_line.iloc[-1])
    hist_val   = float(hist_v.iloc[-1])
    hist_prev  = float(hist_v.iloc[-2]) if n > 1 else 0

    # ── Mínimos y máximos recientes (últimas 10 velas) ────────────────
    min_10 = min(lows[-10:])
    max_10 = max(highs[-10:])
    min_prev = min(lows[-11:-1]) if n > 11 else min_10
    max_prev = max(highs[-11:-1]) if n > 11 else max_10

    # ── Tamaño de la vela actual ──────────────────────────────────────
    cuerpo     = abs(precio - open_h)
    rango_vela = high_h - low_h
    es_vela_grande = cuerpo > 0 and rango_vela > 0 and (cuerpo / rango_vela) >= 0.6
    vela_roja  = precio < open_h and es_vela_grande
    vela_verde = precio > open_h and es_vela_grande

    # ── Promedio de cuerpos para comparar ────────────────────────────
    cuerpos_avg = float(pd.Series([abs(closes[i] - opens[i]) for i in range(-10, -1)]).mean())
    vela_fuerte = cuerpo >= cuerpos_avg * 1.5  # vela 1.5x más grande que el promedio

    # ════════════════════════════════════════════════════════════════
    # ════════════════════════════════════════════════════════════════
    pasos_bajista = []

    paso1_bajista = precio < e9 and precio_1 >= e9 * 0.995
    pasos_bajista.append({
        "num": 1, "ok": paso1_bajista,
        "desc": "Pierde EMA9 diaria",
        "detalle": f"Precio ${precio*tc:,.2f} {'< ✅' if paso1_bajista else '> ❌'} EMA9 ${e9*tc:,.2f}"
    })

    paso2_bajista = vela_roja and vela_fuerte and vol_rel >= 1.3
    pasos_bajista.append({
        "num": 2, "ok": paso2_bajista,
        "desc": "Vela roja fuerte con volumen",
        "detalle": f"Vela {'roja fuerte ✅' if paso2_bajista else 'no confirmada ❌'} · Volumen {vol_rel:.1f}x"
    })

    vela_anterior_verde = closes[-2] > opens[-2] if n > 1 else False
    paso3_bajista = vela_anterior_verde and precio < e9 and macd_val < signal_val
    pasos_bajista.append({
        "num": 3, "ok": paso3_bajista,
        "desc": "El rebote falla",
        "detalle": f"Rebote {'falló ✅' if paso3_bajista else 'pendiente ❌'} · MACD {'bajista' if macd_val < signal_val else 'alcista'}"
    })

    paso4_bajista = low_h < min_prev and vol_rel >= 1.2
    pasos_bajista.append({
        "num": 4, "ok": paso4_bajista,
        "desc": "Rompe mínimos previos",
        "detalle": f"Min actual ${low_h*tc:,.2f} {'< mín previo ✅' if paso4_bajista else '>= mín previo ❌'} ${min_prev*tc:,.2f}"
    })

    pasos_bajista_ok = sum(1 for p in pasos_bajista if p["ok"])

    # ════════════════════════════════════════════════════════════════
    # ════════════════════════════════════════════════════════════════
    pasos_alcista = []

    paso1_alcista = precio > e9 and precio_1 <= e9 * 1.005
    pasos_alcista.append({
        "num": 1, "ok": paso1_alcista,
        "desc": "Supera EMA9 diaria",
        "detalle": f"Precio ${precio*tc:,.2f} {'> ✅' if paso1_alcista else '< ❌'} EMA9 ${e9*tc:,.2f}"
    })

    paso2_alcista = vela_verde and vela_fuerte and vol_rel >= 1.3
    pasos_alcista.append({
        "num": 2, "ok": paso2_alcista,
        "desc": "Vela verde fuerte con volumen",
        "detalle": f"Vela {'verde fuerte ✅' if paso2_alcista else 'no confirmada ❌'} · Volumen {vol_rel:.1f}x"
    })

    vela_anterior_roja = closes[-2] < opens[-2] if n > 1 else False
    paso3_alcista = vela_anterior_roja and precio > e9 and macd_val > signal_val
    pasos_alcista.append({
        "num": 3, "ok": paso3_alcista,
        "desc": "Pullback se sostiene sobre EMA9",
        "detalle": f"Pullback {'sostenido ✅' if paso3_alcista else 'pendiente ❌'} · MACD {'alcista' if macd_val > signal_val else 'bajista'}"
    })

    paso4_alcista = high_h > max_prev and vol_rel >= 1.2
    pasos_alcista.append({
        "num": 4, "ok": paso4_alcista,
        "desc": "Rompe máximos previos",
        "detalle": f"Max actual ${high_h*tc:,.2f} {'> máx previo ✅' if paso4_alcista else '<= máx previo ❌'} ${max_prev*tc:,.2f}"
    })

    pasos_alcista_ok = sum(1 for p in pasos_alcista if p["ok"])

    # ── Señal final ───────────────────────────────────────────────
    if pasos_bajista_ok == 4:
        senal = "SOXS_ENTRADA"
        senal_desc = "🔴 SEÑAL COMPLETA — Corrección confirmada · Oportunidad SOXS"
        senal_color = "#ef4444"
    elif pasos_bajista_ok == 3:
        senal = "SOXS_PROBABLE"
        senal_desc = "🟠 3/4 pasos bajistas · Preparar entrada SOXS"
        senal_color = "#f97316"
    elif pasos_alcista_ok == 4:
        senal = "SOXL_ENTRADA"
        senal_desc = "🟢 SEÑAL COMPLETA — Tendencia alcista confirmada · Oportunidad SOXL"
        senal_color = "#22c55e"
    elif pasos_alcista_ok == 3:
        senal = "SOXL_PROBABLE"
        senal_desc = "🟡 3/4 pasos alcistas · Preparar entrada SOXL"
        senal_color = "#eab308"
    else:
        senal = "NEUTRAL"
        senal_desc = f"⚪ Sin señal clara · {pasos_bajista_ok}/4 bajistas · {pasos_alcista_ok}/4 alcistas"
        senal_color = "var(--muted)"

    tendencia_sector = "alcista" if precio > e50 > e200 else "bajista" if precio < e50 else "lateral"

    return {
        "valido": True,
        "simbolo": symbol,
        "precio_usd": round(precio, 4),
        "precio_mxn": round(precio * tc, 2),
        "e9_mxn": round(e9 * tc, 2),
        "e21_mxn": round(e21 * tc, 2),
        "e50_mxn": round(e50 * tc, 2),
        "e200_mxn": round(e200 * tc, 2),
        "rsi": round(rsi_v, 1),
        "vol_rel": round(vol_rel, 2),
        "macd_hist": round(hist_val, 4),
        "macd_hist_prev": round(hist_prev, 4),
        "vela_roja": vela_roja,
        "vela_verde": vela_verde,
        "vela_fuerte": vela_fuerte,
        "pasos_bajista": pasos_bajista,
        "pasos_bajista_ok": pasos_bajista_ok,
        "pasos_alcista": pasos_alcista,
        "pasos_alcista_ok": pasos_alcista_ok,
        "senal": senal,
        "senal_desc": senal_desc,
        "senal_color": senal_color,
        "tendencia_sector": tendencia_sector,
        "min_10_mxn": round(min_10 * tc, 2),
        "max_10_mxn": round(max_10 * tc, 2),
    }

def _analizar_base_semis(symbol: str, exchange: str, tc: float) -> dict:
    """
    Análisis técnico base para cualquier ticker del módulo semis.
    SOLO usa datos ya en cache — NUNCA hace llamadas API nuevas.
    Si el ticker no está en cache, devuelve valido=False.
    """
    key_1d = f"{symbol}:1day"
    key_1d_ex = f"{symbol}:{exchange}:1day"
    
    vals = None
    for key in (key_1d, key_1d_ex):
        if key in _TD_CACHE and _TD_CACHE[key]:
            vals = _TD_CACHE[key]
            break
    
    if not vals:
        for k, v in _TD_CACHE.items():
            if symbol.upper() in k.upper() and "1day" in k.lower() and v:
                vals = v
                break
    
    if not vals or len(vals) < 30:
        return {"valido": False, "simbolo": symbol}

    closes = [float(x["close"]) for x in vals]
    highs  = [float(x.get("high",  x["close"])) for x in vals]
    lows   = [float(x.get("low",   x["close"])) for x in vals]
    opens  = [float(x.get("open",  x["close"])) for x in vals]
    vols   = [float(x.get("volume", 0))          for x in vals]

    c = pd.Series(closes)
    v = pd.Series(vols)
    n = len(c)

    e9   = float(ema(c, 9).iloc[-1])
    e21  = float(ema(c, 21).iloc[-1])
    e50  = float(ema(c, 50).iloc[-1])
    e200 = float(ema(c, min(n-1, 200)).iloc[-1])

    precio   = closes[-1]
    precio_1 = closes[-2] if n > 1 else precio
    open_h   = opens[-1]
    high_h   = highs[-1]
    low_h    = lows[-1]

    vol_avg = float(v.rolling(20).mean().iloc[-1])
    vol_rel = float(v.iloc[-1]) / vol_avg if vol_avg > 0 else 1.0
    rsi_v   = float(rsi(c, 14).iloc[-1])

    macd_line, signal_line, hist_v = macd(c)
    macd_val   = float(macd_line.iloc[-1])
    signal_val = float(signal_line.iloc[-1])
    hist_val   = float(hist_v.iloc[-1])
    hist_prev  = float(hist_v.iloc[-2]) if n > 1 else 0

    min_10  = min(lows[-10:])
    max_10  = max(highs[-10:])
    min_prev = min(lows[-11:-1]) if n > 11 else min_10
    max_prev = max(highs[-11:-1]) if n > 11 else max_10

    cuerpo     = abs(precio - open_h)
    rango_vela = high_h - low_h
    cuerpos_avg = abs(pd.Series([abs(closes[i]-opens[i]) for i in range(-10,-1)]).mean())
    es_vela_grande = cuerpo > 0 and rango_vela > 0 and (cuerpo/rango_vela) >= 0.6
    vela_roja   = precio < open_h and es_vela_grande
    vela_verde  = precio > open_h and es_vela_grande
    vela_fuerte = cuerpo >= cuerpos_avg * 1.5

    sobre_e9   = precio > e9
    sobre_e50  = precio > e50
    sobre_e200 = precio > e200
    tendencia  = "alcista" if sobre_e50 and sobre_e200 else "bajista" if not sobre_e50 else "lateral"

    momentum = "acelerando_alcista" if hist_val > 0 and hist_val > hist_prev else \
               "acelerando_bajista" if hist_val < 0 and hist_val < hist_prev else \
               "desacelerando"

    cambio_dia_pct = (precio - precio_1) / precio_1 * 100 if precio_1 else 0

    return {
        "valido": True, "simbolo": symbol,
        "precio": precio, "precio_mxn": round(precio * tc, 2),
        "precio_1": precio_1,
        "open_h": open_h, "high_h": high_h, "low_h": low_h,
        "e9": e9, "e21": e21, "e50": e50, "e200": e200,
        "e9_mxn": round(e9*tc,2), "e50_mxn": round(e50*tc,2), "e200_mxn": round(e200*tc,2),
        "rsi": round(rsi_v, 1),
        "vol_rel": round(vol_rel, 2),
        "macd_val": macd_val, "signal_val": signal_val,
        "hist_val": hist_val, "hist_prev": hist_prev,
        "vela_roja": vela_roja, "vela_verde": vela_verde, "vela_fuerte": vela_fuerte,
        "sobre_e9": sobre_e9, "sobre_e50": sobre_e50, "sobre_e200": sobre_e200,
        "tendencia": tendencia, "momentum": momentum,
        "cambio_dia_pct": round(cambio_dia_pct, 2),
        "min_10": min_10, "max_10": max_10,
        "min_prev": min_prev, "max_prev": max_prev,
        "min_10_mxn": round(min_10*tc,2), "max_10_mxn": round(max_10*tc,2),
        "closes": closes[-20:],   # últimas 20 para mini-gráfico
    }

def _detectar_4_pasos(base: dict, tc: float) -> dict:
    """Detecta los 4 pasos bajistas y alcistas + Estado Actual de la tendencia."""
    precio   = base["precio"]
    precio_1 = base["precio_1"]
    e9       = base["e9"]
    e21      = base["e21"]
    e50      = base["e50"]
    vol_rel  = base["vol_rel"]
    vela_roja   = base["vela_roja"]
    vela_verde  = base["vela_verde"]
    vela_fuerte = base["vela_fuerte"]
    macd_val    = base["macd_val"]
    signal_val  = base["signal_val"]
    hist_val    = base["hist_val"]
    hist_prev   = base["hist_prev"]
    low_h    = base["low_h"]
    high_h   = base["high_h"]
    min_prev = base["min_prev"]
    max_prev = base["max_prev"]
    rsi_v    = base["rsi"]
    tendencia = base["tendencia"]
    cambio   = base["cambio_dia_pct"]

    closes = base["closes"]
    vela_anterior_verde = closes[-2] > base["open_h"] if len(closes) > 1 else False
    vela_anterior_roja  = closes[-2] < base["open_h"] if len(closes) > 1 else False

    # ── ESTADO ACTUAL (modo confirmación) ────────────────────
    alcista_fuerte   = (precio > e9 > e21 and macd_val > signal_val and
                        hist_val > 0 and rsi_v > 55 and cambio > 2.0)
    alcista_activo   = (precio > e9 and macd_val > signal_val and rsi_v > 45)
    bajista_fuerte   = (precio < e9 < e21 and macd_val < signal_val and
                        hist_val < 0 and rsi_v < 45 and cambio < -2.0)
    bajista_activo   = (precio < e9 and macd_val < signal_val and rsi_v < 55)
    lateral          = not alcista_activo and not bajista_activo

    if alcista_fuerte:
        estado       = "TENDENCIA_FUERTE_ALCISTA"
        estado_desc  = "🚀 Tendencia alcista fuerte activa"
        estado_color = "#22c55e"
        estado_accion = "NO ENTRES AHORA — el tren ya salió. Espera que corrija y RSI baje a 45-55. Ahí compras SOXL más barato con mejor R:R."
    elif alcista_activo:
        estado       = "TENDENCIA_ALCISTA"
        estado_desc  = "📈 Tendencia alcista activa"
        estado_color = "#4ade80"
        estado_accion = "VIGILA — mercado alcista pero sin señal de entrada nueva. Espera los 4 pasos de GIRO ALCISTA para entrar a SOXL."
    elif bajista_fuerte:
        estado       = "TENDENCIA_FUERTE_BAJISTA"
        estado_desc  = "📉 Tendencia bajista fuerte activa"
        estado_color = "#ef4444"
        estado_accion = "SOXS EN TERRENO FAVORABLE — si ya tienes posición, mantén. Si no, espera los 4 pasos completos para entrar."
    elif bajista_activo:
        estado       = "TENDENCIA_BAJISTA"
        estado_desc  = "🔻 Tendencia bajista activa"
        estado_color = "#f87171"
        estado_accion = "VIGILA SOXS — mercado bajista pero sin confirmación total. Espera los 4 pasos completos antes de entrar."
    else:
        estado       = "LATERAL"
        estado_desc  = "⚪ Lateral — sin dirección"
        estado_color = "var(--muted)"
        estado_accion = "NO HAGAS NADA — mercado sin rumbo. SOXL y SOXS pierden dinero por decay en lateral. Espera señal clara."

    # ── 4 pasos bajistas (modo cambio — detecta el GIRO) ─────
    p1b = precio < e9 and precio_1 >= e9 * 0.995
    p2b = vela_roja and vela_fuerte and vol_rel >= 1.3
    p3b = vela_anterior_verde and precio < e9 and macd_val < signal_val
    p4b = low_h < min_prev and vol_rel >= 1.2

    pasos_bajista = [
        {"num":1,"ok":p1b,"desc":"Pierde EMA9 diaria",
         "detalle":f"Precio ${precio*tc:,.2f} {'< ✅' if p1b else '> ❌'} EMA9 ${e9*tc:,.2f}"},
        {"num":2,"ok":p2b,"desc":"Vela roja fuerte con volumen",
         "detalle":f"{'Confirmada ✅' if p2b else 'No confirmada ❌'} · Vol {vol_rel:.1f}x"},
        {"num":3,"ok":p3b,"desc":"El rebote falla",
         "detalle":f"{'Falló ✅' if p3b else 'Pendiente ❌'} · MACD {'bajista' if macd_val < signal_val else 'alcista'}"},
        {"num":4,"ok":p4b,"desc":"Rompe mínimos previos",
         "detalle":f"${low_h*tc:,.2f} {'< mín ✅' if p4b else '>= mín ❌'} ${min_prev*tc:,.2f}"},
    ]

    # ── 4 pasos alcistas (modo cambio — detecta el GIRO) ─────
    p1a = precio > e9 and precio_1 <= e9 * 1.005
    p2a = vela_verde and vela_fuerte and vol_rel >= 1.3
    p3a = vela_anterior_roja and precio > e9 and macd_val > signal_val
    p4a = high_h > max_prev and vol_rel >= 1.2

    pasos_alcista = [
        {"num":1,"ok":p1a,"desc":"Supera EMA9 diaria",
         "detalle":f"Precio ${precio*tc:,.2f} {'> ✅' if p1a else '< ❌'} EMA9 ${e9*tc:,.2f}"},
        {"num":2,"ok":p2a,"desc":"Vela verde fuerte con volumen",
         "detalle":f"{'Confirmada ✅' if p2a else 'No confirmada ❌'} · Vol {vol_rel:.1f}x"},
        {"num":3,"ok":p3a,"desc":"Pullback se sostiene sobre EMA9",
         "detalle":f"{'Sostenido ✅' if p3a else 'Pendiente ❌'} · MACD {'alcista' if macd_val > signal_val else 'bajista'}"},
        {"num":4,"ok":p4a,"desc":"Rompe máximos previos",
         "detalle":f"${high_h*tc:,.2f} {'> máx ✅' if p4a else '<= máx ❌'} ${max_prev*tc:,.2f}"},
    ]

    pb_ok = sum(1 for p in pasos_bajista if p["ok"])
    pa_ok = sum(1 for p in pasos_alcista if p["ok"])

    if pb_ok == 4:
        senal="BAJISTA_FULL"; senal_desc="🔴 SEÑAL DE ENTRADA — 4/4 pasos bajistas · Entra a SOXS"; senal_color="#ef4444"
    elif pb_ok == 3:
        senal="BAJISTA_PROB"; senal_desc="🟠 3/4 pasos bajistas — Prepárate para SOXS"; senal_color="#f97316"
    elif pb_ok == 2:
        senal="BAJISTA_INIT"; senal_desc="🔶 2/4 pasos bajistas — Empieza a vigilar"; senal_color="#fb923c"
    elif pa_ok == 4:
        senal="ALCISTA_FULL"; senal_desc="🟢 SEÑAL DE ENTRADA — 4/4 pasos alcistas · Entra a SOXL"; senal_color="#22c55e"
    elif pa_ok == 3:
        senal="ALCISTA_PROB"; senal_desc="🟡 3/4 pasos alcistas — Prepárate para SOXL"; senal_color="#eab308"
    elif pa_ok == 2:
        senal="ALCISTA_INIT"; senal_desc="🔷 2/4 pasos alcistas — Empieza a vigilar"; senal_color="#60a5fa"
    else:
        senal="NEUTRAL"; senal_desc=f"⚪ Sin señal de cambio · {pb_ok}/4 bajistas · {pa_ok}/4 alcistas"; senal_color="var(--muted)"

    return {
        "pasos_bajista": pasos_bajista, "pasos_bajista_ok": pb_ok,
        "pasos_alcista": pasos_alcista, "pasos_alcista_ok": pa_ok,
        "senal": senal, "senal_desc": senal_desc, "senal_color": senal_color,
        "estado": estado, "estado_desc": estado_desc,
        "estado_color": estado_color, "estado_accion": estado_accion,
    }

def _calcular_decay_estimado(dias: int, tipo: str = "3x") -> float:
    """Estima el decay acumulado de un ETF apalancado 3x en N días."""
    decay_diario = 0.001
    return round((1 - (1 - decay_diario) ** dias) * 100, 2)

def analizar_todos_semis(tc: float) -> dict:
    """
    Analiza ETFs, empresas clave y macro de semiconductores.
    USA SOLO _get_cached — no hace llamadas API nuevas si los datos ya están en memoria.
    Para tickers nuevos (no en scanner) hace UNA llamada y los cachea.
    """
    etfs      = {}
    empresas  = {}
    macro     = {}

    for nombre, (symbol, exchange) in SEMIS_ETFS.items():
        try:
            base = _analizar_base_semis(symbol, exchange, tc)
            if base["valido"]:
                pasos = _detectar_4_pasos(base, tc)
                etfs[nombre] = {**base, **pasos, "nombre": nombre}
            else:
                etfs[nombre] = {"valido": False, "nombre": nombre}
        except Exception as e:
            print(f"[semis] ETF {nombre}: {e}")
            etfs[nombre] = {"valido": False, "nombre": nombre}

    for nombre, (symbol, exchange) in SEMIS_EMPRESAS.items():
        try:
            base = _analizar_base_semis(symbol, exchange, tc)
            if base["valido"]:
                empresas[nombre] = {**base, "nombre": nombre}
            else:
                empresas[nombre] = {"valido": False, "nombre": nombre}
        except Exception as e:
            print(f"[semis] Empresa {nombre}: {e}")
            empresas[nombre] = {"valido": False, "nombre": nombre}

    for nombre, (symbol, exchange) in SEMIS_MACRO.items():
        try:
            base = _analizar_base_semis(symbol, exchange, tc)
            if base["valido"]:
                macro[nombre] = {**base, "nombre": nombre}
            else:
                macro[nombre] = {"valido": False, "nombre": nombre}
        except Exception as e:
            print(f"[semis] Macro {nombre}: {e}")
            macro[nombre] = {"valido": False, "nombre": nombre}

    # ── Semáforo del sector basado en SMH ───────────────────
    smh = etfs.get("SMH", {})
    qqq = macro.get("QQQ", {})
    if smh.get("valido") and qqq.get("valido"):
        smh_alc = smh.get("tendencia") == "alcista"
        qqq_alc = qqq.get("tendencia") == "alcista"
        if smh_alc and qqq_alc:
            semaforo = "verde"; semaforo_desc = "🟢 Sector alcista — condiciones para SOXL"
        elif not smh_alc and not qqq_alc:
            semaforo = "rojo"; semaforo_desc = "🔴 Sector bajista — condiciones para SOXS"
        elif smh_alc and not qqq_alc:
            semaforo = "amarillo"; semaforo_desc = "🟡 Divergencia: semis alcistas pero NASDAQ bajista — precaución"
        else:
            semaforo = "amarillo"; semaforo_desc = "🟡 Divergencia: NASDAQ alcista pero semis rezagados"
    elif smh.get("valido"):
        smh_alc = smh.get("tendencia") == "alcista"
        semaforo = "verde" if smh_alc else "rojo"
        semaforo_desc = f"{'🟢 SMH alcista' if smh_alc else '🔴 SMH bajista'} — QQQ sin datos"
    else:
        semaforo = "gris"; semaforo_desc = "⚪ Sin datos suficientes"

    # ── Divergencia SOXL vs SMH ──────────────────────────────
    soxl = etfs.get("SOXL", {})
    divergencia_desc = ""
    if smh.get("valido") and soxl.get("valido"):
        smh_sube  = smh.get("cambio_dia_pct", 0) > 0
        soxl_sube = soxl.get("cambio_dia_pct", 0) > 0
        if smh_sube and not soxl_sube:
            divergencia_desc = "⚠️ Trampa: SMH sube pero SOXL baja — no entrar a SOXL todavía"
        elif not smh_sube and soxl_sube:
            divergencia_desc = "💡 Oportunidad: SMH baja pero SOXL se mantiene — sector resistente"
        elif not smh_sube and not soxl_sube:
            divergencia_desc = "🔴 Corrección confirmada en ambos — SOXS tiene viento a favor"

    # ── Empresas que más afectan hoy ─────────────────────────
    impacto_hoy = []
    for nombre, emp in empresas.items():
        if not emp.get("valido"):
            continue
        cambio = emp.get("cambio_dia_pct", 0)
        if abs(cambio) >= 2.0:
            icon = "🔴" if cambio < 0 else "🟢"
            impacto_hoy.append(f"{icon} {nombre} {cambio:+.1f}%")
    impacto_hoy.sort(key=lambda x: abs(float(x.split()[-1].replace('%',''))), reverse=True)

    return {
        "etfs": etfs,
        "empresas": empresas,
        "macro": macro,
        "semaforo": semaforo,
        "semaforo_desc": semaforo_desc,
        "divergencia_desc": divergencia_desc,
        "impacto_hoy": impacto_hoy,
    }

def render_tab_semis(semis_data: dict, tc: float) -> str:
    """Tab completo de Semis ETF."""
    etfs     = semis_data.get("etfs", {})
    empresas = semis_data.get("empresas", {})
    macro    = semis_data.get("macro", {})
    semaforo = semis_data.get("semaforo", "gris")
    semaforo_desc = semis_data.get("semaforo_desc", "")
    divergencia_desc = semis_data.get("divergencia_desc", "")
    impacto_hoy = semis_data.get("impacto_hoy", [])

    for nombre, r in etfs.items():
        if r.get("valido") and r.get("senal", "NEUTRAL") != "NEUTRAL":
            guardar_senal_semis(nombre, r["senal"], r["precio_mxn"],
                                max(r.get("pasos_bajista_ok",0), r.get("pasos_alcista_ok",0)), "ETF")

    # ── Posición actual ──────────────────────────────────────
    pos = SEMIS_POSICION_ACTUAL
    soxs_data = etfs.get("SOXS", {})
    if soxs_data.get("valido"):
        precio_actual = soxs_data["precio_mxn"]
        pl_mxn = (precio_actual - pos["precio_entrada_mxn"]) * pos["titulos"]
        pl_pct = (precio_actual - pos["precio_entrada_mxn"]) / pos["precio_entrada_mxn"] * 100
        pl_col = "var(--green)" if pl_mxn >= 0 else "var(--red)"
        dias_aprox = 5  # estimado
        decay_est  = _calcular_decay_estimado(dias_aprox)
        stop_mxn   = round(pos["precio_entrada_mxn"] * 1.08, 2)
        obj1_mxn   = round(pos["precio_entrada_mxn"] * 0.85, 2)
        obj2_mxn   = round(pos["precio_entrada_mxn"] * 0.75, 2)
        obj3_mxn   = round(pos["precio_entrada_mxn"] * 0.60, 2)
        senal_soxs = soxs_data.get("senal_desc", "")
        senal_col  = soxs_data.get("senal_color", "var(--muted)")

        posicion_html = f'''
<div style="background:linear-gradient(135deg,#1a0a0a,#2d1515);border:2px solid #ef4444;border-radius:14px;padding:20px;margin-bottom:20px">
  <div style="display:flex;align-items:flex-start;justify-content:space-between;margin-bottom:12px">
    <div>
      <div style="font-size:11px;color:#fca5a5;font-weight:700;letter-spacing:1px">📍 TU POSICIÓN ACTUAL</div>
      <div style="font-size:24px;font-weight:800;color:#fff;margin-top:4px">{pos["ticker"]} <span style="font-size:13px;color:#fca5a5">3x Bajista · {pos["titulos"]} títulos</span></div>
      <div style="font-size:12px;color:#fca5a5">Entrada ${pos["precio_entrada_mxn"]:,.2f} MXN · Actual ${precio_actual:,.2f} MXN</div>
    </div>
    <div style="text-align:right">
      <div style="font-size:22px;font-weight:800;color:{pl_col}">{pl_mxn:+,.2f}</div>
      <div style="font-size:13px;color:{pl_col}">{pl_pct:+.1f}%</div>
    </div>
  </div>
  <div style="background:#1a0808;border-radius:8px;padding:8px 12px;margin-bottom:12px;font-size:12px;color:{senal_col};font-weight:600">{senal_soxs}</div>
  <div style="display:grid;grid-template-columns:repeat(5,1fr);gap:6px;font-size:10px;margin-bottom:12px">
    <div style="background:#2d1515;border-radius:8px;padding:8px;text-align:center">
      <div style="color:#fca5a5">STOP</div>
      <div style="font-weight:700;color:#ef4444">${stop_mxn:,.2f}</div>
    </div>
    <div style="background:#2d1515;border-radius:8px;padding:8px;text-align:center">
      <div style="color:#fca5a5">OBJ 1 · 25%</div>
      <div style="font-weight:700;color:#22c55e">${obj1_mxn:,.2f}</div>
    </div>
    <div style="background:#2d1515;border-radius:8px;padding:8px;text-align:center">
      <div style="color:#fca5a5">OBJ 2 · 50%</div>
      <div style="font-weight:700;color:#22c55e">${obj2_mxn:,.2f}</div>
    </div>
    <div style="background:#2d1515;border-radius:8px;padding:8px;text-align:center">
      <div style="color:#fca5a5">OBJ 3 · 75%</div>
      <div style="font-weight:700;color:#22c55e">${obj3_mxn:,.2f}</div>
    </div>
    <div style="background:#2d1515;border-radius:8px;padding:8px;text-align:center">
      <div style="color:#fca5a5">DECAY ~{dias_aprox}d</div>
      <div style="font-weight:700;color:#f97316">-{decay_est:.1f}%</div>
    </div>
  </div>
  <div style="font-size:11px;color:#fca5a5">⚠️ SOXS tiene decay diario 3x. Stop loss en ${stop_mxn:,.2f} — sin excepciones.</div>
</div>'''
    else:
        posicion_html = f'''
<div style="background:var(--surface);border:1px solid #ef4444;border-radius:12px;padding:16px;margin-bottom:20px">
  <div style="font-weight:700">{pos["ticker"]} — {pos["titulos"]} títulos a ${pos["precio_entrada_mxn"]:,.2f} MXN</div>
  <div style="font-size:11px;color:var(--muted)">Cargando datos...</div>
</div>'''

    # ── Semáforo del sector ──────────────────────────────────
    sem_col = {"verde":"#22c55e","rojo":"#ef4444","amarillo":"#eab308","gris":"var(--muted)"}.get(semaforo,"var(--muted)")
    sem_bg  = {"verde":"#052e16","rojo":"#1a0a0a","amarillo":"#1c1400","gris":"var(--surface)"}.get(semaforo,"var(--surface)")

    semaforo_html = f'''
<div style="background:{sem_bg};border:2px solid {sem_col};border-radius:12px;padding:14px 18px;margin-bottom:16px;display:flex;flex-wrap:wrap;gap:12px;align-items:center">
  <div style="font-size:14px;font-weight:700;color:{sem_col}">{semaforo_desc}</div>
  {f'<div style="font-size:12px;color:#eab308">{divergencia_desc}</div>' if divergencia_desc else ""}
  {f'<div style="font-size:11px;color:var(--muted)">{"  ·  ".join(impacto_hoy[:5])}</div>' if impacto_hoy else ""}
</div>'''

    # ── Alerta SOXS — cuándo entrar ─────────────────────────
    smh_r  = etfs.get("SMH", {})
    soxs_r = etfs.get("SOXS", {})
    soxl_r = etfs.get("SOXL", {})

    smh_bajista  = smh_r.get("estado","") in ("TENDENCIA_BAJISTA","TENDENCIA_FUERTE_BAJISTA")
    soxl_bajista = soxl_r.get("estado","") in ("TENDENCIA_BAJISTA","TENDENCIA_FUERTE_BAJISTA")
    pasos_soxs   = soxs_r.get("pasos_bajista_ok", 0) if soxs_r.get("valido") else 0
    pasos_soxs_entrada = soxs_r.get("pasos_alcista_ok", 0) if soxs_r.get("valido") else 0

    if smh_bajista and soxl_bajista and pasos_soxs_entrada >= 4:
        alerta_nivel  = "ENTRAR"
        alerta_bg     = "linear-gradient(135deg,#1a0a0a,#450a0a)"
        alerta_borde  = "#ef4444"
        alerta_titulo = "🚨 MOMENTO DE ENTRAR A SOXS"
        alerta_texto  = "SMH bajista + SOXL bajista + 4/4 señales confirmadas. ES EL MOMENTO. Entra a SOXS ahora con stop loss definido."
        alerta_accion = "COMPRA SOXS"
        alerta_col    = "#ef4444"
    elif smh_bajista and soxl_bajista and pasos_soxs_entrada >= 3:
        alerta_nivel  = "PREPARAR"
        alerta_bg     = "linear-gradient(135deg,#1c0a00,#431407)"
        alerta_borde  = "#f97316"
        alerta_titulo = "⚡ CASI LISTO PARA SOXS — Prepárate"
        alerta_texto  = "SMH bajista + SOXL bajista + 3/4 señales. Falta 1 paso. Ten el dinero listo, define tu stop loss. Puede ser mañana."
        alerta_accion = "PREPARA LA ORDEN"
        alerta_col    = "#f97316"
    elif smh_bajista and pasos_soxs_entrada >= 2:
        alerta_nivel  = "VIGILAR"
        alerta_bg     = "linear-gradient(135deg,#1c1400,#451a03)"
        alerta_borde  = "#eab308"
        alerta_titulo = "👁 EMPIEZA A VIGILAR SOXS"
        alerta_texto  = "SMH bajista + 2/4 señales. El movimiento está empezando. No entres todavía — espera 3/4 o 4/4."
        alerta_accion = "SOLO VIGILAR"
        alerta_col    = "#eab308"
    else:
        alerta_nivel  = "ESPERAR"
        alerta_bg     = "var(--surface)"
        alerta_borde  = "var(--brd)"
        alerta_titulo = "⏳ AÚN NO ES MOMENTO PARA SOXS"
        alerta_texto  = "El sector no muestra señales bajistas suficientes. Mantén tu posición actual si ya tienes SOXS, pero no agregues más todavía."
        alerta_accion = "ESPERA"
        alerta_col    = "var(--muted)"

    alerta_soxs_html = f'''
<div style="background:{alerta_bg};border:2px solid {alerta_borde};border-radius:14px;padding:20px;margin-bottom:20px">
  <div style="display:flex;align-items:center;justify-content:space-between;flex-wrap:wrap;gap:10px">
    <div>
      <div style="font-size:16px;font-weight:800;color:{alerta_col};margin-bottom:6px">{alerta_titulo}</div>
      <div style="font-size:12px;color:var(--muted);max-width:500px;line-height:1.6">{alerta_texto}</div>
    </div>
    <div style="background:{alerta_borde};color:#fff;font-weight:800;font-size:13px;padding:12px 20px;border-radius:10px;text-align:center;min-width:120px">
      {alerta_accion}
    </div>
  </div>
  <div style="display:flex;gap:16px;margin-top:12px;font-size:11px;flex-wrap:wrap">
    <div>SMH: <span style="color:{"#ef4444" if smh_bajista else "#22c55e"};font-weight:700">{"🔴 Bajista" if smh_bajista else "🟢 Alcista"}</span></div>
    <div>SOXL: <span style="color:{"#ef4444" if soxl_bajista else "#22c55e"};font-weight:700">{"🔴 Bajista" if soxl_bajista else "🟢 Alcista"}</span></div>
    <div>Señales SOXS: <span style="color:{alerta_col};font-weight:700">{pasos_soxs_entrada}/4</span></div>
  </div>
</div>'''

    # ── Cards ETFs con 4 pasos ───────────────────────────────
    etf_cards = ""
    for nombre in ["SMH", "SOXL", "SOXS", "SOXX"]:
        r = etfs.get(nombre, {})
        if not r.get("valido"):
            etf_cards += f'<div style="background:var(--surface);border:1px solid var(--brd);border-radius:12px;padding:16px;opacity:0.4"><strong>{nombre}</strong><div style="font-size:11px;color:var(--muted)">Sin datos</div></div>'
            continue
        senal_col = r.get("senal_color","var(--muted)")
        borde_w = "2px" if r.get("senal","NEUTRAL") != "NEUTRAL" else "1px"
        pb_ok = r.get("pasos_bajista_ok",0)
        pa_ok = r.get("pasos_alcista_ok",0)
        estado_desc   = r.get("estado_desc", "")
        estado_color  = r.get("estado_color", "var(--muted)")
        estado_accion = r.get("estado_accion", "")

        borde_col = estado_color if r.get("estado","") != "LATERAL" else senal_col

        pasos_b = "".join(
            f'<div style="font-size:10px;color:{"#22c55e" if p["ok"] else "var(--muted)"};margin-top:2px">{"✅" if p["ok"] else "❌"} {p["desc"]}</div>'
            for p in r.get("pasos_bajista",[])
        )
        pasos_a = "".join(
            f'<div style="font-size:10px;color:{"#22c55e" if p["ok"] else "var(--muted)"};margin-top:2px">{"✅" if p["ok"] else "❌"} {p["desc"]}</div>'
            for p in r.get("pasos_alcista",[])
        )
        etf_cards += f'''
<div style="background:var(--surface);border:2px solid {borde_col};border-radius:14px;padding:18px;position:relative;overflow:hidden">
  <div style="position:absolute;top:0;left:0;right:0;height:4px;background:{estado_color}"></div>
  <div style="display:flex;justify-content:space-between;align-items:flex-start;margin-bottom:10px">
    <div>
      <div style="font-size:20px;font-weight:800">{nombre}</div>
      <div style="font-size:11px;color:var(--muted)">${r["precio_mxn"]:,.2f} MXN · RSI {r["rsi"]:.0f} · Vol {r["vol_rel"]:.1f}x · {r["cambio_dia_pct"]:+.1f}%</div>
    </div>
    <div style="text-align:right;font-size:10px;color:var(--muted)">
      <div>EMA9 ${r["e9_mxn"]:,.2f}</div>
      <div>EMA50 ${r["e50_mxn"]:,.2f}</div>
    </div>
  </div>

  <!-- ESTADO ACTUAL — lo más importante -->
  <div style="background:var(--surface2);border-left:4px solid {estado_color};border-radius:0 8px 8px 0;padding:10px 12px;margin-bottom:10px">
    <div style="font-size:13px;font-weight:800;color:{estado_color};margin-bottom:3px">{estado_desc}</div>
    <div style="font-size:11px;color:var(--muted)">{estado_accion}</div>
  </div>

  <!-- SEÑAL DE CAMBIO (los 4 pasos) -->
  <div style="background:var(--surface2);border-radius:8px;padding:7px 10px;margin-bottom:10px;font-size:11px;font-weight:600;color:{senal_col}">{r.get("senal_desc","")}</div>

  <div style="display:grid;grid-template-columns:1fr 1fr;gap:10px">
    <div>
      <div style="font-size:10px;font-weight:700;color:#ef4444;margin-bottom:3px">🔴 GIRO BAJISTA ({pb_ok}/4)</div>
      {pasos_b}
    </div>
    <div>
      <div style="font-size:10px;font-weight:700;color:#22c55e;margin-bottom:3px">🟢 GIRO ALCISTA ({pa_ok}/4)</div>
      {pasos_a}
    </div>
  </div>
</div>'''

    # ── Empresas que mueven el sector ────────────────────────
    emp_rows = ""
    for nombre in ["NVDA","AMD","ASML","AVGO","MU","QCOM","ARM","INTC"]:
        e = empresas.get(nombre, {})
        if not e.get("valido"):
            continue
        cambio = e.get("cambio_dia_pct", 0)
        tend   = e.get("tendencia","—")
        mom    = e.get("momentum","—")
        c_col  = "#22c55e" if cambio > 0 else "#ef4444" if cambio < 0 else "var(--muted)"
        t_col  = "#22c55e" if tend=="alcista" else "#ef4444" if tend=="bajista" else "var(--muted)"
        sobre_e9_icon = "▲" if e.get("sobre_e9") else "▽"
        emp_rows += f'''<tr>
          <td style="font-weight:700;padding:8px 10px">{nombre}</td>
          <td style="font-family:var(--mono);padding:8px 10px">${e["precio_mxn"]:,.2f}</td>
          <td style="color:{c_col};font-family:var(--mono);padding:8px 10px">{cambio:+.1f}%</td>
          <td style="color:var(--muted);padding:8px 10px">{e["rsi"]:.0f}</td>
          <td style="color:{t_col};padding:8px 10px">{sobre_e9_icon} {tend}</td>
          <td style="color:var(--muted);font-size:10px;padding:8px 10px">{mom.replace("_"," ")}</td>
        </tr>'''

    # ── Macro QQQ ────────────────────────────────────────────
    qqq = macro.get("QQQ",{})
    qqq_html = ""
    if qqq.get("valido"):
        qt  = qqq.get("tendencia","—")
        qc  = qqq.get("cambio_dia_pct",0)
        qt_col = "#22c55e" if qt=="alcista" else "#ef4444" if qt=="bajista" else "var(--muted)"
        qc_col = "#22c55e" if qc>0 else "#ef4444"
        qqq_html = f'''
<div style="background:var(--surface);border:1px solid var(--brd);border-radius:10px;padding:14px 16px;margin-bottom:16px;display:flex;align-items:center;gap:16px;flex-wrap:wrap">
  <div style="font-weight:700;font-size:13px">📊 QQQ (NASDAQ)</div>
  <div style="font-size:12px">${qqq["precio_mxn"]:,.2f} MXN</div>
  <div style="font-size:12px;color:{qc_col}">{qc:+.1f}% hoy</div>
  <div style="font-size:12px;color:{qt_col}">Tendencia: {qt}</div>
  <div style="font-size:11px;color:var(--muted)">RSI {qqq["rsi"]:.0f}</div>
  <div style="font-size:11px;color:var(--muted)">Vol {qqq["vol_rel"]:.1f}x</div>
</div>'''

    # ── Historial de señales ─────────────────────────────────
    historial = get_historial_señales(20)
    hist_rows = ""
    for h in historial:
        s = h.get("senal","")
        s_col = "#22c55e" if "ALCISTA" in s else "#ef4444" if "BAJISTA" in s else "var(--muted)"
        hist_rows += f'<tr><td style="padding:6px 10px">{h["fecha"]}</td><td style="font-weight:700;padding:6px 10px">{h["simbolo"]}</td><td style="color:{s_col};padding:6px 10px;font-size:11px">{s.replace("_"," ")}</td><td style="padding:6px 10px;font-family:var(--mono)">${h["precio_mxn"]:,.2f}</td><td style="padding:6px 10px;color:var(--muted)">{h["pasos_ok"]}/4</td></tr>'

    hist_html = f'''
<div style="background:var(--surface);border:1px solid var(--brd);border-radius:10px;overflow:hidden;margin-top:16px">
  <div style="padding:12px 16px;font-weight:700;font-size:13px;border-bottom:1px solid var(--brd)">📋 Historial de señales</div>
  <table style="width:100%;font-size:12px;border-collapse:collapse">
    <thead style="background:var(--surface2)">
      <tr style="color:var(--muted);font-size:10px">
        <th style="text-align:left;padding:6px 10px">Fecha</th>
        <th style="text-align:left;padding:6px 10px">Símbolo</th>
        <th style="text-align:left;padding:6px 10px">Señal</th>
        <th style="text-align:left;padding:6px 10px">Precio MXN</th>
        <th style="text-align:left;padding:6px 10px">Pasos</th>
      </tr>
    </thead>
    <tbody>{hist_rows if hist_rows else '<tr><td colspan="5" style="padding:16px;text-align:center;color:var(--muted)">Sin señales registradas aún</td></tr>'}</tbody>
  </table>
</div>'''

    # ── Notas educativas ─────────────────────────────────────
    notas_html = '''
<div style="background:var(--surface);border:1px solid var(--brd);border-radius:10px;padding:16px 18px;margin-top:16px;font-size:12px;line-height:1.8">
  <div style="font-weight:700;font-size:13px;margin-bottom:12px">📖 Manual del operador de Semis ETF</div>
    <div style="background:linear-gradient(135deg,#052e16,#14532d);border:1px solid #22c55e;border-radius:10px;padding:14px 16px;margin-bottom:14px">
      <div style="font-weight:700;color:#22c55e;margin-bottom:8px;font-size:13px">💡 Estrategia del Pullback — cómo entrar a SOXL cuando ya está alcista</div>
      <div style="font-size:12px;color:#86efac;line-height:1.8">
        <div>1. SOXL está en tendencia alcista fuerte (🚀) → <strong>NO entres todavía</strong></div>
        <div>2. Espera que corrija 2-3 días → RSI baja de 77 a zona 45-55</div>
        <div>3. El precio toca EMA9 o EMA21 y rebota → aparece vela verde fuerte</div>
        <div>4. Los 4 pasos de GIRO ALCISTA empiezan a activarse</div>
        <div>5. Cuando llega a 3/4 o 4/4 → <strong>ESE es tu momento de entrar</strong></div>
        <div style="margin-top:8px;color:#4ade80">✅ Así compras SOXL 10-15% más barato que si entras cuando ya está arriba</div>
      </div>
    </div>
    <div>
      <div style="font-weight:600;color:#ef4444;margin-bottom:6px">🔴 Los 4 pasos bajistas → SOXS</div>
      <div style="color:var(--muted)">
        <div>▼ Paso 1: Pierde EMA9 diaria</div>
        <div>▼ Paso 2: Vela roja fuerte con volumen</div>
        <div>▼ Paso 3: El rebote falla</div>
        <div>▼ Paso 4: Rompe mínimos previos</div>
      </div>
    </div>
    <div>
      <div style="font-weight:600;color:#22c55e;margin-bottom:6px">🟢 Los 4 pasos alcistas → SOXL</div>
      <div style="color:var(--muted)">
        <div>▲ Paso 1: Supera EMA9 diaria</div>
        <div>▲ Paso 2: Vela verde fuerte con volumen</div>
        <div>▲ Paso 3: El pullback se sostiene</div>
        <div>▲ Paso 4: Rompe máximos previos</div>
      </div>
    </div>
  </div>
  <div style="display:grid;grid-template-columns:1fr 1fr;gap:12px;font-size:11px">
    <div style="background:var(--surface2);border-radius:8px;padding:10px">
      <div style="font-weight:600;margin-bottom:4px">⚠️ Reglas del decay</div>
      <div style="color:var(--muted)">SOXL y SOXS pierden ~0.1% diario en mercados laterales por el apalancamiento 3x. Nunca mantengas más de 10 días sin señal activa. Usa SMH para confirmar dirección antes de entrar.</div>
    </div>
    <div style="background:var(--surface2);border-radius:8px;padding:10px">
      <div style="font-weight:600;margin-bottom:4px">🏢 Las empresas que más importan</div>
      <div style="color:var(--muted)">NVDA mueve el sector. Si NVDA cae -3% en un día, SMH cae -1.5% y SOXL cae -4.5%. Siempre revisa NVDA y ASML antes de entrar. Earnings de NVDA = volatilidad extrema.</div>
    </div>
    <div style="background:var(--surface2);border-radius:8px;padding:10px">
      <div style="font-weight:600;margin-bottom:4px">📊 Cómo usar el semáforo</div>
      <div style="color:var(--muted)">🟢 Verde: SMH y QQQ alcistas → condiciones para SOXL. 🔴 Rojo: ambos bajistas → condiciones para SOXS. 🟡 Amarillo: divergencia → esperar confirmación.</div>
    </div>
    <div style="background:var(--surface2);border-radius:8px;padding:10px">
      <div style="font-weight:600;margin-bottom:4px">🔮 Por qué semiconductores es el futuro</div>
      <div style="color:var(--muted)">Todo lo que corre IA necesita chips: servidores, autos autónomos, smartphones, satélites. NVDA, ASML y AMD son el petróleo del siglo XXI. El ciclo alcista de semis tiene años por delante.</div>
    </div>
  </div>
</div>'''

    return f'''<div id="tab-semis" class="tab">
  <div style="padding:20px 0 14px">
    <h2 style="font-size:20px;font-weight:600;letter-spacing:-.4px">📡 Semis ETF — Detector de Cambio de Tendencia</h2>
    <p class="hint">SMH · SOXL · SOXS · SOXX · NVDA · AMD · ASML · AVGO · MU · QCOM · ARM · INTC · QQQ</p>
  </div>

  {posicion_html}
  {alerta_soxs_html}
  {qqq_html}
  {semaforo_html}

  <div style="font-size:13px;font-weight:700;margin-bottom:10px">ETFs de semiconductores</div>
  <div style="display:grid;grid-template-columns:repeat(auto-fill,minmax(320px,1fr));gap:14px;margin-bottom:20px">
    {etf_cards}
  </div>

  <div style="font-size:13px;font-weight:700;margin-bottom:10px">Empresas que mueven el sector</div>
  <div style="background:var(--surface);border:1px solid var(--brd);border-radius:10px;overflow:hidden;margin-bottom:16px">
    <table style="width:100%;font-size:12px;border-collapse:collapse">
      <thead style="background:var(--surface2)">
        <tr style="color:var(--muted);font-size:10px">
          <th style="text-align:left;padding:8px 10px">Empresa</th>
          <th style="text-align:left;padding:8px 10px">Precio MXN</th>
          <th style="text-align:left;padding:8px 10px">Hoy</th>
          <th style="text-align:left;padding:8px 10px">RSI</th>
          <th style="text-align:left;padding:8px 10px">Tendencia</th>
          <th style="text-align:left;padding:8px 10px">Momentum</th>
        </tr>
      </thead>
      <tbody>{emp_rows if emp_rows else '<tr><td colspan="6" style="padding:16px;text-align:center;color:var(--muted)">Cargando empresas...</td></tr>'}</tbody>
    </table>
  </div>

  {hist_html}
  {notas_html}
</div>'''

# ═══════════════════════════════════════════════════════════
#   MAIN
# ═══════════════════════════════════════════════════════════
if __name__ == "__main__":
    print("\n" + "="*56)
    print("   FINBIT PRO  v3.2  — servidor web (non-blocking)")
    print("="*56)

    # ── Restaurar DB desde GitHub antes de init_db ────────
    db_restore_from_github()

    init_db()

    # ── Cargar dashboard anterior si existe ───────────────
    # Firma de versión: si el HTML cacheado tiene código viejo, se descarta
    _HTML_VERSION_MARKER = "removeProperty('display')"
    if os.path.exists(OUTPUT_FILE):
        try:
            with open(OUTPUT_FILE, "r", encoding="utf-8") as f:
                _cached = f.read()
            if len(_cached) > 500 and _HTML_VERSION_MARKER in _cached:
                with _dash_lock:
                    _dash_html = _cached
                print(f"[server] 📂 Dashboard anterior cargado desde disco ({len(_cached)//1024}KB)")
                print(f"[server]    Puedes usar Finbit mientras se actualiza en background.")
            elif len(_cached) > 500:
                print(f"[server] ⚠️  Dashboard anterior descartado (versión vieja) — se regenerará")
        except Exception as _e:
            print(f"[server] No se pudo cargar dashboard anterior: {_e}")

    threading.Thread(target=_loop_backup_github, daemon=True).start()
    threading.Thread(target=_loop_alertas_telegram, daemon=True).start()
    print("[alertas] 🔔 Monitoreo Telegram activo — Ganga y Pre-breakout 4/5 en horario de mercado")

    port = int(os.environ.get("PORT", 5000))
    print(f"[server] Puerto: {port}  |  http://localhost:{port}")
    app.run(host="0.0.0.0", port=port, debug=False, use_reloader=False)

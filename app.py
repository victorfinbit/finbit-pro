"""
╔══════════════════════════════════════════════════════════════╗
║  FINBIT PRO — Flask Web App                                  ║
║  Para Render: gunicorn app:app                               ║
║  Local:       python app.py                                  ║
╚══════════════════════════════════════════════════════════════╝

requirements.txt:
    flask
    pandas
    requests
    gunicorn
    pytz
"""

from flask import Flask, jsonify, request, render_template_string, redirect
import threading, json, os, time, sqlite3
from datetime import datetime
import pytz
import requests as req
import pandas as pd

# ── Importar toda la lógica de análisis desde finbit.py ──────
# (finbit.py debe estar en la misma carpeta)
import importlib.util, sys

def cargar_finbit():
    """Carga finbit.py como módulo sin ejecutar el bloque main."""
    spec = importlib.util.spec_from_file_location("finbit", 
           os.path.join(os.path.dirname(__file__), "finbit.py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod

try:
    fb = cargar_finbit()
except Exception as e:
    print(f"⚠️  No se pudo cargar finbit.py: {e}")
    fb = None

# ── Flask app ─────────────────────────────────────────────────
app = Flask(__name__)

# ── Zona horaria México (CDT/CST) ─────────────────────────────
TZ_MX = pytz.timezone("America/Mexico_City")

def ahora_mx():
    """Hora actual en México."""
    return datetime.now(TZ_MX)

def ts_mx():
    """Timestamp legible en hora México."""
    return ahora_mx().strftime("%d/%m/%Y %H:%M:%S CDT")

# ── Cache en memoria ──────────────────────────────────────────
_cache = {
    "html":        None,   # HTML renderizado
    "ts":          None,   # Timestamp del último update
    "updating":    False,  # Flag para evitar updates simultáneos
    "scan_data":   [],
    "radar_data":  [],
    "port_data":   [],
    "tc":          17.5,
    "error":       None,
    "version":     0,      # Incrementa en cada update → cache busting
}

# ─────────────────────────────────────────────────────────────
#  LÓGICA DE ACTUALIZACIÓN
# ─────────────────────────────────────────────────────────────

def run_update(force: bool = False):
    """
    Corre el análisis completo en un hilo separado.
    Si ya está corriendo, ignora la petición (a menos que sea force=True).
    """
    if _cache["updating"] and not force:
        return {"status": "already_running", "ts": _cache["ts"]}

    _cache["updating"] = True
    _cache["error"] = None

    try:
        if fb is None:
            raise RuntimeError("finbit.py no cargado correctamente")

        # Re-cargar config por si cambió
        cfg = fb.cargar_config()
        capital    = cfg["capital"]
        riesgo_pct = cfg["riesgo"]
        rr_min     = cfg["rr_min"]

        fb.init_db()
        tc = fb.get_tipo_cambio(fb.API_KEY)
        fb.seed_portafolio(tc)

        # Cargar tickers extra guardados en DB
        tickers_extra = {}
        tickers_db = fb.get_tickers_db()
        tickers_extra.update(tickers_db)

        # Analizar
        port_data  = fb.analizar_portafolio(tc, capital, riesgo_pct, rr_min)

        # Scanner: TODOS los tickers del código + los de la DB
        combinados = dict(fb.SCANNER_TICKERS)
        combinados.update(tickers_db)
        scan_data  = _correr_scanner_completo(combinados, tc, capital, riesgo_pct, rr_min)

        radar_data = fb.radar_masivo(tc, capital, riesgo_pct, rr_min)

        ops = fb.get_operaciones()
        html = fb.generar_html(port_data, scan_data, radar_data, ops,
                               tc, capital, riesgo_pct, rr_min)

        # Guardar en cache
        _cache["html"]       = html
        _cache["ts"]         = ts_mx()
        _cache["tc"]         = tc
        _cache["port_data"]  = port_data
        _cache["scan_data"]  = scan_data
        _cache["radar_data"] = radar_data
        _cache["version"]   += 1
        _cache["error"]      = None

        print(f"✅ Update completo [{_cache['ts']}] — v{_cache['version']}")
        return {"status": "ok", "ts": _cache["ts"], "version": _cache["version"]}

    except Exception as e:
        _cache["error"] = str(e)
        print(f"❌ Error en update: {e}")
        return {"status": "error", "error": str(e)}
    finally:
        _cache["updating"] = False


def _correr_scanner_completo(combinados: dict, tc, capital, riesgo_pct, rr_min):
    """Corre el scanner con el diccionario completo de tickers."""
    if fb is None:
        return []
    port_map = {p["ticker"]: p["titulos"] for p in fb.get_portafolio()}
    resultados = []

    for nombre, (symbol, exchange) in combinados.items():
        tit = port_map.get(nombre, 0.0)
        an  = fb.analizar_ticker_1d(nombre, symbol, exchange, capital,
                                     riesgo_pct, rr_min, tit, tc=tc, origen="USA")
        tf_1d = an["tf"].get("1D", {})
        if not tf_1d.get("valido"):
            continue

        c     = {k: v["ok"] for k, v in tf_1d["criterios"].items()}
        score = tf_1d["score"]
        exp   = tf_1d.get("explosion", False)

        if exp:
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
            "nombre": nombre, "estado": estado,
            "precio_usd": p_usd, "precio_mxn": p_usd * tc,
            "entrada_mxn": tf_1d.get("entrada_sugerida", p_usd) * tc,
            "stop_mxn": tf_1d.get("stop", 0) * tc,
            "obj_mxn":  tf_1d.get("objetivo", 0) * tc,
            "rsi": tf_1d["rsi"], "rr": tf_1d["rr"],
            "macd_ok": tf_1d["macd_alcista"],
            "ema200_ok": c.get("ema200", False),
            "score": score,
            "total_criterios": tf_1d.get("total_criterios", 8),
            "criterios": tf_1d["criterios"],
            "sizing": tf_1d.get("sizing", {}),
            "tfs": an["tf"],
            "confluencia": an["confluencia"],
            "titulos_cartera": tit,
        })

    orden = {"ROCKET": 0, "BUY": 1, "WATCH": 2, "SKIP": 3, "SHORT": 4}
    resultados.sort(key=lambda x: (orden.get(x["estado"], 9), -x["rr"]))
    return resultados


# ─────────────────────────────────────────────────────────────
#  RUTAS FLASK
# ─────────────────────────────────────────────────────────────

@app.route("/")
def index():
    """Página principal — sirve el HTML del dashboard."""
    if _cache["html"] is None:
        # Primera carga: mostrar pantalla de espera y disparar update
        if not _cache["updating"]:
            threading.Thread(target=run_update, daemon=True).start()
        return _loading_page()

    # Cache busting: añadir versión al HTML para forzar recarga en browser
    html = _cache["html"]
    ts   = _cache["ts"] or "—"
    ver  = _cache["version"]

    # Inyectar: botón de refresh, hora México, anti-cache meta
    html = _inject_refresh_ui(html, ts, ver)
    return html


@app.route("/update", methods=["GET", "POST"])
def update():
    """
    Dispara una actualización completa.
    GET  → redirige al dashboard después de actualizar
    POST → retorna JSON con el status
    """
    if _cache["updating"]:
        msg = "Ya hay una actualización en curso..."
        if request.method == "POST":
            return jsonify({"status": "already_running", "ts": _cache["ts"]})
        return _loading_page(msg)

    # Correr en hilo separado
    threading.Thread(target=run_update, daemon=True).start()

    if request.method == "POST":
        return jsonify({"status": "started", "ts": ts_mx()})

    # Redirigir a loading page
    return _loading_page("Actualizando datos... esto toma 1-2 minutos.")


@app.route("/status")
def status():
    """JSON con el estado actual del sistema."""
    return jsonify({
        "updating":  _cache["updating"],
        "ts":        _cache["ts"],
        "version":   _cache["version"],
        "tc":        _cache["tc"],
        "error":     _cache["error"],
        "hora_mx":   ts_mx(),
        "n_scanner": len(_cache["scan_data"]),
        "n_radar":   len(_cache["radar_data"]),
        "n_port":    len(_cache["port_data"]),
    })


@app.route("/api/tickers", methods=["GET"])
def get_tickers():
    """Lista todos los tickers del scanner (defaults + DB)."""
    if fb is None:
        return jsonify({"error": "finbit no cargado"}), 500
    defaults = list(fb.SCANNER_TICKERS.keys())
    db_tickers = list(fb.get_tickers_db().keys())
    return jsonify({
        "defaults": defaults,
        "custom": db_tickers,
        "total": len(set(defaults + db_tickers))
    })


@app.route("/api/tickers/add", methods=["POST"])
def add_ticker():
    """
    Agrega un ticker a la DB (persiste entre updates).
    Body JSON: {"ticker": "NVDA", "exchange": "NASDAQ"}
    """
    if fb is None:
        return jsonify({"error": "finbit no cargado"}), 500
    data = request.get_json() or {}
    ticker   = data.get("ticker", "").upper().strip()
    exchange = data.get("exchange", "").upper().strip()

    if not ticker:
        return jsonify({"error": "ticker requerido"}), 400

    fb.add_ticker_db(ticker, exchange)
    return jsonify({"status": "ok", "ticker": ticker, "exchange": exchange,
                    "msg": f"{ticker} agregado. Actualiza para verlo analizado."})


@app.route("/api/tickers/remove", methods=["POST"])
def remove_ticker():
    """Elimina un ticker de la DB."""
    if fb is None:
        return jsonify({"error": "finbit no cargado"}), 500
    data   = request.get_json() or {}
    ticker = data.get("ticker", "").upper().strip()
    if not ticker:
        return jsonify({"error": "ticker requerido"}), 400
    fb.remove_ticker_db(ticker)
    return jsonify({"status": "ok", "ticker": ticker})


@app.route("/api/portafolio/add", methods=["POST"])
def add_posicion():
    """Agrega o actualiza una posición directamente."""
    if fb is None:
        return jsonify({"error": "finbit no cargado"}), 500
    data = request.get_json() or {}
    op = {
        "ticker":    data.get("ticker","").upper().strip(),
        "tipo":      data.get("tipo","COMPRA"),
        "titulos":   float(data.get("titulos", 0)),
        "precio_mxn":float(data.get("precio_mxn", 0)),
        "origen":    data.get("origen","USA"),
        "mercado":   data.get("mercado","SIC"),
    }
    if not op["ticker"] or op["titulos"] <= 0:
        return jsonify({"error": "datos incompletos"}), 400

    # Guardar operación en DB
    tc = _cache["tc"]
    con = sqlite3.connect(fb.DB_FILE)
    con.execute("""INSERT INTO operaciones 
        (fecha,ticker,tipo,titulos,precio_mxn,total_mxn,tc_dia,origen,mercado,notas)
        VALUES(?,?,?,?,?,?,?,?,?,?)""",
        (datetime.now(TZ_MX).strftime("%Y-%m-%d"), op["ticker"], op["tipo"],
         op["titulos"], op["precio_mxn"], op["titulos"]*op["precio_mxn"],
         tc, op["origen"], op["mercado"], data.get("notas","")))
    con.commit(); con.close()

    fb.upsert_portafolio_from_op(op)
    return jsonify({"status": "ok", "msg": "Operación guardada. Actualiza para ver cambios."})


# ─────────────────────────────────────────────────────────────
#  HELPERS HTML
# ─────────────────────────────────────────────────────────────

def _inject_refresh_ui(html: str, ts: str, version: int) -> str:
    """
    Inyecta en el HTML existente:
    1. Meta anti-caché
    2. Botón flotante de actualización con spinner
    3. Hora en zona México
    4. Auto-check de nueva versión cada 5 min
    """
    # 1. Anti-cache meta tags
    anti_cache = f'''
    <meta http-equiv="Cache-Control" content="no-cache, no-store, must-revalidate">
    <meta http-equiv="Pragma" content="no-cache">
    <meta http-equiv="Expires" content="0">'''
    html = html.replace("<head>", f"<head>{anti_cache}", 1)

    # 2. Botón flotante + JS de refresh + hora MX
    refresh_ui = f'''
<!-- FINBIT REFRESH UI -->
<style>
.fab-refresh{{
  position:fixed;bottom:24px;right:24px;z-index:9999;
  background:#dc2626;color:#fff;border:none;border-radius:50px;
  padding:10px 20px;font-size:13px;font-weight:600;cursor:pointer;
  box-shadow:0 4px 16px rgba(220,38,38,.4);
  display:flex;align-items:center;gap:8px;transition:all .2s;
  font-family:'DM Sans',sans-serif;
}}
.fab-refresh:hover{{transform:translateY(-2px);box-shadow:0 6px 20px rgba(220,38,38,.5)}}
.fab-refresh:disabled{{background:#9ca3af;box-shadow:none;cursor:not-allowed}}
.fab-refresh .spin{{animation:spin360 1s linear infinite;display:none}}
.fab-refresh.loading .spin{{display:inline}}
.fab-refresh.loading .ico{{display:none}}
@keyframes spin360{{to{{transform:rotate(360deg)}}}}
.hora-mx-badge{{
  position:fixed;bottom:72px;right:24px;z-index:9998;
  background:rgba(0,0,0,.75);color:#fff;border-radius:8px;
  padding:4px 10px;font-size:10px;font-family:monospace;
  pointer-events:none;
}}
.update-toast{{
  position:fixed;top:70px;right:16px;z-index:9999;
  background:#16a34a;color:#fff;border-radius:8px;
  padding:10px 16px;font-size:12px;font-weight:500;
  box-shadow:0 4px 12px rgba(0,0,0,.2);display:none;
  font-family:'DM Sans',sans-serif;
}}
</style>

<div class="hora-mx-badge" id="hora-mx">🕐 {ts}</div>
<div class="update-toast" id="update-toast">✅ ¡Datos actualizados!</div>
<button class="fab-refresh" id="fab-refresh" onclick="triggerUpdate()" title="Actualizar datos">
  <span class="ico">🔄</span>
  <span class="spin">⟳</span>
  <span id="fab-txt">Actualizar</span>
</button>

<script>
const _CURRENT_VERSION = {version};
let _checkInterval = null;

function triggerUpdate() {{
  const btn = document.getElementById('fab-refresh');
  const txt = document.getElementById('fab-txt');
  btn.classList.add('loading');
  btn.disabled = true;
  txt.textContent = 'Actualizando...';

  fetch('/update', {{method:'POST', headers:{{'Content-Type':'application/json'}}}})
    .then(r => r.json())
    .then(d => {{
      txt.textContent = 'Esperando...';
      // Poll hasta que termine
      _checkInterval = setInterval(checkForUpdate, 4000);
    }})
    .catch(e => {{
      btn.classList.remove('loading');
      btn.disabled = false;
      txt.textContent = 'Actualizar';
      alert('Error al actualizar: ' + e);
    }});
}}

function checkForUpdate() {{
  fetch('/status')
    .then(r => r.json())
    .then(d => {{
      // Actualizar hora
      const hEl = document.getElementById('hora-mx');
      if (hEl) hEl.textContent = '🕐 ' + d.hora_mx;

      if (!d.updating && d.version > _CURRENT_VERSION) {{
        // Nueva versión disponible → recargar con cache-busting
        clearInterval(_checkInterval);
        showToast('✅ ¡Actualizado! Recargando...');
        setTimeout(() => {{
          window.location.href = '/?v=' + d.version + '&t=' + Date.now();
        }}, 1200);
      }} else if (!d.updating && d.error) {{
        clearInterval(_checkInterval);
        const btn = document.getElementById('fab-refresh');
        const txt = document.getElementById('fab-txt');
        btn.classList.remove('loading');
        btn.disabled = false;
        txt.textContent = 'Actualizar';
        alert('Error: ' + d.error);
      }}
    }})
    .catch(() => {{}});
}}

function showToast(msg) {{
  const t = document.getElementById('update-toast');
  t.textContent = msg;
  t.style.display = 'block';
  setTimeout(() => t.style.display = 'none', 4000);
}}

// Actualizar reloj cada segundo
setInterval(() => {{
  fetch('/status')
    .then(r => r.json())
    .then(d => {{
      const hEl = document.getElementById('hora-mx');
      if (hEl) hEl.textContent = '🕐 ' + d.hora_mx;
    }})
    .catch(() => {{}});
}}, 30000);  // cada 30s actualiza la hora (no queremos saturar el servidor)

// Auto-check cada 5 minutos si hay nueva versión
setInterval(() => {{
  fetch('/status')
    .then(r => r.json())
    .then(d => {{
      if (d.version > _CURRENT_VERSION) {{
        showToast('🔄 Nueva versión disponible — haz clic en Actualizar');
      }}
    }})
    .catch(() => {{}});
}}, 300000);
</script>'''

    # Insertar antes del </body>
    html = html.replace("</body>", refresh_ui + "\n</body>", 1)
    return html


def _loading_page(msg: str = "Cargando datos por primera vez...") -> str:
    """Página de espera mientras corre el análisis."""
    return f"""<!DOCTYPE html>
<html lang="es"><head>
<meta charset="UTF-8">
<meta http-equiv="refresh" content="8">
<title>finbit pro — actualizando</title>
<style>
  body{{margin:0;background:#f5f5f3;display:flex;align-items:center;justify-content:center;
       min-height:100vh;font-family:'DM Sans',sans-serif}}
  .box{{text-align:center;padding:40px;background:#fff;border-radius:16px;
        box-shadow:0 4px 24px rgba(0,0,0,.08);max-width:400px}}
  .logo{{font-size:22px;font-weight:600;margin-bottom:8px}}
  .logo em{{color:#dc2626;font-style:normal}}
  .msg{{color:#666;font-size:14px;margin:16px 0}}
  .spinner{{width:40px;height:40px;border:3px solid #e5e5e3;border-top-color:#dc2626;
             border-radius:50%;animation:spin 0.8s linear infinite;margin:16px auto}}
  @keyframes spin{{to{{transform:rotate(360deg)}}}}
  .sub{{font-size:11px;color:#aaa;margin-top:12px}}
  .hora{{font-size:12px;color:#dc2626;margin-top:8px;font-weight:500}}
  .btn-reload{{background:#dc2626;color:#fff;border:none;border-radius:8px;
               padding:10px 24px;font-size:13px;cursor:pointer;margin-top:16px}}
</style>
</head><body>
<div class="box">
  <div class="logo">fin<em>bit</em> pro</div>
  <div class="spinner"></div>
  <div class="msg">{msg}</div>
  <div class="sub">Esta página se recarga automáticamente cada 8 segundos</div>
  <div class="hora" id="hora-mx">Hora México: {ts_mx()}</div>
  <button class="btn-reload" onclick="window.location.reload()">↻ Recargar ahora</button>
</div>
<script>
// Mostrar hora actualizada
setInterval(() => {{
  fetch('/status').then(r=>r.json()).then(d=>{{
    const el = document.getElementById('hora-mx');
    if(el && d.hora_mx) el.textContent = 'Hora México: ' + d.hora_mx;
    // Si terminó de actualizar, recargar la página principal
    if(!d.updating && d.version > 0) {{
      window.location.href = '/?v=' + d.version + '&t=' + Date.now();
    }}
  }}).catch(()=>{{}});
}}, 3000);
</script>
</body></html>"""


# ─────────────────────────────────────────────────────────────
#  ARRANQUE
# ─────────────────────────────────────────────────────────────

def arranque_inicial():
    """Corre el primer análisis en background al iniciar el servidor."""
    print(f"🚀 finbit pro arrancando — {ts_mx()}")
    if fb:
        fb.init_db()
        threading.Thread(target=run_update, daemon=True).start()
    else:
        print("⚠️  finbit.py no disponible — solo rutas de estado funcionarán")


if __name__ == "__main__":
    arranque_inicial()
    app.run(debug=False, host="0.0.0.0", port=int(os.environ.get("PORT", 5000)))
else:
    # Modo gunicorn (Render)
    arranque_inicial()

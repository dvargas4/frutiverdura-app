"""
Frutiverdura a Domicilio - App Streamlit
Replica del flujo de Google Colab para captura y gestión de tickets.
"""
import streamlit as st
import pandas as pd
from PIL import Image, ImageDraw, ImageFont
from datetime import datetime
import pytz
import io
import zipfile
import os

# ============================
# Configuración de página
# ============================
st.set_page_config(
    page_title="Frutiverdura a Domicilio",
    page_icon="🥬",
    layout="wide",
    initial_sidebar_state="expanded",
)

# ============================
# Constantes
# ============================
COSTO_ENVIO = 35
UTILIDAD_MINIMA_PCT = 0.25
MIN_PRODUCTOS_DESCUENTO = 5
ZONA_HORARIA = "America/Mexico_City"
SPREADSHEET_URL = "https://docs.google.com/spreadsheets/d/1J3-J_evoyTJcLP94GwixduwD-wFv3zuMjAQX_oBrcPQ/edit"

CONTACTOS = {
    "IVAN": "55 3497 6860",
    "JUAN PABLO": "55 3058 4417",
    "DIEGO": "55 5056 2131",
}

# ============================
# Fuentes (busca en el sistema, fallback a default)
# ============================
def encontrar_fuente(nombres_posibles):
    """Busca una fuente entre varias rutas comunes en Linux/Mac/Windows."""
    rutas_busqueda = [
        "/usr/share/fonts/truetype/dejavu/",
        "/usr/share/fonts/dejavu/",
        "/usr/share/fonts/TTF/",
        "/Library/Fonts/",
        "/System/Library/Fonts/",
        "C:/Windows/Fonts/",
        "fonts/",
    ]
    for ruta in rutas_busqueda:
        for nombre in nombres_posibles:
            full = os.path.join(ruta, nombre)
            if os.path.exists(full):
                return full
    return None


FUENTE_BOLD = encontrar_fuente([
    "DejaVuSans-Bold.ttf",
    "DejaVuSansBold.ttf",
    "LiberationSans-Bold.ttf",
])
FUENTE_REG = encontrar_fuente([
    "DejaVuSans.ttf",
    "LiberationSans-Regular.ttf",
])

# ============================
# Session state inicial
# ============================
if "pedidos" not in st.session_state:
    st.session_state.pedidos = []
if "productos_actuales" not in st.session_state:
    st.session_state.productos_actuales = []
if "precios_dict" not in st.session_state:
    st.session_state.precios_dict = {}
if "costos_dict" not in st.session_state:
    st.session_state.costos_dict = {}
if "gastos_sesion" not in st.session_state:
    # Lista de dicts: {"contacto": "IVAN", "concepto": "...", "monto": 0.0}
    st.session_state.gastos_sesion = []
if "pegar_reset_count" not in st.session_state:
    # Contador que se incrementa para forzar reset de los widgets del tab Pegar
    st.session_state.pegar_reset_count = 0

# ============================
# Carga del catálogo (Google Sheets o CSV local)
# ============================
def limpiar_valor(valor):
    """Conserva la lógica original: coma decimal -> punto decimal."""
    try:
        return float(str(valor).replace("$", "").replace(",", ".").strip())
    except (ValueError, AttributeError):
        return 0.0


@st.cache_data(ttl=300, show_spinner="Cargando catálogo desde Google Sheets...")
def cargar_catalogo_gsheets():
    """Carga el catálogo desde Google Sheets usando service account en st.secrets."""
    import gspread
    from google.oauth2.service_account import Credentials

    creds = Credentials.from_service_account_info(
        dict(st.secrets["gcp_service_account"]),
        scopes=["https://www.googleapis.com/auth/spreadsheets.readonly"],
    )
    client = gspread.authorize(creds)
    sheet = client.open_by_url(SPREADSHEET_URL).worksheet("PRECIOS")

    productos_lista = [p.strip().lower() for p in sheet.col_values(2)[1:] if p.strip()]
    costos_lista = [limpiar_valor(v) for v in sheet.col_values(8)[1:] if v.strip()]
    precios_lista = [limpiar_valor(v) for v in sheet.col_values(9)[1:] if v.strip()]

    precios = {
        p: {"precio_venta_kg": pv}
        for p, pv in zip(productos_lista, precios_lista)
    }
    costos = {
        p: {"costo_kg": c}
        for p, c in zip(productos_lista, costos_lista)
    }
    return precios, costos


def cargar_catalogo_csv(archivo):
    """Alternativa: cargar desde CSV subido con columnas Producto, Costo, Precio."""
    df = pd.read_csv(archivo)
    df.columns = [c.strip().lower() for c in df.columns]

    precios = {}
    costos = {}
    for _, row in df.iterrows():
        nombre = str(row.get("producto", "")).strip().lower()
        if not nombre:
            continue
        precios[nombre] = {"precio_venta_kg": limpiar_valor(row.get("precio", 0))}
        costos[nombre] = {"costo_kg": limpiar_valor(row.get("costo", 0))}
    return precios, costos


# ============================
# Persistencia: Histórico en Google Sheets
# ============================
HEADERS_TICKETS = [
    "fecha", "cliente", "contacto", "telefono", "lleva_envio", "num_productos",
    "subtotal_costo", "subtotal_venta", "costo_envio", "descuento",
    "total_final", "utilidad",
]
HEADERS_PRODUCTOS = [
    "fecha", "cliente", "contacto", "producto", "gramos", "costo", "venta",
]
# Gastos: bitácora independiente. NO se mezcla con tickets/productos ni
# afecta utilidad, análisis de clientes ni resultados. Es solo un registro
# de "le debo X a este contacto" para llevar control de reembolsos.
HEADERS_GASTOS = [
    "fecha", "contacto", "concepto", "monto", "pagado",
]


def _abrir_spreadsheet():
    """Abre el spreadsheet con permisos de escritura."""
    import gspread
    from google.oauth2.service_account import Credentials
    creds = Credentials.from_service_account_info(
        dict(st.secrets["gcp_service_account"]),
        scopes=["https://www.googleapis.com/auth/spreadsheets"],
    )
    client = gspread.authorize(creds)
    return client.open_by_url(SPREADSHEET_URL)


def _obtener_o_crear_pestaña(spreadsheet, nombre, headers):
    """Devuelve la pestaña; si no existe la crea con los headers indicados."""
    try:
        ws = spreadsheet.worksheet(nombre)
    except Exception:
        ws = spreadsheet.add_worksheet(title=nombre, rows=1000, cols=len(headers))
        ws.append_row(headers)
    return ws


def guardar_pedidos_en_historico(pedidos):
    """Apenda los pedidos al histórico en Google Sheets.
    Si el pedido tiene 'fecha_custom', se usa esa fecha. Si no, usa la fecha actual.
    Devuelve (tickets_guardados, productos_guardados) o (None, error_msg)."""
    if not pedidos:
        return 0, 0

    try:
        ss = _abrir_spreadsheet()
        ws_tickets = _obtener_o_crear_pestaña(ss, "HISTORICO_TICKETS", HEADERS_TICKETS)
        ws_productos = _obtener_o_crear_pestaña(ss, "HISTORICO_PRODUCTOS", HEADERS_PRODUCTOS)

        fecha_ahora = datetime.now(pytz.timezone(ZONA_HORARIA)).strftime("%Y-%m-%d %H:%M")

        filas_tickets = []
        filas_productos = []

        for p in pedidos:
            # Si el pedido fue marcado con fecha pasada, usar esa. Si no, usar ahora.
            fecha_p = p.get("fecha_custom", fecha_ahora)

            filas_tickets.append([
                fecha_p,
                p["cliente"],
                p["contacto"],
                p.get("telefono", ""),
                "Sí" if p["lleva_envio"] else "No",
                len(p["productos"]),
                round(p["subtotal_costo"], 2),
                round(p["subtotal_venta"], 2),
                round(p["costo_envio"], 2),
                round(p.get("descuento", 0.0), 2),
                round(p["nuevo_total"], 2),
                round(p["utilidad"], 2),
            ])

            for nombre, gramos, costo, venta in p["productos"]:
                filas_productos.append([
                    fecha_p,
                    p["cliente"],
                    p["contacto"],
                    nombre,
                    int(gramos),
                    round(costo, 2),
                    round(venta, 2),
                ])

        if filas_tickets:
            ws_tickets.append_rows(filas_tickets, value_input_option="RAW")
        if filas_productos:
            ws_productos.append_rows(filas_productos, value_input_option="RAW")

        return len(filas_tickets), len(filas_productos)
    except Exception as e:
        return None, str(e)


@st.cache_data(ttl=120, show_spinner="Cargando histórico...")
def cargar_historico():
    """Carga el histórico completo desde Google Sheets.
    Devuelve (df_tickets, df_productos) como pandas DataFrames."""

    def parsear_dinero(valor):
        """Parsea un valor monetario que puede venir en varios formatos:
        - float Python: 1154.42 → 1154.42
        - int Python: 1154 → 1154.0
        - string '1154.42' (punto decimal) → 1154.42
        - string '1,154.42' (coma miles + punto decimal) → 1154.42
        - string '$1,154.42' (con prefijo) → 1154.42
        - string '1154,42' (coma decimal estilo MX, sin separador miles) → 1154.42
        - vacío / None → 0.0
        """
        if valor is None or valor == "":
            return 0.0
        # Si ya es número, regresar directo
        if isinstance(valor, (int, float)):
            return float(valor)
        # Es string
        s = str(valor).replace("$", "").strip()
        if not s:
            return 0.0
        # Caso 1: contiene tanto coma como punto → coma es separador de miles
        if "," in s and "." in s:
            s = s.replace(",", "")
            try:
                return float(s)
            except ValueError:
                return 0.0
        # Caso 2: solo coma → es separador decimal estilo MX
        if "," in s and "." not in s:
            s = s.replace(",", ".")
            try:
                return float(s)
            except ValueError:
                return 0.0
        # Caso 3: solo punto o solo dígitos
        try:
            return float(s)
        except ValueError:
            return 0.0

    try:
        ss = _abrir_spreadsheet()

        def hoja_a_df(ws_name, headers_default):
            """Lee una pestaña como strings literales (sin parseo automático)."""
            try:
                ws = ss.worksheet(ws_name)
                valores = ws.get_all_values()
            except Exception:
                return pd.DataFrame(columns=headers_default)
            if not valores or len(valores) < 2:
                return pd.DataFrame(columns=headers_default)
            headers = valores[0]
            filas = valores[1:]
            return pd.DataFrame(filas, columns=headers)

        df_t = hoja_a_df("HISTORICO_TICKETS", HEADERS_TICKETS)
        df_p = hoja_a_df("HISTORICO_PRODUCTOS", HEADERS_PRODUCTOS)

        # Normalizar montos del histórico de tickets
        columnas_dinero_t = [
            "subtotal_costo", "subtotal_venta", "costo_envio",
            "descuento", "total_final", "utilidad",
        ]
        for col in columnas_dinero_t:
            if col in df_t.columns:
                df_t[col] = df_t[col].apply(parsear_dinero)

        # num_productos es entero
        if "num_productos" in df_t.columns:
            df_t["num_productos"] = pd.to_numeric(
                df_t["num_productos"], errors="coerce"
            ).fillna(0).astype(int)

        # Productos: costo y venta son dinero, gramos es entero
        for col in ["costo", "venta"]:
            if col in df_p.columns:
                df_p[col] = df_p[col].apply(parsear_dinero)
        if "gramos" in df_p.columns:
            df_p["gramos"] = pd.to_numeric(
                df_p["gramos"], errors="coerce"
            ).fillna(0).astype(int)

        if "fecha" in df_t.columns:
            df_t["fecha_dt"] = pd.to_datetime(df_t["fecha"], errors="coerce")
        if "fecha" in df_p.columns:
            df_p["fecha_dt"] = pd.to_datetime(df_p["fecha"], errors="coerce")

        return df_t, df_p
    except Exception as e:
        return None, str(e)



# ============================
# Cálculo de totales
# ============================
def calcular_totales(productos, lleva_envio, descuento=0.0):
    subtotal_costo = sum(p[2] for p in productos)
    subtotal_venta = sum(p[3] for p in productos)
    costo_envio = COSTO_ENVIO if lleva_envio else 0
    total_venta = subtotal_venta + costo_envio
    nuevo_total = total_venta - descuento
    utilidad = nuevo_total - subtotal_costo
    utilidad_pct = (utilidad / subtotal_costo * 100) if subtotal_costo > 0 else 0
    return {
        "subtotal_costo": subtotal_costo,
        "subtotal_venta": subtotal_venta,
        "costo_envio": costo_envio,
        "total_venta": total_venta,
        "nuevo_total": nuevo_total,
        "utilidad": utilidad,
        "utilidad_pct": utilidad_pct,
    }


# ============================
# Generación del ticket PNG (en memoria, sin escribir a disco)
# ============================
def generar_ticket_png(pedido):
    """Genera la imagen del ticket y devuelve los bytes PNG.
    Layout estilo Imagen 2: header centrado, 3 columnas (COSTO | PRODUCTO | VENTA),
    totales abajo a la derecha.
    """
    productos = pedido["productos"]
    totales = calcular_totales(productos, pedido["lleva_envio"], pedido["descuento"])

    # ---- Configuración de fuentes ----
    try:
        if FUENTE_REG and FUENTE_BOLD:
            f_titulo = ImageFont.truetype(FUENTE_BOLD, 16)
            f_header = ImageFont.truetype(FUENTE_BOLD, 13)
            f_reg = ImageFont.truetype(FUENTE_REG, 13)
            f_total_bold = ImageFont.truetype(FUENTE_BOLD, 14)
            f_compacta = ImageFont.truetype(FUENTE_REG, 10)  # para COSTO/DIF en zona estrecha
        else:
            raise IOError("Fuentes no encontradas")
    except (IOError, OSError):
        default = ImageFont.load_default()
        f_titulo = default
        f_header = default
        f_reg = default
        f_total_bold = default
        f_compacta = default

    # ---- Dimensiones ----
    ancho = 440
    margen_x = 20
    interlinea = 26
    interlinea_pequena = 22

    # Calcular altura dinámicamente
    alto = 50  # margen superior + título
    alto += interlinea  # fecha
    alto += interlinea  # PEDIDO
    alto += interlinea + 12  # CONTACTO + espacio antes de la línea
    alto += interlinea  # línea separadora
    alto += interlinea  # header columnas
    alto += 10  # espacio
    alto += len(productos) * interlinea  # productos
    alto += 18  # línea separadora
    alto += 70  # bloque compacto COSTO + DIF (5 líneas pequeñas)
    alto += interlinea  # Subtotal venta
    if totales["costo_envio"] > 0:
        alto += interlinea
    if pedido["descuento"] > 0:
        alto += interlinea
    alto += interlinea  # línea + total
    alto += 30  # GRACIAS
    alto += 20  # margen inferior

    # ---- Colores ----
    c_fondo = (255, 255, 255)
    c_titulo = (0, 0, 0)
    c_venta = (0, 102, 204)
    c_costo = (204, 0, 0)
    c_sec = (120, 120, 120)

    img = Image.new("RGB", (ancho, alto), c_fondo)
    draw = ImageDraw.Draw(img)

    # Helper: medir ancho de texto (compatible con varias versiones de PIL)
    def text_width(texto, fuente):
        try:
            bbox = draw.textbbox((0, 0), texto, font=fuente)
            return bbox[2] - bbox[0]
        except AttributeError:
            return draw.textlength(texto, font=fuente)

    # ---- Posiciones de columnas ----
    col_costo_x = margen_x  # alineado izquierda
    col_producto_x = 130  # alineado izquierda, cabe a partir de aquí
    col_venta_x = ancho - margen_x  # alineado derecha

    # ---- Encabezado ----
    # El centro de los textos para el cliente es la zona entre col_producto_x y borde derecho.
    # Esto es porque el ticket se recorta en col_producto_x (línea de corte): la izquierda
    # es info para el contacto/proveedor, la derecha es lo que ve el cliente.
    centro_cliente = (col_producto_x + (ancho - margen_x)) // 2

    y = 18
    titulo = "FRUTIVERDURA A DOMICILIO"
    tw = text_width(titulo, f_titulo)
    draw.text((centro_cliente - tw // 2, y), titulo, font=f_titulo, fill=c_titulo)
    y += interlinea + 4

    mx_time = datetime.now(pytz.timezone(ZONA_HORARIA))
    fecha = mx_time.strftime("%d/%m/%Y")
    tw = text_width(fecha, f_reg)
    draw.text((centro_cliente - tw // 2, y), fecha, font=f_reg, fill=c_titulo)
    y += interlinea + 4

    pedido_txt = f"PEDIDO : {pedido['cliente'].upper()}"
    tw = text_width(pedido_txt, f_reg)
    draw.text((ancho - margen_x - tw, y), pedido_txt, font=f_reg, fill=c_titulo)
    y += interlinea

    contacto_txt = f"CONTACTO : {pedido['contacto']} ({pedido['telefono']})"
    tw = text_width(contacto_txt, f_reg)
    draw.text((ancho - margen_x - tw, y), contacto_txt, font=f_reg, fill=c_titulo)
    y += interlinea + 4

    # Línea separadora
    draw.line([(margen_x, y), (ancho - margen_x, y)], fill=c_titulo, width=1)
    y += 12

    # Headers de columna
    draw.text((col_costo_x, y), "COSTO", font=f_header, fill=c_sec)
    draw.text((col_producto_x, y), "PRODUCTO", font=f_header, fill=c_titulo)
    venta_h = "VENTA"
    tw = text_width(venta_h, f_header)
    draw.text((col_venta_x - tw, y), venta_h, font=f_header, fill=c_titulo)
    y += interlinea

    # ---- Productos (ordenados por venta descendente) ----
    productos_ord = sorted(productos, key=lambda p: p[3], reverse=True)
    for nombre, gramos, costo, venta in productos_ord:
        # Costo (rojo, izquierda)
        draw.text((col_costo_x, y), f"${costo:,.2f}", font=f_reg, fill=c_costo)
        # Producto (negro, centro)
        draw.text(
            (col_producto_x, y),
            f"{nombre} {int(gramos)}g",
            font=f_reg,
            fill=c_titulo,
        )
        # Venta (azul, derecha)
        venta_txt = f"${venta:,.2f}"
        tw = text_width(venta_txt, f_reg)
        draw.text((col_venta_x - tw, y), venta_txt, font=f_reg, fill=c_venta)
        y += interlinea

    y += 4
    draw.line([(margen_x, y), (ancho - margen_x, y)], fill=c_titulo, width=1)
    y += 10

    # ---- Totales ----
    # Zona del PROVEEDOR (izquierda de la línea de corte en col_producto_x):
    # COSTO y DIF compactos, sobre dos líneas cada uno para no rebasar la línea
    # de corte. La imagen se recorta por col_producto_x al entregar al cliente.
    y_izq_inicio = y
    ancho_zona_izq = col_producto_x - col_costo_x - 8  # 8px de margen interno

    def dibujar_compacto_izq(y_local, label, valor, color):
        """Dibuja label y valor en la zona estrecha izquierda. Si no caben en una
        sola línea las pone en dos líneas."""
        linea_completa = f"{label} {valor}"
        if text_width(linea_completa, f_compacta) <= ancho_zona_izq:
            draw.text((col_costo_x, y_local), linea_completa, font=f_compacta, fill=color)
            return y_local + 14
        # No cabe: poner label arriba y valor abajo
        draw.text((col_costo_x, y_local), label, font=f_compacta, fill=color)
        draw.text((col_costo_x, y_local + 12), valor, font=f_compacta, fill=color)
        return y_local + 26

    y_izq = y_izq_inicio
    y_izq = dibujar_compacto_izq(
        y_izq, "COSTO:", f"${totales['subtotal_costo']:,.2f}", c_sec
    )
    y_izq = dibujar_compacto_izq(
        y_izq,
        "DIF:",
        f"${totales['utilidad']:,.2f} ({totales['utilidad_pct']:.1f}%)",
        c_sec,
    )

    # Zona del CLIENTE (derecha de la línea de corte): Subtotal / Envío / Descuento / TOTAL
    sub_txt = f"Subtotal Venta : ${totales['subtotal_venta']:,.2f}"
    tw = text_width(sub_txt, f_total_bold)
    draw.text((col_venta_x - tw, y), sub_txt, font=f_total_bold, fill=c_venta)
    y += interlinea

    if totales["costo_envio"] > 0:
        env_txt = f"Envio : ${totales['costo_envio']:,.2f}"
        tw = text_width(env_txt, f_reg)
        draw.text((col_venta_x - tw, y), env_txt, font=f_reg, fill=c_titulo)
        y += interlinea

    if pedido["descuento"] > 0:
        desc_txt = f"Descuento : -${pedido['descuento']:,.2f}"
        tw = text_width(desc_txt, f_reg)
        draw.text((col_venta_x - tw, y), desc_txt, font=f_reg, fill=c_sec)
        y += interlinea

    # Línea fina antes del total (solo en zona cliente)
    draw.line([(col_producto_x, y), (ancho - margen_x, y)], fill=c_titulo, width=1)
    y += 8

    total_txt = f"TOTAL : ${totales['nuevo_total']:,.2f}"
    tw = text_width(total_txt, f_total_bold)
    draw.text((col_venta_x - tw, y), total_txt, font=f_total_bold, fill=c_venta)
    y += interlinea + 8

    # Asegurar que el ticket no termine antes de que acabe la zona izquierda
    y = max(y, y_izq + 4)

    gracias = "GRACIAS POR TU COMPRA"
    tw = text_width(gracias, f_total_bold)
    # Centrado en zona cliente (entre col_producto_x y borde derecho), no en ancho total
    draw.text((centro_cliente - tw // 2, y), gracias, font=f_total_bold, fill=c_titulo)

    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


# ============================
# Sidebar: catálogo y status
# ============================
with st.sidebar:
    st.title("🥬 Frutiverdura")

    st.subheader("Catálogo")
    fuente = st.radio(
        "Fuente del catálogo",
        ["Google Sheets", "CSV subido"],
        help="Elige Google Sheets si tienes configurado el service account en secrets, o sube un CSV manualmente.",
    )

    if fuente == "Google Sheets":
        if st.button("🔄 Cargar / Recargar catálogo"):
            try:
                cargar_catalogo_gsheets.clear()
                p, c = cargar_catalogo_gsheets()
                st.session_state.precios_dict = p
                st.session_state.costos_dict = c
                st.success(f"✅ {len(p)} productos cargados")
            except Exception as e:
                st.error(f"Error: {e}")
                st.info("Revisa la configuración de secrets o sube un CSV como alternativa.")
    else:
        archivo_csv = st.file_uploader("Sube CSV con columnas: Producto, Costo, Precio", type=["csv"])
        if archivo_csv is not None:
            try:
                p, c = cargar_catalogo_csv(archivo_csv)
                st.session_state.precios_dict = p
                st.session_state.costos_dict = c
                st.success(f"✅ {len(p)} productos cargados")
            except Exception as e:
                st.error(f"Error: {e}")

    st.caption(f"Productos en catálogo: **{len(st.session_state.precios_dict)}**")

    st.divider()
    st.subheader("Sesión")
    st.metric("Tickets generados", len(st.session_state.pedidos))

    if st.button("🗑️ Limpiar sesión completa", type="secondary"):
        st.session_state.pedidos = []
        st.session_state.productos_actuales = []
        st.session_state.gastos_sesion = []
        st.session_state["historico_guardado_sesion"] = False
        st.rerun()


# ============================
# Main: tabs
# ============================
st.title("Frutiverdura a Domicilio")

if not st.session_state.precios_dict:
    st.warning("⚠️ Primero carga el catálogo desde el panel lateral.")
    st.stop()

tab_capturar, tab_pegar, tab_modificar, tab_resumen, tab_analisis, tab_mayoreo, tab_catalogo = st.tabs(
    ["📝 Capturar", "📋 Pegar pedido", "✏️ Modificar", "📊 Resumen y Descarga",
     "📈 Análisis de clientes", "🛒 Lista de compra", "🛍️ Catálogo"]
)


# ============================
# Tab 1: Capturar
# ============================
with tab_capturar:
    st.subheader("Nuevo ticket")

    col1, col2, col3 = st.columns([2, 2, 1])
    with col1:
        nombre_cliente = st.text_input("Nombre del cliente", key="in_cliente")
    with col2:
        nombre_contacto = st.selectbox("Contacto", list(CONTACTOS.keys()), key="in_contacto")
    with col3:
        lleva_envio = st.checkbox("Envío (+$35)", value=False, key="in_envio")

    st.divider()
    st.markdown("**Agregar productos**")

    productos_catalogo = sorted(st.session_state.precios_dict.keys())

    col_p, col_g, col_b = st.columns([3, 1, 1])
    with col_p:
        producto_sel = st.selectbox(
            "Producto",
            [""] + productos_catalogo,
            format_func=lambda x: x.title() if x else "-- selecciona --",
            key="in_producto",
        )
    with col_g:
        gramos = st.number_input("Gramos", min_value=0, value=500, step=50, key="in_gramos")
    with col_b:
        st.write("")
        st.write("")
        agregar = st.button("➕ Agregar", use_container_width=True)

    with st.expander("¿Producto no está en el catálogo? Agregar manualmente"):
        col_mp, col_mc, col_mv, col_mb = st.columns([2, 1, 1, 1])
        with col_mp:
            nuevo_nombre = st.text_input("Nombre", key="in_manual_nombre")
        with col_mc:
            nuevo_costo = st.number_input("Costo/kg", min_value=0.0, step=1.0, key="in_manual_costo")
        with col_mv:
            nuevo_precio = st.number_input("Precio/kg", min_value=0.0, step=1.0, key="in_manual_precio")
        with col_mb:
            st.write("")
            st.write("")
            if st.button("Agregar al catálogo", use_container_width=True):
                if nuevo_nombre.strip() and nuevo_costo > 0 and nuevo_precio > 0:
                    key = nuevo_nombre.strip().lower()
                    st.session_state.precios_dict[key] = {"precio_venta_kg": nuevo_precio}
                    st.session_state.costos_dict[key] = {"costo_kg": nuevo_costo}
                    st.success(f"✅ {key.title()} agregado al catálogo")
                    st.rerun()

    if agregar and producto_sel and gramos > 0:
        precio_kg = st.session_state.precios_dict[producto_sel]["precio_venta_kg"]
        costo_kg = st.session_state.costos_dict[producto_sel]["costo_kg"]
        precio_final = (gramos * precio_kg) / 1000
        costo_final = (gramos * costo_kg) / 1000

        st.session_state.productos_actuales.append(
            (producto_sel.title(), float(gramos), round(costo_final, 2), round(precio_final, 2))
        )
        st.success(f"✓ {producto_sel.title()} {gramos}g agregado")
        st.rerun()

    # Mostrar productos actuales
    if st.session_state.productos_actuales:
        st.divider()
        st.markdown("**Productos del ticket actual**")

        df_actual = pd.DataFrame(
            st.session_state.productos_actuales,
            columns=["Producto", "Gramos", "Costo", "Venta"],
        )
        df_actual["Gramos"] = df_actual["Gramos"].astype(int)
        df_actual["Costo"] = df_actual["Costo"].apply(lambda x: f"${x:,.2f}")
        df_actual["Venta"] = df_actual["Venta"].apply(lambda x: f"${x:,.2f}")
        st.dataframe(df_actual, use_container_width=True, hide_index=False)

        # Botones para quitar productos
        cols_quitar = st.columns(min(len(st.session_state.productos_actuales), 6))
        for i, prod in enumerate(st.session_state.productos_actuales):
            with cols_quitar[i % len(cols_quitar)]:
                if st.button(f"🗑️ {prod[0][:10]}", key=f"quit_{i}"):
                    st.session_state.productos_actuales.pop(i)
                    st.rerun()

        # Totales y descuento
        totales = calcular_totales(st.session_state.productos_actuales, lleva_envio)

        mc1, mc2, mc3, mc4 = st.columns(4)
        mc1.metric("Costo", f"${totales['subtotal_costo']:,.2f}")
        mc2.metric("Venta", f"${totales['subtotal_venta']:,.2f}")
        mc3.metric("Envío", f"${totales['costo_envio']:,.2f}")
        mc4.metric("Utilidad", f"${totales['utilidad']:,.2f}", f"{totales['utilidad_pct']:.1f}%")

        descuento = 0.0
        if len(st.session_state.productos_actuales) >= MIN_PRODUCTOS_DESCUENTO:
            util_min = totales["subtotal_costo"] * UTILIDAD_MINIMA_PCT
            max_desc = totales["utilidad"] - util_min
            if max_desc > 0:
                descuento = st.number_input(
                    f"Descuento (máx. ${max_desc:.2f})",
                    min_value=0.0,
                    max_value=float(max_desc),
                    value=0.0,
                    step=5.0,
                )

        totales_final = calcular_totales(st.session_state.productos_actuales, lleva_envio, descuento)
        st.markdown(f"### TOTAL: ${totales_final['nuevo_total']:,.2f}")

        col_gen, col_cancel = st.columns(2)
        with col_gen:
            if st.button("✅ Generar ticket", type="primary", use_container_width=True):
                if not nombre_cliente.strip():
                    st.error("Falta el nombre del cliente")
                else:
                    pedido = {
                        "cliente": nombre_cliente.strip(),
                        "contacto": nombre_contacto,
                        "telefono": CONTACTOS[nombre_contacto],
                        "lleva_envio": lleva_envio,
                        "descuento": descuento,
                        "productos": list(st.session_state.productos_actuales),
                        **totales_final,
                    }
                    st.session_state.pedidos.append(pedido)
                    st.session_state.productos_actuales = []
                    st.success(f"✅ Ticket de {pedido['cliente']} generado")
                    st.rerun()
        with col_cancel:
            if st.button("🚫 Cancelar ticket", use_container_width=True):
                st.session_state.productos_actuales = []
                st.rerun()

    # Vista previa de tickets generados en esta sesión
    if st.session_state.pedidos:
        st.divider()
        st.markdown(f"**Tickets generados: {len(st.session_state.pedidos)}**")
        ultimo = st.session_state.pedidos[-1]
        with st.expander(f"Ver último ticket: {ultimo['cliente']}"):
            png_bytes = generar_ticket_png(ultimo)
            st.image(png_bytes, width=400)


# ============================
# Tab 2: Pegar pedido (parseo masivo)
# ============================
import re
import difflib


def parsear_bloque_pedido(texto):
    """Parsea un bloque de texto y extrae (nombre_cliente, [(descripcion, gramos), ...]).

    Soporta formatos variados:
      Abue Lucero
      * 1k dominico (1130)
      * 6 manzanas golden (772)

    También:
      Tienda Tamp
      - [x] Caja huevo 17,100
      - [ ] Pollo 2,500

    Y mezclas. Las viñetas válidas son: *, -, •, [x], [ ], 1., 1), etc.
    Los gramos pueden venir entre () o como número final, con o sin coma de miles.
    """
    lineas = [l.strip() for l in texto.strip().split("\n") if l.strip()]
    if not lineas:
        return None, []

    # Regex para detectar viñetas, incluyendo checkboxes tipo Markdown [x] [ ] [X]
    patron_viñeta = r"^(?:[\*\-\u2022]|\d+[\.\)])\s*(?:\[\s*[xX\s]?\s*\]\s*)?"

    cliente = None
    productos_raw = []
    for linea in lineas:
        es_viñeta = bool(re.match(patron_viñeta, linea))
        if not es_viñeta and cliente is None:
            cliente = linea
            continue
        if not es_viñeta:
            # Línea sin viñeta después del cliente: la ignoramos
            continue

        # Quitar la viñeta y el checkbox
        contenido = re.sub(patron_viñeta, "", linea).strip()
        if not contenido:
            continue

        # Estrategia 1: gramos entre paréntesis al final, ej: "tomate (1500)"
        gramos = None
        m = re.search(r"\(\s*([\d.,]+)\s*\)\s*$", contenido)
        if m:
            num_str = m.group(1).replace(",", "").replace(".", "")
            if num_str.isdigit():
                gramos = int(num_str)
                contenido = re.sub(r"\s*\([\d.,\s]+\)\s*$", "", contenido)

        # Estrategia 2: gramos como número final sin paréntesis, ej: "Caja huevo 17,100"
        if gramos is None:
            m = re.search(r"([\d]{1,3}(?:[,.]\d{3})+|\d{3,6})\s*g?\s*$", contenido)
            if m:
                num_str = m.group(1).replace(",", "").replace(".", "")
                if num_str.isdigit():
                    posibles_gramos = int(num_str)
                    # Validar que sea un número razonable (10g a 100kg)
                    if 10 <= posibles_gramos <= 100000:
                        gramos = posibles_gramos
                        contenido = contenido[: m.start()].strip()

        if gramos is None or gramos <= 0:
            continue

        # Limpiar descripción: quitar precios ($X), emojis, normalizar espacios
        desc = re.sub(r"\$\s*[\d.,]+", "", contenido)
        desc = "".join(c for c in desc if c.isascii() or c.isalpha() or c.isspace() or c in "/.")
        desc = re.sub(r"\s+", " ", desc).strip()
        # Quitar puntos sueltos al final
        desc = re.sub(r"[\.\,]+$", "", desc).strip()

        if desc:
            productos_raw.append((desc, gramos))

    return cliente, productos_raw


# Aliases manuales: descripción literal o palabra clave -> nombre exacto en catálogo.
# Se aplican ANTES del matcher general. Las claves se buscan como palabra completa
# o como substring si tienen 4+ caracteres.
ALIASES_PRODUCTOS = {
    "papa": "papa blanca",
    "papas": "papa blanca",
    "tomate": "tomate",
    "tomates": "tomate",
    "jitomate bola": "jitomate bola",
    "jitomates bola": "jitomate bola",
    "jito bola": "jitomate bola",
    "jitomate cherry": "jitomate cherry",
    "jitomates cherry": "jitomate cherry",
    "jito cherry": "jitomate cherry",
    "cherry": "jitomate cherry",
    "cherrys": "jitomate cherry",
    "cherries": "jitomate cherry",
    "jitomate saladet": "jitomate saladet",
    "jitomates saladet": "jitomate saladet",
    "jito saladet": "jitomate saladet",
    "jito": "jitomate",
    "jitos": "jitomate",
    "jitomate": "jitomate",
    "jitomates": "jitomate",
    "gouda": "queso gouda",
    "panela": "queso panela",
    "oaxaca": "queso oaxaca",
    "manchego": "queso manchego",
    "caja huevo": "caja huevo",
    "caja de huevo": "caja huevo",
    "cajas de huevo": "caja huevo",
    "cajas huevo": "caja huevo",
    "huevo": "huevo",
    "huevos": "huevo",
    "cartón de huevo": "huevo",
    "cartones de huevo": "huevo",
    "papas fritas": "papas fritas",
    # Aliases en inglés / nombres alternativos
    "blue berries": "mora azul",
    "blueberries": "mora azul",
    "blueberry": "mora azul",
    "blue berry": "mora azul",
    "raspberry": "frambuesa",
    "raspberries": "frambuesa",
    "blackberry": "zarzamora",
    "blackberries": "zarzamora",
    "strawberry": "fresa",
    "strawberries": "fresa",
    # Variantes de manzana (golden = amarilla)
    "manzana golden": "manzana amarilla",
    "manzanas golden": "manzana amarilla",
    "golden": "manzana amarilla",
    "manzana red": "manzana roja",
    "manzanas red": "manzana roja",
    # Churritos / churros
    "churritos": "churros",
    "churrito": "churros",
    # Limón amarillo (eureka) vs limón verde común
    "limon amarillo": "limon eureka",
    "limones amarillos": "limon eureka",
    "limón amarillo": "limon eureka",
    "limones amarillos": "limon eureka",
    "limon eureka": "limon eureka",
    "eureka": "limon eureka",
    # Cebolla morada (separada de cebolla blanca)
    "cebolla morada": "cebolla morada",
    "cebollas moradas": "cebolla morada",
    "cebolla blanca": "cebolla blanca",
    "cebollas blancas": "cebolla blanca",
    # Productos empacados en bolsa (distintos del fresco a granel)
    "bolsa espinaca": "bolsa espinaca",
    "bolsa de espinaca": "bolsa espinaca",
    "bolsas de espinaca": "bolsa espinaca",
    "espinaca en bolsa": "bolsa espinaca",
    "espinacas en bolsa": "bolsa espinaca",
    "bolsa apio": "bolsa apio",
    "bolsa de apio": "bolsa apio",
    "bolsas de apio": "bolsa apio",
    "apio en bolsa": "bolsa apio",
    "apios en bolsa": "bolsa apio",
    "bolsa lechuga": "bolsa lechuga",
    "bolsa de lechuga": "bolsa lechuga",
    "bolsas de lechuga": "bolsa lechuga",
    "lechuga en bolsa": "bolsa lechuga",
    "lechugas en bolsa": "bolsa lechuga",
}

# Palabras que cuando aparecen indican que el producto viene EN BOLSA o EMPACADO,
# lo cual lo hace un producto distinto al fresco/granel. La app no asigna alias
# automático en estos casos: tú decides en la vista previa si crear nuevo o mapear.
INDICADORES_EMPAQUE = [
    "en bolsa",
    "en bolsita",
    "en bolsitas",
    "bolsa de",
    "bolsas de",
    "bolsita de",
    "bolsitas de",
    "empacado",
    "empacada",
    "envasado",
    "envasada",
]


def tiene_indicador_empaque(descripcion):
    """Devuelve True si la descripción menciona bolsa/empaque,
    sugiriendo que es un producto distinto al fresco."""
    desc = descripcion.lower()
    return any(ind in desc for ind in INDICADORES_EMPAQUE)


# Productos base que tienen una versión "bolsa <producto>" en el catálogo.
# Si la descripción contiene "bolsa" Y uno de estos productos (en cualquier orden,
# por ejemplo "espinaca bolsa", "1 bolsa espinaca", "2 bolsas de apio", "apio en bolsa"),
# se mapea a "bolsa <producto>".
PRODUCTOS_CON_VERSION_BOLSA = ["espinaca", "apio", "lechuga"]


def detectar_bolsa_flexible(descripcion, catalogo_keys):
    """Si la descripción contiene la palabra 'bolsa' (o variantes) Y alguno de los
    productos base, devuelve 'bolsa <producto>' si está en el catálogo.
    Detecta cualquier orden: 'bolsa espinaca', 'espinaca bolsa', 'espinaca en bolsa',
    'bolsa de espinaca', '2 bolsas de apio', etc."""
    desc = descripcion.lower()
    # Buscar palabras tipo "bolsa", "bolsas", "bolsita", "bolsitas"
    palabras = desc.split()
    tiene_bolsa = any(
        p.rstrip(".,;:").startswith("bolsa") or p.rstrip(".,;:").startswith("bolsit")
        for p in palabras
    )
    if not tiene_bolsa:
        return None

    # Buscar si alguno de los productos base aparece en la descripción
    for base in PRODUCTOS_CON_VERSION_BOLSA:
        # Aceptar singular y plural simple
        if base in desc or (base + "s") in desc:
            target = f"bolsa {base}"
            if target in catalogo_keys:
                return target

    return None


def aplicar_alias(descripcion, catalogo_keys):
    """Si la descripción contiene un alias, devuelve el match directo.
    Si un alias multi-palabra coincide con la descripción pero su target NO está
    en el catálogo, devuelve None en vez de caer a aliases de una sola palabra
    (esto evita que 'papas fritas' caiga a 'papas'→'papa blanca' cuando el
    catálogo no tiene 'papas fritas' registrado: mejor que aparezca sin match
    para que el usuario lo cree como producto nuevo)."""
    desc = descripcion.lower().strip()
    palabras = set(desc.split())

    # 1. PRIMERO: aliases multi-palabra (más específicos)
    for alias, target in ALIASES_PRODUCTOS.items():
        if " " in alias and alias in desc:
            if target in catalogo_keys:
                return target
            # El alias coincidió pero el target no está en catálogo:
            # NO caigas a aliases de una sola palabra que darían resultado erróneo.
            return None

    # 2. DESPUÉS: aliases de una sola palabra (solo si ningún alias multi-palabra coincidió)
    for alias, target in ALIASES_PRODUCTOS.items():
        if " " not in alias and alias in palabras:
            if target in catalogo_keys:
                return target

    return None


def buscar_match_catalogo(descripcion, catalogo_keys, umbral=0.7):
    """Busca el mejor match. Prioriza coincidencia de la PRIMERA palabra significativa.

    Orden de prioridad:
    1. Detección flexible de "bolsa" (espinaca/apio/lechuga en cualquier orden).
    2. Aliases manuales.
    3. Si hay indicador de empaque sin match específico, retorna None.
    4. Matching general por palabras y difflib.
    """
    # 1. PRIORIDAD: detectar "bolsa" + producto base en cualquier orden
    bolsa_match = detectar_bolsa_flexible(descripcion, catalogo_keys)
    if bolsa_match:
        return bolsa_match

    # 2. Intentar aliases manuales
    alias_match = aplicar_alias(descripcion, catalogo_keys)
    if alias_match:
        return alias_match

    # 3. Si menciona empaque pero no había match específico, forzar revisión manual
    if tiene_indicador_empaque(descripcion):
        return None

    desc = descripcion.lower().strip()
    desc = re.sub(r"^[\d\.\,/]+\s*(kg|kilo|kilos|k|gr|gramos|g|pz|pza|piezas|pieza|domo|domos|cabeza|manojo|ramo|ramos|penca|cartón|cartones|carton|bolsa|de)\s*", "", desc)
    desc = re.sub(r"^[\d\.\,/]+\s+", "", desc)
    desc = re.sub(r"^de\s+", "", desc)  # "de plátano" -> "plátano"

    # Quitar adjetivos de tamaño/cualidad que no son parte del nombre del producto
    palabras_adjetivos = {
        "mediana", "medianas", "mediano", "medianos",
        "chica", "chicas", "chico", "chicos", "pequeña", "pequeño", "pequeñas", "pequeños",
        "grande", "grandes",
        "mini", "minis",
        "extra",
        "fresca", "fresco", "frescas", "frescos",
        "madura", "maduro", "maduras", "maduros",
        "verde", "verdes",  # solo como adjetivo, no como variedad (manzana verde es alias propio)
    }
    palabras_filtradas = [p for p in desc.split() if p not in palabras_adjetivos]
    desc = " ".join(palabras_filtradas) if palabras_filtradas else desc

    palabras = desc.split()
    palabras_norm = []
    for p in palabras:
        if p.endswith("es") and len(p) > 4:
            palabras_norm.append(p[:-2])
        elif p.endswith("s") and len(p) > 3:
            palabras_norm.append(p[:-1])
        else:
            palabras_norm.append(p)

    # La primera palabra significativa es la más importante (define qué es)
    primera = palabras_norm[0] if palabras_norm else ""

    mejor = None
    mejor_score = 0
    for key in catalogo_keys:
        key_palabras = key.lower().split()
        key_primera = key_palabras[0]

        # Score base: cuántas palabras del catálogo aparecen
        compartidas = 0
        for kp in key_palabras:
            for p in palabras_norm:
                if kp == p:
                    compartidas += 2  # match exacto pesa más
                    break
                elif (len(kp) > 3 and kp in p) or (len(p) > 3 and p in kp):
                    compartidas += 1
                    break

        if compartidas == 0:
            continue

        score = compartidas / len(key_palabras)

        # BONO grande si la primera palabra del catálogo coincide con la primera significativa de la descripción
        if primera and (primera == key_primera or
                        (len(primera) > 3 and primera in key_primera) or
                        (len(key_primera) > 3 and key_primera in primera)):
            score += 2.0

        # BONO si TODAS las palabras del catálogo aparecen
        if compartidas >= len(key_palabras) * 2:
            score += 0.5

        if score > mejor_score:
            mejor_score = score
            mejor = key

    if mejor and mejor_score >= 0.8:
        return mejor

    matches = difflib.get_close_matches(desc, catalogo_keys, n=1, cutoff=umbral)
    if matches:
        return matches[0]

    for p in palabras_norm:
        if len(p) < 3:
            continue
        matches = difflib.get_close_matches(p, catalogo_keys, n=1, cutoff=0.6)
        if matches:
            return matches[0]
    return None


with tab_pegar:
    # Sufijo dinámico para los keys de los widgets. Al incrementar el contador
    # (después de generar tickets), los widgets se reinstancian vacíos.
    rk = st.session_state.pegar_reset_count

    st.subheader("Pegar pedido")
    st.caption(
        "Pega solo la lista de productos. El nombre del cliente y el contacto los pones aquí arriba. "
        "Si pegas varios clientes, sepáralos con línea en blanco y pon el nombre arriba de cada lista."
    )

    # Selector de fecha del pedido (para registrar pedidos pasados al histórico)
    col_f1, col_f2 = st.columns([1, 2])
    with col_f1:
        opcion_fecha = st.radio(
            "Fecha del pedido",
            ["Hoy", "Fecha anterior"],
            horizontal=True,
            key="opcion_fecha_pedido",
        )
    with col_f2:
        hoy_mx = datetime.now(pytz.timezone(ZONA_HORARIA)).date()
        if opcion_fecha == "Fecha anterior":
            fecha_pedido = st.date_input(
                "Selecciona la fecha",
                value=hoy_mx,
                max_value=hoy_mx,
                key="fecha_pedido_custom",
            )
            st.caption("⚠️ Los tickets se guardarán con esta fecha en el histórico.")
        else:
            fecha_pedido = hoy_mx
            st.caption(f"📅 Usando fecha actual: {hoy_mx.strftime('%d/%m/%Y')}")

    # Campos rápidos arriba
    col_n, col_c, col_e = st.columns([2, 1, 1])
    with col_n:
        nombre_rapido = st.text_input(
            "Nombre del cliente (si pegas un solo pedido)",
            key=f"rapido_cliente_{rk}",
            placeholder="ej: Abue Lucero",
        )
    with col_c:
        contacto_default = st.selectbox(
            "Contacto",
            list(CONTACTOS.keys()),
            key="pegar_contacto",
        )
    with col_e:
        envio_rapido = st.checkbox("Lleva envío", key=f"rapido_envio_{rk}", value=False)

    ejemplo_simple = """* 1k dominico 🍏 (1130)
* 6 manzanas golden 🍏 (772)
* 4 zanahorias 🧑🏿‍🌾 (529)"""

    ejemplo_multi = """Abue Lucero
* 1k dominico (1130)
* 4 zanahorias (529)

Laura Canales
* 2 Kg Limón (2029)
* 1 Kg Jitomate (1025)"""

    with st.expander("Ver formatos aceptados"):
        st.markdown("**Un solo pedido (escribe el nombre arriba):**")
        st.code(ejemplo_simple, language="text")
        st.markdown("**Varios pedidos (nombres dentro del texto):**")
        st.code(ejemplo_multi, language="text")

    texto_pegado = st.text_area(
        "Pega aquí",
        height=300,
        placeholder=ejemplo_simple,
        key=f"texto_pegado_{rk}",
    )

    clientes_con_envio = st.text_input(
        "Si pegaste varios pedidos, lista aquí los clientes con envío (separados por coma)",
        placeholder="ej: Laura, Macry",
        key=f"pegar_envio_multi_{rk}",
    )

    # Auto-procesar en cuanto haya texto suficiente
    procesar = st.button("🔍 Procesar pedido(s)", type="primary")

    if procesar:
        if not texto_pegado.strip():
            st.warning("Pega al menos un pedido.")
        else:
            texto = texto_pegado.strip()
            envios_set = {
                e.strip().lower() for e in clientes_con_envio.split(",") if e.strip()
            }
            catalogo_keys = list(st.session_state.precios_dict.keys())
            preview = []

            # Detectar formato: ¿empieza con viñeta? -> es un solo cliente y el nombre está arriba
            primera_linea = texto.split("\n", 1)[0].strip()
            es_pedido_unico = bool(re.match(r"^(?:[\*\-\u2022]|\d+[\.\)])\s*(?:\[\s*[xX\s]?\s*\]\s*)?", primera_linea))

            if es_pedido_unico:
                # Modo simple: un solo cliente, nombre del campo de arriba
                cliente_final = nombre_rapido.strip() or "Cliente"
                # Inyectar el nombre al inicio para que el parser funcione igual
                bloque = f"{cliente_final}\n{texto}"
                cliente, productos_raw = parsear_bloque_pedido(bloque)
                if cliente and productos_raw:
                    productos_match = []
                    for desc, gramos in productos_raw:
                        match = buscar_match_catalogo(desc, catalogo_keys)
                        productos_match.append({
                            "descripcion_original": desc,
                            "gramos": gramos,
                            "match": match,
                        })
                    preview.append({
                        "cliente": cliente,
                        "lleva_envio": envio_rapido,
                        "productos": productos_match,
                    })
            else:
                # Modo multi: separar por línea en blanco, primera línea de cada bloque es el nombre
                bloques = [b for b in re.split(r"\n\s*\n", texto) if b.strip()]
                for bloque in bloques:
                    cliente, productos_raw = parsear_bloque_pedido(bloque)
                    if not cliente or not productos_raw:
                        continue
                    lleva_envio = any(e in cliente.lower() for e in envios_set)
                    productos_match = []
                    for desc, gramos in productos_raw:
                        match = buscar_match_catalogo(desc, catalogo_keys)
                        productos_match.append({
                            "descripcion_original": desc,
                            "gramos": gramos,
                            "match": match,
                        })
                    preview.append({
                        "cliente": cliente,
                        "lleva_envio": lleva_envio,
                        "productos": productos_match,
                    })

            if not preview:
                st.error("No se pudieron detectar productos. Revisa que cada línea termine con (gramos).")
            else:
                st.session_state["preview_pedidos"] = preview
                # Si TODO matcheó perfecto, mostrar mensaje verde
                total_prods = sum(len(p["productos"]) for p in preview)
                no_match = sum(
                    1 for p in preview for prod in p["productos"] if not prod["match"]
                )
                if no_match == 0:
                    st.success(
                        f"✅ {total_prods} productos detectados, todos con match. "
                        "Revisa abajo y dale 'Generar' si todo está bien."
                    )
                else:
                    st.warning(
                        f"⚠️ {total_prods - no_match}/{total_prods} productos con match. "
                        f"Faltan {no_match} por asignar (corrige abajo)."
                    )

    # Mostrar y editar preview
    if "preview_pedidos" in st.session_state and st.session_state["preview_pedidos"]:
        st.divider()
        st.markdown("### Vista previa - corrige los matches incorrectos")
        st.caption(
            "Si algún producto quedó mal asignado, cámbialo del dropdown. "
            "Los productos sin match (rojo) se omitirán al generar el ticket."
        )

        catalogo_keys = sorted(st.session_state.precios_dict.keys())

        for i, ped in enumerate(st.session_state["preview_pedidos"]):
            no_match = sum(1 for p in ped["productos"] if not p["match"])
            con_match = len(ped["productos"]) - no_match

            label = f"**{ped['cliente']}** — {con_match} productos OK"
            if no_match > 0:
                label += f", ⚠️ {no_match} sin match"
            if ped["lleva_envio"]:
                label += " 🚚 envío"

            with st.expander(label, expanded=(no_match > 0)):
                # Toggle de envío
                ped["lleva_envio"] = st.checkbox(
                    "Lleva envío",
                    value=ped["lleva_envio"],
                    key=f"prev_envio_{i}",
                )

                for j, prod in enumerate(ped["productos"]):
                    col1, col2, col3 = st.columns([3, 3, 1])
                    with col1:
                        etiqueta = f"{prod['descripcion_original']} ({int(prod['gramos'])}g)"
                        if tiene_indicador_empaque(prod['descripcion_original']):
                            etiqueta = "📦 " + etiqueta
                        st.text(etiqueta)
                    with col2:
                        opciones = ["(omitir)", "➕ Crear nuevo producto"] + catalogo_keys
                        idx_default = (
                            opciones.index(prod["match"])
                            if prod["match"] in opciones
                            else 0
                        )
                        nuevo_match = st.selectbox(
                            "Mapear a",
                            opciones,
                            index=idx_default,
                            key=f"prev_match_{i}_{j}",
                            label_visibility="collapsed",
                        )
                        if nuevo_match == "(omitir)":
                            prod["match"] = None
                        elif nuevo_match == "➕ Crear nuevo producto":
                            prod["match"] = "__nuevo__"
                        else:
                            prod["match"] = nuevo_match
                    with col3:
                        nuevos_g = st.number_input(
                            "g",
                            min_value=0,
                            value=int(prod["gramos"]),
                            step=10,
                            key=f"prev_gr_{i}_{j}",
                            label_visibility="collapsed",
                        )
                        prod["gramos"] = nuevos_g

                    # Si el usuario eligió "Crear nuevo producto", mostrar formulario
                    if prod["match"] == "__nuevo__":
                        sub1, sub2, sub3 = st.columns([2, 1, 1])
                        with sub1:
                            sugerencia = re.sub(
                                r"^[\d\.\,/]+\s*\w*\s*", "", prod["descripcion_original"].lower()
                            ).strip()
                            nuevo_nombre = st.text_input(
                                "Nombre del producto",
                                value=sugerencia,
                                key=f"new_name_{i}_{j}",
                            )
                        with sub2:
                            nuevo_costo = st.number_input(
                                "Costo/kg",
                                min_value=0.0,
                                step=1.0,
                                key=f"new_costo_{i}_{j}",
                            )
                        with sub3:
                            nuevo_precio = st.number_input(
                                "Precio/kg",
                                min_value=0.0,
                                step=1.0,
                                key=f"new_precio_{i}_{j}",
                            )
                        if st.button("Guardar nuevo", key=f"save_new_{i}_{j}"):
                            if nuevo_nombre.strip() and nuevo_costo > 0 and nuevo_precio > 0:
                                key = nuevo_nombre.strip().lower()
                                st.session_state.precios_dict[key] = {"precio_venta_kg": nuevo_precio}
                                st.session_state.costos_dict[key] = {"costo_kg": nuevo_costo}
                                prod["match"] = key
                                st.success(f"✅ {key.title()} agregado al catálogo")
                                st.rerun()
                            else:
                                st.error("Completa nombre, costo y precio")

                # ---- Descuento (solo si tiene MIN_PRODUCTOS_DESCUENTO+ productos válidos) ----
                productos_validos = [
                    p for p in ped["productos"]
                    if p["match"]
                    and p["match"] != "__nuevo__"
                    and p["match"] in st.session_state.precios_dict
                    and p["gramos"] > 0
                ]

                if len(productos_validos) >= MIN_PRODUCTOS_DESCUENTO:
                    # Calcular subtotales con productos válidos para saber el descuento máximo
                    subtotal_costo_prev = sum(
                        (p["gramos"] * st.session_state.costos_dict[p["match"]]["costo_kg"]) / 1000
                        for p in productos_validos
                    )
                    subtotal_venta_prev = sum(
                        (p["gramos"] * st.session_state.precios_dict[p["match"]]["precio_venta_kg"]) / 1000
                        for p in productos_validos
                    )
                    costo_envio_prev = COSTO_ENVIO if ped["lleva_envio"] else 0
                    utilidad_prev = subtotal_venta_prev + costo_envio_prev - subtotal_costo_prev
                    util_min = subtotal_costo_prev * UTILIDAD_MINIMA_PCT
                    max_descuento = utilidad_prev - util_min

                    if max_descuento > 0:
                        st.markdown(
                            f"💰 Elegible a descuento ({len(productos_validos)} productos). "
                            f"Máximo aplicable: **${max_descuento:,.2f}**"
                        )
                        ped["descuento"] = st.number_input(
                            "Descuento a aplicar",
                            min_value=0.0,
                            max_value=float(max_descuento),
                            value=float(ped.get("descuento", 0.0)) if ped.get("descuento", 0.0) <= max_descuento else 0.0,
                            step=5.0,
                            key=f"prev_desc_{i}",
                        )
                    else:
                        ped["descuento"] = 0.0
                else:
                    # No elegible: ni se muestra ni se aplica
                    ped["descuento"] = 0.0

        st.divider()

        # Detectar productos sin asignar ANTES de mostrar botón de generar
        productos_sin_asignar = []
        for ped in st.session_state["preview_pedidos"]:
            for prod in ped["productos"]:
                if (not prod["match"]
                    or prod["match"] == "__nuevo__"
                    or prod["match"] not in st.session_state.precios_dict):
                    productos_sin_asignar.append({
                        "cliente": ped["cliente"],
                        "descripcion": prod["descripcion_original"],
                        "gramos": prod["gramos"],
                    })

        if productos_sin_asignar:
            st.error(
                f"⚠️ Hay {len(productos_sin_asignar)} productos sin asignar. "
                "Si generas los tickets ahora, esos productos NO se cobrarán al cliente. "
                "Asigna cada uno a un producto del catálogo o créalo como nuevo."
            )
            with st.expander("Ver productos sin asignar", expanded=True):
                for sa in productos_sin_asignar:
                    st.markdown(
                        f"- **{sa['cliente']}**: `{sa['descripcion']}` ({int(sa['gramos'])}g)"
                    )
            forzar_generar = st.checkbox(
                "Sé que faltan productos por asignar y aún así quiero generar los tickets "
                "(los productos sin asignar NO se cobrarán)",
                key="forzar_generar",
            )
        else:
            forzar_generar = True

        col_g1, col_g2 = st.columns(2)
        with col_g1:
            puede_generar = (not productos_sin_asignar) or forzar_generar
            if st.button(
                "✅ Generar todos los tickets",
                type="primary",
                use_container_width=True,
                disabled=not puede_generar,
            ):
                generados = 0
                for ped in st.session_state["preview_pedidos"]:
                    productos_finales = []
                    for prod in ped["productos"]:
                        if not prod["match"] or prod["match"] == "__nuevo__" or prod["gramos"] <= 0:
                            continue
                        if prod["match"] not in st.session_state.precios_dict:
                            continue
                        precio_kg = st.session_state.precios_dict[prod["match"]]["precio_venta_kg"]
                        costo_kg = st.session_state.costos_dict[prod["match"]]["costo_kg"]
                        precio_final = (prod["gramos"] * precio_kg) / 1000
                        costo_final = (prod["gramos"] * costo_kg) / 1000
                        productos_finales.append((
                            prod["match"].title(),
                            float(prod["gramos"]),
                            round(costo_final, 2),
                            round(precio_final, 2),
                        ))

                    if not productos_finales:
                        continue

                    descuento_aplicar = float(ped.get("descuento", 0.0))
                    totales = calcular_totales(productos_finales, ped["lleva_envio"], descuento_aplicar)
                    pedido = {
                        "cliente": ped["cliente"],
                        "contacto": contacto_default,
                        "telefono": CONTACTOS[contacto_default],
                        "lleva_envio": ped["lleva_envio"],
                        "descuento": descuento_aplicar,
                        "productos": productos_finales,
                        "fecha_custom": fecha_pedido.strftime("%Y-%m-%d %H:%M"),
                        **totales,
                    }
                    st.session_state.pedidos.append(pedido)
                    generados += 1

                del st.session_state["preview_pedidos"]
                # Forzar reset de los widgets del tab Pegar incrementando el contador.
                # Esto cambia los keys de los widgets, así que se recrean vacíos.
                if generados > 0:
                    st.session_state.pegar_reset_count += 1
                msg = f"✅ Se generaron {generados} tickets."
                if productos_sin_asignar:
                    msg += f" ⚠️ {len(productos_sin_asignar)} productos quedaron sin cobrar."
                st.success(msg)
                st.rerun()
        with col_g2:
            if st.button("🚫 Descartar vista previa", use_container_width=True):
                del st.session_state["preview_pedidos"]
                st.rerun()


# ============================
# Tab 3: Modificar
# ============================
with tab_modificar:
    if not st.session_state.pedidos:
        st.info("Aún no hay tickets para modificar.")
    else:
        st.subheader("Modificar ticket existente")

        idx_sel = st.selectbox(
            "Selecciona el ticket",
            range(len(st.session_state.pedidos)),
            format_func=lambda i: (
                f"{i+1}. {st.session_state.pedidos[i]['cliente']} "
                f"({st.session_state.pedidos[i]['contacto']}) - "
                f"${st.session_state.pedidos[i]['nuevo_total']:,.2f}"
            ),
        )

        pedido = st.session_state.pedidos[idx_sel]

        col_vista, col_edit = st.columns([1, 1])

        with col_vista:
            st.markdown("**Vista actual**")
            png_bytes = generar_ticket_png(pedido)
            st.image(png_bytes, width=380)

        with col_edit:
            st.markdown("**Acciones**")

            # Cambiar contacto asignado
            contactos_list = list(CONTACTOS.keys())
            idx_contacto_actual = (
                contactos_list.index(pedido["contacto"])
                if pedido["contacto"] in contactos_list else 0
            )
            nuevo_contacto = st.selectbox(
                "Contacto asignado",
                contactos_list,
                index=idx_contacto_actual,
                key=f"contacto_mod_{idx_sel}",
            )
            if nuevo_contacto != pedido["contacto"]:
                pedido["contacto"] = nuevo_contacto
                pedido["telefono"] = CONTACTOS[nuevo_contacto]
                st.rerun()

            # Cambiar envío
            nuevo_envio = st.checkbox(
                "Lleva envío (+$35)", value=pedido["lleva_envio"], key=f"envio_{idx_sel}"
            )
            if nuevo_envio != pedido["lleva_envio"]:
                pedido["lleva_envio"] = nuevo_envio
                totales = calcular_totales(pedido["productos"], pedido["lleva_envio"], 0.0)
                pedido.update(totales)
                pedido["descuento"] = 0.0
                st.rerun()

            st.markdown("**Productos del ticket**")
            if pedido["productos"]:
                for i, (nombre, gr, cto, vta) in enumerate(pedido["productos"]):
                    col_p, col_g, col_b = st.columns([3, 2, 1])
                    col_p.write(f"{nombre}")
                    with col_g:
                        nuevos_gr = st.number_input(
                            "g",
                            min_value=0,
                            value=int(gr),
                            step=50,
                            key=f"gr_{idx_sel}_{i}",
                            label_visibility="collapsed",
                        )
                    with col_b:
                        if st.button("🗑️", key=f"del_{idx_sel}_{i}"):
                            pedido["productos"].pop(i)
                            totales = calcular_totales(pedido["productos"], pedido["lleva_envio"], 0.0)
                            pedido.update(totales)
                            pedido["descuento"] = 0.0
                            if not pedido["productos"]:
                                st.session_state.pedidos.pop(idx_sel)
                            st.rerun()

                    if nuevos_gr != int(gr) and nuevos_gr > 0:
                        key = nombre.lower()
                        if key in st.session_state.precios_dict:
                            precio_kg = st.session_state.precios_dict[key]["precio_venta_kg"]
                            costo_kg = st.session_state.costos_dict[key]["costo_kg"]
                            pedido["productos"][i] = (
                                nombre,
                                float(nuevos_gr),
                                round(nuevos_gr * costo_kg / 1000, 2),
                                round(nuevos_gr * precio_kg / 1000, 2),
                            )
                            totales = calcular_totales(pedido["productos"], pedido["lleva_envio"], 0.0)
                            pedido.update(totales)
                            pedido["descuento"] = 0.0
                            st.rerun()

            # Agregar producto
            st.markdown("**Agregar producto**")
            col_np, col_ng, col_nb = st.columns([3, 1, 1])
            with col_np:
                opciones_prod = [""] + ["➕ Crear nuevo producto"] + sorted(st.session_state.precios_dict.keys())
                nuevo_prod = st.selectbox(
                    "Producto",
                    opciones_prod,
                    format_func=lambda x: x.title() if x and not x.startswith("➕") else (x or "-- selecciona --"),
                    key=f"nuevo_prod_{idx_sel}",
                    label_visibility="collapsed",
                )
            with col_ng:
                nuevos_g = st.number_input(
                    "g",
                    min_value=0,
                    value=500,
                    step=50,
                    key=f"nuevo_g_{idx_sel}",
                    label_visibility="collapsed",
                )
            with col_nb:
                if st.button("➕", key=f"add_{idx_sel}"):
                    if nuevo_prod and nuevo_prod != "➕ Crear nuevo producto" and nuevos_g > 0:
                        precio_kg = st.session_state.precios_dict[nuevo_prod]["precio_venta_kg"]
                        costo_kg = st.session_state.costos_dict[nuevo_prod]["costo_kg"]
                        pedido["productos"].append(
                            (
                                nuevo_prod.title(),
                                float(nuevos_g),
                                round(nuevos_g * costo_kg / 1000, 2),
                                round(nuevos_g * precio_kg / 1000, 2),
                            )
                        )
                        totales = calcular_totales(pedido["productos"], pedido["lleva_envio"], 0.0)
                        pedido.update(totales)
                        pedido["descuento"] = 0.0
                        st.rerun()

            # Si eligió "Crear nuevo producto", mostrar formulario inline
            if nuevo_prod == "➕ Crear nuevo producto":
                st.markdown("**Crear producto nuevo y agregarlo al ticket:**")
                sub_n, sub_c, sub_v, sub_g, sub_btn = st.columns([2, 1, 1, 1, 1])
                with sub_n:
                    nuevo_nombre = st.text_input(
                        "Nombre",
                        key=f"new_name_mod_{idx_sel}",
                        placeholder="ej: papas fritas",
                    )
                with sub_c:
                    nuevo_costo_kg = st.number_input(
                        "Costo/kg",
                        min_value=0.0,
                        step=1.0,
                        key=f"new_costo_mod_{idx_sel}",
                    )
                with sub_v:
                    nuevo_precio_kg = st.number_input(
                        "Precio/kg",
                        min_value=0.0,
                        step=1.0,
                        key=f"new_precio_mod_{idx_sel}",
                    )
                with sub_g:
                    nuevo_g_mod = st.number_input(
                        "Gramos",
                        min_value=0,
                        value=500,
                        step=50,
                        key=f"new_g_mod_{idx_sel}",
                    )
                with sub_btn:
                    st.write("")
                    st.write("")
                    if st.button("Guardar", key=f"save_new_mod_{idx_sel}", use_container_width=True):
                        if (nuevo_nombre.strip() and nuevo_costo_kg > 0
                                and nuevo_precio_kg > 0 and nuevo_g_mod > 0):
                            key = nuevo_nombre.strip().lower()
                            # Agregar al catálogo en sesión
                            st.session_state.precios_dict[key] = {"precio_venta_kg": nuevo_precio_kg}
                            st.session_state.costos_dict[key] = {"costo_kg": nuevo_costo_kg}
                            # Agregar al ticket
                            pedido["productos"].append((
                                key.title(),
                                float(nuevo_g_mod),
                                round(nuevo_g_mod * nuevo_costo_kg / 1000, 2),
                                round(nuevo_g_mod * nuevo_precio_kg / 1000, 2),
                            ))
                            totales = calcular_totales(pedido["productos"], pedido["lleva_envio"], 0.0)
                            pedido.update(totales)
                            pedido["descuento"] = 0.0
                            st.success(f"✅ {key.title()} creado y agregado al ticket")
                            st.rerun()
                        else:
                            st.error("Completa todos los campos (nombre, costo, precio, gramos)")

            st.divider()
            if st.button("🗑️ Eliminar ticket completo", key=f"del_ticket_{idx_sel}"):
                st.session_state.pedidos.pop(idx_sel)
                st.rerun()


# ============================
# Tab 4: Resumen
# ============================
with tab_resumen:
    pedidos = st.session_state.pedidos
    if not pedidos:
        st.info("Aún no hay tickets generados.")
    else:
        st.subheader("Resumen global de la sesión")

        total_costo = sum(p["subtotal_costo"] for p in pedidos)
        total_venta_bruta = sum(p["subtotal_venta"] for p in pedidos)
        total_envios = sum(p["costo_envio"] for p in pedidos)
        total_descuentos = sum(p["descuento"] for p in pedidos)
        total_cobrado = sum(p["nuevo_total"] for p in pedidos)
        utilidad_total = sum(p["utilidad"] for p in pedidos)
        utilidad_pct = (utilidad_total / total_costo * 100) if total_costo > 0 else 0

        c1, c2, c3, c4 = st.columns(4)
        c1.metric("Tickets", len(pedidos))
        c2.metric("Costo total", f"${total_costo:,.2f}")
        c3.metric("Total cobrado", f"${total_cobrado:,.2f}")
        c4.metric("Utilidad", f"${utilidad_total:,.2f}", f"{utilidad_pct:.2f}%")

        c5, c6, c7 = st.columns(3)
        c5.metric("Venta bruta productos", f"${total_venta_bruta:,.2f}")
        c6.metric("Envíos cobrados", f"${total_envios:,.2f}")
        c7.metric("Descuentos", f"${total_descuentos:,.2f}")

        st.divider()
        st.subheader("👤 Desglose por persona")

        cols_personas = st.columns(len(CONTACTOS))
        for idx, nombre_contacto in enumerate(CONTACTOS.keys()):
            pc = [p for p in pedidos if p["contacto"] == nombre_contacto]
            envios = sum(p["costo_envio"] for p in pc)
            num_env = sum(1 for p in pc if p["costo_envio"] > 0)
            costo_c = sum(p["subtotal_costo"] for p in pc)
            util_c = sum(p["utilidad"] for p in pc)
            util_pct_c = (util_c / costo_c * 100) if costo_c > 0 else 0

            with cols_personas[idx]:
                st.markdown(f"### {nombre_contacto}")
                st.metric("Tickets", f"{len(pc)} ({num_env} con envío)")
                st.metric("Envíos cobrados", f"${envios:,.2f}")
                st.metric("Costo", f"${costo_c:,.2f}")
                st.metric("Utilidad", f"${util_c:,.2f}", f"{util_pct_c:.2f}%")

        st.divider()
        st.subheader("⚖️ Gramos consumidos por producto")

        gramos_prod = {}
        for p in pedidos:
            for nombre, gr, _, _ in p["productos"]:
                gramos_prod[nombre] = gramos_prod.get(nombre, 0) + gr

        if gramos_prod:
            df_gramos = pd.DataFrame(
                [(k, int(v), v / 1000) for k, v in sorted(gramos_prod.items(), key=lambda x: -x[1])],
                columns=["Producto", "Gramos", "Kilos"],
            )
            df_gramos.loc["TOTAL"] = ["TOTAL", df_gramos["Gramos"].sum(), df_gramos["Kilos"].sum()]
            st.dataframe(df_gramos, use_container_width=True, hide_index=True)

        st.divider()
        st.subheader("📥 Descargar")

        # ZIP con todos los PNGs
        zip_buf = io.BytesIO()
        with zipfile.ZipFile(zip_buf, "w") as zf:
            for p in pedidos:
                png = generar_ticket_png(p)
                zf.writestr(f"{p['cliente'].replace(' ', '_')}.png", png)
        zip_buf.seek(0)

        # ---- Gastos extra para el CSV (independiente de tickets/utilidad/análisis) ----
        st.divider()
        st.subheader("💸 Gastos adicionales")
        st.caption(
            "Registra gastos como gasolina, empaques, etc. Se sumarán como filas extra "
            "en el CSV descargable, asignados al contacto que pagó. **No afectan los tickets, "
            "la utilidad, ni el análisis de clientes.**"
        )

        with st.expander("➕ Agregar gasto", expanded=False):
            cg1, cg2, cg3, cg4 = st.columns([1, 2, 1, 1])
            with cg1:
                gasto_contacto = st.selectbox(
                    "Contacto",
                    list(CONTACTOS.keys()),
                    key="gasto_contacto",
                )
            with cg2:
                gasto_concepto = st.text_input(
                    "Concepto",
                    placeholder="ej: Gasolina, Bolsas, Estacionamiento",
                    key="gasto_concepto",
                )
            with cg3:
                gasto_monto = st.number_input(
                    "Monto",
                    min_value=0.0,
                    step=10.0,
                    key="gasto_monto",
                )
            with cg4:
                st.write("")
                st.write("")
                if st.button("Agregar", use_container_width=True):
                    if gasto_monto > 0:
                        st.session_state.gastos_sesion.append({
                            "contacto": gasto_contacto,
                            "concepto": gasto_concepto.strip() or "Gasto",
                            "monto": float(gasto_monto),
                        })
                        st.rerun()
                    else:
                        st.error("El monto debe ser mayor a 0")

        if st.session_state.gastos_sesion:
            st.markdown("**Gastos registrados en esta sesión:**")
            for idx, g in enumerate(st.session_state.gastos_sesion):
                gc1, gc2, gc3, gc4 = st.columns([1, 2, 1, 1])
                gc1.text(g["contacto"])
                gc2.text(g["concepto"])
                gc3.text(f"${g['monto']:,.2f}")
                with gc4:
                    if st.button("🗑️", key=f"del_gasto_{idx}"):
                        st.session_state.gastos_sesion.pop(idx)
                        st.rerun()

            # Resumen rápido por contacto
            total_por_contacto = {}
            for g in st.session_state.gastos_sesion:
                total_por_contacto[g["contacto"]] = total_por_contacto.get(g["contacto"], 0) + g["monto"]
            resumen_gastos = "  |  ".join(
                f"{c}: ${m:,.2f}" for c, m in total_por_contacto.items()
            )
            st.caption(f"Total gastos: {resumen_gastos}")

        # ---- CSV de resumen (incluye gastos como filas extra con cliente='GASTO') ----
        data = {nombre: [] for nombre in CONTACTOS.keys()}
        costos = {nombre: [] for nombre in CONTACTOS.keys()}
        # 1. Filas de clientes (pedidos normales)
        for p in pedidos:
            data[p["contacto"]].append(p["cliente"])
            costos[p["contacto"]].append(f"{p['subtotal_costo']:.2f}")
        # 2. Filas de gastos (independientes, marcadas como 'GASTO')
        for g in st.session_state.gastos_sesion:
            etiqueta = f"GASTO: {g['concepto']}" if g["concepto"] else "GASTO"
            data[g["contacto"]].append(etiqueta)
            costos[g["contacto"]].append(f"{g['monto']:.2f}")

        max_p = max((len(v) for v in data.values()), default=0)
        for key in data:
            while len(data[key]) < max_p:
                data[key].append("")
                costos[key].append("")
        merged = []
        for i in range(max_p):
            row = []
            for key in data:
                row.append(data[key][i])
                row.append(costos[key][i])
            merged.append(row)
        columnas = []
        for key in data:
            columnas.append(key)
            columnas.append("COSTO")
        df_csv = pd.DataFrame(merged, columns=columnas)
        csv_bytes = df_csv.to_csv(index=False).encode("utf-8")

        st.divider()

        col_zip, col_csv = st.columns(2)
        with col_zip:
            st.download_button(
                "📦 Descargar ZIP con tickets",
                data=zip_buf.getvalue(),
                file_name="tickets_generados.zip",
                mime="application/zip",
                use_container_width=True,
            )
        with col_csv:
            st.download_button(
                "📄 Descargar CSV de pedidos",
                data=csv_bytes,
                file_name="pedidos_resumen.csv",
                mime="text/csv",
                use_container_width=True,
            )

        st.divider()
        st.subheader("💾 Guardar al histórico")
        st.caption(
            "Guarda permanentemente estos pedidos en Google Sheets para análisis futuros. "
            "Los datos quedan en las pestañas HISTORICO_TICKETS y HISTORICO_PRODUCTOS."
        )

        if st.session_state.get("historico_guardado_sesion", False):
            st.success("✅ Esta sesión ya fue guardada al histórico.")
            if st.button("Guardar de nuevo (duplicará registros)"):
                st.session_state["historico_guardado_sesion"] = False
                st.rerun()
        else:
            if st.button("💾 Guardar todos los tickets al histórico", type="primary"):
                with st.spinner("Guardando en Google Sheets..."):
                    tickets_g, productos_g = guardar_pedidos_en_historico(pedidos)
                if tickets_g is None:
                    st.error(f"Error al guardar: {productos_g}")
                else:
                    st.success(
                        f"✅ Guardados {tickets_g} tickets y {productos_g} productos al histórico. "
                        "Velos en la pestaña 'Análisis de clientes'."
                    )
                    st.session_state["historico_guardado_sesion"] = True
                    cargar_historico.clear()

        st.divider()
        st.subheader("Galería de tickets")
        cols_gal = st.columns(3)
        for i, p in enumerate(pedidos):
            with cols_gal[i % 3]:
                png = generar_ticket_png(p)
                st.image(png, caption=f"{p['cliente']} - ${p['nuevo_total']:,.2f}", use_container_width=True)


# ============================
# Tab 5: Análisis de clientes
# ============================
with tab_analisis:
    st.subheader("📈 Análisis de clientes")
    st.caption(
        "Datos acumulados de todos los pedidos guardados al histórico. "
        "Usa esta vista para identificar clientes fieles, productos más vendidos y oportunidades."
    )

    col_recargar, col_info = st.columns([1, 3])
    with col_recargar:
        if st.button("🔄 Recargar histórico"):
            cargar_historico.clear()
            st.rerun()

    resultado = cargar_historico()
    if resultado is None or (isinstance(resultado[0], type(None))):
        st.error(f"No se pudo cargar el histórico: {resultado[1]}")
        st.stop()

    df_tickets, df_productos = resultado

    if df_tickets is None or df_tickets.empty:
        st.info(
            "Aún no hay datos en el histórico. Genera tickets en la pestaña Capturar o "
            "Pegar pedido, y dale 'Guardar al histórico' en la pestaña Resumen."
        )
        st.stop()

    # ---- Filtros temporales ----
    st.markdown("### Filtros")
    col_f1, col_f2, col_f3 = st.columns(3)

    fecha_min = df_tickets["fecha_dt"].min()
    fecha_max = df_tickets["fecha_dt"].max()

    with col_f1:
        rango = st.selectbox(
            "Período",
            ["Todo el histórico", "Últimos 30 días", "Últimos 90 días", "Este año"],
            key="rango_analisis",
        )
    with col_f2:
        contacto_filter = st.selectbox(
            "Contacto",
            ["Todos"] + sorted(df_tickets["contacto"].unique().tolist()),
            key="contacto_analisis",
        )
    with col_f3:
        st.metric(
            "Período total",
            f"{(fecha_max - fecha_min).days if pd.notna(fecha_min) else 0} días",
        )

    # Aplicar filtros
    df_t = df_tickets.copy()
    df_p = df_productos.copy()

    hoy = pd.Timestamp.now()
    if rango == "Últimos 30 días":
        corte = hoy - pd.Timedelta(days=30)
        df_t = df_t[df_t["fecha_dt"] >= corte]
        df_p = df_p[df_p["fecha_dt"] >= corte]
    elif rango == "Últimos 90 días":
        corte = hoy - pd.Timedelta(days=90)
        df_t = df_t[df_t["fecha_dt"] >= corte]
        df_p = df_p[df_p["fecha_dt"] >= corte]
    elif rango == "Este año":
        df_t = df_t[df_t["fecha_dt"].dt.year == hoy.year]
        df_p = df_p[df_p["fecha_dt"].dt.year == hoy.year]

    if contacto_filter != "Todos":
        df_t = df_t[df_t["contacto"] == contacto_filter]
        df_p = df_p[df_p["contacto"] == contacto_filter]

    if df_t.empty:
        st.warning("No hay datos para los filtros seleccionados.")
        st.stop()

    # ---- KPIs globales ----
    st.divider()
    st.markdown("### Resumen del período")

    k1, k2, k3, k4 = st.columns(4)
    k1.metric("Tickets totales", len(df_t))
    k2.metric("Clientes únicos", df_t["cliente"].nunique())
    k3.metric("Ingresos", f"${df_t['total_final'].sum():,.2f}")
    k4.metric("Utilidad total", f"${df_t['utilidad'].sum():,.2f}")

    k5, k6, k7, k8 = st.columns(4)
    k5.metric("Ticket promedio", f"${df_t['total_final'].mean():,.2f}")
    k6.metric("Productos vendidos", int(df_t["num_productos"].sum()))
    k7.metric("Kg vendidos", f"{df_p['gramos'].sum() / 1000:,.2f}")
    margen_pct = (df_t['utilidad'].sum() / df_t['subtotal_costo'].sum() * 100) if df_t['subtotal_costo'].sum() > 0 else 0
    k8.metric("Margen sobre costo", f"{margen_pct:.1f}%")

    # ---- Ranking de clientes fieles ----
    st.divider()
    st.markdown("### 🏆 Top clientes")

    df_clientes = (
        df_t.groupby("cliente")
        .agg(
            tickets=("cliente", "count"),
            total_gastado=("total_final", "sum"),
            ticket_promedio=("total_final", "mean"),
            utilidad_generada=("utilidad", "sum"),
            ultima_visita=("fecha_dt", "max"),
            primera_visita=("fecha_dt", "min"),
        )
        .reset_index()
    )
    df_clientes["dias_sin_comprar"] = (hoy - df_clientes["ultima_visita"]).dt.days
    df_clientes = df_clientes.sort_values("total_gastado", ascending=False)

    # Score de fidelidad simple: tickets * 10 + frecuencia (tickets / dias_activo)
    df_clientes["dias_activo"] = (df_clientes["ultima_visita"] - df_clientes["primera_visita"]).dt.days.clip(lower=1)
    df_clientes["frecuencia"] = (df_clientes["tickets"] / df_clientes["dias_activo"] * 30).round(2)  # tickets por mes

    col_ranking_a, col_ranking_b = st.columns(2)
    with col_ranking_a:
        st.markdown("**Por monto gastado**")
        df_show = df_clientes.head(10)[["cliente", "tickets", "total_gastado", "ticket_promedio"]].copy()
        df_show["total_gastado"] = df_show["total_gastado"].apply(lambda x: f"${x:,.2f}")
        df_show["ticket_promedio"] = df_show["ticket_promedio"].apply(lambda x: f"${x:,.2f}")
        df_show.columns = ["Cliente", "Tickets", "Total gastado", "Promedio"]
        st.dataframe(df_show, use_container_width=True, hide_index=True)

    with col_ranking_b:
        st.markdown("**Por frecuencia (más recurrentes)**")
        df_freq = df_clientes.sort_values("tickets", ascending=False).head(10).copy()
        df_freq_show = df_freq[["cliente", "tickets", "frecuencia", "dias_sin_comprar"]].copy()
        df_freq_show["frecuencia"] = df_freq_show["frecuencia"].apply(lambda x: f"{x:.1f}/mes")
        df_freq_show["dias_sin_comprar"] = df_freq_show["dias_sin_comprar"].astype(int).astype(str) + " días"
        df_freq_show.columns = ["Cliente", "Tickets", "Frecuencia", "Última compra hace"]
        st.dataframe(df_freq_show, use_container_width=True, hide_index=True)

    # ---- Clientes que se están perdiendo ----
    st.divider()
    st.markdown("### ⚠️ Clientes en riesgo de pérdida")
    st.caption("Clientes que solían comprar pero no han vuelto en 14+ días.")

    df_riesgo = df_clientes[
        (df_clientes["dias_sin_comprar"] >= 14)
        & (df_clientes["tickets"] >= 2)
    ].sort_values("total_gastado", ascending=False).head(15)

    if df_riesgo.empty:
        st.success("Ningún cliente recurrente en riesgo. ¡Bien!")
    else:
        df_riesgo_show = df_riesgo[["cliente", "tickets", "total_gastado", "ultima_visita", "dias_sin_comprar"]].copy()
        df_riesgo_show["total_gastado"] = df_riesgo_show["total_gastado"].apply(lambda x: f"${x:,.2f}")
        df_riesgo_show["ultima_visita"] = df_riesgo_show["ultima_visita"].dt.strftime("%Y-%m-%d")
        df_riesgo_show["dias_sin_comprar"] = df_riesgo_show["dias_sin_comprar"].astype(int).astype(str) + " días"
        df_riesgo_show.columns = ["Cliente", "Tickets", "Total gastado", "Última compra", "Hace"]
        st.dataframe(df_riesgo_show, use_container_width=True, hide_index=True)

    # ---- Productos top ----
    st.divider()
    st.markdown("### 🥬 Productos más vendidos")

    if not df_p.empty:
        df_prods = (
            df_p.groupby("producto")
            .agg(
                veces_pedido=("producto", "count"),
                total_gramos=("gramos", "sum"),
                ingresos=("venta", "sum"),
            )
            .reset_index()
            .sort_values("ingresos", ascending=False)
        )
        df_prods["total_kg"] = (df_prods["total_gramos"] / 1000).round(2)
        df_prods_show = df_prods.head(15)[["producto", "veces_pedido", "total_kg", "ingresos"]].copy()
        df_prods_show["ingresos"] = df_prods_show["ingresos"].apply(lambda x: f"${x:,.2f}")
        df_prods_show.columns = ["Producto", "Veces pedido", "Kg vendidos", "Ingresos"]
        st.dataframe(df_prods_show, use_container_width=True, hide_index=True)

    # ---- Evolución de costos de productos ----
    st.divider()
    st.markdown("### 💵 Evolución de costos por producto")
    st.caption(
        "Una sola línea con el promedio de costo por kilo en el tiempo. "
        "Selecciona un producto específico para ver solo su evolución."
    )

    if df_p.empty:
        st.info("No hay datos de productos suficientes para graficar costos.")
    else:
        # Calcular costo y venta por kg
        df_costos = df_p.copy()
        df_costos = df_costos[df_costos["gramos"] > 0].copy()
        df_costos["costo_por_kg"] = df_costos["costo"] / (df_costos["gramos"] / 1000)
        df_costos["venta_por_kg"] = df_costos["venta"] / (df_costos["gramos"] / 1000)
        df_costos["fecha_dia"] = df_costos["fecha_dt"].dt.date

        productos_disponibles = sorted(df_costos["producto"].unique().tolist())
        producto_filtro = st.selectbox(
            "Producto",
            ["📊 Todos (promedio general)"] + productos_disponibles,
            key="producto_costo_filtro",
        )

        if producto_filtro == "📊 Todos (promedio general)":
            # DOS líneas: costo promedio y venta promedio por día
            df_general = df_costos.groupby("fecha_dia").agg(
                costo_promedio=("costo_por_kg", "mean"),
                venta_promedio=("venta_por_kg", "mean"),
            ).reset_index()
            df_general = df_general.set_index("fecha_dia").sort_index()
            df_general.columns = ["Costo por kg (nosotros)", "Precio venta por kg"]
            st.line_chart(df_general, height=400)

            # KPIs generales con costo y venta
            costo_prom = df_costos["costo_por_kg"].mean()
            venta_prom = df_costos["venta_por_kg"].mean()
            margen_gral = ((venta_prom - costo_prom) / costo_prom * 100) if costo_prom > 0 else 0

            k1, k2, k3, k4 = st.columns(4)
            k1.metric("Productos distintos", len(productos_disponibles))
            k2.metric("Costo promedio", f"${costo_prom:,.2f}/kg")
            k3.metric("Venta promedio", f"${venta_prom:,.2f}/kg")
            k4.metric("Margen promedio", f"{margen_gral:.1f}%")

        else:
            # Solo la línea del producto seleccionado: costo y venta
            df_pp = df_costos[df_costos["producto"] == producto_filtro].sort_values("fecha_dt")

            if df_pp.empty:
                st.warning("No hay datos para este producto.")
            else:
                df_grafica = df_pp.groupby("fecha_dia").agg(
                    costo_kg=("costo_por_kg", "mean"),
                    venta_kg=("venta_por_kg", "mean"),
                ).reset_index()
                df_grafica = df_grafica.set_index("fecha_dia").sort_index()
                df_grafica.columns = [
                    f"Costo {producto_filtro.title()} (nosotros)",
                    f"Precio venta {producto_filtro.title()}",
                ]
                st.line_chart(df_grafica, height=400)

                # KPIs del producto
                k1, k2, k3, k4 = st.columns(4)
                k1.metric("Veces vendido", len(df_pp))
                k2.metric("Total kg", f"{df_pp['gramos'].sum() / 1000:.2f} kg")
                margen_prom = (
                    (df_pp["venta_por_kg"].mean() - df_pp["costo_por_kg"].mean())
                    / df_pp["costo_por_kg"].mean() * 100
                ) if df_pp["costo_por_kg"].mean() > 0 else 0
                k3.metric("Margen promedio", f"{margen_prom:.1f}%")
                cambio = (
                    (df_pp.iloc[-1]["costo_por_kg"] - df_pp.iloc[0]["costo_por_kg"])
                    / df_pp.iloc[0]["costo_por_kg"] * 100
                ) if df_pp.iloc[0]["costo_por_kg"] > 0 else 0
                k4.metric("Δ costo", f"{cambio:+.1f}%")

                # Línea adicional: costo y venta promedio
                k5, k6 = st.columns(2)
                k5.metric("Costo promedio", f"${df_pp['costo_por_kg'].mean():,.2f}/kg")
                k6.metric("Venta promedio", f"${df_pp['venta_por_kg'].mean():,.2f}/kg")

                # Tabla histórica
                with st.expander("Historial detallado"):
                    df_hist = df_pp[["fecha", "cliente", "gramos", "costo", "venta", "costo_por_kg", "venta_por_kg"]].copy()
                    df_hist.columns = ["Fecha", "Cliente", "Gramos", "Costo total", "Venta total", "Costo/kg", "Venta/kg"]
                    for col in ["Costo total", "Venta total", "Costo/kg", "Venta/kg"]:
                        df_hist[col] = df_hist[col].apply(lambda x: f"${x:,.2f}")
                    st.dataframe(df_hist, use_container_width=True, hide_index=True)

    # ---- Tendencia (gráfica de línea estilo mercado) ----
    st.divider()
    st.markdown("### 📈 Tendencia")

    col_t1, col_t2 = st.columns([1, 3])
    with col_t1:
        granularidad = st.selectbox(
            "Agrupar por",
            ["Día", "Semana", "Mes"],
            index=0,
            key="granularidad_tendencia",
        )
    with col_t2:
        metricas_sel = st.multiselect(
            "Métricas a mostrar",
            ["Ingresos", "Utilidad", "Tickets"],
            default=["Ingresos", "Utilidad"],
            key="metricas_tendencia",
        )

    df_t_temp = df_t.copy()
    if granularidad == "Día":
        df_t_temp["periodo"] = df_t_temp["fecha_dt"].dt.strftime("%Y-%m-%d")
    elif granularidad == "Semana":
        df_t_temp["periodo"] = df_t_temp["fecha_dt"].dt.to_period("W").apply(
            lambda p: p.start_time.strftime("%Y-%m-%d")
        )
    else:  # Mes
        df_t_temp["periodo"] = df_t_temp["fecha_dt"].dt.to_period("M").astype(str)

    df_tendencia = df_t_temp.groupby("periodo").agg(
        tickets=("cliente", "count"),
        ingresos=("total_final", "sum"),
        utilidad=("utilidad", "sum"),
        clientes_unicos=("cliente", "nunique"),
    ).reset_index()
    df_tendencia = df_tendencia.sort_values("periodo")

    if len(df_tendencia) > 0:
        # Gráfica de línea (estilo mercado: línea horizontal con variaciones verticales)
        mapeo_metricas = {"Ingresos": "ingresos", "Utilidad": "utilidad", "Tickets": "tickets"}
        cols_graficar = [mapeo_metricas[m] for m in metricas_sel if m in mapeo_metricas]

        if cols_graficar:
            df_chart = df_tendencia.set_index("periodo")[cols_graficar]
            # Renombrar para que la leyenda salga en español capitalizado
            renombre = {v: k for k, v in mapeo_metricas.items()}
            df_chart = df_chart.rename(columns=renombre)
            st.line_chart(df_chart, height=350)
        else:
            st.info("Selecciona al menos una métrica para graficar.")

        # Tabla con los mismos datos
        df_mes_show = df_tendencia.copy()
        df_mes_show["ingresos"] = df_mes_show["ingresos"].apply(lambda x: f"${x:,.2f}")
        df_mes_show["utilidad"] = df_mes_show["utilidad"].apply(lambda x: f"${x:,.2f}")
        df_mes_show.columns = ["Período", "Tickets", "Ingresos", "Utilidad", "Clientes únicos"]
        with st.expander(f"Ver datos por {granularidad.lower()}"):
            st.dataframe(df_mes_show, use_container_width=True, hide_index=True)

    # ---- Ficha individual de cliente ----
    st.divider()
    st.markdown("### 🔍 Ficha individual de cliente")

    cliente_buscar = st.selectbox(
        "Selecciona un cliente",
        [""] + sorted(df_t["cliente"].unique().tolist()),
        key="ficha_cliente",
    )

    if cliente_buscar:
        df_c_t = df_tickets[df_tickets["cliente"] == cliente_buscar]
        df_c_p = df_productos[df_productos["cliente"] == cliente_buscar]

        fc1, fc2, fc3, fc4 = st.columns(4)
        fc1.metric("Tickets", len(df_c_t))
        fc2.metric("Total gastado", f"${df_c_t['total_final'].sum():,.2f}")
        fc3.metric("Ticket promedio", f"${df_c_t['total_final'].mean():,.2f}")
        ult = df_c_t["fecha_dt"].max()
        dias_ult = (hoy - ult).days if pd.notna(ult) else 0
        fc4.metric("Hace", f"{dias_ult} días")

        col_hist, col_top = st.columns(2)

        with col_hist:
            st.markdown("**Histórico de tickets**")
            df_c_show = df_c_t.sort_values("fecha_dt", ascending=False).head(10)[
                ["fecha", "num_productos", "total_final", "lleva_envio"]
            ].copy()
            df_c_show["total_final"] = df_c_show["total_final"].apply(lambda x: f"${x:,.2f}")
            df_c_show.columns = ["Fecha", "# Productos", "Total", "Envío"]
            st.dataframe(df_c_show, use_container_width=True, hide_index=True)

        with col_top:
            st.markdown("**Productos favoritos**")
            if not df_c_p.empty:
                df_c_top = (
                    df_c_p.groupby("producto")
                    .agg(veces=("producto", "count"), total_g=("gramos", "sum"))
                    .reset_index()
                    .sort_values("veces", ascending=False)
                    .head(10)
                )
                df_c_top["total_kg"] = (df_c_top["total_g"] / 1000).round(2)
                df_c_top_show = df_c_top[["producto", "veces", "total_kg"]].copy()
                df_c_top_show.columns = ["Producto", "Veces", "Kg total"]
                st.dataframe(df_c_top_show, use_container_width=True, hide_index=True)

    # ---- Descargar histórico completo ----
    st.divider()
    st.markdown("### 📥 Exportar histórico")
    col_exp1, col_exp2 = st.columns(2)
    with col_exp1:
        st.download_button(
            "Descargar tickets (CSV)",
            data=df_tickets.to_csv(index=False).encode("utf-8"),
            file_name="historico_tickets.csv",
            mime="text/csv",
            use_container_width=True,
        )
    with col_exp2:
        st.download_button(
            "Descargar productos (CSV)",
            data=df_productos.to_csv(index=False).encode("utf-8"),
            file_name="historico_productos.csv",
            mime="text/csv",
            use_container_width=True,
        )


# ============================
# Tab 6: Lista de compra (mayoreo)
# ============================
def parsear_linea_compra(linea):
    """Parsea una línea tipo:
        '* 5 calabazas'         → ('calabaza', 5, 'pz')
        '* 1 kg jitomate'       → ('jitomate', 1.0, 'kg')
        '* 1/2 kg cebolla'      → ('cebolla', 0.5, 'kg')
        '* 100g arándano'       → ('arandano', 0.1, 'kg')
        '* 1 manojo cilantro'   → ('cilantro', 1, 'pz')
        '* 1 cabeza ajos'       → ('ajo', 1, 'pz')
        '* 1.5k plátano'        → ('platano', 1.5, 'kg')

    Devuelve (producto_normalizado, cantidad, unidad) o None si no parseable.
    Unidad es 'kg' o 'pz'.
    """
    s = linea.strip()
    if not s:
        return None

    # Quitar viñetas (*, -, •, [x], 1., 1))
    s = re.sub(r"^(?:[\*\-\u2022]|\d+[\.\)])\s*(?:\[\s*[xX\s]?\s*\]\s*)?", "", s).strip()
    if not s:
        return None

    # Quitar paréntesis al final (gramos comprados, no es lo que pidieron)
    s = re.sub(r"\s*\([^)]*\)\s*$", "", s).strip()
    # Quitar precios
    s = re.sub(r"\$\s*[\d.,]+", "", s)
    # Quitar emojis
    s = "".join(c for c in s if c.isascii() or c.isalpha() or c.isspace() or c in "/.")
    s = re.sub(r"\s+", " ", s).strip()
    if not s:
        return None

    # Convertir fracciones tipo "1/2" en su valor decimal AL INICIO
    # Patrón: número entero opcional + fracción
    def num_a_float(token):
        token = token.strip()
        if "/" in token:
            try:
                a, b = token.split("/")
                return float(a) / float(b)
            except (ValueError, ZeroDivisionError):
                return None
        # Intentar como número simple
        token_norm = token.replace(",", ".")
        try:
            return float(token_norm)
        except ValueError:
            return None

    # Buscar la cantidad al inicio. Acepta: "5", "1.5", "1/2", "1 1/2"
    m = re.match(r"^(\d+\s+\d+/\d+|\d+/\d+|[\d.,]+)\s*(.*)$", s)
    if not m:
        return None
    cant_str, resto = m.group(1).strip(), m.group(2).strip()

    # Manejar "1 1/2" (entero + fracción)
    if " " in cant_str:
        partes = cant_str.split()
        cantidad = sum(filter(None, (num_a_float(p) for p in partes)))
        if cantidad == 0:
            cantidad = num_a_float(cant_str.split()[0])
    else:
        cantidad = num_a_float(cant_str)

    if cantidad is None or cantidad <= 0:
        return None

    # Detectar unidad: kg, g, k, o palabras de pieza
    unidades_kg = ["kg", "kgs", "kilo", "kilos", "k"]
    unidades_g = ["g", "gr", "grs", "gramos"]
    palabras_pieza = [
        "pz", "pza", "pzas", "pieza", "piezas",
        "manojo", "manojos", "ramo", "ramos",
        "cabeza", "cabezas",
        "domo", "domos", "domito", "domitos",
        "penca", "pencas",
        "cartón", "carton", "cartones",
        "cajita", "cajitas", "caja", "cajas",
        "bolsa", "bolsas", "bolsita", "bolsitas",
        "lata", "latas", "frasco", "frascos",
        "atado", "atados", "racimo", "racimos",
    ]

    # Tomar primera palabra del resto, ver si es unidad
    primera = resto.split()[0].lower() if resto else ""
    primera_norm = primera.rstrip(".,;:")

    unidad = None
    if primera_norm in unidades_kg:
        unidad = "kg"
        resto = resto.split(maxsplit=1)[1] if " " in resto else ""
    elif primera_norm in unidades_g:
        # gramos -> kg dividiendo entre 1000
        cantidad = cantidad / 1000
        unidad = "kg"
        resto = resto.split(maxsplit=1)[1] if " " in resto else ""
    elif primera_norm in palabras_pieza:
        unidad = "pz"
        resto = resto.split(maxsplit=1)[1] if " " in resto else ""
    else:
        # Caso especial: número pegado con "g" o "kg" (ej "100g", "1kg")
        m2 = re.match(r"^([\d.,/]+)\s*(kg|kgs|k|g|gr)\b\s*(.*)", s, re.IGNORECASE)
        if m2:
            num = num_a_float(m2.group(1))
            uni = m2.group(2).lower()
            if num and num > 0:
                if uni in ("g", "gr"):
                    cantidad = num / 1000
                    unidad = "kg"
                else:
                    cantidad = num
                    unidad = "kg"
                resto = m2.group(3).strip()
        if unidad is None:
            # Si no se detectó unidad, asumir piezas (ej "5 calabazas")
            # EXCEPTO si es fracción sola sin unidad: ahí asumir kg (ej "1/2 jamaica")
            if "/" in cant_str and not cant_str.replace("/", "").replace(" ", "").isdigit() == False:
                # Es fracción pura como "1/4 jamaica" -> asumir kg
                unidad = "kg"
            elif cantidad < 1 and "/" in cant_str:
                unidad = "kg"
            else:
                unidad = "pz"

    # Quitar "de" inicial: "de plátano" -> "plátano"
    resto = re.sub(r"^de\s+", "", resto, flags=re.IGNORECASE).strip()

    if not resto:
        return None

    # Aplicar matcher para normalizar a nombre de catálogo
    catalogo_keys = list(st.session_state.precios_dict.keys()) if st.session_state.precios_dict else []
    nombre_norm = buscar_match_catalogo(resto, catalogo_keys) if catalogo_keys else None
    if not nombre_norm:
        # Si no hay match, usar la descripción limpia (singularizada en español)
        palabras = resto.lower().split()
        palabras_limpias = []
        for p in palabras:
            # En español:
            # - palabras terminadas en vocal forman plural con -s (manzana->manzanas, jitomate->jitomates)
            # - palabras terminadas en consonante forman plural con -es (limón->limones, color->colores)
            # Para singularizar miramos la letra que queda antes de 's' al quitar la 's' final.
            if len(p) > 3 and p.endswith("s"):
                # Quitar 's' tentativamente
                sin_s = p[:-1]
                # Si lo que queda termina en 'e' Y la letra antes de la 'e' es consonante,
                # probablemente venía de un plural -es de palabra terminada en consonante
                # Ej: 'limones' -> sin_s='limone' -> 'n' antes de 'e' -> quitar 'e' -> 'limon'
                # Ej: 'jitomates' -> sin_s='jitomate' -> 't' antes de 'e' -> mantener (es 'jitomate')
                # La diferencia: limón termina en consonante (n) y forma -es. jitomate termina en vocal y forma -s.
                # Heurística: si la palabra original termina en 'ones', 'anes', 'ales' (consonante+es), quitar 'es'
                if len(sin_s) > 2 and sin_s.endswith("e"):
                    # Posibles terminaciones de plural -es: nes, res, les, ces, ses, des, jes
                    if len(sin_s) >= 3 and sin_s[-2] in "nrlcsdjzpvfbgkmhqtxy":
                        # Verificar que no sea palabra que naturalmente termina en 'e' + esas letras
                        # Lista de excepciones (palabras que terminan en consonante+e en singular):
                        # 'jitomate', 'tomate', 'aguacate', 'chayote', 'elote', 'paquete', 'machete'
                        # Si la palabra original es 'jitomates', sin_s sería 'jitomate' que es válida.
                        # Para distinguir: si sin_s sin la 'e' final también es palabra válida, usamos esa.
                        # Heurística más simple: si la consonante antes de 'e' es l, n, r, s, z, d -> probable plural -es
                        # Si es t, c -> probable singular ya (jitomate, chayote)
                        consonante = sin_s[-2]
                        if consonante in "lnrszd":
                            palabras_limpias.append(sin_s[:-1])  # quitar 'e' también
                        else:
                            palabras_limpias.append(sin_s)
                    else:
                        palabras_limpias.append(sin_s)
                else:
                    palabras_limpias.append(sin_s)
            else:
                palabras_limpias.append(p)
        nombre_norm = " ".join(palabras_limpias)

    return (nombre_norm, cantidad, unidad)


with tab_mayoreo:
    st.subheader("🛒 Lista de compra al mayoreo")
    st.caption(
        "Pega todos los pedidos del día. La app suma piezas y kilos por producto, "
        "manteniéndolos separados, para que sepas cuánto comprar de cada cosa."
    )

    ejemplo = """Lulú Velasco
* 5 calabazas
* 5 zanahorias
* 6 jitomates
* 6 plátanos
* 2 papas

Macry Funez
* 1 kilo mandarina
* 1 kilo jitomate
* 1 kilo zanahoria
* 1/2 kg cebolla

Abue Lucero
* 1 kg dominico
* 6 manzanas golden
* 1/4 jamaica
* 100g arándano"""

    with st.expander("Ver ejemplo de formato"):
        st.code(ejemplo, language="text")

    texto_compra = st.text_area(
        "Pega los pedidos completos aquí",
        height=400,
        placeholder=ejemplo,
        key="texto_compra",
    )

    if st.button("🧮 Calcular lista de compra", type="primary"):
        if not texto_compra.strip():
            st.warning("Pega al menos un pedido.")
        else:
            # Acumuladores
            kg_por_producto = {}
            pz_por_producto = {}
            no_parseables = []

            # Procesar línea por línea
            for linea in texto_compra.split("\n"):
                linea = linea.strip()
                if not linea:
                    continue
                # Detectar si es viñeta (producto) o nombre (cliente)
                es_viñeta = bool(re.match(r"^(?:[\*\-\u2022]|\d+[\.\)])", linea))
                if not es_viñeta:
                    continue  # nombres de clientes se ignoran

                resultado = parsear_linea_compra(linea)
                if resultado is None:
                    no_parseables.append(linea)
                    continue
                nombre, cantidad, unidad = resultado
                if unidad == "kg":
                    kg_por_producto[nombre] = kg_por_producto.get(nombre, 0) + cantidad
                else:
                    pz_por_producto[nombre] = pz_por_producto.get(nombre, 0) + cantidad

            st.session_state["lista_compra"] = {
                "kg": kg_por_producto,
                "pz": pz_por_producto,
                "no_parseables": no_parseables,
            }

    # Mostrar resultado
    if "lista_compra" in st.session_state:
        lc = st.session_state["lista_compra"]
        kg_dict = lc["kg"]
        pz_dict = lc["pz"]

        # Combinar todos los productos, ordenados de mayor a menor
        # Criterio: max(kg, pz) descendente; si están en pz se compara con pz, si en kg con kg.
        # Como kg suele ser número pequeño (1, 2, 3) y pz puede ser 5-30, ordenamos
        # por la suma de ambos para tener un ranking justo (kilos cuentan como su valor numérico).
        productos_con_total = [
            (p, kg_dict.get(p, 0) + pz_dict.get(p, 0))
            for p in set(list(kg_dict.keys()) + list(pz_dict.keys()))
        ]
        productos_con_total.sort(key=lambda x: x[1], reverse=True)
        todos_productos = [p for p, _ in productos_con_total]

        if not todos_productos:
            st.warning("No se detectó nada parseable.")
        else:
            st.divider()
            st.markdown("### 📋 Lista de compra consolidada")

            # Tabla con kg y pz separados, ya ordenada
            filas = []
            for prod in todos_productos:
                kg = kg_dict.get(prod, 0)
                pz = pz_dict.get(prod, 0)
                filas.append({
                    "Producto": prod.title(),
                    "Kilos": f"{kg:.3f} kg" if kg > 0 else "—",
                    "Piezas": f"{int(pz)} pz" if pz > 0 and pz == int(pz) else (f"{pz:.1f} pz" if pz > 0 else "—"),
                })

            df_compra = pd.DataFrame(filas)
            st.dataframe(df_compra, use_container_width=True, hide_index=True)

            # Resumen
            col_res1, col_res2 = st.columns(2)
            with col_res1:
                st.metric("Productos distintos", len(todos_productos))
            with col_res2:
                kg_total = sum(kg_dict.values())
                pz_total = sum(pz_dict.values())
                st.metric("Total", f"{kg_total:.2f} kg + {int(pz_total)} pz")

            # Vista en dos columnas: solo kg / solo pz
            col_kg, col_pz = st.columns(2)
            with col_kg:
                st.markdown("**🟢 Comprar por kilos**")
                if kg_dict:
                    df_kg = pd.DataFrame(
                        sorted(kg_dict.items(), key=lambda x: -x[1]),
                        columns=["Producto", "Kilos"],
                    )
                    df_kg["Producto"] = df_kg["Producto"].str.title()
                    df_kg["Kilos"] = df_kg["Kilos"].apply(lambda x: f"{x:.3f}")
                    st.dataframe(df_kg, use_container_width=True, hide_index=True)
                else:
                    st.caption("Nada por kilos")

            with col_pz:
                st.markdown("**🔵 Comprar por piezas**")
                if pz_dict:
                    df_pz = pd.DataFrame(
                        sorted(pz_dict.items(), key=lambda x: -x[1]),
                        columns=["Producto", "Piezas"],
                    )
                    df_pz["Producto"] = df_pz["Producto"].str.title()
                    df_pz["Piezas"] = df_pz["Piezas"].apply(
                        lambda x: f"{int(x)}" if x == int(x) else f"{x:.1f}"
                    )
                    st.dataframe(df_pz, use_container_width=True, hide_index=True)
                else:
                    st.caption("Nada por piezas")

            # Líneas que no se pudieron parsear
            if lc["no_parseables"]:
                with st.expander(f"⚠️ {len(lc['no_parseables'])} líneas no parseables"):
                    for ln in lc["no_parseables"]:
                        st.code(ln, language="text")

            # Descargar como CSV
            csv_compra = df_compra.to_csv(index=False).encode("utf-8")
            st.download_button(
                "📄 Descargar lista de compra (CSV)",
                data=csv_compra,
                file_name="lista_compra.csv",
                mime="text/csv",
            )

            if st.button("🗑️ Limpiar"):
                del st.session_state["lista_compra"]
                st.rerun()


# ============================
# Tab 7: Catálogo (genera PNG / PDF para enviar a clientes)
# ============================

# Frases organizadas por categoría
FRASES_CATALOGO = {
    "🌿 Calidad / Frescura": [
        "Del mercado a tu puerta, fresco como debe ser.",
        "Seleccionado a mano. Entregado el mismo día.",
        "Lo bueno del mercado, sin las filas.",
    ],
    "🛵 Servicio / Comodidad": [
        "Tu mercado, sin filas ni estacionamiento.",
        "Pide hoy. Recibe mañana. Disfruta toda la semana.",
        "Frutas y verduras frescas en la comodidad de tu casa.",
    ],
    "💛 Personal / Cercana": [
        "Te llevamos lo mejor del mercado cada sábado.",
        "Como ir al mercado, pero sin moverte de tu casa.",
        "Cosechado el viernes, en tu mesa el sábado.",
    ],
    "🥗 Saludable / Aspiracional": [
        "Comer fresco es comer bien.",
        "Lo natural sabe mejor.",
        "Salud que se nota desde el primer bocado.",
    ],
    "⚡ Con personalidad": [
        "No vendemos verdura. Llevamos mercado a tu casa.",
        "Hacemos las filas por ti.",
        "Madrugamos para que tú no tengas que hacerlo.",
    ],
}


def emoji_para_producto(nombre):
    """Asigna un emoji solo si existe uno que corresponda fielmente al producto.
    Si no hay emoji correcto, devuelve cadena vacía (sin emoji)."""
    n = nombre.lower()
    # Solo emojis verificados que coinciden con el producto.
    # Si no hay uno correcto, prefiero no poner nada.
    mapa = {
        # Frutas con emoji exacto
        ("manzana",): "🍎",
        ("plátano", "platano"): "🍌",
        ("uva",): "🍇",
        ("naranja",): "🍊",
        ("mandarina",): "🍊",
        ("limón", "limon"): "🍋",
        ("piña", "pina"): "🍍",
        ("mango",): "🥭",
        ("pera",): "🍐",
        ("durazno",): "🍑",
        ("fresa",): "🍓",
        ("sandía", "sandia"): "🍉",
        ("melón", "melon"): "🍈",
        ("cereza", "cherry"): "🍒",
        ("kiwi",): "🥝",
        ("aguacate",): "🥑",
        ("coco",): "🥥",
        ("mora azul", "blueberry", "blueberries"): "🫐",
        # Verduras con emoji exacto
        ("jitomate", "tomate"): "🍅",
        ("zanahoria",): "🥕",
        ("papa", "patata"): "🥔",
        ("camote", "batata"): "🍠",
        ("brócoli", "brocoli"): "🥦",
        ("lechuga",): "🥬",
        ("espinaca",): "🥬",
        ("cebolla",): "🧅",
        ("ajo",): "🧄",
        ("pepino",): "🥒",
        ("elote", "maíz", "maiz"): "🌽",
        ("chile",): "🌶️",
        ("jalapeño", "jalapeno"): "🌶️",
        # Otros con emoji exacto
        ("huevo",): "🥚",
        ("queso",): "🧀",
        ("leche",): "🥛",
        ("pan",): "🍞",
        ("miel",): "🍯",
    }
    for claves, emoji in mapa.items():
        for k in claves:
            if k in n:
                return emoji
    return ""  # Sin emoji si no hay uno fiel al producto


def detectar_unidad_producto(nombre):
    """Detecta si un producto se vende por kg o por unidad fija (pieza/bolsa/litro/etc).
    Devuelve la etiqueta de unidad que debe mostrarse en el catálogo y un flag
    indicando si admite fracciones (1/4, 1/2, 1).

    Returns:
        (etiqueta_unidad, admite_fracciones)
    """
    n = nombre.lower()

    # 1. Aceites siempre por Lt (aunque no diga "lt" en el nombre)
    if "aceite" in n:
        return ("Lt", False)

    # 2. Berries pequeños se venden por canastilla de 170g
    if any(re.search(rf"\b{k}\b", n) for k in [
        "frambuesa", "frambuesas", "zarzamora", "zarzamoras",
        "blueberry", "blueberries", "blue berry", "blue berries",
        "mora azul", "moras azules", "arándano", "arandano",
        "arándanos", "arandanos"
    ]):
        return ("170g", False)

    # 3. Litros / mililitros explícitos
    if any(re.search(rf"\b{k}\b", n) for k in ["lt", "litro", "litros", "ml"]):
        return ("Lt", False)
    if re.search(r"\d+\s*(lt|ml|litro|litros)", n):
        return ("Lt", False)

    # 4. Productos en bolsa (la bolsa es unidad fija)
    if "bolsa" in n or "bolsit" in n:
        return ("Bolsa", False)

    # 5. Productos en lata / frasco
    if any(re.search(rf"\b{k}\b", n) for k in ["lata", "latas", "frasco", "frascos"]):
        return ("Pieza", False)

    # 6. Domo (otros berries / fresas en canastilla)
    if any(re.search(rf"\b{k}\b", n) for k in ["domo", "domos", "domito", "domitos"]):
        return ("Domo", False)

    # 7. Productos que claramente son por pieza
    if any(re.search(rf"\b{k}\b", n) for k in ["pieza", "piezas", "pza", "pzas",
                                                 "unidad", "unidades"]):
        return ("Pieza", False)

    # Default: kg con fracciones permitidas
    return ("Kg", True)


def unidad_default_producto(nombre):
    """Devuelve la unidad por default que debería tener un producto al ser agregado al catálogo.
    Por ejemplo, fresas por 1/2 kg por default, berries por 170g, aceites por Lt, etc."""
    n = nombre.lower()

    # Fresas por 1/2 kg por default
    if "fresa" in n:
        return "1/2 kg"

    etiq, admite_frac = detectar_unidad_producto(nombre)
    if admite_frac:
        return "1 kg"
    # Si la etiqueta ya contiene cantidad (ej "170g"), usarla tal cual sin "1 "
    if etiq[0].isdigit():
        return etiq
    return f"1 {etiq.lower()}"


def categorizar_producto(nombre):
    """Clasifica un producto en una categoría visible.
    Devuelve (categoria, orden_categoria) donde orden_categoria sirve para mostrar primero
    las categorías más importantes."""
    n = nombre.lower().strip()

    # 1. Hierbas y aromáticos (específicas)
    if any(re.search(rf"\b{k}\b", n) for k in [
        "cilantro", "perejil", "hierbabuena", "yerbabuena", "epazote",
        "menta", "romero", "tomillo", "laurel", "orégano", "oregano",
        "albahaca", "ruda", "borraja", "jamaica", "hierba santa"
    ]):
        return ("HIERBAS Y AROMÁTICOS", 4)

    # 2. Chiles (la palabra "chile" o variedad específica)
    if re.search(r"\bchile\b", n) or any(re.search(rf"\b{k}\b", n) for k in [
        "jalapeño", "jalapeno", "serrano", "habanero",
        "poblano", "guajillo", "ancho", "pasilla",
        "morita", "chipotle", "árbol", "arbol",
        "cascabel", "manzano"
    ]):
        return ("CHILES", 3)

    # 3. Frutas (palabras completas para evitar falsos positivos)
    frutas = ["manzana", "manzanas", "plátano", "platano", "plátanos", "platanos",
              "uva", "uvas", "naranja", "naranjas", "mandarina", "mandarinas",
              "limón", "limon", "limones", "limón sin semilla",
              "piña", "pina", "piñas", "pinas", "mango", "mangos",
              "pera", "peras", "durazno", "duraznos", "fresa", "fresas",
              "sandía", "sandia", "melón", "melon", "melones",
              "cereza", "cerezas", "cherry", "kiwi", "kiwis", "coco", "cocos",
              "mora", "moras", "blueberry", "blueberries", "zarzamora", "zarzamoras",
              "frambuesa", "frambuesas", "papaya", "papayas", "guayaba", "guayabas",
              "granada", "granadas", "mamey", "chicozapote", "chicopazote", "zapote",
              "tuna", "tunas", "xoconostle", "tejocote", "tejocotes",
              "ciruela", "ciruelas", "membrillo", "níspero", "nispero",
              "carambola", "maracuyá", "maracuya", "lichi", "lichis",
              "rambután", "rambutan", "pitahaya", "tamarindo", "tamarindos",
              "higo", "higos", "datil", "dátil", "dátiles", "datiles",
              "arándano", "arandano", "arándanos", "arandanos",
              "toronja", "toronjas", "pomelo", "pomelos"]
    if any(re.search(rf"\b{k}\b", n) for k in frutas):
        return ("FRUTAS", 1)

    # 4. Lácteos y huevo
    if any(re.search(rf"\b{k}\b", n) for k in [
        "queso", "quesos", "leche", "crema", "yogurt",
        "mantequilla", "huevo", "huevos"
    ]):
        return ("LÁCTEOS Y HUEVO", 5)

    # 5. Legumbres y semillas
    if any(re.search(rf"\b{k}\b", n) for k in [
        "frijol", "frijoles", "lenteja", "lentejas", "garbanzo", "garbanzos",
        "haba", "habas", "alubia", "alubias", "alverjón", "alverjon",
        "ejote", "ejotes", "chícharo", "chicharo", "chícharos", "chicharos",
        "cacahuate", "cacahuates", "nuez", "nueces", "almendra", "almendras",
        "pistache", "pistaches", "semilla", "semillas", "pepita", "pepitas",
        "ajonjolí", "ajonjoli", "chía", "chia", "linaza", "amaranto"
    ]):
        return ("LEGUMBRES Y SEMILLAS", 6)

    # 6. Abarrotes (productos específicos, no por substring)
    if any(re.search(rf"\b{k}\b", n) for k in [
        "miel", "tortilla", "tortillas", "pan", "salsa", "salsas",
        "aceite", "vinagre", "azúcar", "azucar", "café", "cafe",
        "chocolate", "harina", "arroz", "pasta"
    ]):
        return ("ABARROTES", 7)

    # 7. Verduras (cualquier vegetal restante)
    verduras = ["jitomate", "jitomates", "tomate", "tomates", "tomatillo", "tomatillos",
                "aguacate", "aguacates", "elote", "elotes",
                "zanahoria", "zanahorias", "papa", "papas", "patata", "patatas",
                "camote", "camotes", "batata", "brócoli", "brocoli",
                "lechuga", "lechugas", "espinaca", "espinacas", "acelga", "acelgas",
                "cebolla", "cebollas", "ajo", "ajos", "pepino", "pepinos",
                "calabaza", "calabazas", "calabacita", "calabacitas",
                "calabacín", "calabacin", "chayote", "chayotes",
                "betabel", "betabeles", "remolacha", "rábano", "rabano", "rábanos", "rabanos",
                "nabo", "nabos", "nopal", "nopales",
                "hongo", "hongos", "champiñón", "champinon", "champiñones", "champinones",
                "seta", "setas", "portobello", "apio",
                "espárrago", "esparrago", "espárragos", "esparragos",
                "coliflor", "col", "repollo", "kale", "berro", "berros",
                "arúgula", "arugula", "pimiento", "pimientos", "morrón", "morron",
                "puerro", "poro", "berenjena", "berenjenas", "okra",
                "germinado", "germinados", "jengibre", "jenjibre",
                "cúrcuma", "curcuma", "yuca", "jícama", "jicama",
                "verdolaga", "verdolagas", "huauzontle", "huauzontles",
                "quelite", "quelites", "romerito", "romeritos",
                "flor de calabaza", "huitlacoche", "cuitlacoche", "maíz", "maiz"]
    if any(re.search(rf"\b{k}\b", n) for k in verduras):
        return ("VERDURAS", 2)

    return ("OTROS", 8)


def generar_catalogo_imagen(productos, contacto_nombre, contacto_tel, dia_entrega, frase, ancho=900):
    """Genera el catálogo en una o más páginas con diseño elegante por categorías.
    Compacta automáticamente: 1 columna si hay pocos productos, 2 columnas si hay muchos."""

    # Paleta elegante
    BG = (252, 250, 245)              # crema muy suave
    NEGRO = (35, 35, 35)
    GRIS_OSCURO = (90, 90, 90)
    GRIS_CLARO = (180, 180, 180)
    VERDE_PROFUNDO = (40, 70, 45)     # verde elegante
    VERDE_HOJA = (110, 150, 90)
    DORADO = (175, 130, 60)           # acento dorado/marrón cálido
    BLANCO = (255, 255, 255)

    def font(size, bold=False, italic=False):
        candidatos_bold = [
            "/usr/share/fonts/truetype/dejavu/DejaVuSerif-Bold.ttf",
            "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
            "/Library/Fonts/Georgia Bold.ttf",
            "C:/Windows/Fonts/georgiab.ttf",
        ]
        candidatos_italic = [
            "/usr/share/fonts/truetype/dejavu/DejaVuSerif-Italic.ttf",
            "/usr/share/fonts/truetype/dejavu/DejaVuSans-Oblique.ttf",
        ]
        candidatos_reg = [
            "/usr/share/fonts/truetype/dejavu/DejaVuSerif.ttf",
            "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
            "/Library/Fonts/Georgia.ttf",
            "C:/Windows/Fonts/georgia.ttf",
        ]
        if italic:
            candidatos = candidatos_italic + candidatos_reg
        elif bold:
            candidatos = candidatos_bold
        else:
            candidatos = candidatos_reg
        for c in candidatos:
            try:
                return ImageFont.truetype(c, size)
            except (OSError, IOError):
                continue
        return ImageFont.load_default()

    f_titulo = font(54, italic=True)       # "Servicio de entrega"
    f_subtitulo = font(22, bold=True)      # "FRUTAS Y VERDURAS A DOMICILIO"
    f_categoria = font(16, bold=True)      # Separadores de sección
    f_producto = font(15)                  # Nombre del producto
    f_precio = font(15, bold=True)         # Precio
    f_frase = font(13, bold=True)          # Frase pegadora
    f_envio_big = font(18, bold=True)      # "ENTREGA DÍAS / SÁBADOS"
    f_envio_circ = font(20, bold=True)     # "$35 envío" del círculo
    f_contacto = font(17, italic=True)     # Contacto al final
    f_buscar = font(13, bold=True)         # Mensaje "si tu producto no está"
    f_footer = font(11)                    # Footer chiquito

    # Ordenar productos por categoría y luego alfabéticamente
    productos_con_cat = [
        (categorizar_producto(p["nombre"])[1], categorizar_producto(p["nombre"])[0], p["nombre"], p)
        for p in productos
    ]
    productos_con_cat.sort(key=lambda x: (x[0], x[2].lower()))

    # Agrupar por categoría preservando orden
    categorias_orden = []
    productos_por_cat = {}
    for orden, cat, nombre, prod in productos_con_cat:
        if cat not in productos_por_cat:
            productos_por_cat[cat] = []
            categorias_orden.append(cat)
        productos_por_cat[cat].append(prod)

    # Decidir columnas: 1 si pocos productos, 2 si muchos
    total = len(productos)
    usar_2_columnas = total > 22

    # Alturas de elementos
    pad_lateral = 60
    header_h = 200
    espacio_top_lista = 30
    alto_fila = 28 if usar_2_columnas else 32
    alto_separador_cat = 50
    bloque_envio_h = 240
    footer_h = 60

    # Calcular cuántos productos caben por página
    if usar_2_columnas:
        alto_disponible = 1100  # altura útil de zona productos
        filas_disponibles = alto_disponible // alto_fila
        # Estimación: distribuir categorías en 2 columnas
        prods_por_pagina = filas_disponibles * 2 - 8  # margen para separadores
    else:
        alto_disponible = 1100
        filas_disponibles = alto_disponible // alto_fila
        prods_por_pagina = filas_disponibles - 5

    # Una sola imagen larga: meter todos los productos en una sola "página"
    items_unica_pagina = []
    for cat in categorias_orden:
        items_unica_pagina.append(("cat", cat))
        for p in productos_por_cat[cat]:
            items_unica_pagina.append(("prod", p))

    paginas = [items_unica_pagina] if items_unica_pagina else [[]]

    imagenes = []
    DIAS_MAYUS = dia_entrega.upper()

    for idx_pag, items_pagina in enumerate(paginas):
        # Calcular altura: como es UNA sola imagen larga, el alto depende del contenido total
        es_ultima = True  # siempre es última: solo hay una
        h_productos = sum(
            alto_separador_cat if t == "cat" else alto_fila
            for t, _ in items_pagina
        )
        alto = header_h + espacio_top_lista + h_productos + 60 + bloque_envio_h + footer_h
        if alto < 1200:
            alto = 1200

        img = Image.new("RGB", (ancho, alto), BG)
        draw = ImageDraw.Draw(img)

        # ---- HEADER ELEGANTE ----
        # Título "Servicio de entrega" en cursiva grande
        titulo = "Servicio de entrega"
        bbox = draw.textbbox((0, 0), titulo, font=f_titulo)
        tw = bbox[2] - bbox[0]
        draw.text(((ancho - tw) // 2, 50), titulo, fill=NEGRO, font=f_titulo)

        # Subtítulo en versalitas
        subtitulo = "FRUTAS Y VERDURAS A DOMICILIO"
        bbox = draw.textbbox((0, 0), subtitulo, font=f_subtitulo)
        tw = bbox[2] - bbox[0]
        # Letra-espaciado manual: dibujar carácter por carácter
        spacing = 4
        total_w = sum(draw.textbbox((0, 0), c, font=f_subtitulo)[2] for c in subtitulo) + spacing * (len(subtitulo) - 1)
        x_letra = (ancho - total_w) // 2
        for c in subtitulo:
            draw.text((x_letra, 130), c, fill=NEGRO, font=f_subtitulo)
            x_letra += draw.textbbox((0, 0), c, font=f_subtitulo)[2] + spacing

        # Línea decorativa delgada bajo el header
        linea_y = 175
        draw.line([(ancho // 2 - 100, linea_y), (ancho // 2 + 100, linea_y)], fill=DORADO, width=2)
        # Punto central
        draw.ellipse([ancho // 2 - 4, linea_y - 4, ancho // 2 + 4, linea_y + 4], fill=DORADO)

        # Número de página (si hay varias)
        if len(paginas) > 1:
            pag_txt = f"Página {idx_pag + 1} de {len(paginas)}"
            draw.text((ancho - 150, 20), pag_txt, fill=GRIS_CLARO, font=f_footer)

        y = header_h + espacio_top_lista

        # ---- LISTADO POR CATEGORÍAS ----
        if usar_2_columnas:
            # Dividir items_pagina en dos columnas balanceadas
            mitad = len(items_pagina) // 2 + (1 if len(items_pagina) % 2 else 0)
            # Ajustar para que la división no parta una categoría sin productos
            for i in range(mitad - 1, min(mitad + 3, len(items_pagina))):
                if items_pagina[i][0] == "cat":
                    mitad = i
                    break
            col1_items = items_pagina[:mitad]
            col2_items = items_pagina[mitad:]
            col_ancho = (ancho - pad_lateral * 2 - 40) // 2
            x_col1 = pad_lateral
            x_col2 = pad_lateral + col_ancho + 40

            def dibujar_columna(items, x_inicio, y_inicio, ancho_col):
                y_local = y_inicio
                for tipo, cont in items:
                    if tipo == "cat":
                        # Separador de categoría
                        cat_w = draw.textbbox((0, 0), cont, font=f_categoria)[2]
                        # Línea izquierda
                        draw.line([(x_inicio, y_local + 20), (x_inicio + 20, y_local + 20)], fill=DORADO, width=1)
                        # Texto centrado
                        draw.text((x_inicio + 28, y_local + 11), cont, fill=VERDE_PROFUNDO, font=f_categoria)
                        # Línea derecha
                        draw.line([(x_inicio + 28 + cat_w + 8, y_local + 20),
                                   (x_inicio + ancho_col, y_local + 20)], fill=DORADO, width=1)
                        y_local += alto_separador_cat
                    else:
                        prod = cont
                        nombre = prod["nombre"].title()
                        precio_txt = f"${prod['precio']:.2f}"
                        unidad_raw = prod["unidad"]
                        # Transformar etiquetas
                        if unidad_raw == "1 kg":
                            precio_completo = f"{precio_txt} Kg"
                        elif unidad_raw == "1/4 kg":
                            precio_completo = f"{precio_txt} ¼ Kg"
                        elif unidad_raw == "1/2 kg":
                            precio_completo = f"{precio_txt} ½ Kg"
                        elif unidad_raw.startswith("1 "):
                            # ej "1 bolsa", "1 lt" -> "Bolsa", "Lt"
                            etiq = unidad_raw[2:].capitalize()
                            precio_completo = f"{precio_txt} {etiq}"
                        elif unidad_raw and unidad_raw[0].isdigit():
                            # ej "170g" -> tal cual
                            precio_completo = f"{precio_txt} {unidad_raw}"
                        else:
                            precio_completo = f"{precio_txt} {unidad_raw}"

                        # Truncar nombre si es muy largo
                        max_nombre_w = ancho_col - draw.textbbox((0, 0), precio_completo, font=f_precio)[2] - 20
                        while draw.textbbox((0, 0), nombre, font=f_producto)[2] > max_nombre_w and len(nombre) > 3:
                            nombre = nombre[:-1]
                        if draw.textbbox((0, 0), nombre, font=f_producto)[2] > max_nombre_w:
                            nombre = nombre[:-3] + "..."

                        draw.text((x_inicio, y_local), nombre, fill=NEGRO, font=f_producto)
                        # Precio alineado a la derecha
                        p_w = draw.textbbox((0, 0), precio_completo, font=f_precio)[2]
                        draw.text((x_inicio + ancho_col - p_w, y_local), precio_completo, fill=NEGRO, font=f_precio)
                        y_local += alto_fila
                return y_local

            y1 = dibujar_columna(col1_items, x_col1, y, col_ancho)
            y2 = dibujar_columna(col2_items, x_col2, y, col_ancho)
            y = max(y1, y2) + 30

        else:
            # Una sola columna centrada
            col_ancho = ancho - pad_lateral * 2
            x_col = pad_lateral

            for tipo, cont in items_pagina:
                if tipo == "cat":
                    cat_w = draw.textbbox((0, 0), cont, font=f_categoria)[2]
                    # Línea decorativa antes del texto
                    line_inicio_x = x_col + (col_ancho - cat_w - 80) // 2
                    draw.line([(line_inicio_x, y + 20), (line_inicio_x + 30, y + 20)], fill=DORADO, width=1)
                    draw.text((line_inicio_x + 40, y + 11), cont, fill=VERDE_PROFUNDO, font=f_categoria)
                    draw.line([(line_inicio_x + 40 + cat_w + 10, y + 20),
                               (line_inicio_x + 80 + cat_w, y + 20)], fill=DORADO, width=1)
                    y += alto_separador_cat
                else:
                    prod = cont
                    nombre = prod["nombre"].title()
                    precio_txt = f"${prod['precio']:.2f}"
                    unidad_raw = prod["unidad"]
                    if unidad_raw == "1 kg":
                        precio_completo = f"{precio_txt} Kg"
                    elif unidad_raw == "1/4 kg":
                        precio_completo = f"{precio_txt} ¼ Kg"
                    elif unidad_raw == "1/2 kg":
                        precio_completo = f"{precio_txt} ½ Kg"
                    elif unidad_raw.startswith("1 "):
                        etiq = unidad_raw[2:].capitalize()
                        precio_completo = f"{precio_txt} {etiq}"
                    elif unidad_raw and unidad_raw[0].isdigit():
                        precio_completo = f"{precio_txt} {unidad_raw}"
                    else:
                        precio_completo = f"{precio_txt} {unidad_raw}"

                    draw.text((x_col + 100, y), nombre, fill=NEGRO, font=f_producto)
                    p_w = draw.textbbox((0, 0), precio_completo, font=f_precio)[2]
                    draw.text((x_col + col_ancho - p_w - 100, y), precio_completo, fill=NEGRO, font=f_precio)
                    y += alto_fila

            y += 30

        # ---- BLOQUE FINAL (solo en última página) ----
        if es_ultima:
            # Frase pegadora en versalitas centrada
            frase_upper = frase.upper()
            # Wrap
            palabras = frase_upper.split()
            lineas_frase = []
            linea_actual = ""
            for w in palabras:
                test = (linea_actual + " " + w).strip()
                if draw.textbbox((0, 0), test, font=f_frase)[2] > ancho - 160:
                    if linea_actual:
                        lineas_frase.append(linea_actual)
                    linea_actual = w
                else:
                    linea_actual = test
            if linea_actual:
                lineas_frase.append(linea_actual)

            for ln in lineas_frase:
                bbox = draw.textbbox((0, 0), ln, font=f_frase)
                tw = bbox[2] - bbox[0]
                draw.text(((ancho - tw) // 2, y), ln, fill=NEGRO, font=f_frase)
                y += 22
            y += 30

            # Bloque "ENTREGA DÍAS + $35 envío"
            bloque_y = y
            # Lado izquierdo: ENTREGA DÍAS
            entrega_x = ancho // 2 - 180
            draw.text((entrega_x, bloque_y), "ENTREGA", fill=NEGRO, font=f_envio_big)
            draw.text((entrega_x, bloque_y + 30), "DÍAS", fill=NEGRO, font=f_envio_big)
            draw.text((entrega_x, bloque_y + 60), DIAS_MAYUS, fill=NEGRO, font=f_envio_big)

            # Signo "+" en medio
            draw.text((ancho // 2 - 15, bloque_y + 30), "+", fill=NEGRO, font=f_titulo)

            # Lado derecho: círculo con "$35 de envío"
            circulo_cx = ancho // 2 + 130
            circulo_cy = bloque_y + 55
            circulo_r = 55
            draw.ellipse(
                [circulo_cx - circulo_r, circulo_cy - circulo_r,
                 circulo_cx + circulo_r, circulo_cy + circulo_r],
                fill=NEGRO,
            )
            # Texto blanco centrado en el círculo
            envio_l1 = "$35"
            envio_l2 = "de envío"
            bbox1 = draw.textbbox((0, 0), envio_l1, font=f_envio_circ)
            bbox2 = draw.textbbox((0, 0), envio_l2, font=font(13, bold=True))
            draw.text(
                (circulo_cx - (bbox1[2] - bbox1[0]) // 2, circulo_cy - 22),
                envio_l1, fill=BLANCO, font=f_envio_circ
            )
            draw.text(
                (circulo_cx - (bbox2[2] - bbox2[0]) // 2, circulo_cy + 8),
                envio_l2, fill=BLANCO, font=font(13, bold=True)
            )

            y = bloque_y + 130

            # Pago
            pago_txt = "Efectivo · Transferencia · Tarjeta débito o crédito"
            bbox = draw.textbbox((0, 0), pago_txt, font=font(13))
            tw = bbox[2] - bbox[0]
            draw.text(((ancho - tw) // 2, y), pago_txt, fill=GRIS_OSCURO, font=font(13))
            y += 30

            # Mensaje "Si tu producto no está..."
            buscar_txt = "SI TU PRODUCTO NO ESTÁ EN EL CATÁLOGO, NOSOTROS TE LO CONSEGUIMOS"
            bbox = draw.textbbox((0, 0), buscar_txt, font=f_buscar)
            tw = bbox[2] - bbox[0]
            if tw > ancho - 80:
                # Dividir
                l1 = "SI TU PRODUCTO NO ESTÁ EN EL CATÁLOGO,"
                l2 = "NOSOTROS TE LO CONSEGUIMOS"
                b1 = draw.textbbox((0, 0), l1, font=f_buscar)
                b2 = draw.textbbox((0, 0), l2, font=f_buscar)
                draw.text(((ancho - (b1[2] - b1[0])) // 2, y), l1, fill=NEGRO, font=f_buscar)
                draw.text(((ancho - (b2[2] - b2[0])) // 2, y + 20), l2, fill=NEGRO, font=f_buscar)
                y += 50
            else:
                draw.text(((ancho - tw) // 2, y), buscar_txt, fill=NEGRO, font=f_buscar)
                y += 30

            # Contacto con ícono WhatsApp simulado
            contacto_completo = f"  {contacto_nombre.title()}  -  {contacto_tel}"
            # Ícono WhatsApp simple: círculo verde con check
            ico_x = ancho // 2 - 150
            ico_y = y + 5
            draw.ellipse([ico_x, ico_y, ico_x + 24, ico_y + 24], outline=VERDE_HOJA, width=2)
            # "tail" del bocadillo
            draw.polygon([(ico_x + 4, ico_y + 22), (ico_x + 10, ico_y + 18), (ico_x + 4, ico_y + 14)],
                         fill=VERDE_HOJA)
            # Texto contacto
            draw.text((ico_x + 30, ico_y - 2), contacto_completo, fill=NEGRO, font=f_contacto)

        # ---- Footer ----
        fecha_gen = datetime.now(pytz.timezone(ZONA_HORARIA)).strftime("%d/%m/%Y")
        footer_txt = f"Catálogo generado el {fecha_gen}"
        bbox = draw.textbbox((0, 0), footer_txt, font=f_footer)
        tw = bbox[2] - bbox[0]
        draw.text(((ancho - tw) // 2, alto - 25), footer_txt, fill=GRIS_CLARO, font=f_footer)

        imagenes.append(img)

    return imagenes


with tab_catalogo:
    st.subheader("🛍️ Generar catálogo para clientes")
    st.caption("Selecciona productos, define unidades, día de entrega y genera un catálogo bonito en PNG o PDF para mandar por WhatsApp.")

    if not st.session_state.precios_dict:
        st.warning("⚠️ Carga el catálogo primero (desde Google Sheets o CSV) antes de generar.")
    else:
        # ---- CONFIGURACIÓN GENERAL ----
        st.markdown("### ⚙️ Configuración del catálogo")
        col_c1, col_c2 = st.columns(2)
        with col_c1:
            cat_contacto = st.selectbox(
                "Contacto que aparece en el catálogo",
                list(CONTACTOS.keys()),
                key="cat_contacto",
            )
        with col_c2:
            DIAS_SEMANA = ["Lunes", "Martes", "Miércoles", "Jueves", "Viernes", "Sábado", "Domingo"]
            dia_entrega_str = st.selectbox(
                "Día de entrega",
                DIAS_SEMANA,
                index=5,  # Sábado por default
                key="cat_dia_entrega",
            )

        st.markdown("### 💬 Frase del catálogo")
        st.caption("Elige una categoría para ver opciones de frase, o escribe la tuya.")

        # Opción de escribir frase propia
        usar_propia = st.checkbox("✍️ Escribir mi propia frase", key="usar_frase_propia")
        if usar_propia:
            frase_seleccionada = st.text_input(
                "Tu frase",
                value="",
                placeholder="ej: Sabor del mercado, directo a tu casa.",
                key="frase_propia",
            )
        else:
            # Mostrar todas las categorías como expanders
            frase_seleccionada = ""
            categoria_elegida = st.session_state.get("cat_categoria_elegida", None)

            for categoria, frases in FRASES_CATALOGO.items():
                with st.expander(categoria, expanded=(categoria == categoria_elegida)):
                    for i, fr in enumerate(frases):
                        key_btn = f"frase_{categoria}_{i}"
                        is_selected = (st.session_state.get("frase_actual", "") == fr)
                        label = f"{'✅ ' if is_selected else ''}{fr}"
                        if st.button(label, key=key_btn, use_container_width=True):
                            st.session_state["frase_actual"] = fr
                            st.session_state["cat_categoria_elegida"] = categoria
                            st.rerun()

            frase_seleccionada = st.session_state.get("frase_actual", "")
            if frase_seleccionada:
                st.success(f"Frase seleccionada: *{frase_seleccionada}*")
            else:
                st.info("Haz click en una frase para elegirla.")

        # ---- SELECCIÓN DE PRODUCTOS ----
        st.markdown("### 🥬 Productos a incluir")

        # Inicializar lista de productos seleccionados (formato: dict con nombre -> unidad)
        if "catalogo_seleccionados" not in st.session_state:
            st.session_state.catalogo_seleccionados = {}

        # Toggle URL de fotos (preparado pero apagado)
        st.checkbox(
            "🖼️ Incluir foto del producto desde URL (próximamente)",
            value=False,
            disabled=True,
            key="usar_fotos_url",
        )

        productos_ordenados = sorted(st.session_state.precios_dict.keys())

        # ---- Botones de selección masiva ----
        col_b1, col_b2, col_b3 = st.columns([1.5, 1.5, 1])
        with col_b1:
            if st.button("✅ Seleccionar TODO el catálogo", use_container_width=True, type="primary"):
                # Llenar el dict respetando el tipo y unidad por default de cada producto
                nuevo_sel = {}
                for prod in productos_ordenados:
                    nuevo_sel[prod] = unidad_default_producto(prod)
                st.session_state.catalogo_seleccionados = nuevo_sel
                st.rerun()
        with col_b2:
            if st.button("❌ Deseleccionar todos", use_container_width=True):
                st.session_state.catalogo_seleccionados = {}
                st.rerun()
        with col_b3:
            st.metric(
                "Seleccionados",
                f"{len(st.session_state.catalogo_seleccionados)} / {len(productos_ordenados)}"
            )

        # ---- Buscador ----
        st.markdown("**🔎 Buscar y agregar producto**")
        col_busq, col_unidad = st.columns([3, 1])
        with col_busq:
            # Filtrar productos ya seleccionados
            disponibles = [p for p in productos_ordenados if p not in st.session_state.catalogo_seleccionados]
            opciones_busqueda = [""] + disponibles
            producto_a_agregar = st.selectbox(
                "Escribe para buscar...",
                opciones_busqueda,
                format_func=lambda x: (
                    f"{(emoji_para_producto(x) + ' ') if emoji_para_producto(x) else ''}{x.title()} (${st.session_state.precios_dict[x]['precio_venta_kg']:.2f}/{detectar_unidad_producto(x)[0]})"
                    if x else "Selecciona un producto..."
                ),
                key="busqueda_producto",
                label_visibility="collapsed",
            )
        with col_unidad:
            # Solo mostrar selector de fracciones si el producto admite kg fraccionable
            admite_frac = False
            if producto_a_agregar:
                _, admite_frac = detectar_unidad_producto(producto_a_agregar)

            if admite_frac:
                # Para productos que admiten fracciones, usar el default del producto (ej fresa = 1/2 kg)
                default_unidad = unidad_default_producto(producto_a_agregar) if producto_a_agregar else "1 kg"
                opciones_frac = ["1/4 kg", "1/2 kg", "1 kg"]
                idx_default = opciones_frac.index(default_unidad) if default_unidad in opciones_frac else 2
                unidad_nueva = st.selectbox(
                    "Unidad",
                    opciones_frac,
                    index=idx_default,
                    key="unidad_nueva",
                    label_visibility="collapsed",
                )
            else:
                if producto_a_agregar:
                    etiq, _ = detectar_unidad_producto(producto_a_agregar)
                    # Si etiq empieza con dígito (ej "170g"), no prefijar "1"
                    if etiq[0].isdigit():
                        unidad_nueva = etiq
                        display = etiq
                    else:
                        unidad_nueva = f"1 {etiq.lower()}"
                        display = f"1 {etiq}"
                    st.markdown(f"<div style='padding-top: 8px; color: #888;'>{display}</div>", unsafe_allow_html=True)
                else:
                    unidad_nueva = "1 kg"

        if producto_a_agregar:
            if st.button(f"➕ Agregar {producto_a_agregar.title()}", use_container_width=True):
                st.session_state.catalogo_seleccionados[producto_a_agregar] = unidad_nueva
                st.rerun()

        # ---- Lista compacta de seleccionados ----
        if st.session_state.catalogo_seleccionados:
            st.markdown(f"**🛒 Productos en el catálogo ({len(st.session_state.catalogo_seleccionados)})**")

            seleccionados_ordenados = sorted(st.session_state.catalogo_seleccionados.keys())

            for prod in seleccionados_ordenados:
                precio_kg = st.session_state.precios_dict[prod]["precio_venta_kg"]
                unidad_actual = st.session_state.catalogo_seleccionados[prod]
                etiq_unidad, admite_frac_prod = detectar_unidad_producto(prod)

                # Calcular precio según unidad
                if admite_frac_prod and unidad_actual in ["1/4 kg", "1/2 kg", "1 kg"]:
                    factor = {"1/4 kg": 0.25, "1/2 kg": 0.5, "1 kg": 1.0}[unidad_actual]
                    precio_final = precio_kg * factor
                else:
                    # Unidad fija: el precio es el del producto tal cual (no se multiplica)
                    precio_final = precio_kg

                col_n, col_u, col_p, col_x = st.columns([3, 1.5, 1.5, 0.5])
                with col_n:
                    em = emoji_para_producto(prod)
                    st.write(f"{em + ' ' if em else ''}{prod.title()}")
                with col_u:
                    if admite_frac_prod:
                        # Si la unidad guardada no es válida (de antes), corregir a "1 kg"
                        opciones_unidad = ["1/4 kg", "1/2 kg", "1 kg"]
                        idx = opciones_unidad.index(unidad_actual) if unidad_actual in opciones_unidad else 2
                        nueva_u = st.selectbox(
                            "u",
                            opciones_unidad,
                            index=idx,
                            key=f"sel_unit_{prod}",
                            label_visibility="collapsed",
                        )
                        if nueva_u != unidad_actual:
                            st.session_state.catalogo_seleccionados[prod] = nueva_u
                            st.rerun()
                    else:
                        # Producto con unidad fija: solo mostrar la etiqueta
                        if etiq_unidad[0].isdigit():
                            display = etiq_unidad
                        else:
                            display = f"1 {etiq_unidad}"
                        st.markdown(f"<div style='padding-top: 8px;'>{display}</div>", unsafe_allow_html=True)
                with col_p:
                    st.write(f"**${precio_final:,.2f}**")
                with col_x:
                    if st.button("🗑️", key=f"del_{prod}", help="Quitar del catálogo"):
                        del st.session_state.catalogo_seleccionados[prod]
                        st.rerun()
        else:
            st.info("Aún no has agregado productos. Usa el buscador arriba o el botón 'Seleccionar TODO'.")

        # ---- GENERAR CATÁLOGO ----
        st.markdown("### 📤 Generar catálogo")
        productos_incluidos = []
        for prod in sorted(st.session_state.catalogo_seleccionados.keys()):
            unidad = st.session_state.catalogo_seleccionados[prod]
            etiq_unidad, admite_frac_prod = detectar_unidad_producto(prod)
            precio_kg = st.session_state.precios_dict[prod]["precio_venta_kg"]
            if admite_frac_prod and unidad in ["1/4 kg", "1/2 kg", "1 kg"]:
                factor = {"1/4 kg": 0.25, "1/2 kg": 0.5, "1 kg": 1.0}[unidad]
                precio_final = precio_kg * factor
            else:
                precio_final = precio_kg
                # Si la etiqueta ya incluye cantidad (ej "170g"), usar tal cual
                if etiq_unidad[0].isdigit():
                    unidad = etiq_unidad
                else:
                    unidad = f"1 {etiq_unidad.lower()}"
            productos_incluidos.append({
                "nombre": prod,
                "unidad": unidad,
                "precio": precio_final,
                "emoji": emoji_para_producto(prod),
            })

        if not productos_incluidos:
            st.warning("Selecciona al menos un producto.")
        elif not frase_seleccionada:
            st.warning("Elige o escribe una frase para el catálogo.")
        else:
            st.success(f"Listo para generar con {len(productos_incluidos)} productos.")

            if st.button("🎨 Generar catálogo", type="primary", use_container_width=True):
                with st.spinner("Generando catálogo..."):
                    imagenes = generar_catalogo_imagen(
                        productos_incluidos,
                        cat_contacto,
                        CONTACTOS[cat_contacto],
                        dia_entrega_str,
                        frase_seleccionada,
                        ancho=900,
                    )
                    st.session_state["catalogo_generado"] = imagenes

            if "catalogo_generado" in st.session_state:
                imagenes = st.session_state["catalogo_generado"]

                st.markdown("#### 👁️ Vista previa")
                # Como ahora generamos UNA sola imagen larga, siempre hay 1 elemento
                st.image(imagenes[0], caption="Catálogo", use_container_width=True)

                st.markdown("#### 📥 Descargar")
                col_d1, col_d2 = st.columns(2)

                with col_d1:
                    png_buf = io.BytesIO()
                    imagenes[0].save(png_buf, format="PNG")
                    png_buf.seek(0)
                    st.download_button(
                        "🖼️ Descargar PNG",
                        data=png_buf.getvalue(),
                        file_name=f"catalogo_{dia_entrega_str.lower()}_{datetime.now(pytz.timezone(ZONA_HORARIA)).strftime('%Y%m%d')}.png",
                        mime="image/png",
                        use_container_width=True,
                    )

                with col_d2:
                    pdf_buf = io.BytesIO()
                    imagenes[0].save(pdf_buf, format="PDF", resolution=100)
                    pdf_buf.seek(0)
                    st.download_button(
                        "📄 Descargar PDF",
                        data=pdf_buf.getvalue(),
                        file_name=f"catalogo_{dia_entrega_str.lower()}_{datetime.now(pytz.timezone(ZONA_HORARIA)).strftime('%Y%m%d')}.pdf",
                        mime="application/pdf",
                        use_container_width=True,
                    )

                if st.button("🗑️ Limpiar y generar de nuevo"):
                    del st.session_state["catalogo_generado"]
                    st.rerun()

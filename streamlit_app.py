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
        else:
            raise IOError("Fuentes no encontradas")
    except (IOError, OSError):
        default = ImageFont.load_default()
        f_titulo = default
        f_header = default
        f_reg = default
        f_total_bold = default

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
    alto += interlinea_pequena  # COSTO
    alto += interlinea_pequena  # DIF
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
    y = 18
    titulo = "FRUTIVERDURA A DOMICILIO"
    tw = text_width(titulo, f_titulo)
    draw.text(((ancho - tw) // 2, y), titulo, font=f_titulo, fill=c_titulo)
    y += interlinea + 4

    mx_time = datetime.now(pytz.timezone(ZONA_HORARIA))
    fecha = mx_time.strftime("%d/%m/%Y")
    tw = text_width(fecha, f_reg)
    draw.text(((ancho - tw) // 2, y), fecha, font=f_reg, fill=c_titulo)
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

    # ---- Totales (izquierda: COSTO/DIF; derecha: Subtotal/Envio/Total) ----
    draw.text((col_costo_x, y), f"COSTO : ${totales['subtotal_costo']:,.2f}", font=f_reg, fill=c_sec)
    y += interlinea_pequena

    dif_txt = f"DIF : ${totales['utilidad']:,.2f} ({totales['utilidad_pct']:.2f}%)"
    draw.text((col_costo_x, y), dif_txt, font=f_reg, fill=c_sec)
    y += interlinea_pequena + 4

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

    # Línea fina antes del total
    draw.line([(col_producto_x, y), (ancho - margen_x, y)], fill=c_titulo, width=1)
    y += 8

    total_txt = f"TOTAL : ${totales['nuevo_total']:,.2f}"
    tw = text_width(total_txt, f_total_bold)
    draw.text((col_venta_x - tw, y), total_txt, font=f_total_bold, fill=c_venta)
    y += interlinea + 8

    gracias = "GRACIAS POR TU COMPRA"
    tw = text_width(gracias, f_total_bold)
    draw.text(((ancho - tw) // 2, y), gracias, font=f_total_bold, fill=c_titulo)

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
        st.rerun()


# ============================
# Main: tabs
# ============================
st.title("Frutiverdura a Domicilio")

if not st.session_state.precios_dict:
    st.warning("⚠️ Primero carga el catálogo desde el panel lateral.")
    st.stop()

tab_capturar, tab_pegar, tab_modificar, tab_resumen = st.tabs(
    ["📝 Capturar", "📋 Pegar pedido", "✏️ Modificar", "📊 Resumen y Descarga"]
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
    "jito": "jitomate",
    "jitos": "jitomate",
    "jitomate": "jitomate",
    "jitomates": "jitomate",
    "gouda": "queso gouda",
    "panela": "queso panela",
    "oaxaca": "queso oaxaca",
    "manchego": "queso manchego",
    "huevo": "huevo",
    "huevos": "huevo",
    "cartón de huevo": "huevo",
    "cartones de huevo": "huevo",
    "papas fritas": "papas fritas",
}


def aplicar_alias(descripcion, catalogo_keys):
    """Si la descripción contiene un alias, devuelve el match directo."""
    desc = descripcion.lower().strip()
    palabras = set(desc.split())

    # 1. PRIMERO: aliases multi-palabra (más específicos)
    for alias, target in ALIASES_PRODUCTOS.items():
        if " " in alias and alias in desc:
            if target in catalogo_keys:
                return target

    # 2. DESPUÉS: aliases de una sola palabra
    for alias, target in ALIASES_PRODUCTOS.items():
        if " " not in alias and alias in palabras:
            if target in catalogo_keys:
                return target

    return None


def buscar_match_catalogo(descripcion, catalogo_keys, umbral=0.4):
    """Busca el mejor match. Prioriza coincidencia de la PRIMERA palabra significativa."""
    # 0. Primero intentar con aliases manuales
    alias_match = aplicar_alias(descripcion, catalogo_keys)
    if alias_match:
        return alias_match

    desc = descripcion.lower().strip()
    desc = re.sub(r"^[\d\.\,/]+\s*(kg|kilo|kilos|k|gr|gramos|g|pz|pza|piezas|pieza|domo|domos|cabeza|manojo|ramo|ramos|penca|cartón|cartones|carton|bolsa|de)\s*", "", desc)
    desc = re.sub(r"^[\d\.\,/]+\s+", "", desc)
    desc = re.sub(r"^de\s+", "", desc)  # "de plátano" -> "plátano"

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
    st.subheader("Pegar pedido")
    st.caption(
        "Pega solo la lista de productos. El nombre del cliente y el contacto los pones aquí arriba. "
        "Si pegas varios clientes, sepáralos con línea en blanco y pon el nombre arriba de cada lista."
    )

    # Campos rápidos arriba
    col_n, col_c, col_e = st.columns([2, 1, 1])
    with col_n:
        nombre_rapido = st.text_input(
            "Nombre del cliente (si pegas un solo pedido)",
            key="rapido_cliente",
            placeholder="ej: Abue Lucero",
        )
    with col_c:
        contacto_default = st.selectbox(
            "Contacto",
            list(CONTACTOS.keys()),
            key="pegar_contacto",
        )
    with col_e:
        envio_rapido = st.checkbox("Lleva envío", key="rapido_envio", value=False)

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
        key="texto_pegado",
    )

    clientes_con_envio = st.text_input(
        "Si pegaste varios pedidos, lista aquí los clientes con envío (separados por coma)",
        placeholder="ej: Laura, Macry",
        key="pegar_envio_multi",
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
                        st.text(f"{prod['descripcion_original']} ({int(prod['gramos'])}g)")
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

        st.divider()
        col_g1, col_g2 = st.columns(2)
        with col_g1:
            if st.button("✅ Generar todos los tickets", type="primary", use_container_width=True):
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

                    totales = calcular_totales(productos_finales, ped["lleva_envio"], 0.0)
                    pedido = {
                        "cliente": ped["cliente"],
                        "contacto": contacto_default,
                        "telefono": CONTACTOS[contacto_default],
                        "lleva_envio": ped["lleva_envio"],
                        "descuento": 0.0,
                        "productos": productos_finales,
                        **totales,
                    }
                    st.session_state.pedidos.append(pedido)
                    generados += 1

                del st.session_state["preview_pedidos"]
                st.success(f"✅ Se generaron {generados} tickets. Velos en la pestaña Resumen.")
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
                nuevo_prod = st.selectbox(
                    "Producto",
                    [""] + sorted(st.session_state.precios_dict.keys()),
                    format_func=lambda x: x.title() if x else "-- selecciona --",
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
                    if nuevo_prod and nuevos_g > 0:
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

        # CSV de resumen (mismo formato que el original)
        data = {nombre: [] for nombre in CONTACTOS.keys()}
        costos = {nombre: [] for nombre in CONTACTOS.keys()}
        for p in pedidos:
            data[p["contacto"]].append(p["cliente"])
            costos[p["contacto"]].append(f"{p['subtotal_costo']:.2f}")
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
        st.subheader("Galería de tickets")
        cols_gal = st.columns(3)
        for i, p in enumerate(pedidos):
            with cols_gal[i % 3]:
                png = generar_ticket_png(p)
                st.image(png, caption=f"{p['cliente']} - ${p['nuevo_total']:,.2f}", use_container_width=True)

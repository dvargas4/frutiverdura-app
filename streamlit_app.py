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
from uuid import uuid4

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
DOMICILIO_EMISOR_PREDETERMINADO = {
    "nombre": "LAURA GONZALEZ ARGUELLO",
    "rfc": "GOAL7212217T2",
    "regimen": "RESICO",
    "domicilio": "Ghana núm. 36, colonia Residencial Chimali",
    "lugar": "Tlalpan, Ciudad de México",
    "cp": "14370",
}
SPREADSHEET_URL = "https://docs.google.com/spreadsheets/d/1J3-J_evoyTJcLP94GwixduwD-wFv3zuMjAQX_oBrcPQ/edit"

CONTACTOS = {
    "IVAN": "55 3497 6860",
    "MINISUPER DOS V": "220 647 7892",
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

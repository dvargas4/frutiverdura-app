"""Cola de trabajos del SAT compartida entre Streamlit y el agente local.

Solo se guardan datos del pedido. La e.firma y la contraseña nunca se
transfieren a Google Sheets ni a Streamlit Cloud.
"""
from __future__ import annotations

import base64
import json
import uuid
import zlib
from datetime import datetime, timezone
from typing import Any

NOMBRE_HOJA = "Sat_Cola"
CABECERAS = ["ID", "Creado UTC", "Estado", "Pedido comprimido", "Comando", "Mensaje", "Paso", "Actualizado UTC"]
ESTADOS_CERRADOS = {"FINALIZADO", "CANCELADO", "FALLO_FINAL"}
COMANDOS_PERMITIDOS = {"CONTINUAR", "REINTENTAR", "OMITIR_PASO", "GUARDADO", "CERRAR"}


def _ahora() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _hoja(ss, crear: bool = False):
    import gspread
    try:
        ws = ss.worksheet(NOMBRE_HOJA)
    except gspread.WorksheetNotFound:
        if not crear:
            raise RuntimeError(f'Falta la hoja {NOMBRE_HOJA}. Inicia un trabajo desde la app para crearla.')
        ws = ss.add_worksheet(title=NOMBRE_HOJA, rows=1000, cols=len(CABECERAS))
        ws.update(range_name="A1:H1", values=[CABECERAS], value_input_option="RAW")
    encabezados = ws.row_values(1)
    if encabezados[:len(CABECERAS)] != CABECERAS:
        raise RuntimeError(f'Encabezados inesperados en {NOMBRE_HOJA}. No se sobrescribe la hoja.')
    return ws


def comprimir(paquete: dict) -> str:
    crudo = json.dumps(paquete, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    dato = base64.b64encode(zlib.compress(crudo, 9)).decode("ascii")
    if len(dato) > 45000:
        raise ValueError("El pedido excede la capacidad de una celda de Sheets. Divídelo en dos facturas.")
    return dato


def descomprimir(dato: str) -> dict:
    paquete = json.loads(zlib.decompress(base64.b64decode(dato)).decode("utf-8"))
    if paquete.get("schema") != "frutiverdura_sat_v1":
        raise ValueError("Esquema de pedido no admitido")
    return paquete


def solicitar(ss, paquete: dict) -> str:
    if paquete.get("schema") != "frutiverdura_sat_v1" or not paquete.get("conceptos"):
        raise ValueError("Pedido inválido o sin conceptos")
    ws = _hoja(ss, crear=True)
    comprimido = comprimir(paquete)
    # Idempotencia de trabajos en progreso. Evita dos clicks para el mismo pedido.
    for registro in ws.get_all_values()[1:]:
        if len(registro) >= 4 and registro[3] == comprimido and registro[2] not in ESTADOS_CERRADOS:
            return registro[0]
    id_trabajo = uuid.uuid4().hex
    fecha = _ahora()
    ws.append_row([id_trabajo, fecha, "PENDIENTE", comprimido, "", "Esperando al robot de la computadora", "", fecha], value_input_option="RAW", insert_data_option="INSERT_ROWS")
    return id_trabajo


def _buscar(ws, id_trabajo: str) -> tuple[int, list[str]]:
    # Búsqueda por la columna A. No se basa en posiciones inestables de la app.
    celda = ws.find(id_trabajo, in_column=1)
    if celda is None:
        raise ValueError("No se encontró el trabajo SAT")
    fila = ws.row_values(celda.row)
    return celda.row, (fila + [""] * 8)[:8]


def consultar(ss, id_trabajo: str) -> dict:
    ws = _hoja(ss)
    numero, fila = _buscar(ws, id_trabajo)
    return {"fila": numero, "id": fila[0], "creado": fila[1], "estado": fila[2], "comando": fila[4], "mensaje": fila[5], "paso": fila[6], "actualizado": fila[7]}


def enviar_comando(ss, id_trabajo: str, comando: str):
    if comando not in COMANDOS_PERMITIDOS:
        raise ValueError("Comando desconocido")
    ws = _hoja(ss)
    numero, fila = _buscar(ws, id_trabajo)
    estado = fila[2]
    permitido = ({"DATOS_RECEPTOR": {"CONTINUAR"},
                  "ERROR_PAUSADO": {"REINTENTAR", "OMITIR_PASO", "GUARDADO"},
                  "REVISION_SAT": {"CERRAR"},
                  "ERROR_GRAVE": {"CERRAR"}}).get(estado, set())
    if comando not in permitido:
        raise ValueError(f"No puede enviarse {comando} cuando el estado es {estado}")
    if fila[4]:
        raise ValueError("Ya hay un comando pendiente. Actualiza el estado antes de enviar otro.")
    ws.update_cell(numero, 5, comando)


def pendientes(ss) -> list[tuple[int, list[str]]]:
    ws = _hoja(ss)
    resultados = []
    for nro, fila in enumerate(ws.get_all_values(), start=1):
        if nro == 1:
            continue
        if len(fila) >= 4 and fila[2] == "PENDIENTE":
            resultados.append((nro, (fila + [""] * 8)[:8]))
    return resultados


def actualizar(ws, numero: int, *, estado: str, mensaje: str = "", paso: str = ""):
    # C..H, se borra comando para evitar aplicar una instrucción antigua.
    ws.update(range_name=f"C{numero}:H{numero}", values=[[estado, ws.acell(f'D{numero}').value or "", "", mensaje[:2000], paso[:240], _ahora()]], value_input_option="RAW")


def comando_actual(ws, numero: int) -> str:
    return (ws.acell(f"E{numero}").value or "").strip().upper()


def limpiar_comando(ws, numero: int):
    ws.update_cell(numero, 5, "")


def recientes(ss, maximo=20):
    """Solo estado, folio e identificador; no expone datos de e.firma."""
    ws = _hoja(ss)
    filas = ws.get_all_values()[1:]
    salida = []
    for fila in reversed(filas[-maximo:]):
        fila = (fila + [""] * 8)[:8]
        cliente, folio = "", ""
        try:
            p = descomprimir(fila[3]).get("pedido", {})
            cliente = p.get("cliente", "")
            folio = p.get("folio_remision", "")
        except Exception:
            pass
        salida.append({"id": fila[0], "estado": fila[2], "cliente": cliente, "folio": folio, "creado": fila[1]})
    return salida

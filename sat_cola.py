"""Cola de trabajos del SAT compartida entre Streamlit y el agente local.

Solo se guardan datos del pedido. La e.firma y la contraseña nunca se
transfieren a Google Sheets ni a Streamlit Cloud.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import math
import json
import re
import uuid
import zlib
from pathlib import Path
from datetime import datetime, timezone
from typing import Any

NOMBRE_HOJA = "Sat_Cola"
CABECERAS = ["ID", "Creado UTC", "Estado", "Pedido comprimido", "Comando", "Mensaje", "Paso", "Actualizado UTC"]
ESTADOS_CERRADOS = {"FINALIZADO", "CANCELADO", "FALLO_FINAL", "CERRADO_SIN_BORRAR"}
COMANDOS_PERMITIDOS = {"CONTINUAR", "REINTENTAR", "OMITIR_PASO", "GUARDADO", "CERRAR", "ACTUALIZAR_VISTA", "TIMBRAR", "RECUPERAR_ARCHIVOS", "REINTENTAR_BORRADO", "CERRAR_SIN_BORRAR"}
HOJA_VISTAS = "Sat_Vistas"
CABECERAS_VISTAS = ["Trabajo", "Version", "Parte", "Imagen JPEG base64", "Actualizado UTC", "Resumen"]
CABECERAS_PREGUNTA = ["Pregunta SAT (JSON)", "Respuesta SAT (JSON)"]
CABECERA_FIRMA = "Autorizacion firmada HMAC"
SAL_TIMBRADO = b"frutiverdura-sat-cfdi-hmac-v1"


def derivar_clave_timbrado(pin: str) -> bytes:
    if len(pin) < 16:
        raise ValueError("La clave de timbrado requiere al menos 16 caracteres")
    return hashlib.pbkdf2_hmac("sha256", pin.encode("utf-8"), SAL_TIMBRADO, 200_000)


def firmar_timbrado(clave: bytes, id_trabajo: str, hash_vista: str) -> str:
    if not re.fullmatch(r"[a-f0-9]{32}", id_trabajo):
        raise ValueError("Identificador de trabajo inválido")
    if not re.fullmatch(r"[a-f0-9]{64}", hash_vista):
        raise ValueError("Huella de la vista previa inválida")
    mensaje = f"TIMBRAR:{id_trabajo}:{hash_vista}".encode("ascii")
    return hmac.new(clave, mensaje, hashlib.sha256).hexdigest()


def _asegurar_columna_firma(ws):
    _asegurar_columnas_preguntas(ws)
    if ws.col_count < 11:
        ws.add_cols(11 - ws.col_count)
    actual = (ws.acell("K1").value or "").strip()
    if actual and actual != CABECERA_FIRMA:
        raise RuntimeError("La columna K de Sat_Cola está ocupada, no modificaré sus datos")
    if not actual:
        ws.update_cell(1, 11, CABECERA_FIRMA)



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


def _asegurar_columnas_preguntas(ws):
    """Migra Sat_Cola existente A:H a A:J sin tocar ningún trabajo previo."""
    if ws.col_count < 10:
        ws.add_cols(10 - ws.col_count)
    valores = (ws.row_values(1) + [""] * 10)[:10]
    if valores[8:10] == CABECERAS_PREGUNTA:
        return
    if any(valores[8:10]):
        raise RuntimeError("Sat_Cola I:J ya tienen encabezados desconocidos. No sobrescribí datos.")
    ws.update(range_name="I1:J1", values=[CABECERAS_PREGUNTA], value_input_option="RAW")


def publicar_pregunta(ws, numero: int, pregunta: dict):
    _asegurar_columnas_preguntas(ws)
    raw = json.dumps(pregunta, ensure_ascii=False, separators=(",", ":"))
    if len(raw) > 45000:
        raise ValueError("Demasiadas opciones en la pregunta SAT")
    ws.update(range_name=f"I{numero}:J{numero}", values=[[raw, ""]], value_input_option="RAW")


def limpiar_pregunta(ws, numero: int):
    _asegurar_columnas_preguntas(ws)
    ws.batch_update([
        {"range": f"E{numero}", "values": [[""]]},
        {"range": f"I{numero}:J{numero}", "values": [["", ""]]},
    ], value_input_option="RAW")


def respuesta_actual(ws, numero: int, pregunta_id: str):
    """Lee comando E y respuesta J con UNA solicitud de lectura, no dos."""
    valores = ws.get(range_name=f"E{numero}:J{numero}")
    fila = (valores[0] if valores else []) + [""] * 6
    comando = (fila[0] or "").strip().upper()
    if comando not in ("RESPONDER_SAT", "BUSCAR_SAT"):
        return None
    raw = fila[5] or ""
    if not raw:
        return None
    respuesta = json.loads(raw)
    if respuesta.get("id") != pregunta_id:
        raise ValueError("La respuesta corresponde a otra pregunta SAT. No se aplicará.")
    if (comando == "BUSCAR_SAT") != (respuesta.get("accion") == "buscar"):
        raise ValueError("Comando y acción de pregunta SAT no coinciden")
    return respuesta


def enviar_respuesta(ss, id_trabajo: str, pregunta_id: str, accion: str, valor: str):
    """Recibe decisión del usuario; nunca transfiere claves de e.firma."""
    if accion not in ("seleccionar", "buscar", "escribir", "revisar", "otro"):
        raise ValueError("Acción de pregunta desconocida")
    valor = str(valor).strip()
    if len(valor) > 200:
        raise ValueError("Respuesta demasiado larga")
    ws = _hoja(ss)
    _asegurar_columnas_preguntas(ws)
    numero, fila = _buscar(ws, id_trabajo)
    if fila[2] != "PREGUNTA_SAT" or fila[4]:
        raise ValueError("El robot no está esperando una nueva respuesta")
    raw = ws.acell(f"I{numero}").value or ""
    pregunta = json.loads(raw)
    if pregunta.get("id") != pregunta_id:
        raise ValueError("La pregunta cambió. Actualiza el panel antes de responder")
    opciones = pregunta.get("opciones", [])
    tipo = pregunta.get("tipo")
    if accion == "revisar":
        valor = ""
    if accion == "otro":
        if pregunta.get("campo") != "rfc":
            raise ValueError("La opción Otro únicamente corresponde al receptor")
        valor = "Otro"
    if accion == "seleccionar":
        if tipo not in ("seleccion", "autocompletar") or valor not in opciones:
            raise ValueError("Selecciona una opción publicada por el SAT")
    if accion == "escribir":
        if tipo != "texto" and not (tipo == "autocompletar" and pregunta.get("admite_texto_directo")):
            raise ValueError("Este campo requiere seleccionar una opción del SAT")
        if not valor:
            raise ValueError("El campo no puede estar vacío")
        if pregunta.get("campo") in ("codigo_postal", "codigo_postal_otro") and not re.fullmatch(r"\d{5}", valor):
            raise ValueError("El código postal debe tener cinco dígitos")
        if pregunta.get("campo") == "nombre_receptor" and len(valor) < 2:
            raise ValueError("El nombre o razón social debe tener dos caracteres o más")
        if pregunta.get("campo") in ("rfc", "rfc_otro") and not re.fullmatch(r"[A-Za-zÑ&]{3,4}\d{6}[A-Za-z0-9]{3}", valor.upper()):
            raise ValueError("El RFC debe tener 12 o 13 caracteres válidos")
    if accion == "buscar" and tipo not in ("seleccion", "autocompletar"):
        raise ValueError("Este campo no tiene opciones para buscar")
    paquete = json.dumps({"id": pregunta_id, "accion": accion, "valor": valor}, ensure_ascii=False)
    # Una actualización única y ordenada: primero respuesta J, después comando E.
    ws.batch_update([
        {"range": f"J{numero}", "values": [[paquete]]},
        {"range": f"E{numero}", "values": [["BUSCAR_SAT" if accion == "buscar" else "RESPONDER_SAT"]]},
    ], value_input_option="RAW")


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
    pregunta = None
    if fila[2] == "PREGUNTA_SAT":
        _asegurar_columnas_preguntas(ws)
        raw = ws.acell(f"I{numero}").value or ""
        if raw:
            pregunta = json.loads(raw)
    return {"fila": numero, "id": fila[0], "creado": fila[1], "estado": fila[2], "comando": fila[4], "mensaje": fila[5], "paso": fila[6], "actualizado": fila[7], "pregunta": pregunta}


def enviar_comando(ss, id_trabajo: str, comando: str, *, vista_hash: str = "", confirmacion: str = "", firma: str = ""):
    if comando not in COMANDOS_PERMITIDOS:
        raise ValueError("Comando desconocido")
    ws = _hoja(ss)
    numero, fila = _buscar(ws, id_trabajo)
    estado = fila[2]
    permitido = ({"DATOS_RECEPTOR": {"CONTINUAR"},
                  "ERROR_PAUSADO": {"REINTENTAR", "OMITIR_PASO", "GUARDADO"},
                  "REVISION_MOVIL": {"ACTUALIZAR_VISTA", "TIMBRAR", "CERRAR"},
                  "RESULTADO_SELLO": {"ACTUALIZAR_VISTA", "RECUPERAR_ARCHIVOS", "CERRAR"},
                  "REVISION_SAT": {"CERRAR"},
                  "ERROR_GRAVE": {"CERRAR"},
                  "CANCELACION_NO_CONFIRMADA": {"REINTENTAR_BORRADO", "CERRAR_SIN_BORRAR"}}).get(estado, set())
    if comando not in permitido:
        raise ValueError(f"No puede enviarse {comando} cuando el estado es {estado}")
    if fila[4]:
        raise ValueError("Ya hay un comando pendiente. Actualiza el estado antes de enviar otro.")
    if comando == "TIMBRAR":
        if confirmacion.strip().upper() != "TIMBRAR":
            raise ValueError("Escribe TIMBRAR para confirmar la emisión")
        vista = consultar_vista(ss, id_trabajo)
        if not vista or not vista.get("hash") or vista_hash != vista["hash"]:
            raise ValueError("La vista previa cambió. Actualízala antes de autorizar el sellado")
        if not re.fullmatch(r"[a-f0-9]{64}", firma):
            raise ValueError("Falta autorización criptográfica válida para timbrar")
        _asegurar_columna_firma(ws)
        # Se coloca la firma antes del comando para que el agente nunca vea
        # un TIMBRAR sin autorización. El comando es la última escritura.
        ws.update_cell(numero, 11, firma)
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
    # Preserva D (pedido) y actualiza C, E:H en UNA escritura batch.
    # Evita la lectura adicional de D en cada cambio de estado.
    ws.batch_update([
        {"range": f"C{numero}", "values": [[estado]]},
        {"range": f"E{numero}:H{numero}", "values": [["", mensaje[:2000], paso[:240], _ahora()]]},
    ], value_input_option="RAW")


def comando_actual(ws, numero: int) -> str:
    return (ws.acell(f"E{numero}").value or "").strip().upper()


def limpiar_comando(ws, numero: int):
    ws.update_cell(numero, 5, "")
    if ws.col_count >= 11:
        ws.update_cell(numero, 11, "")


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


# ============= CAPTURA DE PREVISUALIZACIÓN EN EL SAT ====================

def _hoja_vistas(ss, crear=False):
    import gspread
    try:
        ws = ss.worksheet(HOJA_VISTAS)
    except gspread.WorksheetNotFound:
        if not crear:
            return None
        ws = ss.add_worksheet(title=HOJA_VISTAS, rows=1200, cols=6)
        ws.update(range_name="A1:F1", values=[CABECERAS_VISTAS], value_input_option="RAW")
    if ws.row_values(1)[:6] != CABECERAS_VISTAS:
        raise ValueError("Sat_Vistas tiene encabezados distintos; no modificaré datos existentes")
    return ws


def publicar_vista(ss, id_trabajo: str, imagen_jpeg: bytes, resumen: str = "", texto_hash: str = "") -> str:
    """Envía una captura REAL del navegador SAT para revisión desde el teléfono.

    Visible para los editores del documento Sheets. Nunca se usa en login.
    """
    if not imagen_jpeg.startswith(b"\xff\xd8"):
        raise ValueError("Se requiere una imagen JPEG real del navegador")
    if len(imagen_jpeg) > 880_000:
        raise ValueError("La vista SAT supera 880 KB; el robot debe capturar menor calidad")
    ws = _hoja_vistas(ss, crear=True)
    hash_imagen = hashlib.sha256(imagen_jpeg).hexdigest()
    version = hash_imagen[:24]
    contenido = base64.b64encode(imagen_jpeg).decode("ascii")
    partes = [contenido[i:i+35000] for i in range(0, len(contenido), 35000)]
    if len(partes) > 40:
        raise ValueError("Demasiadas partes de imagen para Sheets")
    # Elimina todas las capturas anteriores de ESTE trabajo, no las ajenas.
    registros = ws.get_all_values()
    antiguas = [indice for indice, fila in enumerate(registros, start=1)
                if indice > 1 and fila and fila[0] == id_trabajo]
    for indice in reversed(antiguas):
        ws.delete_rows(indice)
    fecha = _ahora()
    filas = [[id_trabajo, version, n, parte, fecha,
              json.dumps({"sha256": hash_imagen, "partes": len(partes), "texto": resumen[:2400], "texto_hash": texto_hash}, ensure_ascii=False) if n == 1 else ""]
             for n, parte in enumerate(partes, start=1)]
    ws.append_rows(filas, value_input_option="RAW")
    return hash_imagen


def consultar_vista(ss, id_trabajo: str):
    """Lee una captura comprobando integridad. NO cachear en Streamlit."""
    ws = _hoja_vistas(ss, crear=False)
    if ws is None:
        return None
    filas = [fila for fila in ws.get_all_values()[1:] if fila and fila[0] == id_trabajo]
    if not filas:
        return None
    versiones = set(f[1] for f in filas if len(f) >= 4)
    if len(versiones) != 1:
        return None  # Reescritura en curso; volver a cargar más tarde.
    try:
        datos = json.loads(filas[0][5])
        n = int(datos["partes"])
        segmentos = {int(f[2]): f[3] for f in filas}
        if sorted(segmentos) != list(range(1, n + 1)):
            return None
        contenido = base64.b64decode("".join(segmentos[i] for i in range(1, n + 1)), validate=True)
        if hashlib.sha256(contenido).hexdigest() != datos["sha256"]:
            return None
    except (ValueError, KeyError, IndexError, TypeError):
        return None
    return {"jpeg": contenido, "hash": datos["sha256"], "texto": datos.get("texto", ""), "texto_hash": datos.get("texto_hash", ""),
            "fecha": filas[0][4], "version": filas[0][1]}


def borrar_vista(ss, id_trabajo: str):
    ws = _hoja_vistas(ss, crear=False)
    if ws is None:
        return
    for i, fila in reversed(list(enumerate(ws.get_all_values(), start=1))):
        if i > 1 and fila and fila[0] == id_trabajo:
            ws.delete_rows(i)


# =========== ARCHIVOS FISCALES SAT (XML Y PDF) ===============
HOJA_ARCHIVOS = "Sat_Archivos"
ARCHIVOS_ENCABEZADO = ["Trabajo", "Tipo", "SHA256", "Parte", "Datos comprimidos base64", "Fecha UTC", "Total partes", "Nombre"]

def _hoja_archivos(ss, crear=False):
    import gspread
    try:
        hoja = ss.worksheet(HOJA_ARCHIVOS)
    except gspread.WorksheetNotFound:
        if not crear:
            return None
        hoja = ss.add_worksheet(title=HOJA_ARCHIVOS, rows=800, cols=8)
        hoja.update(range_name="A1:H1", values=[ARCHIVOS_ENCABEZADO], value_input_option="RAW")
    if hoja.row_values(1)[:8] != ARCHIVOS_ENCABEZADO:
        raise ValueError("Encabezados inesperados en Sat_Archivos; no se modificó")
    return hoja

def _validar_archivo_fiscal(tipo, datos):
    if tipo == "PDF":
        if not datos.startswith(b"%PDF-"):
            raise ValueError("El archivo descargado no tiene formato PDF")
        return None
    if tipo == "XML":
        import xml.etree.ElementTree as ET
        raiz = ET.fromstring(datos)
        if raiz.tag.rsplit("}", 1)[-1] != "Comprobante":
            raise ValueError("El XML no contiene un Comprobante CFDI")
        for elem in raiz.iter():
            if elem.tag.rsplit("}", 1)[-1] == "TimbreFiscalDigital":
                uuid = elem.attrib.get("UUID", "")
                if uuid:
                    return uuid
        raise ValueError("El XML no contiene TimbreFiscalDigital; no se confirmó emisión")
    raise ValueError("Tipo de documento no permitido")

def publicar_archivo(ss, id_trabajo: str, tipo: str, datos: bytes, nombre: str) -> str:
    """Publica SOLO archivos reales del SAT, verificando su estructura.
    En Sheets quedan accesibles a personas con permisos sobre el documento.
    """
    tipo = tipo.upper()
    uuid = _validar_archivo_fiscal(tipo, datos)
    if len(datos) > 3_000_000:
        raise ValueError("Archivo superior a 3 MB. Descárgalo directamente del SAT")
    hoja = _hoja_archivos(ss, crear=True)
    huella = hashlib.sha256(datos).hexdigest()
    comprimidos = base64.b64encode(zlib.compress(datos, 9)).decode("ascii")
    partes = [comprimidos[i:i + 30000] for i in range(0, len(comprimidos), 30000)]
    if not partes: raise ValueError("Archivo vacío")
    if len(partes) > 180: raise ValueError("Archivo demasiado grande para Sheets")
    # Nunca publicar dos versiones distintas del mismo documento en un trabajo.
    existentes = hoja.get_all_values()
    coincidentes = [ (n, row) for n, row in enumerate(existentes, 1)
                    if n>1 and len(row)>1 and row[0]==id_trabajo and row[1]==tipo]
    if coincidentes:
        previas = {row[2] for _,row in coincidentes if len(row)>2}
        if previas == {huella}: return uuid or huella
        raise ValueError("Ya existe otro documento de este tipo para esta factura; requiere revisión")
    fecha = _ahora()
    hoja.append_rows([[id_trabajo, tipo, huella, i, pedazo, fecha, len(partes), nombre] for i,pedazo in enumerate(partes,1)], value_input_option="RAW")
    return uuid or huella

def consultar_archivos(ss, id_trabajo: str):
    hoja = _hoja_archivos(ss)
    if hoja is None: return {}
    registros = [r for r in hoja.get_all_values()[1:] if len(r)>=8 and r[0] == id_trabajo]
    resultado = {}
    for tipo in ("XML", "PDF"):
        filas = [r for r in registros if r[1] == tipo]
        if not filas: continue
        huellas = {r[2] for r in filas}; totales = {int(r[6]) for r in filas}
        if len(huellas)!=1 or len(totales)!=1: continue
        total=next(iter(totales)); m={int(r[3]):r[4] for r in filas}
        if len(filas)!=total or sorted(m)!=list(range(1,total+1)): continue
        try:
            datos=zlib.decompress(base64.b64decode(''.join(m[i] for i in range(1,total+1)),validate=True))
            if hashlib.sha256(datos).hexdigest() != next(iter(huellas)): continue
            uuid=_validar_archivo_fiscal(tipo,datos)
        except (ValueError, KeyError, TypeError, zlib.error):
            continue
        resultado[tipo]={"bytes": datos, "nombre":filas[0][7], "uuid":uuid}
    return resultado


# ======= PDF DE VISTA PREVIA (BORRADOR, NO CFDI TIMBRADO) =======
HOJA_PDF_PREVIA = 'Sat_Previas_PDF'
CABECERAS_PDF_PREVIA = ['Trabajo', 'SHA256', 'Parte', 'Contenido zlib base64', 'Total partes', 'Fecha UTC', 'Nombre']


def _hoja_pdf_previa(ss, crear=False):
    import gspread
    try:
        ws = ss.worksheet(HOJA_PDF_PREVIA)
    except gspread.WorksheetNotFound:
        if not crear:
            return None
        ws = ss.add_worksheet(title=HOJA_PDF_PREVIA, rows=800, cols=7)
        ws.update(range_name='A1:G1', values=[CABECERAS_PDF_PREVIA], value_input_option='RAW')
    if ws.row_values(1)[:7] != CABECERAS_PDF_PREVIA:
        raise ValueError('Encabezados no reconocidos en Sat_Previas_PDF. Sin cambios.')
    return ws


def _validar_pdf_previa(datos):
    if not isinstance(datos, bytes) or len(datos) < 32 or not datos.startswith(b'%PDF-') or b'%%EOF' not in datos[-2048:]:
        raise ValueError('Vista Previa no es un archivo PDF real')
    if len(datos) > 3_000_000:
        raise ValueError('Vista Previa supera 3 MB y no se puede sincronizar en Sheets')


def publicar_pdf_previa(ss, id_trabajo, datos, nombre='Vista_Previa_SAT.pdf'):
    """Publica PDF descargado realmente al pulsar Vista Previa, nunca un PDF inventado.

    Es un BORRADOR. No se mezcla con XML/PDF oficiales timbrados de Sat_Archivos.
    """
    _validar_pdf_previa(datos)
    if not re.fullmatch(r'[a-f0-9]{32}', str(id_trabajo)):
        raise ValueError('Trabajo SAT inválido')
    ws = _hoja_pdf_previa(ss, crear=True)
    huella = hashlib.sha256(datos).hexdigest()
    codificado = base64.b64encode(zlib.compress(datos,9)).decode('ascii')
    bloques = [codificado[i:i+30000] for i in range(0,len(codificado),30000)]
    if not bloques or len(bloques) > 150:
        raise ValueError('Vista Previa supera el máximo de partes permitido')
    registros = ws.get_all_values()
    otras = [(n,fila) for n,fila in enumerate(registros, 1) if n>1 and fila and fila[0]==id_trabajo]
    # Al actualizar un borrador se conservan otros trabajos, no otros PDF de este trabajo.
    for n,_ in reversed(otras):
        ws.delete_rows(n)
    nombre = Path(str(nombre or 'Vista_Previa_SAT.pdf')).name
    if not nombre.lower().endswith('.pdf'):
        nombre += '.pdf'
    fecha = _ahora()
    ws.append_rows([[id_trabajo,huella,i,trozo,len(bloques),fecha,nombre] for i,trozo in enumerate(bloques,1)],value_input_option='RAW')
    return huella


def consultar_pdf_previa(ss,id_trabajo):
    ws = _hoja_pdf_previa(ss)
    if ws is None:
        return None
    filas = [r for r in ws.get_all_values()[1:] if len(r)>=7 and r[0]==id_trabajo]
    if not filas:
        return None
    try:
        versiones = {r[1] for r in filas}
        totales = {int(r[4]) for r in filas}
        if len(versiones)!=1 or len(totales)!=1:
            return None
        cantidad = next(iter(totales))
        partes = {int(r[2]):r[3] for r in filas}
        if len(filas)!=cantidad or sorted(partes)!=list(range(1,cantidad+1)):
            return None
        datos = zlib.decompress(base64.b64decode(''.join(partes[n] for n in range(1,cantidad+1)),validate=True))
        _validar_pdf_previa(datos)
        huella = hashlib.sha256(datos).hexdigest()
        if huella != next(iter(versiones)):
            return None
        return {'bytes':datos,'hash':huella,'fecha':filas[0][5], 'nombre':filas[0][6]}
    except (ValueError,TypeError,KeyError,IndexError,zlib.error):
        return None

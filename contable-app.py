import streamlit as st
import pandas as pd

from PIL import Image
from io import BytesIO

import time
import re
import os
import copy

from openpyxl import load_workbook
from openpyxl.utils import get_column_letter


# ============================================================
# CONFIGURACIÓN GENERAL
# ============================================================

st.set_page_config(
    page_title="Procesador Contable",
    page_icon="📊",
    layout="wide"
)


# ============================================================
# CONFIGURACIÓN DE GEMINI
# ============================================================

try:
    API_KEY = st.secrets.get("GEMINI_API_KEY", "")
    API_KEY = str(API_KEY).strip()
except Exception:
    API_KEY = ""


# ============================================================
# IMPORTACIÓN SEGURA DEL SDK DE GEMINI
# ============================================================

try:
    from google import genai
    from google.genai import types

    GEMINI_SDK_OK = True
    GEMINI_SDK_ERROR = ""
except Exception as e:
    GEMINI_SDK_OK = False
    GEMINI_SDK_ERROR = str(e)


# ============================================================
# PYDANTIC
# ============================================================

try:
    from pydantic import (
        BaseModel,
        Field,
        ValidationError
    )

    PYDANTIC_OK = True
    PYDANTIC_ERROR = ""
except Exception as e:
    PYDANTIC_OK = False
    PYDANTIC_ERROR = str(e)


# ============================================================
# CONFIGURACIÓN DEL MODELO
# ============================================================

GEMINI_MODEL = "gemini-2.5-flash"


# ============================================================
# HOJAS QUE DEBEN PERMANECER
# ============================================================

HOJAS_PERMITIDAS = [
    "INGRESOS",
    "GASTOS",
    "ARTIS",
    "SALDOS",
    "PROVEEDORES"
]


# ============================================================
# COMPROBACIÓN DE CONFIGURACIÓN
# ============================================================

if not API_KEY:
    st.error("❌ No se encontró GEMINI_API_KEY.")
    st.info(
        """
Configure la variable GEMINI_API_KEY en:

Streamlit Cloud
→ Manage app
→ Settings
→ Secrets
"""
    )
    st.stop()


if not GEMINI_SDK_OK:
    st.error("❌ No fue posible cargar el SDK de Google Gemini.")
    st.code(GEMINI_SDK_ERROR, language="text")
    st.info("Verifique que requirements.txt contenga 'google-genai'.")
    st.stop()


if not PYDANTIC_OK:
    st.error("❌ No fue posible cargar Pydantic.")
    st.code(PYDANTIC_ERROR, language="text")
    st.info("Verifique que requirements.txt contenga 'pydantic'.")
    st.stop()


# ============================================================
# MODELO ESTRUCTURADO PARA LOS COMPROBANTES
# ============================================================

class ComprobanteData(BaseModel):

    tipo_movimiento: str = Field(
        description=(
            "Tipo de movimiento contable. "
            "Debe ser Ingreso o Egreso."
        )
    )

    concepto_factura: str = Field(
        description=(
            "Número de factura, concepto o identificación "
            "principal del comprobante."
        )
    )

    destinatario_remitente: str = Field(
        description=(
            "Nombre o identificación de la persona, empresa, "
            "comercio o entidad receptora/emisora."
        )
    )

    tienda: str = Field(
        description=(
            "Identificación del establecimiento o punto de venta "
            "al que corresponde la operación. "
            "Solo debe identificarse si aparece claramente "
            "en el comprobante o puede establecerse de forma "
            "directa a partir del nombre del archivo. "
            "No debe confundirse con el destinatario, remitente "
            "o entidad financiera. Si no es posible identificarla, "
            "utilizar 'No identificado'."
        )
    )

    monto_cop: float = Field(
        description=(
            "Valor total de la transacción en pesos colombianos "
            "(COP), expresado como número sin símbolo monetario "
            "ni separadores."
        )
    )

    fecha_hora: str = Field(
        description=(
            "Fecha y hora de la transacción en formato "
            "YYYY-MM-DD HH:MM. Si la hora no aparece, utilizar "
            "00:00."
        )
    )

    referencia_operacion: str = Field(
        description=(
            "Número de comprobante, autorización, referencia, "
            "ID de operación o código de transacción."
        )
    )

    banco_plataforma: str = Field(
        description=(
            "Entidad financiera o plataforma de origen/destino."
        )
    )

    medio_pago_tipo: str = Field(
        description=(
            "Medio o tipo de operación. "
            "Ejemplos: Transferencia, QR, Llave, "
            "Pago de servicios, depósito, efectivo, "
            "tarjeta de crédito, etc."
        )
    )


# ============================================================
# FUNCIONES AUXILIARES
# ============================================================

def limpiar_texto(valor):
    if valor is None:
        return ""
    return str(valor).strip()


def normalizar_texto(valor):
    valor = limpiar_texto(valor)
    return (
        valor
        .lower()
        .replace("á", "a")
        .replace("é", "e")
        .replace("í", "i")
        .replace("ó", "o")
        .replace("ú", "u")
        .replace("ü", "u")
    )


def validar_resultado(
    data: ComprobanteData,
    tipo_seleccionado: str
):
    errores = []
    tipo = normalizar_texto(data.tipo_movimiento)

    if tipo not in ["ingreso", "egreso"]:
        errores.append(
            f"Tipo de movimiento no válido: {data.tipo_movimiento}"
        )

    if tipo != normalizar_texto(tipo_seleccionado):
        errores.append(
            f"El comprobante fue clasificado como '{data.tipo_movimiento}', "
            f"pero el usuario seleccionó '{tipo_seleccionado}'."
        )

    if data.monto_cop < 0:
        errores.append(
            "El monto de la transacción no puede ser negativo."
        )

    fecha_validada = pd.to_datetime(
        data.fecha_hora,
        errors="coerce"
    )

    if pd.isna(fecha_validada):
        errores.append(
            f"Fecha/hora no válida: {data.fecha_hora}"
        )

    return errores


def obtener_espera(error_msg, intento):
    match = re.search(
        r"retry in\s+(\d+(?:\.\d+)?)s",
        error_msg,
        re.IGNORECASE
    )

    if match:
        espera = float(match.group(1)) + 2
    else:
        espera = min(60, 10 * (2 ** intento))

    return espera


def clasificar_error(error_msg):
    mensaje = error_msg.lower()

    if (
        "401" in mensaje
        or "unauthenticated" in mensaje
        or "invalid api key" in mensaje
        or "api key not valid" in mensaje
    ):
        return "AUTH"

    if (
        "403" in mensaje
        or "permission_denied" in mensaje
        or "permission denied" in mensaje
    ):
        return "PERMISSION"

    if (
        "429" in mensaje
        or "resource_exhausted" in mensaje
        or "quota" in mensaje
    ):
        return "QUOTA"

    if (
        "timeout" in mensaje
        or "timed out" in mensaje
        or "connection" in mensaje
        or "503" in mensaje
        or "502" in mensaje
        or "500" in mensaje
    ):
        return "TEMPORARY"

    return "OTHER"


# ============================================================
# PROCESAR COMPROBANTE
# ============================================================

def procesar_comprobante(
    client,
    image,
    nombre_archivo,
    tipo_movimiento,
    tienda_predeterminada=""
):
    prompt = f"""
Analiza detalladamente este comprobante bancario.

CONTEXTO:
El usuario ha indicado que este archivo corresponde a:
{tipo_movimiento}

Nombre original del archivo:
"{nombre_archivo}"

Identificación del establecimiento proporcionada manualmente por el usuario, si existe:
"{tienda_predeterminada}"

OBJETIVO:
Extrae exclusivamente la información que pueda ser identificada claramente en el comprobante.

REGLAS IMPORTANTES:
1. No inventes información.
2. Si un campo no aparece claramente, utiliza: "No identificado".
3. El monto debe corresponder al valor real de la operación.
4. El monto debe expresarse como número en COP, sin símbolo de moneda, puntos ni separadores.
5. La fecha debe convertirse al formato: YYYY-MM-DD HH:MM
6. Si la hora no está visible, utiliza: 00:00
7. Identifica correctamente la entidad financiera o plataforma.
8. Identifica la referencia, autorización o ID de operación cuando exista.
9. Conserva el concepto o número de factura cuando sea visible.
10. No confundas número de factura con número de autorización, referencia o ID.
11. El campo TIENDA / ESTABLECIMIENTO es independiente del campo DESTINATARIO/REMITENTE.
12. No utilices automáticamente el destinatario, remitente, banco o plataforma como nombre del establecimiento.
13. Si el establecimiento aparece claramente en el comprobante, extrae su nombre.
14. Si el establecimiento no aparece en el comprobante pero el usuario proporcionó una predeterminada, utiliza dicha opción.
15. Si no existe información suficiente para identificar el establecimiento, utiliza: "No identificado"
16. El nombre del archivo puede utilizarse como apoyo únicamente cuando exista una relación evidente y directa.
17. No conviertas una inferencia débil en un dato cierto.
18. Devuelve únicamente la estructura solicitada.
"""

    max_intentos = 5

    for intento in range(max_intentos):
        try:
            response = client.models.generate_content(
                model=GEMINI_MODEL,
                contents=[
                    image,
                    prompt
                ],
                config=types.GenerateContentConfig(
                    response_mime_type="application/json",
                    response_schema=ComprobanteData,
                    temperature=0.1
                )
            )

            parsed = getattr(response, "parsed", None)

            if parsed is not None:
                if isinstance(parsed, ComprobanteData):
                    data = parsed
                else:
                    data = ComprobanteData.model_validate(parsed)
            else:
                texto_respuesta = getattr(response, "text", "")
                if not texto_respuesta:
                    return None, "Gemini no devolvió contenido."

                data = ComprobanteData.model_validate_json(texto_respuesta)

            return data, None

        except ValidationError as e:
            return None, f"La respuesta de Gemini no pudo validarse contra el esquema contable: {e}"

        except Exception as e:
            error_msg = str(e)
            categoria = clasificar_error(error_msg)

            if categoria == "AUTH":
                return None, "❌ Error de autenticación con Gemini. Verifique GEMINI_API_KEY en Streamlit Secrets."

            if categoria == "PERMISSION":
                return None, "❌ Gemini rechazó la solicitud por permisos. Revise el proyecto de Google Cloud."

            if categoria == "QUOTA":
                if intento < max_intentos - 1:
                    espera = obtener_espera(error_msg, intento)
                    st.warning(f"⏳ Límite de cuota para '{nombre_archivo}'. Reintentando en {espera:.0f} segundos...")
                    time.sleep(espera)
                    continue
                return None, "❌ Se agotó la cuota disponible de Gemini después de varios intentos."

            if categoria == "TEMPORARY":
                if intento < max_intentos - 1:
                    espera = min(30, 5 * (2 ** intento))
                    st.warning(f"⚠️ Error temporal procesando '{nombre_archivo}'. Reintentando en {espera} segundos...")
                    time.sleep(espera)
                    continue
                return None, "❌ El servicio de Gemini no respondió correctamente después de varios intentos."

            return None, f"❌ Error procesando '{nombre_archivo}': {error_msg}"

    return None, f"❌ No fue posible procesar '{nombre_archivo}'."


# ============================================================
# NORMALIZAR MEDIO DE PAGO
# ============================================================

def clasificar_medio_pago(medio_pago, banco_plataforma=""):
    texto = normalizar_texto(f"{medio_pago} {banco_plataforma}")

    if (
        "pago credito" in texto
        or "pago de credito" in texto
        or "abono credito" in texto
        or "abono a credito" in texto
    ):
        return "PAGO CREDITO"

    if "nequi" in texto:
        return "NEQUI"

    if (
        "credito" in texto
        or "credit card" in texto
        or "tarjeta de credito" in texto
    ):
        return "CREDITO"

    if (
        "efectivo" in texto
        or "cash" in texto
        or "deposito efectivo" in texto
    ):
        return "EFECTIVO"

    return "OTRO"


# ============================================================
# PREPARAR DATOS PARA INGRESOS
# ============================================================

def preparar_ingresos(df):
    if df is None or df.empty:
        return pd.DataFrame()

    datos = df.copy()

    datos["fecha_dt"] = pd.to_datetime(
        datos["fecha_hora"],
        errors="coerce"
    )

    datos = datos[datos["fecha_dt"].notna()].copy()

    datos["monto_cop"] = pd.to_numeric(
        datos["monto_cop"],
        errors="coerce"
    ).fillna(0)

    if "tienda" not in datos.columns:
        datos["tienda"] = "No identificado"

    datos["tienda"] = (
        datos["tienda"]
        .fillna("No identificado")
        .astype(str)
        .str.strip()
    )

    datos.loc[datos["tienda"] == "", "tienda"] = "No identificado"

    datos["categoria_pago"] = datos.apply(
        lambda fila: clasificar_medio_pago(
            fila.get("medio_pago_tipo", ""),
            fila.get("banco_plataforma", "")
        ),
        axis=1
    )

    datos = datos[
        datos["tipo_movimiento"]
        .astype(str)
        .str.lower()
        .eq("ingreso")
    ].copy()

    return datos


# ============================================================
# DETECTAR BLOQUES DE DÍAS EN LA PLANTILLA
# ============================================================

def detectar_bloques_dias(ws, columna_fecha="A"):
    filas_fecha = []

    for fila in range(1, ws.max_row + 1):
        valor = ws[f"{columna_fecha}{fila}"].value

        if isinstance(valor, (pd.Timestamp,)):
            filas_fecha.append(fila)
        elif hasattr(valor, "year") and hasattr(valor, "month") and hasattr(valor, "day"):
            filas_fecha.append(fila)

    filas_fecha = sorted(set(filas_fecha))
    bloques = []

    for idx, fila_inicio in enumerate(filas_fecha):
        if idx < len(filas_fecha) - 1:
            fila_fin = filas_fecha[idx + 1] - 1
        else:
            fila_fin = ws.max_row - 1

        bloques.append((fila_inicio, fila_fin))

    return bloques


# ============================================================
# COPIAR ESTILO DE UNA FILA
# ============================================================

def copiar_estilo_fila(ws, fila_origen, fila_destino, columnas=7):
    for col in range(1, columnas + 1):
        origen = ws.cell(fila_origen, col)
        destino = ws.cell(fila_destino, col)

        if origen.has_style:
            destino._style = copy.copy(origen._style)
        if origen.number_format:
            destino.number_format = origen.number_format
        if origen.alignment:
            destino.alignment = copy.copy(origen.alignment)
        if origen.protection:
            destino.protection = copy.copy(origen.protection)
        if origen.font:
            destino.font = copy.copy(origen.font)
        if origen.fill:
            destino.fill = copy.copy(origen.fill)
        if origen.border:
            destino.border = copy.copy(origen.border)


# ============================================================
# VERIFICAR SI UNA CELDA ES PARTE DE UNA FUSIÓN
# ============================================================

def celda_fusionada(ws, fila, columna):
    coordenada = f"{get_column_letter(columna)}{fila}"
    for rango in ws.merged_cells.ranges:
        if coordenada in rango:
            return True
    return False


# ============================================================
# ESCRIBIR CELDA DE FORMA SEGURA
# ============================================================

def escribir_celda_segura(ws, fila, columna, valor):
    if celda_fusionada(ws, fila, columna):
        coordenada = f"{get_column_letter(columna)}{fila}"
        for rango in ws.merged_cells.ranges:
            if coordenada in rango:
                if rango.min_row == fila and rango.min_col == columna:
                    ws.cell(fila, columna).value = valor
                return
    ws.cell(fila, columna).value = valor


# ============================================================
# LIMPIAR CONTENIDO DE UNA FILA SIN DESTRUIR FORMATO
# ============================================================

def limpiar_fila(ws, fila, columnas):
    for columna in range(1, columnas + 1):
        if not celda_fusionada(ws, fila, columna):
            ws.cell(fila, columna).value = None


# ============================================================
# ESCRIBIR BLOQUE DE INGRESOS
# ============================================================

def escribir_bloque_ingresos(ws, df_mes, bloques, columnas_inicio):
    if df_mes.empty:
        return

    agrupado = (
        df_mes
        .groupby(
            ["fecha_dt", "tienda", "categoria_pago"],
            as_index=False
        )["monto_cop"]
        .sum()
    )

    pivot = (
        agrupado
        .pivot_table(
            index=["fecha_dt", "tienda"],
            columns="categoria_pago",
            values="monto_cop",
            aggfunc="sum",
            fill_value=0
        )
        .reset_index()
    )

    for columna in ["EFECTIVO", "NEQUI", "CREDITO", "PAGO CREDITO"]:
        if columna not in pivot.columns:
            pivot[columna] = 0

    for fila_inicio, fila_fin in bloques:
        fecha_plantilla = ws.cell(fila_inicio, columnas_inicio).value
        fecha = pd.to_datetime(fecha_plantilla, errors="coerce")

        if pd.isna(fecha):
            continue

        fecha = fecha.normalize()
        datos_dia = pivot[pivot["fecha_dt"].dt.normalize() == fecha].copy()

        if datos_dia.empty:
            continue

        filas_disponibles = list(range(fila_inicio, fila_fin + 1))

        if len(filas_disponibles) > 1:
            filas_datos = filas_disponibles[:-1]
        else:
            filas_datos = filas_disponibles

        if len(datos_dia) > len(filas_datos):
            datos_dia = datos_dia.head(len(filas_datos))

        for idx, (_, registro) in enumerate(datos_dia.iterrows()):
            if idx >= len(filas_datos):
                break

            fila = filas_datos[idx]
            efectivo = float(registro.get("EFECTIVO", 0))
            nequi = float(registro.get("NEQUI", 0))
            credito = float(registro.get("CREDITO", 0))
            pago_credito = float(registro.get("PAGO CREDITO", 0))

            escribir_celda_segura(ws, fila, columnas_inicio, fecha.to_pydatetime())
            escribir_celda_segura(ws, fila, columnas_inicio + 1, registro["tienda"])
            escribir_celda_segura(ws, fila, columnas_inicio + 2, efectivo)
            escribir_celda_segura(ws, fila, columnas_inicio + 3, nequi)
            escribir_celda_segura(ws, fila, columnas_inicio + 4, credito)
            escribir_celda_segura(ws, fila, columnas_inicio + 5, pago_credito)
            escribir_celda_segura(
                ws,
                fila,
                columnas_inicio + 6,
                f"=SUM({get_column_letter(columnas_inicio + 2)}{fila}:{get_column_letter(columnas_inicio + 5)}{fila})"
            )


# ============================================================
# INTERFAZ PRINCIPAL DE STREAMLIT
# ============================================================

def main():
    st.title("📊 Procesador Contable de Comprobantes")
    st.markdown("Extrae la información contable de tus comprobantes e imágenes usando IA.")

    # Inicialización del cliente Gemini
    try:
        client = genai.Client(api_key=API_KEY)
    except Exception as e:
        st.error(f"Error inicializando cliente de Gemini: {e}")
        st.stop()

    # Formulario / Entradas de usuario
    col1, col2 = st.columns(2)
    with col1:
        tipo_movimiento = st.selectbox(
            "Seleccione el tipo de movimiento:",
            ["Ingreso", "Egreso"]
        )
    with col2:
        tienda_predeterminada = st.text_input(
            "Identificación del establecimiento (Opcional):",
            placeholder="Ej. Tienda Central / Sede Norte"
        )

    archivos_subidos = st.file_uploader(
        "Cargue los comprobantes (Imágenes PNG, JPG, JPEG):",
        type=["png", "jpg", "jpeg"],
        accept_multiple_files=True
    )

    if archivos_subidos:
        if st.button("🚀 Procesar Comprobantes", type="primary"):
            resultados = []

            progreso = st.progress(0)
            status = st.empty()

            total_archivos = len(archivos_subidos)

            for idx, archivo in enumerate(archivos_subidos):
                status.text(f"Procesando {idx + 1}/{total_archivos}: {archivo.name}")

                try:
                    imagen = Image.open(archivo)
                    data, err = procesar_comprobante(
                        client=client,
                        image=imagen,
                        nombre_archivo=archivo.name,
                        tipo_movimiento=tipo_movimiento,
                        tienda_predeterminada=tienda_predeterminada
                    )

                    if err:
                        st.error(f"Error en '{archivo.name}': {err}")
                    elif data:
                        res_dict = data.model_dump()
                        res_dict["archivo_origen"] = archivo.name
                        resultados.append(res_dict)

                except Exception as ex:
                    st.error(f"Error al leer el archivo '{archivo.name}': {ex}")

                progreso.progress((idx + 1) / total_archivos)

            status.text("✅ Procesamiento finalizado.")

            if resultados:
                df_resultados = pd.DataFrame(resultados)
                st.subheader("📋 Resultados Extraídos")
                st.dataframe(df_resultados, use_container_width=True)

                # Descarga CSV
                csv_data = df_resultados.to_csv(index=False).encode('utf-8')
                st.download_button(
                    label="📥 Descargar CSV",
                    data=csv_data,
                    file_name="comprobantes_procesados.csv",
                    mime="text/csv"
                )


if __name__ == "__main__":
    main()

    






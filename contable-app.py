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

# IMPORTANTE:
#
# La API KEY NO debe estar escrita en este archivo.
#
# En Streamlit Cloud:
#
# Manage app
# → Settings
# → Secrets
#
# Utilizar:
#
# GEMINI_API_KEY = "TU_API_KEY"
#
# ============================================================

try:

    API_KEY = st.secrets.get(
        "GEMINI_API_KEY",
        ""
    )

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
# NOMBRE DE LA PLANTILLA
# ============================================================

ARCHIVO_PLANTILLA = (
    "FORMATOS INGRESOS GASTOS.xlsx"
)


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

    st.error(
        "❌ No se encontró GEMINI_API_KEY."
    )

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

    st.error(
        "❌ No fue posible cargar el SDK de Google Gemini."
    )

    st.code(
        GEMINI_SDK_ERROR,
        language="text"
    )

    st.info(
        "Verifique que requirements.txt contenga "
        "'google-genai'."
    )

    st.stop()


if not PYDANTIC_OK:

    st.error(
        "❌ No fue posible cargar Pydantic."
    )

    st.code(
        PYDANTIC_ERROR,
        language="text"
    )

    st.info(
        "Verifique que requirements.txt contenga "
        "'pydantic'."
    )

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
            "Nombre de la tienda, sede, establecimiento o "
            "punto de venta al que corresponde la operación. "
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


# ============================================================
# NORMALIZAR NOMBRE
# ============================================================

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


# ============================================================
# VALIDAR RESULTADO
# ============================================================

def validar_resultado(
    data: ComprobanteData,
    tipo_seleccionado: str
):

    errores = []

    tipo = normalizar_texto(
        data.tipo_movimiento
    )

    if tipo not in [
        "ingreso",
        "egreso"
    ]:

        errores.append(
            f"Tipo de movimiento no válido: "
            f"{data.tipo_movimiento}"
        )

    if tipo != normalizar_texto(
        tipo_seleccionado
    ):

        errores.append(
            f"El comprobante fue clasificado como "
            f"'{data.tipo_movimiento}', pero el usuario "
            f"seleccionó '{tipo_seleccionado}'."
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
            f"Fecha/hora no válida: "
            f"{data.fecha_hora}"
        )

    return errores


# ============================================================
# CALCULAR ESPERA PARA ERRORES 429
# ============================================================

def obtener_espera(
    error_msg,
    intento
):

    match = re.search(
        r"retry in\s+(\d+(?:\.\d+)?)s",
        error_msg,
        re.IGNORECASE
    )

    if match:

        espera = (
            float(match.group(1))
            + 2
        )

    else:

        espera = min(
            60,
            10 * (2 ** intento)
        )

    return espera


# ============================================================
# CLASIFICAR ERROR
# ============================================================

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

Tienda o sede proporcionada manualmente por el usuario,
si existe:

"{tienda_predeterminada}"


OBJETIVO:

Extrae exclusivamente la información que pueda ser
identificada claramente en el comprobante.


REGLAS IMPORTANTES:

1. No inventes información.

2. Si un campo no aparece claramente, utiliza:
   "No identificado".

3. El monto debe corresponder al valor real
   de la operación.

4. El monto debe expresarse como número en COP,
   sin símbolo de moneda, puntos ni separadores.

5. La fecha debe convertirse al formato:

   YYYY-MM-DD HH:MM

6. Si la hora no está visible, utiliza:

   00:00

7. Identifica correctamente la entidad financiera
   o plataforma.

8. Identifica la referencia, autorización o ID
   de operación cuando exista.

9. Conserva el concepto o número de factura
   cuando sea visible.

10. No confundas número de factura con número
    de autorización, referencia o ID.

11. El campo TIENDA es independiente del campo
    DESTINATARIO/REMITENTE.

12. No utilices automáticamente el destinatario,
    remitente, banco o plataforma como nombre
    de la tienda.

13. Si la tienda aparece claramente en el comprobante,
    extrae su nombre.

14. Si la tienda no aparece en el comprobante pero
    el usuario proporcionó una tienda predeterminada,
    utiliza dicha tienda.

15. Si no existe información suficiente para identificar
    la tienda, utiliza:

    "No identificado"

16. El nombre del archivo puede utilizarse como apoyo
    para identificar la tienda únicamente cuando exista
    una relación evidente y directa.

17. No conviertas una inferencia débil en un dato cierto.

18. Devuelve únicamente la estructura solicitada.
"""


    max_intentos = 5


    for intento in range(
        max_intentos
    ):

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


            # ==================================================
            # RESPUESTA ESTRUCTURADA
            # ==================================================

            parsed = getattr(
                response,
                "parsed",
                None
            )


            if parsed is not None:

                if isinstance(
                    parsed,
                    ComprobanteData
                ):

                    data = parsed

                else:

                    data = (
                        ComprobanteData
                        .model_validate(
                            parsed
                        )
                    )


            else:

                texto_respuesta = getattr(
                    response,
                    "text",
                    ""
                )


                if not texto_respuesta:

                    return (
                        None,
                        "Gemini no devolvió contenido."
                    )


                data = (
                    ComprobanteData
                    .model_validate_json(
                        texto_respuesta
                    )
                )


            return data, None


        # ======================================================
        # ERROR DE VALIDACIÓN
        # ======================================================

        except ValidationError as e:

            return (
                None,
                "La respuesta de Gemini no pudo "
                "validarse contra el esquema contable: "
                f"{e}"
            )


        # ======================================================
        # OTROS ERRORES
        # ======================================================

        except Exception as e:

            error_msg = str(e)

            categoria = clasificar_error(
                error_msg
            )


            # --------------------------------------------------
            # AUTENTICACIÓN
            # --------------------------------------------------

            if categoria == "AUTH":

                return (
                    None,
                    "❌ Error de autenticación con Gemini. "
                    "Verifique GEMINI_API_KEY en Streamlit Secrets."
                )


            # --------------------------------------------------
            # PERMISOS
            # --------------------------------------------------

            if categoria == "PERMISSION":

                return (
                    None,
                    "❌ Gemini rechazó la solicitud por permisos. "
                    "Revise el proyecto de Google Cloud asociado "
                    "a la API key."
                )


            # --------------------------------------------------
            # CUOTA
            # --------------------------------------------------

            if categoria == "QUOTA":

                if intento < max_intentos - 1:

                    espera = obtener_espera(
                        error_msg,
                        intento
                    )

                    st.warning(
                        f"⏳ Límite de cuota para "
                        f"'{nombre_archivo}'. "
                        f"Reintentando en "
                        f"{espera:.0f} segundos..."
                    )

                    time.sleep(
                        espera
                    )

                    continue


                return (
                    None,
                    "❌ Se agotó la cuota disponible "
                    "de Gemini después de varios intentos."
                )


            # --------------------------------------------------
            # ERROR TEMPORAL
            # --------------------------------------------------

            if categoria == "TEMPORARY":

                if intento < max_intentos - 1:

                    espera = min(
                        30,
                        5 * (2 ** intento)
                    )

                    st.warning(
                        f"⚠️ Error temporal procesando "
                        f"'{nombre_archivo}'. "
                        f"Reintentando en "
                        f"{espera} segundos..."
                    )

                    time.sleep(
                        espera
                    )

                    continue


                return (
                    None,
                    "❌ El servicio de Gemini no respondió "
                    "correctamente después de varios intentos."
                )


            # --------------------------------------------------
            # OTROS ERRORES
            # --------------------------------------------------

            return (
                None,
                f"❌ Error procesando "
                f"'{nombre_archivo}': "
                f"{error_msg}"
            )


    return (
        None,
        f"❌ No fue posible procesar "
        f"'{nombre_archivo}'."
    )


# ============================================================
# NORMALIZAR MEDIO DE PAGO
# ============================================================

def clasificar_medio_pago(
    medio_pago,
    banco_plataforma=""
):

    texto = normalizar_texto(
        f"{medio_pago} {banco_plataforma}"
    )


    # --------------------------------------------------------
    # PAGO DE CRÉDITO
    # --------------------------------------------------------

    if (
        "pago credito" in texto
        or "pago de credito" in texto
        or "abono credito" in texto
        or "abono a credito" in texto
    ):

        return "PAGO CREDITO"


    # --------------------------------------------------------
    # NEQUI
    # --------------------------------------------------------

    if "nequi" in texto:

        return "NEQUI"


    # --------------------------------------------------------
    # CRÉDITO
    # --------------------------------------------------------

    if (
        "credito" in texto
        or "credit card" in texto
        or "tarjeta de credito" in texto
    ):

        return "CREDITO"


    # --------------------------------------------------------
    # EFECTIVO
    # --------------------------------------------------------

    if (
        "efectivo" in texto
        or "cash" in texto
        or "deposito efectivo" in texto
    ):

        return "EFECTIVO"


    # --------------------------------------------------------
    # OTROS CASOS
    # --------------------------------------------------------

    # Transferencias, QR, Bre-B, etc.
    #
    # Se clasifican como NEQUI solamente si el comprobante
    # identifica expresamente Nequi.
    #
    # Los demás medios no se asignan arbitrariamente.

    return "OTRO"


# ============================================================
# PREPARAR DATOS PARA INGRESOS
# ============================================================

def preparar_ingresos(
    df
):

    if df is None or df.empty:

        return pd.DataFrame()


    datos = df.copy()


    # --------------------------------------------------------
    # FECHA
    # --------------------------------------------------------

    datos["fecha_dt"] = pd.to_datetime(
        datos["fecha_hora"],
        errors="coerce"
    )


    datos = datos[
        datos["fecha_dt"].notna()
    ].copy()


    # --------------------------------------------------------
    # MONTO
    # --------------------------------------------------------

    datos["monto_cop"] = pd.to_numeric(
        datos["monto_cop"],
        errors="coerce"
    ).fillna(0)


    # --------------------------------------------------------
    # TIENDA
    # --------------------------------------------------------

    if "tienda" not in datos.columns:

        datos["tienda"] = (
            "No identificado"
        )


    datos["tienda"] = (
        datos["tienda"]
        .fillna("No identificado")
        .astype(str)
        .str.strip()
    )


    datos.loc[
        datos["tienda"] == "",
        "tienda"
    ] = "No identificado"


    # --------------------------------------------------------
    # MEDIO DE PAGO
    # --------------------------------------------------------

    datos["categoria_pago"] = datos.apply(

        lambda fila:
        clasificar_medio_pago(

            fila.get(
                "medio_pago_tipo",
                ""
            ),

            fila.get(
                "banco_plataforma",
                ""
            )

        ),

        axis=1
    )


    # --------------------------------------------------------
    # SOLO INGRESOS
    # --------------------------------------------------------

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

def detectar_bloques_dias(
    ws,
    columna_fecha="A"
):

    filas_fecha = []


    for fila in range(
        1,
        ws.max_row + 1
    ):

        valor = ws[
            f"{columna_fecha}{fila}"
        ].value


        if isinstance(
            valor,
            (pd.Timestamp,)
        ):

            filas_fecha.append(
                fila
            )

        elif hasattr(
            valor,
            "year"
        ) and hasattr(
            valor,
            "month"
        ) and hasattr(
            valor,
            "day"
        ):

            filas_fecha.append(
                fila
            )


    filas_fecha = sorted(
        set(filas_fecha)
    )


    bloques = []


    for idx, fila_inicio in enumerate(
        filas_fecha
    ):

        if idx < len(filas_fecha) - 1:

            fila_fin = (
                filas_fecha[idx + 1]
                - 1
            )

        else:

            # El TOTAL suele estar al final.
            # No incluimos la fila TOTAL.

            fila_fin = (
                ws.max_row - 1
            )


        bloques.append(
            (
                fila_inicio,
                fila_fin
            )
        )


    return bloques


# ============================================================
# COPIAR ESTILO DE UNA FILA
# ============================================================

def copiar_estilo_fila(
    ws,
    fila_origen,
    fila_destino,
    columnas=7
):

    for col in range(
        1,
        columnas + 1
    ):

        origen = ws.cell(
            fila_origen,
            col
        )

        destino = ws.cell(
            fila_destino,
            col
        )


        if origen.has_style:

            destino._style = copy.copy(
                origen._style
            )


        if origen.number_format:

            destino.number_format = (
                origen.number_format
            )


        if origen.alignment:

            destino.alignment = copy.copy(
                origen.alignment
            )


        if origen.protection:

            destino.protection = copy.copy(
                origen.protection
            )


        if origen.font:

            destino.font = copy.copy(
                origen.font
            )


        if origen.fill:

            destino.fill = copy.copy(
                origen.fill
            )


        if origen.border:

            destino.border = copy.copy(
                origen.border
            )


# ============================================================
# VERIFICAR SI UNA CELDA ES PARTE DE UNA FUSIÓN
# ============================================================

def celda_fusionada(
    ws,
    fila,
    columna
):

    coordenada = (
        f"{get_column_letter(columna)}{fila}"
    )


    for rango in ws.merged_cells.ranges:

        if coordenada in rango:

            return True


    return False


# ============================================================
# ESCRIBIR CELDA DE FORMA SEGURA
# ============================================================

def escribir_celda_segura(
    ws,
    fila,
    columna,
    valor
):

    if celda_fusionada(
        ws,
        fila,
        columna
    ):

        # Solo se permite escribir sobre
        # la celda superior izquierda de
        # una combinación.

        coordenada = (
            f"{get_column_letter(columna)}{fila}"
        )

        for rango in ws.merged_cells.ranges:

            if coordenada in rango:

                if (
                    rango.min_row == fila
                    and rango.min_col == columna
                ):

                    ws.cell(
                        fila,
                        columna
                    ).value = valor

                return


    ws.cell(
        fila,
        columna
    ).value = valor


# ============================================================
# LIMPIAR CONTENIDO DE UNA FILA SIN DESTRUIR FORMATO
# ============================================================

def limpiar_fila(
    ws,
    fila,
    columnas
):

    for columna in range(
        1,
        columnas + 1
    ):

        if not celda_fusionada(
            ws,
            fila,
            columna
        ):

            ws.cell(
                fila,
                columna
            ).value = None


# ============================================================
# ESCRIBIR BLOQUE DE INGRESOS
# ============================================================

def escribir_bloque_ingresos(
    ws,
    df_mes,
    bloques,
    columnas_inicio
):

    if df_mes.empty:

        return


    # ========================================================
    # AGRUPAR POR FECHA + TIENDA
    # ========================================================

    agrupado = (
        df_mes
        .groupby(
            [
                "fecha_dt",
                "tienda",
                "categoria_pago"
            ],
            as_index=False
        )["monto_cop"]
        .sum()
    )


    # ========================================================
    # TABLA PIVOTE
    # ========================================================

    pivot = (
        agrupado
        .pivot_table(
            index=[
                "fecha_dt",
                "tienda"
            ],
            columns="categoria_pago",
            values="monto_cop",
            aggfunc="sum",
            fill_value=0
        )
        .reset_index()
    )


    for columna in [
        "EFECTIVO",
        "NEQUI",
        "CREDITO",
        "PAGO CREDITO"
    ]:

        if columna not in pivot.columns:

            pivot[columna] = 0


    # ========================================================
    # RECORRER DÍAS
    # ========================================================

    for (
        fila_inicio,
        fila_fin
    ) in bloques:


        fecha_plantilla = ws.cell(
            fila_inicio,
            columnas_inicio
        ).value


        fecha = pd.to_datetime(
            fecha_plantilla,
            errors="coerce"
        )


        if pd.isna(fecha):

            continue


        fecha = fecha.normalize()


        datos_dia = pivot[
            pivot["fecha_dt"].dt.normalize()
            == fecha
        ].copy()


        if datos_dia.empty:

            continue


        # ----------------------------------------------------
        # CAPACIDAD DEL BLOQUE
        # ----------------------------------------------------

        filas_disponibles = list(
            range(
                fila_inicio,
                fila_fin + 1
            )
        )


        # Reservamos la última fila del bloque
        # para el TOTAL DEL DÍA cuando sea posible.

        if len(filas_disponibles) > 1:

            filas_datos = (
                filas_disponibles[:-1]
            )

            fila_total_dia = (
                filas_disponibles[-1]
            )

        else:

            filas_datos = (
                filas_disponibles
            )

            fila_total_dia = None


        # ----------------------------------------------------
        # SI HAY MÁS TIENDAS QUE FILAS
        # ----------------------------------------------------

        if len(datos_dia) > len(
            filas_datos
        ):

            # Consolidar excedentes en "Otras tiendas"

            datos_dia = datos_dia.copy()

            datos_dia["orden"] = range(
                len(datos_dia)
            )

            datos_dia = datos_dia.head(
                len(filas_datos)
            )


        # ----------------------------------------------------
        # ESCRIBIR CADA TIENDA
        # ----------------------------------------------------

        for idx, (_, registro) in enumerate(
            datos_dia.iterrows()
        ):

            if idx >= len(filas_datos):

                break


            fila = filas_datos[idx]


            efectivo = float(
                registro.get(
                    "EFECTIVO",
                    0
                )
            )


            nequi = float(
                registro.get(
                    "NEQUI",
                    0
                )
            )


            credito = float(
                registro.get(
                    "CREDITO",
                    0
                )
            )


            pago_credito = float(
                registro.get(
                    "PAGO CREDITO",
                    0
                )
            )


            # ------------------------------------------------
            # FECHA
            # ------------------------------------------------

            escribir_celda_segura(
                ws,
                fila,
                columnas_inicio,
                fecha.to_pydatetime()
            )


            # ------------------------------------------------
            # TIENDA
            # ------------------------------------------------

            escribir_celda_segura(
                ws,
                fila,
                columnas_inicio + 1,
                registro["tienda"]
            )


            # ------------------------------------------------
            # EFECTIVO
            # ------------------------------------------------

            escribir_celda_segura(
                ws,
                fila,
                columnas_inicio + 2,
                efectivo
            )


            # ------------------------------------------------
            # NEQUI
            # ------------------------------------------------

            escribir_celda_segura(
                ws,
                fila,
                columnas_inicio + 3,
                nequi
            )


            # ------------------------------------------------
            # CREDITO
            # ------------------------------------------------

            escribir_celda_segura(
                ws,
                fila,
                columnas_inicio + 4,
                credito
            )


            # ------------------------------------------------
            # PAGO CREDITO
            # ------------------------------------------------

            escribir_celda_segura(
                ws,
                fila,
                columnas_inicio + 5,
                pago_credito
            )


            # ------------------------------------------------
            # TOTAL
            # ------------------------------------------------

            escribir_celda_segura(

                ws,

                fila,

                columnas_inicio + 6,

                f"=SUM("
                f"{get_column_letter(columnas_inicio + 2)}{fila}:"
                f"{get_column_letter(columnas_inicio + 5)}{fila}"
                f")"

            )


        # ====================================================
        # TOTAL DEL DÍA
        # ====================================================

        if (
            fila_total_dia is not None
            and len(datos_dia) > 0
        ):

            escribir_celda_segura(
                ws,
                fila_total_dia,
                columnas_inicio + 1,
                "TOTAL DÍA"
            )


            for offset in range(
                2,
                7
            ):

                columna = (
                    columnas_inicio
                    + offset
                )


                letra = get_column_letter(
                    columna
                )


                fila_primera = (
                    filas_datos[0]
                )


                fila_ultima = (
                    filas_datos[
                        min(
                            len(datos_dia) - 1,
                            len(filas_datos) - 1
                        )
                    ]
                )


                escribir_celda_segura(

                    ws,

                    fila_total_dia,

                    columna,

                    f"=SUM("
                    f"{letra}{fila_primera}:"
                    f"{letra}{fila_ultima}"
                    f")"

                )


# ============================================================
# ACTUALIZAR MES DE LA PLANTILLA
# ============================================================

def actualizar_mes(
    ws,
    fecha,
    columna_mes
):

    meses = [
        "ENERO",
        "FEBRERO",
        "MARZO",
        "ABRIL",
        "MAYO",
        "JUNIO",
        "JULIO",
        "AGOSTO",
        "SEPTIEMBRE",
        "OCTUBRE",
        "NOVIEMBRE",
        "DICIEMBRE"
    ]


    if pd.isna(fecha):

        return


    nombre_mes = meses[
        fecha.month - 1
    ]


    valor_actual = ws.cell(
        1,
        columna_mes
    ).value


    if (
        valor_actual is None
        or str(valor_actual).strip() == ""
    ):

        escribir_celda_segura(
            ws,
            1,
            columna_mes,
            f"MES: {nombre_mes}"
        )

    else:

        texto = str(
            valor_actual
        )


        texto = re.sub(
            r"MES\s*:\s*\w+",
            f"MES: {nombre_mes}",
            texto,
            flags=re.IGNORECASE
        )


        escribir_celda_segura(
            ws,
            1,
            columna_mes,
            texto
        )


# ============================================================
# GENERAR EXCEL CONTABLE CON PLANTILLA
# ============================================================

def generar_excel_contable(
    df_resultados,
    ruta_plantilla
):

    if not os.path.exists(
        ruta_plantilla
    ):

        raise FileNotFoundError(
            f"No se encontró la plantilla: "
            f"{ruta_plantilla}"
        )


    if (
        df_resultados is None
        or df_resultados.empty
    ):

        raise ValueError(
            "No existen resultados para generar "
            "el Excel contable."
        )


    # ========================================================
    # CARGAR PLANTILLA
    # ========================================================

    wb = load_workbook(
        ruta_plantilla
    )


    # ========================================================
    # CONSERVAR ÚNICAMENTE LAS CINCO HOJAS
    # ========================================================

    for nombre_hoja in list(
        wb.sheetnames
    ):

        if nombre_hoja not in (
            HOJAS_PERMITIDAS
        ):

            ws_eliminar = wb[
                nombre_hoja
            ]

            wb.remove(
                ws_eliminar
            )


    # ========================================================
    # ASEGURAR HOJA INGRESOS
    # ========================================================

    if "INGRESOS" not in wb.sheetnames:

        raise ValueError(
            "La plantilla no contiene la hoja "
            "'INGRESOS'."
        )


    ws = wb[
        "INGRESOS"
    ]


    # ========================================================
    # PREPARAR INGRESOS
    # ========================================================

    datos = preparar_ingresos(
        df_resultados
    )


    if datos.empty:

        raise ValueError(
            "No se encontraron movimientos "
            "de tipo Ingreso."
        )


    # ========================================================
    # MESES EXISTENTES EN LOS DATOS
    # ========================================================

    datos["mes_periodo"] = (
        datos["fecha_dt"]
        .dt.to_period("M")
    )


    periodos = sorted(
        datos["mes_periodo"]
        .dropna()
        .unique()
    )


    if len(periodos) == 0:

        raise ValueError(
            "No existen fechas válidas "
            "para generar el Excel."
        )


    # ========================================================
    # LA PLANTILLA TIENE DOS BLOQUES:
    #
    # A:G  → primer mes
    # I:O  → segundo mes
    #
    # ========================================================

    configuracion_bloques = [

        {
            "periodo": periodos[0]
            if len(periodos) >= 1
            else None,

            "columna_inicio": 1,

            "columna_mes": 4
        },

        {
            "periodo": periodos[1]
            if len(periodos) >= 2
            else None,

            "columna_inicio": 9,

            "columna_mes": 12
        }

    ]


    # ========================================================
    # DETECTAR BLOQUES DE FECHAS
    # ========================================================

    bloques_izquierda = (
        detectar_bloques_dias(
            ws,
            "A"
        )
    )


    bloques_derecha = (
        detectar_bloques_dias(
            ws,
            "I"
        )
    )


    # ========================================================
    # PROCESAR CADA BLOQUE MENSUAL
    # ========================================================

    for configuracion in (
        configuracion_bloques
    ):

        periodo = configuracion[
            "periodo"
        ]


        if periodo is None:

            continue


        columna_inicio = configuracion[
            "columna_inicio"
        ]


        columna_mes = configuracion[
            "columna_mes"
        ]


        datos_mes = datos[
            datos["mes_periodo"]
            == periodo
        ].copy()


        if datos_mes.empty:

            continue


        # ----------------------------------------------------
        # ACTUALIZAR NOMBRE DEL MES
        # ----------------------------------------------------

        fecha_referencia = (
            datos_mes["fecha_dt"]
            .min()
        )


        actualizar_mes(
            ws,
            fecha_referencia,
            columna_mes
        )


        # ----------------------------------------------------
        # SELECCIONAR BLOQUES
        # ----------------------------------------------------

        if columna_inicio == 1:

            bloques = (
                bloques_izquierda
            )

        else:

            bloques = (
                bloques_derecha
            )


        # ----------------------------------------------------
        # ESCRIBIR
        # ----------------------------------------------------

        escribir_bloque_ingresos(

            ws=ws,

            df_mes=datos_mes,

            bloques=bloques,

            columnas_inicio=columna_inicio

        )


    # ========================================================
    # ACTUALIZAR TOTAL GENERAL
    # ========================================================

    # La plantilla original utiliza la fila TOTAL.
    #
    # Buscamos la fila que contenga "TOTAL"
    # en la columna A.

    fila_total = None


    for fila in range(
        1,
        ws.max_row + 1
    ):

        valor = ws.cell(
            fila,
            1
        ).value


        if (
            isinstance(
                valor,
                str
            )
            and valor.strip().upper()
            == "TOTAL"
        ):

            fila_total = fila

            break


    if fila_total is not None:

        # ----------------------------------------------------
        # TOTAL A:G
        # ----------------------------------------------------

        for columna in range(
            3,
            8
        ):

            letra = get_column_letter(
                columna
            )


            escribir_celda_segura(

                ws,

                fila_total,

                columna,

                f"=SUM("
                f"{letra}3:"
                f"{letra}{fila_total - 1}"
                f")"

            )


        # ----------------------------------------------------
        # TOTAL I:O
        # ----------------------------------------------------

        for columna in range(
            11,
            16
        ):

            letra = get_column_letter(
                columna
            )


            escribir_celda_segura(

                ws,

                fila_total,

                columna,

                f"=SUM("
                f"{letra}3:"
                f"{letra}{fila_total - 1}"
                f")"

            )


    # ========================================================
    # FORZAR RECÁLCULO DE FÓRMULAS
    # ========================================================

    try:

        wb.calculation.fullCalcOnLoad = True
        wb.calculation.forceFullCalc = True
        wb.calculation.calcMode = "auto"

    except Exception:

        pass


    # ========================================================
    # GUARDAR EN MEMORIA
    # ========================================================

    output = BytesIO()


    wb.save(
        output
    )


    output.seek(0)


    return output.getvalue()


# ============================================================
# INTERFAZ PRINCIPAL
# ============================================================

st.title(
    "📊 Procesador Contable de Comprobantes"
)


st.markdown(
    """
Esta herramienta utiliza **Gemini** para analizar comprobantes
bancarios y convertir la información visual en datos estructurados
para consolidación y exportación contable.
"""
)


# ============================================================
# ESTADO DE LA PLANTILLA
# ============================================================

if os.path.exists(
    ARCHIVO_PLANTILLA
):

    st.success(
        "✅ Plantilla contable encontrada."
    )

else:

    st.error(
        f"❌ No se encontró la plantilla "
        f"'{ARCHIVO_PLANTILLA}'."
    )

    st.info(
        """
Coloque el archivo:

FORMATOS INGRESOS GASTOS.xlsx

en la misma carpeta donde se encuentra:

contable-app.py
"""
    )


# ============================================================
# ENTRADA DE DATOS
# ============================================================

tipo = st.selectbox(

    "Seleccione el Tipo de Movimiento",

    [
        "Ingreso",
        "Egreso"
    ]

)


# ============================================================
# TIENDA PREDETERMINADA
# ============================================================

st.markdown(
    "### 🏪 Identificación de tienda"
)


tienda_predeterminada = st.text_input(

    "Tienda / Sede predeterminada (opcional)",

    placeholder=(
        "Ejemplo: Tienda Centro"
    ),

    help=(
        "Si la tienda no aparece claramente en el comprobante, "
        "este dato podrá utilizarse como tienda predeterminada."
    )

)


st.caption(
    "Si cada comprobante corresponde a una tienda diferente, "
    "déjelo vacío y Gemini intentará identificarla individualmente."
)


# ============================================================
# CARGAR COMPROBANTES
# ============================================================

uploaded_files = st.file_uploader(

    "Cargar comprobantes",

    type=[
        "png",
        "jpg",
        "jpeg",
        "webp"
    ],

    accept_multiple_files=True

)


# ============================================================
# PROCESAMIENTO
# ============================================================

if st.button(

    "Procesar Archivos",

    type="primary"

):

    if not uploaded_files:

        st.warning(
            "⚠️ Debe cargar al menos un comprobante."
        )

        st.stop()


    # ========================================================
    # CREAR CLIENTE GEMINI
    # ========================================================

    try:

        client = genai.Client(
            api_key=API_KEY
        )

    except Exception as e:

        st.error(
            "❌ No fue posible inicializar "
            "el cliente de Gemini."
        )

        st.exception(e)

        st.stop()


    resultados = []

    errores_archivos = []


    progress_bar = st.progress(
        0
    )


    estado = st.empty()


    # ========================================================
    # PROCESAR ARCHIVOS
    # ========================================================

    for idx, uploaded_file in enumerate(
        uploaded_files
    ):

        nombre_archivo = (
            uploaded_file.name
        )


        estado.info(

            f"🔎 Analizando "
            f"{idx + 1} de "
            f"{len(uploaded_files)}: "
            f"{nombre_archivo}"

        )


        try:

            # ------------------------------------------------
            # ABRIR IMAGEN
            # ------------------------------------------------

            image = Image.open(
                uploaded_file
            )


            # ------------------------------------------------
            # NORMALIZAR IMAGEN
            # ------------------------------------------------

            if image.mode != "RGB":

                image = image.convert(
                    "RGB"
                )


            # ------------------------------------------------
            # PROCESAR
            # ------------------------------------------------

            data, error = (
                procesar_comprobante(

                    client=client,

                    image=image,

                    nombre_archivo=nombre_archivo,

                    tipo_movimiento=tipo,

                    tienda_predeterminada=(
                        tienda_predeterminada
                    )

                )
            )


            # ------------------------------------------------
            # RESULTADO EXITOSO
            # ------------------------------------------------

            if data is not None:

                errores_validacion = (
                    validar_resultado(
                        data,
                        tipo
                    )
                )


                if errores_validacion:

                    st.warning(

                        f"⚠️ Advertencia en "
                        f"{nombre_archivo}: "
                        + " | ".join(
                            errores_validacion
                        )

                    )


                resultado = (
                    data.model_dump()
                )


                resultado[
                    "archivo_origen"
                ] = nombre_archivo


                resultados.append(
                    resultado
                )


            # ------------------------------------------------
            # ERROR
            # ------------------------------------------------

            else:

                errores_archivos.append(

                    {
                        "archivo":
                            nombre_archivo,

                        "error":
                            error
                    }

                )


                st.error(
                    error
                )


        except Exception as e:

            errores_archivos.append(

                {
                    "archivo":
                        nombre_archivo,

                    "error":
                        str(e)
                }

            )


            st.error(

                f"❌ No fue posible abrir o procesar "
                f"'{nombre_archivo}': {e}"

            )


        # ----------------------------------------------------
        # PROGRESO
        # ----------------------------------------------------

        progress_bar.progress(

            (idx + 1)
            / len(uploaded_files)

        )


    estado.empty()


    # ========================================================
    # GUARDAR RESULTADOS
    # ========================================================

    if resultados:

        st.session_state[
            "df_resultados"
        ] = pd.DataFrame(
            resultados
        )


        st.success(

            f"✅ Procesamiento completado. "
            f"{len(resultados)} comprobante(s) procesado(s)."

        )


    if errores_archivos:

        st.warning(

            f"⚠️ {len(errores_archivos)} archivo(s) "
            f"presentaron problemas."

        )


        with st.expander(
            "Ver detalle de errores"
        ):

            for item in errores_archivos:

                st.write(
                    f"**{item['archivo']}**"
                )

                st.write(
                    item["error"]
                )


# ============================================================
# VISUALIZACIÓN
# ============================================================

if (

    "df_resultados" in st.session_state

    and not st.session_state[
        "df_resultados"
    ].empty

):

    df = (
        st.session_state[
            "df_resultados"
        ].copy()
    )


    # ========================================================
    # FECHAS
    # ========================================================

    df["fecha_dt"] = (
        pd.to_datetime(
            df["fecha_hora"],
            errors="coerce"
        ).dt.date
    )


    st.markdown("---")


    st.subheader(
        "🔍 Filtros y Resumen Financiero"
    )


    # ========================================================
    # FILTROS
    # ========================================================

    col_filtro1, col_filtro2 = (
        st.columns(2)
    )


    with col_filtro1:

        fechas_unicas = (
            df["fecha_dt"]
            .dropna()
            .unique()
        )


        if len(
            fechas_unicas
        ) > 0:

            min_date = min(
                fechas_unicas
            )

            max_date = max(
                fechas_unicas
            )


            rango_fechas = st.date_input(

                "Filtrar por Fecha (Inicio y Fin)",

                value=(
                    min_date,
                    max_date
                )

            )

        else:

            rango_fechas = None


    # ========================================================
    # FILTRADO
    # ========================================================

    if (

        rango_fechas

        and isinstance(
            rango_fechas,
            (tuple, list)
        )

    ):

        if len(
            rango_fechas
        ) == 2:

            f_inicio, f_fin = (
                rango_fechas
            )


            df_filtrado = df[

                (
                    df["fecha_dt"]
                    >= f_inicio
                )

                &

                (
                    df["fecha_dt"]
                    <= f_fin
                )

            ]


        elif len(
            rango_fechas
        ) == 1:

            df_filtrado = df[

                df["fecha_dt"]
                == rango_fechas[0]

            ]


        else:

            df_filtrado = df

    else:

        df_filtrado = df


    # ========================================================
    # TOTAL
    # ========================================================

    monto_total = (

        pd.to_numeric(

            df_filtrado[
                "monto_cop"
            ],

            errors="coerce"

        )

        .fillna(0)

        .sum()

    )


    cantidad_transacciones = len(
        df_filtrado
    )


    col_m1, col_m2 = (
        st.columns(2)
    )


    col_m1.metric(

        "💰 Total Procesado (COP)",

        f"${monto_total:,.2f}"

    )


    col_m2.metric(

        "📋 Transacciones Filtradas",

        str(
            cantidad_transacciones
        )

    )


    # ========================================================
    # COLUMNAS
    # ========================================================

    columnas_orden = [

        "tipo_movimiento",

        "concepto_factura",

        "monto_cop",

        "fecha_hora",

        "referencia_operacion",

        "banco_plataforma",

        "medio_pago_tipo",

        "destinatario_remitente",

        "tienda",

        "archivo_origen"

    ]


    columnas_disponibles = [

        columna

        for columna in columnas_orden

        if columna in df_filtrado.columns

    ]


    df_display = (

        df_filtrado[
            columnas_disponibles
        ].copy()

    )


    # ========================================================
    # NOMBRES
    # ========================================================

    nombres_columnas = {

        "tipo_movimiento":
            "Tipo Movimiento",

        "concepto_factura":
            "Factura / Concepto",

        "monto_cop":
            "Monto (COP)",

        "fecha_hora":
            "Fecha / Hora",

        "referencia_operacion":
            "Referencia",

        "banco_plataforma":
            "Banco / Canal",

        "medio_pago_tipo":
            "Tipo Operación",

        "destinatario_remitente":
            "Destinatario / Remitente",

        "tienda":
            "Tienda / Sede",

        "archivo_origen":
            "Archivo Origen"

    }


    df_display = (

        df_display.rename(

            columns=nombres_columnas

        )

    )


    # ========================================================
    # TABLA
    # ========================================================

    st.dataframe(

        df_display,

        use_container_width=True,

        hide_index=True

    )


    # ========================================================
    # EXCEL CONSOLIDADO SIMPLE
    # ========================================================

    excel_buffer = BytesIO()


    with pd.ExcelWriter(

        excel_buffer,

        engine="openpyxl"

    ) as writer:

        df_display.to_excel(

            writer,

            index=False,

            sheet_name="Consolidado"

        )


        worksheet = (
            writer.sheets[
                "Consolidado"
            ]
        )


        for column_cells in (
            worksheet.columns
        ):

            max_length = 0


            column_letter = (

                column_cells[0]
                .column_letter

            )


            for cell in column_cells:

                try:

                    cell_length = len(
                        str(
                            cell.value
                        )
                    )


                    if (
                        cell_length
                        > max_length
                    ):

                        max_length = (
                            cell_length
                        )

                except Exception:

                    pass


            worksheet.column_dimensions[
                column_letter
            ].width = min(

                max(
                    max_length + 2,
                    12
                ),

                45

            )


    excel_buffer.seek(0)


    st.download_button(

        label=(

            "📥 Descargar Consolidado "
            "Filtrado en Excel"

        ),

        data=(
            excel_buffer.getvalue()
        ),

        file_name=(
            "Consolidado_Contable.xlsx"
        ),

        mime=(

            "application/vnd.openxmlformats-officedocument."
            "spreadsheetml.sheet"

        )

    )


    # ========================================================
    # EXCEL CON PLANTILLA CONTABLE
    # ========================================================

    st.markdown("---")


    st.subheader(
        "📘 Excel contable con plantilla"
    )


    st.markdown(

        """
El siguiente archivo utiliza la plantilla original
**FORMATOS INGRESOS GASTOS.xlsx** y conserva únicamente
las hojas:

**INGRESOS · GASTOS · ARTIS · SALDOS · PROVEEDORES**

La hoja **LISTAS** no se incluye en el archivo final.
"""
    )


    if os.path.exists(
        ARCHIVO_PLANTILLA
    ):

        try:

            excel_plantilla = (
                generar_excel_contable(

                    df_resultados=df,

                    ruta_plantilla=(
                        ARCHIVO_PLANTILLA
                    )

                )
            )


            st.success(

                "✅ Plantilla contable generada correctamente."

            )


            st.download_button(

                label=(

                    "📥 Descargar Excel "
                    "Contable con Plantilla"

                ),

                data=excel_plantilla,

                file_name=(

                    "Formato_Contable_Procesado.xlsx"

                ),

                mime=(

                    "application/vnd.openxmlformats-officedocument."
                    "spreadsheetml.sheet"

                )

            )


        except Exception as e:

            st.error(

                "❌ No fue posible generar "
                "el Excel con la plantilla."

            )


            with st.expander(
                "Ver detalle técnico"
            ):

                st.exception(e)


    else:

        st.warning(

            f"⚠️ No está disponible la plantilla "
            f"'{ARCHIVO_PLANTILLA}'."

        )


    # ========================================================
    # TRAZABILIDAD
    # ========================================================

    st.caption(

        "Los resultados fueron obtenidos mediante análisis "
        "automatizado del comprobante y deben ser verificados "
        "antes de su incorporación definitiva a la contabilidad."

    )








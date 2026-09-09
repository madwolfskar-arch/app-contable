import streamlit as st
import pandas as pd

from PIL import Image
from io import BytesIO
import time
import re


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
# La API KEY debe estar únicamente en:
#
# Streamlit Cloud
# → Manage app
# → Settings
# → Secrets
#
# Formato:
#
# GEMINI_API_KEY = "TU_API_KEY"
#
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

    from pydantic import BaseModel, Field, ValidationError

    PYDANTIC_OK = True
    PYDANTIC_ERROR = ""

except Exception as e:

    PYDANTIC_OK = False
    PYDANTIC_ERROR = str(e)


# ============================================================
# MODELO GEMINI
# ============================================================

GEMINI_MODEL = "gemini-2.5-flash"


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
        "Verifique que requirements.txt contenga el paquete "
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
        "Verifique que requirements.txt contenga 'pydantic'."
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
            "Pago de servicios, depósito, etc."
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
# VALIDAR RESULTADO
# ============================================================

def validar_resultado(
    data: ComprobanteData,
    tipo_seleccionado: str
):

    errores = []

    tipo = limpiar_texto(
        data.tipo_movimiento
    ).lower()

    if tipo not in [
        "ingreso",
        "egreso"
    ]:

        errores.append(
            f"Tipo de movimiento no válido: "
            f"{data.tipo_movimiento}"
        )

    if tipo != tipo_seleccionado.lower():

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
    tipo_movimiento
):

    prompt = f"""
Analiza detalladamente este comprobante bancario.

CONTEXTO:

El usuario ha indicado que este archivo corresponde a:

{tipo_movimiento}

Nombre original del archivo:

"{nombre_archivo}"


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

10. Usa el nombre del archivo como apoyo,
    pero no sustituyas información visible
    por una suposición.

11. No confundas número de factura con número
    de autorización, referencia o ID.

12. Devuelve únicamente la estructura solicitada.
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
                        .model_validate(parsed)
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
# ENTRADA DE DATOS
# ============================================================

tipo = st.selectbox(
    "Seleccione el Tipo de Movimiento",
    [
        "Ingreso",
        "Egreso"
    ]
)


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
            f"🔎 Analizando {idx + 1} de "
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

            data, error = procesar_comprobante(

                client=client,

                image=image,

                nombre_archivo=nombre_archivo,

                tipo_movimiento=tipo

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


        if len(fechas_unicas) > 0:

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

        if len(rango_fechas) == 2:

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


        elif len(rango_fechas) == 1:

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
            df_filtrado["monto_cop"],
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
        str(cantidad_transacciones)
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
    # EXCEL
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
    # TRAZABILIDAD
    # ========================================================

    st.caption(
        "Los resultados fueron obtenidos mediante análisis "
        "automatizado del comprobante y deben ser verificados "
        "antes de su incorporación definitiva a la contabilidad."
    )







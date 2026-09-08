```python
import streamlit as st
import pandas as pd
from PIL import Image
from io import BytesIO
import time
import re

from google import genai
from google.genai import types
from pydantic import BaseModel, Field, ValidationError


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

# La API KEY debe estar exclusivamente en:
# Streamlit Cloud → Settings → Secrets
#
# Formato:
# GEMINI_API_KEY = "TU_NUEVA_API_KEY"

API_KEY = st.secrets.get("GEMINI_API_KEY", "").strip()

if not API_KEY:
    st.error(
        "❌ No se encontró GEMINI_API_KEY en los Secrets de Streamlit."
    )
    st.info(
        "Configure la clave en: "
        "Streamlit Cloud → Settings → Secrets"
    )
    st.stop()


# Modelo utilizado por la aplicación
GEMINI_MODEL = "gemini-2.5-flash"


# ============================================================
# MODELO ESTRUCTURADO PARA LOS COMPROBANTES
# ============================================================

class ComprobanteData(BaseModel):
    tipo_movimiento: str = Field(
        description=(
            "Tipo de movimiento contable. Debe ser Ingreso o Egreso."
        )
    )

    concepto_factura: str = Field(
        description=(
            "Número de factura, concepto o identificación principal "
            "del comprobante. Utilizar principalmente el nombre del "
            "archivo y el contenido visible del comprobante."
        )
    )

    destinatario_remitente: str = Field(
        description=(
            "Nombre o identificación de la persona, empresa, comercio "
            "o entidad receptora/emisora de la operación."
        )
    )

    monto_cop: float = Field(
        description=(
            "Valor numérico total de la transacción en pesos colombianos "
            "(COP), sin símbolo de moneda, puntos ni separadores."
        )
    )

    fecha_hora: str = Field(
        description=(
            "Fecha y hora de la transacción en formato "
            "YYYY-MM-DD HH:MM. Si la hora no aparece, utilizar "
            "00:00 y conservar la fecha identificada."
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
            "Entidad financiera o plataforma de origen/destino. "
            "Ejemplos: Nequi, Davivienda, Nu, Bancolombia, Bre-B."
        )
    )

    medio_pago_tipo: str = Field(
        description=(
            "Medio o tipo de operación. Ejemplos: Transferencia, "
            "QR, Llave, Pago de servicios, depósito, etc."
        )
    )


# ============================================================
# FUNCIONES AUXILIARES
# ============================================================

def limpiar_texto(valor):
    """
    Convierte valores nulos o espacios innecesarios
    en texto limpio.
    """
    if valor is None:
        return ""

    return str(valor).strip()


def validar_resultado(data: ComprobanteData, tipo_seleccionado: str):
    """
    Realiza validaciones básicas antes de incorporar
    el comprobante al consolidado.
    """

    errores = []

    # --------------------------------------------------------
    # Validar tipo de movimiento
    # --------------------------------------------------------

    tipo = limpiar_texto(data.tipo_movimiento).lower()

    if tipo not in ["ingreso", "egreso"]:
        errores.append(
            f"Tipo de movimiento no válido: {data.tipo_movimiento}"
        )

    # Si el usuario seleccionó un tipo, lo usamos como
    # referencia de control.
    if tipo != tipo_seleccionado.lower():
        errores.append(
            f"El comprobante fue clasificado como "
            f"'{data.tipo_movimiento}', pero el usuario seleccionó "
            f"'{tipo_seleccionado}'."
        )

    # --------------------------------------------------------
    # Validar monto
    # --------------------------------------------------------

    if data.monto_cop < 0:
        errores.append(
            "El monto de la transacción no puede ser negativo."
        )

    # --------------------------------------------------------
    # Validar fecha
    # --------------------------------------------------------

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
    """
    Determina cuánto tiempo esperar ante un error 429.

    Primero intenta obtener el tiempo indicado por Gemini.
    Si no existe, utiliza backoff progresivo.
    """

    # Busca expresiones como:
    # retry in 12.5s
    # retry in 30s

    match = re.search(
        r"retry in\s+(\d+(?:\.\d+)?)s",
        error_msg,
        re.IGNORECASE
    )

    if match:
        espera = float(match.group(1)) + 2
    else:
        # Backoff progresivo:
        # 10, 20, 40, 60...
        espera = min(60, 10 * (2 ** intento))

    return espera


def clasificar_error(error_msg):
    """
    Clasifica errores frecuentes de la API de Gemini.
    """

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


def procesar_comprobante(
    client,
    image,
    nombre_archivo,
    tipo_movimiento
):
    """
    Envía un comprobante a Gemini y devuelve
    un objeto ComprobanteData.
    """

    prompt = f"""
Analiza detalladamente este comprobante bancario.

CONTEXTO:
- El usuario ha indicado que este archivo corresponde a un:
  {tipo_movimiento}
- Nombre original del archivo:
  "{nombre_archivo}"

OBJETIVO:
Extrae exclusivamente la información que pueda ser identificada
en el comprobante.

REGLAS IMPORTANTES:

1. No inventes información.
2. Si un campo no aparece claramente, utiliza "No identificado".
3. El monto debe corresponder al valor real de la operación.
4. El monto debe expresarse como número en COP, sin símbolo de moneda,
   puntos ni separadores.
5. La fecha debe convertirse al formato:
   YYYY-MM-DD HH:MM
6. Si la hora no está visible, utiliza 00:00.
7. Identifica correctamente la entidad financiera o plataforma.
8. Identifica la referencia, autorización o ID de operación cuando exista.
9. Conserva el concepto o número de factura cuando sea visible.
10. Usa el nombre del archivo como apoyo para identificar el comprobante,
    pero no reemplaces información visible por una suposición.
11. No confundas el número de factura con el número de autorización,
    referencia o ID de operación.
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

            # ------------------------------------------------
            # Intentar utilizar la respuesta estructurada
            # proporcionada por el SDK.
            # ------------------------------------------------

            if getattr(response, "parsed", None) is not None:

                parsed = response.parsed

                if isinstance(parsed, ComprobanteData):
                    data = parsed
                else:
                    data = ComprobanteData.model_validate(parsed)

            else:

                # Compatibilidad con respuestas donde parsed
                # no esté disponible.
                data = ComprobanteData.model_validate_json(
                    response.text
                )

            return data, None

        except ValidationError as e:

            return (
                None,
                "La respuesta de Gemini no pudo validarse "
                f"contra el esquema contable: {e}"
            )

        except Exception as e:

            error_msg = str(e)
            categoria = clasificar_error(error_msg)

            # ------------------------------------------------
            # ERROR DE AUTENTICACIÓN
            # ------------------------------------------------

            if categoria == "AUTH":

                return (
                    None,
                    "❌ Error de autenticación con Gemini. "
                    "Verifique GEMINI_API_KEY en Streamlit Secrets."
                )

            # ------------------------------------------------
            # ERROR DE PERMISOS
            # ------------------------------------------------

            if categoria == "PERMISSION":

                return (
                    None,
                    "❌ Gemini rechazó la solicitud por permisos. "
                    "Revise el proyecto de Google Cloud asociado "
                    "a la API key y el acceso a Gemini API."
                )

            # ------------------------------------------------
            # ERROR DE CUOTA
            # ------------------------------------------------

            if categoria == "QUOTA":

                if intento < max_intentos - 1:

                    espera = obtener_espera(
                        error_msg,
                        intento
                    )

                    st.warning(
                        f"⏳ Límite de cuota para "
                        f"'{nombre_archivo}'. "
                        f"Reintentando en {espera:.0f} segundos..."
                    )

                    time.sleep(espera)

                    continue

                return (
                    None,
                    "❌ Se agotó la cuota disponible de Gemini "
                    "después de varios intentos."
                )

            # ------------------------------------------------
            # ERROR TEMPORAL
            # ------------------------------------------------

            if categoria == "TEMPORARY":

                if intento < max_intentos - 1:

                    espera = min(
                        30,
                        5 * (2 ** intento)
                    )

                    st.warning(
                        f"⚠️ Error temporal procesando "
                        f"'{nombre_archivo}'. "
                        f"Reintentando en {espera} segundos..."
                    )

                    time.sleep(espera)

                    continue

                return (
                    None,
                    "❌ El servicio de Gemini no respondió "
                    "correctamente después de varios intentos."
                )

            # ------------------------------------------------
            # OTROS ERRORES
            # ------------------------------------------------

            return (
                None,
                f"❌ Error procesando '{nombre_archivo}': "
                f"{error_msg}"
            )

    return (
        None,
        f"❌ No fue posible procesar '{nombre_archivo}'."
    )


# ============================================================
# INTERFAZ PRINCIPAL
# ============================================================

st.title("📊 Procesador Contable de Comprobantes")

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

    # --------------------------------------------------------
    # Crear cliente Gemini
    # --------------------------------------------------------

    try:

        client = genai.Client(
            api_key=API_KEY
        )

    except Exception as e:

        st.error(
            "❌ No fue posible inicializar el cliente de Gemini."
        )

        st.exception(e)

        st.stop()


    resultados = []

    errores_archivos = []

    progress_bar = st.progress(0)

    estado = st.empty()


    # ========================================================
    # PROCESAR CADA ARCHIVO
    # ========================================================

    for idx, uploaded_file in enumerate(uploaded_files):

        nombre_archivo = uploaded_file.name

        estado.info(
            f"🔎 Analizando {idx + 1} de "
            f"{len(uploaded_files)}: {nombre_archivo}"
        )

        try:

            # -----------------------------------------------
            # Abrir imagen
            # -----------------------------------------------

            image = Image.open(
                uploaded_file
            )

            # -----------------------------------------------
            # Convertir a RGB cuando sea necesario.
            # -----------------------------------------------

            if image.mode not in ["RGB", "L"]:

                image = image.convert("RGB")


            # -----------------------------------------------
            # Procesar con Gemini
            # -----------------------------------------------

            data, error = procesar_comprobante(
                client=client,
                image=image,
                nombre_archivo=nombre_archivo,
                tipo_movimiento=tipo
            )


            # -----------------------------------------------
            # Resultado exitoso
            # -----------------------------------------------

            if data is not None:

                errores_validacion = validar_resultado(
                    data,
                    tipo
                )

                if errores_validacion:

                    # El dato puede ser útil, pero no debemos
                    # ocultar la discrepancia.
                    st.warning(
                        f"⚠️ Advertencia en {nombre_archivo}: "
                        + " | ".join(errores_validacion)
                    )


                resultado = data.model_dump()

                # Guardamos también el nombre real del archivo
                # como trazabilidad.
                resultado["archivo_origen"] = nombre_archivo

                resultados.append(
                    resultado
                )


            # -----------------------------------------------
            # Error
            # -----------------------------------------------

            else:

                errores_archivos.append(
                    {
                        "archivo": nombre_archivo,
                        "error": error
                    }
                )

                st.error(
                    f"{error}"
                )


        except Exception as e:

            errores_archivos.append(
                {
                    "archivo": nombre_archivo,
                    "error": str(e)
                }
            )

            st.error(
                f"❌ No fue posible abrir o procesar "
                f"'{nombre_archivo}': {e}"
            )


        # ----------------------------------------------------
        # Actualizar progreso
        # ----------------------------------------------------

        progress_bar.progress(
            (idx + 1) / len(uploaded_files)
        )


    estado.empty()


    # ========================================================
    # GUARDAR RESULTADOS
    # ========================================================

    if resultados:

        st.session_state["df_resultados"] = pd.DataFrame(
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
# VISUALIZACIÓN DE RESULTADOS
# ============================================================

if (
    "df_resultados" in st.session_state
    and not st.session_state["df_resultados"].empty
):

    df = st.session_state[
        "df_resultados"
    ].copy()


    # ========================================================
    # CONVERSIÓN DE FECHAS
    # ========================================================

    df["fecha_dt"] = pd.to_datetime(
        df["fecha_hora"],
        errors="coerce"
    ).dt.date


    st.markdown("---")

    st.subheader(
        "🔍 Filtros y Resumen Financiero"
    )


    # ========================================================
    # FILTROS
    # ========================================================

    col_filtro1, col_filtro2 = st.columns(
        [2, 2]
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

            f_inicio, f_fin = rango_fechas

            df_filtrado = df[
                (
                    df["fecha_dt"] >= f_inicio
                )
                &
                (
                    df["fecha_dt"] <= f_fin
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
    # TOTALIZADORES
    # ========================================================

    monto_total = pd.to_numeric(
        df_filtrado["monto_cop"],
        errors="coerce"
    ).fillna(0).sum()


    cantidad_transacciones = len(
        df_filtrado
    )


    col_m1, col_m2 = st.columns(
        2
    )


    col_m1.metric(
        "💰 Total Procesado (COP)",
        f"${monto_total:,.2f}"
    )


    col_m2.metric(
        "📋 Transacciones Filtradas",
        f"{cantidad_transacciones}"
    )


    # ========================================================
    # ORDEN DE COLUMNAS
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


    # Solo utilizar columnas existentes.
    columnas_disponibles = [
        columna
        for columna in columnas_orden
        if columna in df_filtrado.columns
    ]


    df_display = df_filtrado[
        columnas_disponibles
    ].copy()


    # ========================================================
    # NOMBRES VISIBLES
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


    df_display = df_display.rename(
        columns=nombres_columnas
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
    # EXPORTACIÓN A EXCEL
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


        # ----------------------------------------------------
        # Ajustar ancho de columnas
        # ----------------------------------------------------

        worksheet = writer.sheets[
            "Consolidado"
        ]


        for column_cells in worksheet.columns:

            max_length = 0

            column_letter = (
                column_cells[0]
                .column_letter
            )

            for cell in column_cells:

                try:

                    cell_length = len(
                        str(cell.value)
                    )

                    if cell_length > max_length:

                        max_length = cell_length

                except Exception:

                    pass


            worksheet.column_dimensions[
                column_letter
            ].width = min(
                max(max_length + 2, 12),
                45
            )


    excel_buffer.seek(0)


    st.download_button(
        label="📥 Descargar Consolidado Filtrado en Excel",
        data=excel_buffer.getvalue(),
        file_name="Consolidado_Contable.xlsx",
        mime=(
            "application/vnd.openxmlformats-officedocument."
            "spreadsheetml.sheet"
        )
    )


    # ========================================================
    # INFORMACIÓN DE TRAZABILIDAD
    # ========================================================

    st.caption(
        "Los resultados fueron obtenidos mediante análisis "
        "automatizado del comprobante y deben ser verificados "
        "antes de su incorporación definitiva a la contabilidad."
    )
```







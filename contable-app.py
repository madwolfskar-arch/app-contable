import streamlit as st
import pandas as pd

from PIL import Image
from io import BytesIO

import time
import re
import json

# ============================================================
# CONFIGURACIÓN GENERAL DE LA PÁGINA
# ============================================================

st.set_page_config(
    page_title="Extractor de Datos Contables",
    page_icon="📊",
    layout="wide"
)

# ============================================================
# CONFIGURACIÓN DE GEMINI API KEY
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
# IMPORTACIÓN SEGURA DE PYDANTIC
# ============================================================

try:
    from pydantic import BaseModel, Field, ValidationError

    PYDANTIC_OK = True
    PYDANTIC_ERROR = ""
except Exception as e:
    PYDANTIC_OK = False
    PYDANTIC_ERROR = str(e)

# ============================================================
# CONFIGURACIÓN DEL MODELO Y VALIDACIONES
# ============================================================

GEMINI_MODEL = "gemini-2.5-flash"

if not API_KEY:
    st.error("❌ No se encontró la variable GEMINI_API_KEY en Streamlit Secrets.")
    st.info("Agregue `GEMINI_API_KEY = \"su_api_key\"` en Settings → Secrets.")
    st.stop()

if not GEMINI_SDK_OK:
    st.error("❌ No fue posible cargar el SDK de Google Gemini.")
    st.code(GEMINI_SDK_ERROR, language="text")
    st.stop()

if not PYDANTIC_OK:
    st.error("❌ No fue posible cargar Pydantic.")
    st.code(PYDANTIC_ERROR, language="text")
    st.stop()


# ============================================================
# ESTRUCTURA DE DATOS EXTRAÍDOS (ESQUEMA PYDANTIC)
# ============================================================

class ComprobanteData(BaseModel):
    tipo_movimiento: str = Field(
        description="Clasificación general del comprobante. Debe ser 'Ingreso' o 'Egreso'."
    )
    concepto_factura: str = Field(
        description="Número de factura, concepto o descripción principal identificada."
    )
    destinatario_remitente: str = Field(
        description="Nombre o razón social de la persona, comercio u organización que emite o recibe."
    )
    tienda_establecimiento: str = Field(
        description="Punto de venta, sucursal o tienda identificable en el documento. Si no aplica, usar 'No identificado'."
    )
    monto_cop: float = Field(
        description="Monto total en pesos colombianos (COP) expresado como un número flotante sin separadores de miles."
    )
    fecha_hora: str = Field(
        description="Fecha y hora exactas registradas en formato 'YYYY-MM-DD HH:MM'. Si la hora no existe, colocar '00:00'."
    )
    referencia_operacion: str = Field(
        description="Número de comprobante, autorización, ID de transacción, CUS o código de referencia."
    )
    banco_plataforma: str = Field(
        description="Entidad financiera, banco o plataforma origen/destino (ej. Bancolombia, Nequi, Daviplata, BCSC, etc.)."
    )
    medio_pago_tipo: str = Field(
        description="Medio o mecanismo utilizado (ej. Transferencia, QR, Débito, Crédito, Efectivo, Pago de servicios, etc.)."
    )
    notas_observaciones: str = Field(
        description="Metadatos o notas adicionales útiles identificados en el comprobante (ej. estado de la transacción)."
    )


# ============================================================
# FUNCIONES AUXILIARES DE PROCESAMIENTO
# ============================================================

def clasificar_error(error_msg):
    mensaje = str(error_msg).lower()
    if any(k in mensaje for k in ["401", "unauthenticated", "invalid api key"]):
        return "AUTH"
    if any(k in mensaje for k in ["403", "permission_denied"]):
        return "PERMISSION"
    if any(k in mensaje for k in ["429", "resource_exhausted", "quota"]):
        return "QUOTA"
    if any(k in mensaje for k in ["timeout", "connection", "503", "502", "500"]):
        return "TEMPORARY"
    return "OTHER"


def obtener_espera(error_msg, intento):
    match = re.search(r"retry in\s+(\d+(?:\.\d+)?)s", str(error_msg), re.IGNORECASE)
    if match:
        return float(match.group(1)) + 2
    return min(60, 10 * (2 ** intento))


def procesar_comprobante(client, image, nombre_archivo, tipo_movimiento, tienda_predeterminada=""):
    prompt = f"""
Analiza objetivamente la siguiente imagen de comprobante o recibo bancario/comercial.

DATOS DE REFERENCIA DEL USUARIO:
- Clasificación seleccionada: {tipo_movimiento}
- Nombre original del archivo: "{nombre_archivo}"
- Establecimiento predeterminado ingresado: "{tienda_predeterminada}"

INSTRUCCIONES DE EXTRACCIÓN DE METADATOS:
1. Extrae únicamente los datos visibles y verificables en el comprobante. No inventes o asumas datos no presentes.
2. Convierte los montos numéricos a COP sin puntos de miles ni símbolos monetarios (ejemplo: 45000.0).
3. Estandariza la fecha en el formato YYYY-MM-DD HH:MM.
4. Identifica con precisión códigos de autorización, CUS, número de comprobante o referencia de transacción.
5. Si un parámetro no se encuentra en el documento, registra explícitamente "No identificado".
"""

    max_intentos = 3

    for intento in range(max_intentos):
        try:
            response = client.models.generate_content(
                model=GEMINI_MODEL,
                contents=[image, prompt],
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
                    return None, "Respuesta vacía del modelo."
                data = ComprobanteData.model_validate_json(texto_respuesta)

            return data, None

        except ValidationError as e:
            return None, f"Error en la estructura devuelta por el modelo: {e}"

        except Exception as e:
            error_msg = str(e)
            categoria = clasificar_error(error_msg)

            if categoria == "AUTH":
                return None, "❌ Error de autenticación con Gemini. Verifique GEMINI_API_KEY."
            if categoria == "PERMISSION":
                return None, "❌ Acceso denegado por permisos en el proyecto de Gemini."
            if categoria == "QUOTA":
                if intento < max_intentos - 1:
                    espera = obtener_espera(error_msg, intento)
                    time.sleep(espera)
                    continue
                return None, "❌ Cuota excedida de la API de Gemini."
            if categoria == "TEMPORARY":
                if intento < max_intentos - 1:
                    time.sleep(5)
                    continue
                return None, "❌ Tiempo de espera agotado o servicio no disponible."

            return None, f"❌ Error durante el procesamiento: {error_msg}"

    return None, f"❌ No se logró procesar la imagen '{nombre_archivo}'."


# ============================================================
# INTERFAZ PRINCIPAL DE LA APLICACIÓN
# ============================================================

def main():
    st.title("📊 Extractor de Metadatos de Comprobantes")
    st.markdown(
        "Cargue sus archivos o imágenes de comprobantes bancarios y recibos para extraer "
        "sus metadatos contables estructurados."
    )

    try:
        client = genai.Client(api_key=API_KEY)
    except Exception as e:
        st.error(f"Error al inicializar el cliente de Gemini: {e}")
        st.stop()

    col1, col2 = st.columns(2)
    with col1:
        tipo_movimiento = st.selectbox(
            "Tipo de movimiento predeterminado:",
            ["Ingreso", "Egreso"]
        )
    with col2:
        tienda_predeterminada = st.text_input(
            "Nombre o sede del establecimiento (Opcional):",
            placeholder="Ej. Sede Central / Tienda Principal"
        )

    archivos_subidos = st.file_uploader(
        "Cargue las imágenes de los comprobantes (PNG, JPG, JPEG):",
        type=["png", "jpg", "jpeg"],
        accept_multiple_files=True
    )

    if archivos_subidos:
        if st.button("🚀 Analizar Comprobantes", type="primary"):
            resultados = []
            progreso = st.progress(0)
            status = st.empty()
            total_archivos = len(archivos_subidos)

            for idx, archivo in enumerate(archivos_subidos):
                status.text(f"Analizando ({idx + 1}/{total_archivos}): {archivo.name}")

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
                    st.error(f"No se pudo abrir el archivo '{archivo.name}': {ex}")

                progreso.progress((idx + 1) / total_archivos)

            status.text("✅ Procesamiento completado.")

            if resultados:
                df_resultados = pd.DataFrame(resultados)

                # Reordenar columnas para una mejor vista tabular
                columnas_ordenadas = [
                    "archivo_origen",
                    "fecha_hora",
                    "tipo_movimiento",
                    "monto_cop",
                    "destinatario_remitente",
                    "concepto_factura",
                    "tienda_establecimiento",
                    "banco_plataforma",
                    "medio_pago_tipo",
                    "referencia_operacion",
                    "notas_observaciones"
                ]

                # Asegurar que todas las columnas existan
                cols_presentes = [c for c in columnas_ordenadas if c in df_resultados.columns]
                df_resultados = df_resultados[cols_presentes]

                st.subheader("📋 Datos y Metadatos Extraídos")
                st.dataframe(df_resultados, use_container_width=True)

                col_dl1, col_dl2 = st.columns(2)

                with col_dl1:
                    csv_data = df_resultados.to_csv(index=False).encode('utf-8')
                    st.download_button(
                        label="📥 Descargar Resultados (CSV)",
                        data=csv_data,
                        file_name="metadatos_comprobantes.csv",
                        mime="text/csv",
                        use_container_width=True
                    )

                with col_dl2:
                    json_data = json.dumps(resultados, ensure_ascii=False, indent=2).encode('utf-8')
                    st.download_button(
                        label="📥 Descargar Resultados (JSON)",
                        data=json_data,
                        file_name="metadatos_comprobantes.json",
                        mime="application/json",
                        use_container_width=True
                    )


if __name__ == "__main__":
    main()


    






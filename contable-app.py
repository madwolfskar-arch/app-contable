import streamlit as st
import pandas as pd
from PIL import Image
from io import BytesIO
import time
import re

# ============================================================
# CONFIGURACIÓN GENERAL DE LA PÁGINA
# ============================================================

st.set_page_config(
    page_title="Extractor de Metadatos Contables",
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
# IMPORTACIÓN SEGURA DE LIBRERÍAS
# ============================================================

try:
    from google import genai
    from google.genai import types
    GEMINI_SDK_OK = True
except Exception as e:
    GEMINI_SDK_OK = False
    GEMINI_SDK_ERROR = str(e)

try:
    from pydantic import BaseModel, Field, ValidationError
    PYDANTIC_OK = True
except Exception as e:
    PYDANTIC_OK = False
    PYDANTIC_ERROR = str(e)

GEMINI_MODEL = "gemini-2.5-flash"

if not API_KEY:
    st.error("❌ No se encontró GEMINI_API_KEY en Streamlit Secrets.")
    st.stop()

if not GEMINI_SDK_OK:
    st.error(f"❌ Error al cargar Google Gemini SDK: {GEMINI_SDK_ERROR}")
    st.stop()

if not PYDANTIC_OK:
    st.error(f"❌ Error al cargar Pydantic: {PYDANTIC_ERROR}")
    st.stop()


# ============================================================
# ESTRUCTURA DE DATOS EXTRAÍDOS (ESQUEMA PYDANTIC)
# ============================================================

class ComprobanteData(BaseModel):
    tipo_movimiento: str = Field(description="Debe ser 'Ingreso' o 'Egreso'.")
    concepto_factura: str = Field(description="Número de factura, concepto o descripción principal.")
    destinatario_remitente: str = Field(description="Nombre o razón social de la persona o comercio.")
    tienda_establecimiento: str = Field(description="Punto de venta o sucursal. Si no aplica, 'No identificado'.")
    monto_cop: float = Field(description="Monto total en COP sin separadores de miles ni decimales innecesarios.")
    fecha_hora: str = Field(description="Fecha y hora en formato YYYY-MM-DD HH:MM.")
    referencia_operacion: str = Field(description="Número de comprobante, autorización, CUS o referencia.")
    banco_plataforma: str = Field(description="Entidad financiera o plataforma origen/destino.")
    medio_pago_tipo: str = Field(description="Medio utilizado (Transferencia, QR, Débito, Crédito, etc.).")
    notas_observaciones: str = Field(description="Notas o metadatos adicionales relevantes.")


# ============================================================
# FUNCIONES AUXILIARES
# ============================================================

def limpiar_cadena(texto):
    """Elimina saltos de línea y espacios múltiples para evitar celdas desestructuradas."""
    if not isinstance(texto, str):
        return texto
    texto = re.sub(r'[\r\n]+', ' ', texto)
    return str(texto).strip()


def obtener_tiempo_espera(error_msg):
    """Extrae el tiempo de espera recomendado desde la respuesta de error de Gemini."""
    match = re.search(r"retry in\s+(\d+(?:\.\d+)?)s", str(error_msg), re.IGNORECASE)
    if match:
        return float(match.group(1)) + 2.0
    return 35.0  # Tiempo base de espera si no se encuentra en el texto


def procesar_comprobante(client, image, nombre_archivo, tipo_movimiento, tienda_predeterminada=""):
    prompt = f"""
Analiza detalladamente la imagen del comprobante.
DATOS DE REFERENCIA:
- Tipo: {tipo_movimiento}
- Archivo: "{nombre_archivo}"
- Establecimiento sugerido: "{tienda_predeterminada}"

INSTRUCCIONES:
1. Extrae únicamente datos visibles y verificables.
2. Monto numérico en COP (sin símbolos monetarios ni puntos de miles).
3. Fecha en formato YYYY-MM-DD HH:MM.
4. Si un dato no existe, coloca "No identificado".
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
                data = parsed if isinstance(parsed, ComprobanteData) else ComprobanteData.model_validate(parsed)
            else:
                texto_respuesta = getattr(response, "text", "")
                data = ComprobanteData.model_validate_json(texto_respuesta)
            return data, None

        except Exception as e:
            error_str = str(e)
            if "429" in error_str or "RESOURCE_EXHAUSTED" in error_str:
                if intento < max_intentos - 1:
                    espera = obtener_tiempo_espera(error_str)
                    st.warning(f"⚠️ Límite de tasa detectado en '{nombre_archivo}'. Esperando {espera:.1f} segundos...")
                    time.sleep(espera)
                    continue
                else:
                    return None, f"❌ Cuota diaria o de tasa excedida (429 RESOURCE_EXHAUSTED). Intente más tarde o revise su plan en Google AI Studio."
            return None, error_str

    return None, f"❌ No se pudo procesar la imagen '{nombre_archivo}'."


def generar_excel_estructurado(df):
    """Genera un archivo de Excel (.xlsx) estructurado con formatos y auto-ancho."""
    output = BytesIO()
    with pd.ExcelWriter(output, engine='xlsxwriter') as writer:
        df.to_excel(writer, index=False, sheet_name='Metadatos Comprobantes')
        workbook = writer.book
        worksheet = writer.sheets['Metadatos Comprobantes']

        formato_header = workbook.add_format({
            'bold': True,
            'text_wrap': True,
            'valign': 'vcenter',
            'align': 'center',
            'fg_color': '#1F4E78',
            'font_color': '#FFFFFF',
            'border': 1
        })
        formato_celda = workbook.add_format({'valign': 'top', 'border': 1})
        formato_moneda = workbook.add_format({'num_format': '$#,##0', 'valign': 'top', 'border': 1})

        for col_num, col_name in enumerate(df.columns):
            worksheet.write(0, col_num, col_name, formato_header)
            max_len = max(df[col_name].astype(str).map(len).max(), len(col_name)) + 3
            max_len = min(max_len, 50)
            
            if col_name == 'monto_cop':
                worksheet.set_column(col_num, col_num, 16, formato_moneda)
            else:
                worksheet.set_column(col_num, col_num, max_len, formato_celda)

    return output.getvalue()


# ============================================================
# INTERFAZ PRINCIPAL EN STREAMLIT
# ============================================================

def main():
    st.title("📊 Extractor de Metadatos de Comprobantes")
    st.markdown("Procesa comprobantes e imágenes para generar un reporte estructurado directamente en formato Excel (`.xlsx`).")

    try:
        client = genai.Client(api_key=API_KEY)
    except Exception as e:
        st.error(f"Error al inicializar cliente Gemini: {e}")
        st.stop()

    col1, col2 = st.columns(2)
    with col1:
        tipo_movimiento = st.selectbox("Tipo de movimiento predeterminado:", ["Ingreso", "Egreso"])
    with col2:
        tienda_predeterminada = st.text_input("Nombre de establecimiento (Opcional):", placeholder="Ej. Tienda Norte")

    archivos_subidos = st.file_uploader(
        "Cargue las imágenes de los comprobantes:",
        type=["png", "jpg", "jpeg"],
        accept_multiple_files=True
    )

    if archivos_subidos and st.button("🚀 Analizar Comprobantes", type="primary"):
        resultados = []
        progreso = st.progress(0)
        status = st.empty()
        total = len(archivos_subidos)

        for idx, archivo in enumerate(archivos_subidos):
            status.text(f"Analizando ({idx + 1}/{total}): {archivo.name}")
            try:
                imagen = Image.open(archivo)
                data, err = procesar_comprobante(client, imagen, archivo.name, tipo_movimiento, tienda_predeterminada)
                if err:
                    st.error(f"Error en '{archivo.name}': {err}")
                elif data:
                    res_dict = data.model_dump()
                    res_dict["archivo_origen"] = archivo.name
                    res_dict = {k: limpiar_cadena(v) if isinstance(v, str) else v for k, v in res_dict.items()}
                    resultados.append(res_dict)
            except Exception as ex:
                st.error(f"Error abriendo '{archivo.name}': {ex}")

            progreso.progress((idx + 1) / total)
            # Pausa de 3 segundos entre archivos para evitar exceder el límite por minuto
            time.sleep(3)

        status.text("✅ Procesamiento completado.")

        if resultados:
            df_resultados = pd.DataFrame(resultados)

            columnas_ordenadas = [
                "archivo_origen", "fecha_hora", "tipo_movimiento", "monto_cop",
                "destinatario_remitente", "concepto_factura", "tienda_establecimiento",
                "banco_plataforma", "medio_pago_tipo", "referencia_operacion", "notas_observaciones"
            ]
            cols_presentes = [c for c in columnas_ordenadas if c in df_resultados.columns]
            df_resultados = df_resultados[cols_presentes]

            st.subheader("📋 Datos Extraídos")
            st.dataframe(df_resultados, use_container_width=True)

            excel_bytes = generar_excel_estructurado(df_resultados)
            
            st.download_button(
                label="📥 Descargar Reporte Excel (.xlsx)",
                data=excel_bytes,
                file_name="metadatos_comprobantes.xlsx",
                mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                use_container_width=True,
                type="primary"
            )

if __name__ == "__main__":
    main()



    






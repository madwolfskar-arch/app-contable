import streamlit as st
import pandas as pd
from PIL import Image
from io import BytesIO
from typing import List
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
# ESTRUCTURA DE DATOS EXTRAÍDOS (ESQUEMAS PYDANTIC)
# ============================================================

class ComprobanteData(BaseModel):
    archivo_origen: str = Field(description="Nombre exacto del archivo cargado asignado a esta imagen.")
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


class LoteComprobantesData(BaseModel):
    comprobantes: List[ComprobanteData] = Field(description="Lista de metadatos extraídos por cada imagen del lote.")


# ============================================================
# FUNCIONES AUXILIARES
# ============================================================

def limpiar_cadena(texto):
    """Elimina saltos de línea y espacios múltiples para evitar celdas desestructuradas."""
    if not isinstance(texto, str):
        return texto
    texto = re.sub(r'[\r\n]+', ' ', texto)
    return str(texto).strip()


def optimizar_imagen(archivo_subido, max_dim=1024):
    """
    Redimensiona la imagen para optimizar la velocidad de envío y reducir el uso de tokens
    en el plan gratuito sin sacrificar la legibilidad del texto/OCR.
    """
    img = Image.open(archivo_subido)
    if img.mode != 'RGB':
        img = img.convert('RGB')
    
    ancho, alto = img.size
    if max(ancho, alto) > max_dim:
        img.thumbnail((max_dim, max_dim), Image.Resampling.LANCZOS)
    return img


def obtener_tiempo_espera(error_msg):
    """Extrae el tiempo de espera recomendado desde la respuesta de error de Gemini."""
    match = re.search(r"retry in\s+(\d+(?:\.\d+)?)s", str(error_msg), re.IGNORECASE)
    if match:
        return float(match.group(1)) + 2.0
    return 35.0  # Tiempo base de espera si no se encuentra en el texto


def procesar_lote_comprobantes(client, lote_archivos, tipo_movimiento, tienda_predeterminada=""):
    """
    Procesa un lote de imágenes en una sola llamada de API para maximizar la velocidad
    y minimizar el consumo de cuotas del plan gratuito.
    """
    contents = []
    nombres_archivos = [arch.name for arch in lote_archivos]

    # Prepares y optimiza cada imagen del lote
    for arch in lote_archivos:
        img_opt = optimizar_imagen(arch)
        contents.append(img_opt)

    prompt = f"""
Analiza detalladamente las {len(lote_archivos)} imágenes de comprobantes adjuntas.
El orden de las imágenes corresponde a la lista de archivos: {nombres_archivos}

DATOS DE REFERENCIA GENERALES:
- Tipo sugerido: {tipo_movimiento}
- Establecimiento sugerido: "{tienda_predeterminada}"

INSTRUCCIONES CRÍTICAS:
1. Genera exactamente un objeto dentro de la lista 'comprobantes' por cada imagen adjunta.
2. Es OBLIGATORIO asignar en 'archivo_origen' el nombre del archivo correspondiente en el mismo orden.
3. Extrae únicamente datos visibles y verificables.
4. Monto numérico en COP (sin símbolos monetarios ni puntos de miles).
5. Fecha en formato YYYY-MM-DD HH:MM.
6. Si un dato no existe, coloca "No identificado".
"""
    contents.append(prompt)
    max_intentos = 3

    for intento in range(max_intentos):
        try:
            response = client.models.generate_content(
                model=GEMINI_MODEL,
                contents=contents,
                config=types.GenerateContentConfig(
                    response_mime_type="application/json",
                    response_schema=LoteComprobantesData,
                    temperature=0.1
                )
            )
            parsed = getattr(response, "parsed", None)
            if parsed is not None:
                data = parsed if isinstance(parsed, LoteComprobantesData) else LoteComprobantesData.model_validate(parsed)
            else:
                texto_respuesta = getattr(response, "text", "")
                data = LoteComprobantesData.model_validate_json(texto_respuesta)
            
            return [c.model_dump() for c in data.comprobantes], None

        except Exception as e:
            error_str = str(e)
            if "429" in error_str or "RESOURCE_EXHAUSTED" in error_str:
                if intento < max_intentos - 1:
                    espera = obtener_tiempo_espera(error_str)
                    st.warning(f"⚠️ Límite de cuota detectado. Esperando {espera:.1f} segundos para reintentar...")
                    time.sleep(espera)
                    continue
                else:
                    return None, f"❌ Cuota diaria o de tasa excedida (429 RESOURCE_EXHAUSTED). Intente más tarde o reduzca el número de archivos."
            return None, error_str

    return None, "❌ No se pudo procesar el lote de imágenes."


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
    st.markdown("Procesa múltiples comprobantes e imágenes optimizados para el **Plan Gratuito de Gemini**, generando un reporte estructurado en Excel (`.xlsx`).")

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

    # Estado de la sesión para mantener los resultados si se vuelve a renderizar
    if "resultados" not in st.session_state:
        st.session_state["resultados"] = []

    if archivos_subidos and st.button("🚀 Analizar Comprobantes en Lotes", type="primary"):
        st.session_state["resultados"] = []
        progreso = st.progress(0)
        status = st.empty()
        
        # Tamaño de lote optimizado para plan gratuito (4 imágenes por llamada)
        TAMANO_LOTE = 4
        total_archivos = len(archivos_subidos)
        
        # Divide la lista de archivos en sublistas/lotes
        lotes = [archivos_subidos[i:i + TAMANO_LOTE] for i in range(0, total_archivos, TAMANO_LOTE)]
        procesados_count = 0

        for idx, lote in enumerate(lotes):
            nombres_lote = ", ".join([a.name for a in lote])
            status.text(f"Procesando lote {idx + 1}/{len(lotes)} ({len(lote)} archivos): [{nombres_lote}]")
            
            items_extraidos, err = procesar_lote_comprobantes(client, lote, tipo_movimiento, tienda_predeterminada)
            
            if err:
                st.error(f"Error en lote {idx + 1}: {err}")
            elif items_extraidos:
                for res_dict in items_extraidos:
                    res_dict = {k: limpiar_cadena(v) if isinstance(v, str) else v for k, v in res_dict.items()}
                    st.session_state["resultados"].append(res_dict)

            procesados_count += len(lote)
            progreso.progress(procesados_count / total_archivos)
            
            # Pausa táctica entre lotes de 4 segundos para respetar el límite de 15 RPM
            if idx < len(lotes) - 1:
                time.sleep(4)

        status.text("✅ Procesamiento completado.")

    if st.session_state["resultados"]:
        df_resultados = pd.DataFrame(st.session_state["resultados"])

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





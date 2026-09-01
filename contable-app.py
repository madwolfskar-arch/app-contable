import streamlit as st
import pandas as pd
from PIL import Image
import json
import os
import time
import re
from google import genai
from google.genai import types
from pydantic import BaseModel, Field

st.set_page_config(page_title="Procesador Contable", layout="wide")

# API Key de Gemini incrustada directamente (oculta de la interfaz visual)
API_KEY = os.environ.get("GEMINI_API_KEY", "AQ.Ab8RN6L0bRHiPJb-L0JO9g2mqbyclh1qJlWJmh6a1Yha5Fi65g")

class ComprobanteData(BaseModel):
    tipo_movimiento: str = Field(description="Ingreso o Egreso segun la naturaleza de la transaccion")
    concepto_factura: str = Field(description="Numero de factura o concepto tomado principalmente del nombre del archivo o del cuerpo del comprobante")
    destinatario_remitente: str = Field(description="Nombre o identificacion de la persona o comercio receptor/emisor")
    monto_cop: float = Field(description="Valor numerico de la transaccion en COP sin simbolos de moneda ni puntos")
    fecha_hora: str = Field(description="Fecha y hora de la transaccion en formato YYYY-MM-DD HH:MM")
    referencia_operacion: str = Field(description="Numero de comprobante, autorizacion, referencia o ID de operacion")
    banco_plataforma: str = Field(description="Entidad financiera de origen/destino (ej. Nequi, Davivienda, Nu, Bancolombia, Bre-B)")
    medio_pago_tipo: str = Field(description="Tipo de movimiento (ej. Transferencia, QR, Llave, Pago de servicios)")

st.title("📊 Procesador Contable de Comprobantes")

tipo = st.selectbox("Seleccione el Tipo de Movimiento", ["Ingreso", "Egreso"])
uploaded_files = st.file_uploader("Cargar comprobantes", type=["png", "jpg", "jpeg", "webp"], accept_multiple_files=True)

if st.button("Procesar Archivos") and uploaded_files:
    client = genai.Client(api_key=API_KEY)
    resultados = []
    progress_bar = st.progress(0)
    
    for idx, uploaded_file in enumerate(uploaded_files):
        image = Image.open(uploaded_file)
        nombre_archivo = uploaded_file.name
        
        prompt = f"""
        Analiza detalladamente este comprobante bancario.
        - Este archivo corresponde a un {tipo}.
        - Nombre original: "{nombre_archivo}".
        Extrae con precision todos los campos segun el esquema.
        """
        
        for intento in range(5):
            try:
                response = client.models.generate_content(
                    model='gemini-2.5-flash',
                    contents=[image, prompt],
                    config=types.GenerateContentConfig(
                        response_mime_type="application/json",
                        response_schema=ComprobanteData,
                        temperature=0.1
                    ),
                )
                data = json.loads(response.text)
                resultados.append(data)
                break
            except Exception as e:
                error_msg = str(e)
                if ("429" in error_msg or "RESOURCE_EXHAUSTED" in error_msg) and intento < 4:
                    match = re.search(r'retry in (\d+(\.\d+)?)s', error_msg, re.IGNORECASE)
                    espera = float(match.group(1)) + 2 if match else 35.0
                    st.warning(f"Límite de cuota en {nombre_archivo}. Esperando {espera:.0f}s para continuar...")
                    time.sleep(espera)
                else:
                    st.error(f"Error en {nombre_archivo}: {e}")
                    break
        
        progress_bar.progress((idx + 1) / len(uploaded_files))
    
    if resultados:
        st.session_state["df_resultados"] = pd.DataFrame(resultados)
        st.success("✅ Procesamiento completado con éxito")

# Visualización de resultados, buscador por fecha y totalizadores
if "df_resultados" in st.session_state and not st.session_state["df_resultados"].empty:
    df = st.session_state["df_resultados"].copy()
    
    # Conversión de fechas para filtrado
    df["fecha_dt"] = pd.to_datetime(df["fecha_hora"], errors="coerce").dt.date
    
    st.markdown("---")
    st.subheader("🔍 Filtros y Resumen Financiero")
    
    col_filtro1, col_filtro2 = st.columns([2, 2])
    
    with col_filtro1:
        fechas_unicas = df["fecha_dt"].dropna().unique()
        if len(fechas_unicas) > 0:
            min_date, max_date = min(fechas_unicas), max(fechas_unicas)
            rango_fechas = st.date_input("Filtrar por Fecha (Inicio y Fin)", value=(min_date, max_date))
        else:
            rango_fechas = None

    # Filtrar por el rango o fecha seleccionada
    if rango_fechas and isinstance(rango_fechas, (tuple, list)):
        if len(rango_fechas) == 2:
            f_inicio, f_fin = rango_fechas
            df_filtrado = df[(df["fecha_dt"] >= f_inicio) & (df["fecha_dt"] <= f_fin)]
        elif len(rango_fechas) == 1:
            df_filtrado = df[df["fecha_dt"] == rango_fechas[0]]
        else:
            df_filtrado = df
    else:
        df_filtrado = df

    # Métrica y Totalizador
    monto_total = df_filtrado["monto_cop"].sum()
    cantidad_transacciones = len(df_filtrado)
    
    col_m1, col_m2 = st.columns(2)
    col_m1.metric("💰 Total Procesado (COP)", f"${monto_total:,.2f}")
    col_m2.metric("📋 Transacciones Filtradas", f"{cantidad_transacciones}")

    # Ordenar y formatear columnas
    columnas_orden = [
        "tipo_movimiento", "concepto_factura", "monto_cop", 
        "fecha_hora", "referencia_operacion", "banco_plataforma", 
        "medio_pago_tipo", "destinatario_remitente"
    ]
    
    df_display = df_filtrado[columnas_orden].copy()
    df_display.columns = [
        "Tipo Movimiento", "Factura / Concepto", "Monto (COP)", 
        "Fecha / Hora", "Referencia", "Banco / Canal", 
        "Tipo Operación", "Destinatario / Remitente"
    ]
    
    st.dataframe(df_display, use_container_width=True)

    # Exportación a Excel
    excel_file = "Consolidado_Contable.xlsx"
    df_display.to_excel(excel_file, index=False)
    with open(excel_file, "rb") as f:
        st.download_button(
            label="📥 Descargar Consolidado Filtrado en Excel",
            data=f,
            file_name="Consolidado_Contable.xlsx",
            mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
        )


        

        






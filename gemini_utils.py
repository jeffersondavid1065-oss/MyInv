"""
Módulo para leer facturas con Google Gemini y extraer productos automáticamente.
Integrado en la página de Inventario → Tab Entradas de Mercancía.
"""

import streamlit as st
import google.generativeai as genai
import json
import re
from PIL import Image
import io


def configurar_gemini():
    """Configura la API de Gemini con la key de los secrets."""
    try:
        api_key = st.secrets["gemini"]["api_key"]
        genai.configure(api_key=api_key)
        return True
    except Exception as e:
        st.error(f"Error al configurar Gemini: {e}")
        return False


def leer_factura_imagen(imagen_bytes, tipo_mime="image/jpeg"):
    """
    Lee una imagen de factura y extrae los productos usando Gemini.
    
    Retorna una lista de diccionarios con:
    - nombre: nombre del producto
    - cantidad: cantidad recibida
    - costo_unitario: precio unitario de compra
    - subtotal: total del ítem
    """
    if not configurar_gemini():
        return None

    try:
        model = genai.GenerativeModel("gemini-2.5-flash")

        prompt = """
        Analiza esta factura de compra y extrae TODOS los productos/artículos que aparecen.
        
        Devuelve ÚNICAMENTE un JSON válido con este formato exacto, sin texto adicional:
        {
            "productos": [
                {
                    "nombre": "nombre del producto",
                    "cantidad": número,
                    "costo_unitario": número,
                    "subtotal": número
                }
            ],
            "proveedor": "nombre del proveedor si aparece",
            "numero_factura": "número de factura si aparece",
            "total_factura": número
        }
        
        Reglas importantes:
        - cantidad debe ser un número (sin texto)
        - costo_unitario debe ser el precio por unidad (sin signos de moneda)
        - subtotal debe ser cantidad * costo_unitario
        - Si no puedes leer un valor, usa 0
        - Incluye TODOS los productos de la factura
        - Si el precio está en miles (ej: 15.000 o 15,000), el valor es 15000
        """

        # Procesar imagen
        imagen = Image.open(io.BytesIO(imagen_bytes))
        
        response = model.generate_content([prompt, imagen])
        texto = response.text.strip()

        # Limpiar respuesta — quitar markdown si viene con ```json
        texto = re.sub(r'```json\s*', '', texto)
        texto = re.sub(r'```\s*', '', texto)
        texto = texto.strip()

        # Parsear JSON
        datos = json.loads(texto)
        return datos

    except json.JSONDecodeError:
        st.error("Gemini no pudo extraer los datos en formato correcto. Intenta con una imagen más clara.")
        return None
    except Exception as e:
        st.error(f"Error al procesar la factura: {e}")
        return None


def leer_factura_pdf(pdf_bytes):
    """
    Lee un PDF de factura y extrae los productos usando Gemini.
    Convierte cada página a imagen y las procesa.
    """
    if not configurar_gemini():
        return None

    try:
        # Intentar con Gemini directamente con el PDF
        model = genai.GenerativeModel("gemini-2.5-flash")

        prompt = """
        Analiza esta factura de compra y extrae TODOS los productos/artículos que aparecen.
        
        Devuelve ÚNICAMENTE un JSON válido con este formato exacto, sin texto adicional:
        {
            "productos": [
                {
                    "nombre": "nombre del producto",
                    "cantidad": número,
                    "costo_unitario": número,
                    "subtotal": número
                }
            ],
            "proveedor": "nombre del proveedor si aparece",
            "numero_factura": "número de factura si aparece",
            "total_factura": número
        }
        
        Reglas importantes:
        - cantidad debe ser un número (sin texto)
        - costo_unitario debe ser el precio por unidad (sin signos de moneda)
        - subtotal debe ser cantidad * costo_unitario
        - Si no puedes leer un valor, usa 0
        - Incluye TODOS los productos de la factura
        - Si el precio está en miles (ej: 15.000 o 15,000), el valor es 15000
        """

        # Enviar PDF directamente a Gemini
        response = model.generate_content([
            prompt,
            {
                "mime_type": "application/pdf",
                "data": pdf_bytes
            }
        ])

        texto = response.text.strip()
        texto = re.sub(r'```json\s*', '', texto)
        texto = re.sub(r'```\s*', '', texto)
        texto = texto.strip()

        datos = json.loads(texto)
        return datos

    except json.JSONDecodeError:
        st.error("Gemini no pudo extraer los datos en formato correcto. Intenta con una imagen más clara.")
        return None
    except Exception as e:
        st.error(f"Error al procesar el PDF: {e}")
        return None


def mostrar_tabla_editable(productos_extraidos, productos_bd, key_prefix="factura"):
    """
    Muestra una tabla editable con los productos extraídos de la factura.
    Permite al usuario corregir antes de confirmar.
    
    productos_bd: lista de productos del inventario para hacer match
    Retorna la lista de productos confirmados o None si se cancela.
    """
    if not productos_extraidos:
        return None

    st.markdown("### 📋 Productos detectados en la factura")
    st.caption("Revisa y corrige los datos antes de confirmar. Puedes editar directamente en la tabla.")

    import pandas as pd

    # Crear dataframe editable
    df = pd.DataFrame(productos_extraidos)

    # Asegurar columnas correctas
    for col in ['nombre', 'cantidad', 'costo_unitario', 'subtotal']:
        if col not in df.columns:
            df[col] = 0 if col != 'nombre' else ""

    df_edit = st.data_editor(
        df[['nombre', 'cantidad', 'costo_unitario', 'subtotal']],
        column_config={
            "nombre": st.column_config.TextColumn("Producto", width="large"),
            "cantidad": st.column_config.NumberColumn("Cantidad", min_value=0, step=1),
            "costo_unitario": st.column_config.NumberColumn("Costo Unitario ($)", min_value=0, step=100, format="$%d"),
            "subtotal": st.column_config.NumberColumn("Subtotal ($)", min_value=0, format="$%d"),
        },
        use_container_width=True,
        hide_index=True,
        num_rows="dynamic",
        key=f"editor_{key_prefix}"
    )

    # Totales
    total_calculado = df_edit['subtotal'].sum()
    st.info(f"**Total de la entrada: ${total_calculado:,.0f}**".replace(",", "."))

    return df_edit.to_dict('records')

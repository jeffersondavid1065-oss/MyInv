"""
Módulo para leer facturas con Google Gemini y extraer productos automáticamente.
Usa el nuevo SDK google-genai con modelos Gemini 3.x
"""

import streamlit as st
import json
import re
from PIL import Image
import io


def configurar_cliente():
    """Configura el cliente de Gemini con la key de los secrets."""
    try:
        from google import genai
        api_key = st.secrets["gemini"]["api_key"]
        client = genai.Client(api_key=api_key)
        return client
    except Exception as e:
        st.error(f"Error al configurar Gemini: {e}")
        return None


PROMPT_FACTURA = """
Analiza esta factura de compra y extrae TODOS los productos/artículos que aparecen.

Devuelve ÚNICAMENTE un JSON válido con este formato exacto, sin texto adicional ni markdown:
{
    "productos": [
        {
            "nombre": "nombre del producto",
            "cantidad": número,
            "costo_unitario": número,
            "subtotal": número
        }
    ],
    "proveedor": "nombre del proveedor si aparece o null",
    "numero_factura": "número de factura si aparece o null",
    "total_factura": número
}

Reglas importantes:
- cantidad debe ser un número entero
- costo_unitario debe ser el precio por unidad (sin signos de moneda ni puntos de miles)
- subtotal debe ser cantidad * costo_unitario
- Si no puedes leer un valor numérico, usa 0
- Incluye TODOS los productos de la factura
- Los precios en Colombia usan puntos como separador de miles: 15.000 = 15000
- NO incluyas ```json ni ningún markdown, solo el JSON puro
"""


def leer_factura_imagen(imagen_bytes):
    """Lee una imagen de factura y extrae los productos usando Gemini."""
    client = configurar_cliente()
    if not client:
        return None

    try:
        from google.genai import types

        imagen = Image.open(io.BytesIO(imagen_bytes))
        buf = io.BytesIO()
        imagen.save(buf, format="JPEG")
        buf.seek(0)
        img_bytes = buf.read()

        response = client.models.generate_content(
            model="gemini-3.1-flash-lite",
            contents=[
                types.Part.from_bytes(data=img_bytes, mime_type="image/jpeg"),
                PROMPT_FACTURA
            ]
        )

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
        st.error(f"Error al procesar la imagen: {e}")
        return None


PROMPT_RUT = """
Analiza este RUT (Registro Único Tributario) de la DIAN, Colombia, y extrae los datos del contribuyente.

Devuelve ÚNICAMENTE un JSON válido con este formato exacto, sin texto adicional ni markdown:
{
    "nombre": "razón social (persona jurídica) o nombre completo (persona natural)",
    "tipo_documento": "NIT o CC",
    "documento": "número de identificación, sin puntos ni el dígito de verificación",
    "digito_verificacion": "dígito de verificación (casilla DV, solo si tipo_documento es NIT) o null",
    "regimen": "COMMON_REGIME o SIMPLIFIED_REGIME",
    "direccion": "dirección principal registrada o null",
    "email": "correo electrónico registrado o null",
    "telefono": "teléfono registrado o null"
}

Reglas importantes:
- El NIT está en la casilla 5 (número de identificación) y el dígito de verificación en la casilla 6 (DV).
- Para "regimen": revisa la sección "Responsabilidades, Calidades y Atributos". Si aparece el código 48
  (Impuesto sobre las ventas - IVA) o dice explícitamente "Responsable de IVA", usa COMMON_REGIME.
  Si dice "No responsable de IVA" o ese código no aparece, usa SIMPLIFIED_REGIME.
- Si no encuentras un dato, usa null (no inventes valores).
- NO incluyas ```json ni ningún markdown, solo el JSON puro
"""


def leer_rut_pdf(pdf_bytes):
    """Lee un PDF del RUT (DIAN) y extrae los datos del contribuyente usando Gemini,
    para prellenar el formulario de registro de clientes."""
    client = configurar_cliente()
    if not client:
        return None

    try:
        from google.genai import types

        response = client.models.generate_content(
            model="gemini-3.1-flash-lite",
            contents=[
                types.Part.from_bytes(data=pdf_bytes, mime_type="application/pdf"),
                PROMPT_RUT
            ]
        )

        texto = response.text.strip()
        texto = re.sub(r'```json\s*', '', texto)
        texto = re.sub(r'```\s*', '', texto)
        texto = texto.strip()

        datos = json.loads(texto)
        return datos

    except json.JSONDecodeError:
        st.error("Gemini no pudo leer el RUT en formato correcto. Intenta con un PDF más claro.")
        return None
    except Exception as e:
        st.error(f"Error al procesar el RUT: {e}")
        return None


def leer_factura_pdf(pdf_bytes):
    """Lee un PDF de factura y extrae los productos usando Gemini."""
    client = configurar_cliente()
    if not client:
        return None

    try:
        from google.genai import types

        response = client.models.generate_content(
            model="gemini-3.1-flash-lite",
            contents=[
                types.Part.from_bytes(data=pdf_bytes, mime_type="application/pdf"),
                PROMPT_FACTURA
            ]
        )

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

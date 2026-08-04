"""
Utilidades para generar PDFs de tickets de venta y comprobantes para MyAlmacén.
Mismo diseño profesional que MyTaller, adaptado para ventas al mostrador.
"""

from fpdf import FPDF
import pandas as pd
from datetime import datetime
import os


def generar_ticket_venta(
    negocio_nombre,
    negocio_nit="",
    negocio_telefono="",
    negocio_direccion="",
    negocio_logo_path=None,
    venta_id=None,
    fecha="",
    cliente="",
    tipo_pago="",
    monto_efectivo=0,
    cambio=0,
    df_items=None,
    subtotal=0,
    descuento=0,
    total=0,
    total_iva=0,
):
    """
    Genera un ticket de venta profesional en PDF.
    Compatible con lector de barras y código QR futuro.
    """

    def safe_str(valor, default=""):
        if valor is None:
            return default
        try:
            s = str(valor)
            if "." in s and " " in s:
                s = s.split(" ")[0]
            return s
        except Exception:
            return default

    negocio_nombre   = safe_str(negocio_nombre, "MyAlmacén")
    negocio_nit      = safe_str(negocio_nit)
    negocio_telefono = safe_str(negocio_telefono)
    negocio_direccion= safe_str(negocio_direccion)
    fecha            = safe_str(fecha, datetime.today().strftime('%Y-%m-%d'))
    cliente          = safe_str(cliente, "Venta directa")
    tipo_pago        = safe_str(tipo_pago, "Efectivo")
    venta_id_str     = str(venta_id).zfill(5) if venta_id else "00000"

    GRIS_OSCURO = (80, 80, 80)
    GRIS_MEDIO  = (120, 120, 120)
    GRIS_CLARO  = (200, 200, 200)
    NEGRO       = (30, 30, 30)

    pdf = FPDF()
    pdf.add_page()
    pdf.set_auto_page_break(auto=True, margin=15)

    y_header = 12

    # Logo o placeholder
    logo_ok = False
    if negocio_logo_path and os.path.exists(negocio_logo_path):
        try:
            pdf.image(negocio_logo_path, x=12, y=y_header, w=22, h=22)
            logo_ok = True
        except Exception:
            pass

    if not logo_ok:
        pdf.set_fill_color(180, 180, 180)
        pdf.rect(12, y_header, 22, 22, "F")
        pdf.set_font("Helvetica", "", 6)
        pdf.set_text_color(255, 255, 255)
        pdf.set_xy(12, y_header + 8)
        pdf.cell(22, 4, "LOGO", align="C")

    # Nombre y datos del negocio
    pdf.set_xy(38, y_header)
    pdf.set_font("Helvetica", "B", 11)
    pdf.set_text_color(*GRIS_OSCURO)
    pdf.cell(80, 5, negocio_nombre)

    pdf.set_font("Helvetica", "", 8)
    pdf.set_text_color(*GRIS_MEDIO)
    pdf.set_xy(38, y_header + 6)
    pdf.cell(80, 4, negocio_direccion if negocio_direccion else "")
    if negocio_nit:
        pdf.set_xy(38, y_header + 10)
        pdf.cell(80, 4, f"NIT: {negocio_nit}")
    if negocio_telefono:
        pdf.set_xy(38, y_header + 14)
        pdf.cell(80, 4, f"Tel: {negocio_telefono}")

    # Ticket # y fecha
    pdf.set_xy(130, y_header)
    pdf.set_font("Helvetica", "B", 12)
    pdf.set_text_color(*GRIS_OSCURO)
    pdf.cell(65, 5, f"Ticket# {venta_id_str}", align="R")

    pdf.set_xy(130, y_header + 7)
    pdf.set_font("Helvetica", "", 8)
    pdf.set_text_color(*GRIS_MEDIO)
    pdf.cell(65, 4, "Fecha de emisión", align="R")

    pdf.set_xy(130, y_header + 12)
    pdf.set_font("Helvetica", "B", 9)
    pdf.set_text_color(*GRIS_OSCURO)
    pdf.cell(65, 4, fecha, align="R")

    # Línea separadora
    pdf.set_draw_color(*GRIS_OSCURO)
    pdf.set_line_width(0.8)
    pdf.line(12, 37, 198, 37)

    # Título y subtítulo
    pdf.set_xy(12, 41)
    pdf.set_font("Helvetica", "B", 18)
    pdf.set_text_color(*NEGRO)
    pdf.cell(0, 8, negocio_nombre)

    pdf.set_xy(12, 50)
    pdf.set_font("Helvetica", "", 9)
    pdf.set_text_color(*GRIS_MEDIO)
    pdf.cell(0, 5, "Comprobante de venta / Ticket")

    # ==========================================
    # TRES COLUMNAS: CLIENTE | DETALLES | PAGO
    # ==========================================
    y_cols = 62
    col1_x, col2_x, col3_x = 12, 80, 148
    col_ancho = 60

    pdf.set_draw_color(*GRIS_CLARO)
    pdf.set_line_width(0.3)
    pdf.line(12, y_cols, 198, y_cols)

    pdf.set_xy(col1_x, y_cols + 2)
    pdf.set_font("Helvetica", "B", 8)
    pdf.set_text_color(*GRIS_OSCURO)
    pdf.cell(col_ancho, 4, "CLIENTE")

    pdf.set_xy(col2_x, y_cols + 2)
    pdf.cell(col_ancho, 4, "TIPO DE PAGO")

    pdf.set_xy(col3_x, y_cols + 2)
    pdf.cell(col_ancho, 4, "TOTAL")

    pdf.set_font("Helvetica", "", 8)
    pdf.set_text_color(*GRIS_OSCURO)

    pdf.set_xy(col1_x, y_cols + 8)
    pdf.cell(col_ancho - 5, 4, cliente)

    pdf.set_xy(col2_x, y_cols + 8)
    pdf.cell(col_ancho - 5, 4, tipo_pago)
    if monto_efectivo > 0:
        pdf.set_xy(col2_x, y_cols + 13)
        pdf.set_font("Helvetica", "", 7)
        pdf.set_text_color(*GRIS_MEDIO)
        pdf.cell(col_ancho - 5, 4, f"Efectivo: ${monto_efectivo:,.0f}".replace(",", "."))
        if cambio > 0:
            pdf.set_xy(col2_x, y_cols + 18)
            pdf.cell(col_ancho - 5, 4, f"Cambio: ${cambio:,.0f}".replace(",", "."))

    pdf.set_xy(col3_x, y_cols + 8)
    pdf.set_font("Helvetica", "B", 10)
    pdf.set_text_color(*NEGRO)
    pdf.cell(col_ancho, 4, f"${total:,.0f}".replace(",", "."))

    # ==========================================
    # TABLA DE ARTÍCULOS
    # ==========================================
    y_tabla = y_cols + 32
    pdf.set_draw_color(*GRIS_CLARO)
    pdf.line(12, y_tabla, 198, y_tabla)

    pdf.set_xy(12, y_tabla + 2)
    pdf.set_font("Helvetica", "B", 9)
    pdf.set_text_color(*GRIS_OSCURO)
    pdf.cell(95, 5, "ARTÍCULO")
    pdf.cell(25, 5, "CANT.", align="C")
    pdf.cell(33, 5, "PRECIO", align="R")
    pdf.cell(33, 5, "SUBTOTAL", align="R")

    pdf.set_draw_color(*GRIS_CLARO)
    pdf.line(12, y_tabla + 8, 198, y_tabla + 8)

    y_item = y_tabla + 10
    pdf.set_font("Helvetica", "", 8)
    pdf.set_text_color(*GRIS_OSCURO)

    if df_items is not None and not df_items.empty:
        for _, row in df_items.iterrows():
            nombre = safe_str(row.get('nombre_producto', ''))[:68]
            cantidad = float(row.get('cantidad', 1))
            precio_u = float(row.get('precio_unitario', 0))
            subtotal_item = float(row.get('subtotal', 0))

            # Formatear cantidad con unidad si está disponible
            unidad = safe_str(row.get('unidad_medida', 'Unidad'), 'Unidad')
            unidades_decimales = {"kg", "g", "lb", "m", "cm", "vara", "pie",
                                  "L", "mL", "galón", "m²", "m³"}
            if unidad in unidades_decimales:
                cant_str = f"{cantidad:.3f}".rstrip('0').rstrip('.') + f" {unidad}"
            elif unidad and unidad != "Unidad":
                cant_str = f"{int(cantidad)} {unidad}"
            else:
                cant_str = str(int(cantidad))

            pdf.set_xy(12, y_item)
            pdf.cell(95, 5, nombre)
            pdf.cell(25, 5, cant_str, align="C")
            pdf.cell(33, 5, f"${precio_u:,.0f}".replace(",", "."), align="R")
            pdf.cell(33, 5, f"${subtotal_item:,.0f}".replace(",", "."), align="R")
            y_item += 5

            pdf.set_draw_color(230, 230, 230)
            pdf.line(12, y_item, 198, y_item)
            y_item += 1

    # ==========================================
    # TOTALES
    # ==========================================
    y_totales = y_item + 4
    pdf.set_draw_color(*GRIS_CLARO)

    if descuento > 0:
        pdf.set_xy(130, y_totales)
        pdf.set_font("Helvetica", "", 9)
        pdf.set_text_color(*GRIS_OSCURO)
        pdf.cell(35, 5, "Subtotal")
        pdf.cell(33, 5, f"${subtotal:,.0f}".replace(",", "."), align="R")
        y_totales += 5

        pdf.set_xy(130, y_totales)
        pdf.cell(35, 5, "Descuento")
        pdf.set_text_color(200, 50, 50)
        pdf.cell(33, 5, f"-${descuento:,.0f}".replace(",", "."), align="R")
        pdf.set_text_color(*GRIS_OSCURO)
        y_totales += 5

    pdf.line(130, y_totales, 198, y_totales)
    y_totales += 1

    pdf.set_xy(130, y_totales)
    pdf.set_font("Helvetica", "B", 11)
    pdf.set_text_color(*NEGRO)
    pdf.cell(35, 7, "Total a pagar")
    pdf.cell(33, 7, f"${total:,.0f}".replace(",", "."), align="R")
    y_totales += 8

    if total_iva and total_iva > 1:
        pdf.set_xy(130, y_totales)
        pdf.set_font("Helvetica", "", 8)
        pdf.set_text_color(*GRIS_MEDIO)
        pdf.cell(35, 5, "IVA incluido")
        pdf.cell(33, 5, f"${total_iva:,.0f}".replace(",", "."), align="R")
        y_totales += 6

    if cambio > 0:
        pdf.set_xy(130, y_totales)
        pdf.set_font("Helvetica", "", 9)
        pdf.set_text_color(*GRIS_MEDIO)
        pdf.cell(35, 5, "Cambio entregado")
        pdf.cell(33, 5, f"${cambio:,.0f}".replace(",", "."), align="R")

    # ==========================================
    # PIE DE PÁGINA
    # ==========================================
    y_pie = y_totales + 18
    pdf.set_xy(12, y_pie)
    pdf.set_font("Helvetica", "", 8)
    pdf.set_text_color(*GRIS_MEDIO)
    pdf.cell(0, 4, "¡Gracias por su compra! Conserve este ticket.", align="C")

    pdf.set_xy(12, y_pie + 5)
    pdf.set_font("Helvetica", "", 7)
    pdf.set_text_color(180, 180, 180)
    pdf.cell(0, 3, "Página 1", align="C")

    return bytes(pdf.output())

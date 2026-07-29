import streamlit as st
import pandas as pd
from io import BytesIO
from fpdf import FPDF
from sqlalchemy import text
from db import obtener_conexion
from queries import obtener_todos_productos
from utils import verificar_auth

st.set_page_config(page_title="Etiquetas de Precio", layout="wide")
user_id, nombre_negocio = verificar_auth()

engine = obtener_conexion()

def formato_cop(numero):
    return f"${float(numero):,.0f}".replace(",", ".")


def generar_etiquetas_pdf(productos_sel, nombre_negocio, tamano, mostrar_codigo, mostrar_nombre_negocio):
    """
    Genera un PDF con etiquetas de precio listas para imprimir y recortar.
    
    Tamaños:
    - pequena: 4x2 cm (8 por fila)
    - mediana: 6x3 cm (5 por fila)  
    - grande:  8x4 cm (3 por fila)
    """
    pdf = FPDF(orientation="P", unit="mm", format="A4")
    pdf.add_page()
    pdf.set_auto_page_break(auto=True, margin=5)

    # Configuración por tamaño
    config = {
        "pequena": {"w": 44, "h": 22, "cols": 4, "font_nombre": 7, "font_precio": 11, "font_cod": 6},
        "mediana": {"w": 63, "h": 30, "cols": 3, "font_nombre": 8, "font_precio": 14, "font_cod": 7},
        "grande":  {"w": 90, "h": 40, "cols": 2, "font_nombre": 10, "font_precio": 18, "font_cod": 8},
    }
    cfg = config[tamano]

    margen_x = 5
    margen_y = 5
    espacio_x = 3
    espacio_y = 3

    x_actual = margen_x
    y_actual = margen_y
    col_actual = 0

    for prod in productos_sel:
        nombre = str(prod['nombre'])[:35]
        precio = float(prod['precio_venta'])
        costo = float(prod.get('costo_compra', 0))
        codigo = str(prod.get('codigo_barras') or prod.get('codigo_ref') or '')
        ganancia_pct = ((precio - costo) / costo * 100) if costo > 0 else 0

        # Fondo blanco con borde
        pdf.set_fill_color(255, 255, 255)
        pdf.set_draw_color(180, 180, 180)
        pdf.set_line_width(0.3)
        pdf.rect(x_actual, y_actual, cfg['w'], cfg['h'], 'FD')

        # Nombre del negocio (opcional)
        if mostrar_nombre_negocio:
            pdf.set_font("Helvetica", "I", cfg['font_cod'])
            pdf.set_text_color(120, 120, 120)
            pdf.set_xy(x_actual + 1, y_actual + 1)
            pdf.cell(cfg['w'] - 2, 3, nombre_negocio[:20], align="C")

        # Nombre del producto
        pdf.set_font("Helvetica", "B", cfg['font_nombre'])
        pdf.set_text_color(40, 40, 40)
        y_nombre = y_actual + (4 if mostrar_nombre_negocio else 2)
        pdf.set_xy(x_actual + 1, y_nombre)
        pdf.multi_cell(cfg['w'] - 2, 3.5, nombre, align="C")

        # Precio (grande y destacado)
        pdf.set_font("Helvetica", "B", cfg['font_precio'])
        pdf.set_text_color(20, 20, 20)
        precio_str = formato_cop(precio)
        y_precio = y_actual + cfg['h'] - (10 if mostrar_codigo and codigo else 7)
        pdf.set_xy(x_actual + 1, y_precio)
        pdf.cell(cfg['w'] - 2, 6, precio_str, align="C")

        # Código de barras (texto)
        if mostrar_codigo and codigo:
            pdf.set_font("Helvetica", "", cfg['font_cod'])
            pdf.set_text_color(100, 100, 100)
            pdf.set_xy(x_actual + 1, y_actual + cfg['h'] - 4)
            pdf.cell(cfg['w'] - 2, 3, f"Cód: {codigo}", align="C")

        # Avanzar posición
        col_actual += 1
        if col_actual >= cfg['cols']:
            col_actual = 0
            x_actual = margen_x
            y_actual += cfg['h'] + espacio_y
        else:
            x_actual += cfg['w'] + espacio_x

    buf = BytesIO()
    pdf.output(buf)
    buf.seek(0)
    return buf


st.title("Etiquetas de Precio")
st.markdown(f"Genera etiquetas para imprimir y pegar en tus productos: **{nombre_negocio}**")
st.markdown("---")

# ==========================================
# CONFIGURACIÓN DE ETIQUETAS
# ==========================================
col_conf1, col_conf2, col_conf3 = st.columns(3)

with col_conf1:
    tamano = st.selectbox(
        "Tamaño de etiqueta",
        ["pequena", "mediana", "grande"],
        format_func=lambda x: {
            "pequena": "Pequeña (4x2 cm) — 4 por fila",
            "mediana": "Mediana (6x3 cm) — 3 por fila",
            "grande": "Grande (9x4 cm) — 2 por fila"
        }[x]
    )

with col_conf2:
    mostrar_codigo = st.checkbox("Mostrar código de barras/ref", value=True)
    mostrar_nombre_negocio = st.checkbox("Mostrar nombre del negocio", value=True)

with col_conf3:
    cantidad_copias = st.number_input(
        "Copias por producto",
        min_value=1, max_value=100, value=1,
        help="Útil si tienes varias unidades del mismo producto"
    )

st.markdown("---")

# ==========================================
# SELECCIÓN DE PRODUCTOS
# ==========================================
st.subheader("Selecciona los Productos")

df_todos = obtener_todos_productos(user_id)

if df_todos.empty:
    st.info("No tienes productos registrados.")
else:
    col_filtro1, col_filtro2 = st.columns(2)
    with col_filtro1:
        busq_etiq = st.text_input("Buscar producto", placeholder="Nombre o categoría...")
    with col_filtro2:
        categorias_disponibles = ["-- Todas --"] + sorted(df_todos['categoria'].dropna().unique().tolist())
        cat_filtro = st.selectbox("Filtrar por categoría", categorias_disponibles)

    df_filtrado = df_todos.copy()
    if busq_etiq:
        df_filtrado = df_filtrado[
            df_filtrado['nombre'].str.contains(busq_etiq, case=False, na=False)
        ]
    if cat_filtro != "-- Todas --":
        df_filtrado = df_filtrado[df_filtrado['categoria'] == cat_filtro]

    # Selección con checkboxes
    st.caption(f"{len(df_filtrado)} producto(s) encontrado(s). Selecciona los que quieres etiquetar.")

    col_sel1, col_sel2 = st.columns(2)
    with col_sel1:
        if st.button("✅ Seleccionar todos", use_container_width=True):
            for i in df_filtrado.index:
                st.session_state[f"etiq_{i}"] = True
            st.rerun()
    with col_sel2:
        if st.button("❌ Deseleccionar todos", use_container_width=True):
            for i in df_filtrado.index:
                st.session_state[f"etiq_{i}"] = False
            st.rerun()

    st.markdown("")

    productos_seleccionados = []

    # Mostrar en grid de 3 columnas
    cols = st.columns(3)
    for idx, (i, prod) in enumerate(df_filtrado.iterrows()):
        col = cols[idx % 3]
        with col:
            seleccionado = st.checkbox(
                f"**{prod['nombre']}**\n{formato_cop(prod['precio_venta'])}",
                key=f"etiq_{i}",
                value=st.session_state.get(f"etiq_{i}", False)
            )
            if seleccionado:
                for _ in range(cantidad_copias):
                    productos_seleccionados.append(prod)

    st.markdown("---")

    # Preview y generación
    if productos_seleccionados:
        st.success(f"**{len(productos_seleccionados)} etiqueta(s)** seleccionada(s) — {len(productos_seleccionados) // (4 if tamano == 'pequena' else 3 if tamano == 'mediana' else 2) + 1} hoja(s) aprox.")

        col_prev1, col_prev2 = st.columns([2, 1])

        with col_prev1:
            st.markdown("**Preview de etiquetas seleccionadas:**")
            preview_data = []
            for p in productos_seleccionados[:10]:
                preview_data.append({
                    "Producto": p['nombre'],
                    "Precio": formato_cop(p['precio_venta']),
                    "Código": p.get('codigo_barras') or p.get('codigo_ref') or 'Sin código'
                })
            st.dataframe(pd.DataFrame(preview_data), hide_index=True, use_container_width=True)
            if len(productos_seleccionados) > 10:
                st.caption(f"... y {len(productos_seleccionados) - 10} más")

        with col_prev2:
            st.markdown("**Generar PDF:**")
            st.info(f"""
            📋 **Configuración:**
            - Tamaño: {tamano}
            - Etiquetas: {len(productos_seleccionados)}
            - Código: {'Sí' if mostrar_codigo else 'No'}
            - Negocio: {'Sí' if mostrar_nombre_negocio else 'No'}
            """)

            pdf_etiquetas = generar_etiquetas_pdf(
                productos_seleccionados,
                nombre_negocio,
                tamano,
                mostrar_codigo,
                mostrar_nombre_negocio
            )

            st.download_button(
                label="🖨️ Descargar Etiquetas PDF",
                data=pdf_etiquetas,
                file_name=f"Etiquetas_{nombre_negocio}_{tamano}.pdf",
                mime="application/pdf",
                type="primary",
                use_container_width=True
            )

            st.caption("💡 Imprime en papel adhesivo A4 para mejores resultados.")
    else:
        st.info("Selecciona al menos un producto para generar etiquetas.")

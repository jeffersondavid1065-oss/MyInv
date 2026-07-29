import streamlit as st
import pandas as pd
import os
from datetime import datetime
from sqlalchemy import text
from db import obtener_conexion
from queries import (
    obtener_todos_productos,
    obtener_metricas_inventario,
    obtener_proveedores,
    invalidar_cache_productos,
)
from utils import aplicar_estilos, verificar_auth

st.set_page_config(page_title="Inventario", layout="wide")
aplicar_estilos()
user_id, nombre_negocio = verificar_auth()

engine = obtener_conexion()

def formato_cop(numero):
    return f"${float(numero):,.0f}".replace(",", ".")

st.title("Inventario y Almacén")
st.markdown(f"Control de stock para: **{nombre_negocio}**")
st.markdown("---")

tab_stock, tab_nuevo, tab_entradas = st.tabs([
    "Stock Actual",
    "Agregar Producto",
    "Entradas de Mercancía"
])

# ==========================================
# TAB 1: STOCK ACTUAL
# ==========================================
with tab_stock:
    # Métricas agregadas
    metricas = obtener_metricas_inventario(user_id)
    if metricas:
        col1, col2, col3, col4, col5 = st.columns(5)
        col1.metric("Total Productos", int(metricas[2]))
        col2.metric("Inversión (Costo)", formato_cop(metricas[0]))
        col3.metric("Valor Comercial", formato_cop(metricas[1]))
        col4.metric("Agotados", int(metricas[3]),
                    delta="⚠️" if int(metricas[3]) > 0 else None,
                    delta_color="inverse")
        col5.metric("Por Agotarse", int(metricas[4]),
                    delta="⚠️" if int(metricas[4]) > 0 else None,
                    delta_color="inverse")

    st.markdown("---")

    # Filtros
    col_f1, col_f2, col_f3 = st.columns(3)
    with col_f1:
        busqueda_inv = st.text_input(
            "Buscar por nombre o código",
            placeholder="Escanea o escribe...",
            key="busq_inv"
        )
    with col_f2:
        filtro_estado = st.selectbox(
            "Estado de stock",
            ["Todos", "Agotados", "Por agotarse", "Con stock"]
        )
    with col_f3:
        filtro_categoria = st.text_input("Categoría", placeholder="Ej: Ferretería")

    # Cargar productos
    df_inv = obtener_todos_productos(user_id)

    if not df_inv.empty:
        # Aplicar filtros en pandas (sin nueva query)
        if busqueda_inv:
            mask = (
                df_inv['nombre'].str.contains(busqueda_inv, case=False, na=False) |
                df_inv['codigo_barras'].astype(str).str.contains(busqueda_inv, case=False, na=False) |
                df_inv['codigo_ref'].astype(str).str.contains(busqueda_inv, case=False, na=False)
            )
            df_inv = df_inv[mask]

        if filtro_estado == "Agotados":
            df_inv = df_inv[df_inv['stock_actual'] <= 0]
        elif filtro_estado == "Por agotarse":
            df_inv = df_inv[(df_inv['stock_actual'] > 0) & (df_inv['stock_actual'] <= df_inv['stock_minimo'])]
        elif filtro_estado == "Con stock":
            df_inv = df_inv[df_inv['stock_actual'] > 0]

        if filtro_categoria:
            df_inv = df_inv[df_inv['categoria'].str.contains(filtro_categoria, case=False, na=False)]

        if df_inv.empty:
            st.info("No hay productos que coincidan con los filtros.")
        else:
            st.caption(f"Mostrando {len(df_inv)} producto(s). Edita directamente en la tabla y guarda.")

            df_edit = st.data_editor(
                df_inv[[
                    'id', 'nombre', 'codigo_barras', 'codigo_ref',
                    'categoria', 'stock_actual', 'stock_minimo',
                    'costo_compra', 'precio_venta'
                ]],
                hide_index=True,
                use_container_width=True,
                disabled=["id"],
                column_config={
                    "id": None,
                    "nombre": "Producto",
                    "codigo_barras": "Código Barras",
                    "codigo_ref": "Referencia",
                    "categoria": "Categoría",
                    "stock_actual": st.column_config.NumberColumn("Stock", min_value=0, step=1),
                    "stock_minimo": st.column_config.NumberColumn("Stock Mín.", min_value=0, step=1),
                    "costo_compra": st.column_config.NumberColumn("Costo ($)", format="$%d"),
                    "precio_venta": st.column_config.NumberColumn("Precio Venta ($)", format="$%d"),
                },
                key=f"editor_inv_{busqueda_inv}_{filtro_estado}"
            )

            if st.button("💾 Guardar Cambios", type="primary"):
                try:
                    with engine.begin() as conn:
                        for _, row in df_edit.iterrows():
                            conn.execute(text("""
                                UPDATE Productos
                                SET nombre = :nom, codigo_barras = :cod,
                                    codigo_ref = :ref, categoria = :cat,
                                    stock_actual = :st_act, stock_minimo = :st_min,
                                    costo_compra = :costo, precio_venta = :pvp
                                WHERE id = :id AND usuario_id = :uid
                            """), {
                                "nom": row['nombre'],
                                "cod": row['codigo_barras'],
                                "ref": row['codigo_ref'],
                                "cat": row['categoria'],
                                "st_act": int(row['stock_actual']),
                                "st_min": int(row['stock_minimo']),
                                "costo": float(row['costo_compra']),
                                "pvp": float(row['precio_venta']),
                                "id": int(row['id']),
                                "uid": user_id
                            })
                    invalidar_cache_productos()
                    st.success("Inventario actualizado correctamente.")
                    st.rerun()
                except Exception as e:
                    st.error(f"Error al guardar: {e}")
    else:
        st.info("No tienes productos registrados. Ve a 'Agregar Producto' para comenzar.")

# ==========================================
# TAB 2: AGREGAR PRODUCTO NUEVO
# ==========================================
with tab_nuevo:
    st.subheader("Registrar Nuevo Producto")
    st.caption("Puedes escanear el código de barras en el campo correspondiente.")

    col_n1, col_n2 = st.columns(2)
    with col_n1:
        nom_p = st.text_input("Nombre del Producto *")
        desc_p = st.text_input("Descripción (opcional)")
        cod_barras = st.text_input(
            "Código de Barras",
            placeholder="Escanea con el lector o escribe manualmente"
        )
        cod_ref = st.text_input("Referencia interna (opcional)")
        categoria_p = st.text_input("Categoría", value="General")
        stock_inicial = st.number_input("Stock Inicial", min_value=0, value=0, step=1)
        stock_min = st.number_input("Stock Mínimo (alerta)", min_value=0, value=2, step=1)

    with col_n2:
        st.markdown("**💰 Precio de Venta**")

        costo_p = st.number_input("Costo de Compra ($) *", min_value=0.0, step=1000.0, key="costo_nuevo")

        # Modo de cálculo del precio
        modo_precio = st.radio(
            "Calcular precio por:",
            ["Porcentaje de ganancia", "Precio fijo"],
            horizontal=True
        )

        if modo_precio == "Porcentaje de ganancia":
            porcentaje = st.slider(
                "% de ganancia",
                min_value=0, max_value=300,
                value=30, step=1,
                help="Porcentaje de ganancia sobre el costo de compra"
            )

            if costo_p > 0:
                precio_calculado = costo_p * (1 + porcentaje / 100)
                ganancia_pesos = precio_calculado - costo_p

                with st.container(border=True):
                    st.markdown(f"**Costo:** {f'${costo_p:,.0f}'.replace(',', '.')}")
                    st.markdown(f"**Ganancia ({porcentaje}%):** {f'${ganancia_pesos:,.0f}'.replace(',', '.')}")
                    st.markdown(f"### Precio de Venta: {f'${precio_calculado:,.0f}'.replace(',', '.')}")

                precio_p = precio_calculado

                # Ajuste fino manual
                ajuste = st.number_input(
                    "Ajuste fino al precio ($)",
                    min_value=-precio_calculado,
                    value=0.0, step=100.0,
                    help="Opcional: ajusta el precio calculado hacia arriba o abajo"
                )
                precio_p = max(0, precio_calculado + ajuste)
                if ajuste != 0:
                    pct_real = ((precio_p - costo_p) / costo_p * 100) if costo_p > 0 else 0
                    st.caption(f"Precio ajustado: {f'${precio_p:,.0f}'.replace(',', '.')} ({pct_real:.1f}% de ganancia)")
            else:
                st.info("Ingresa el costo de compra para calcular el precio.")
                porcentaje = 30
                precio_p = 0.0

        else:  # Precio fijo
            precio_p = st.number_input(
                "Precio de Venta ($) *",
                min_value=0.0, step=1000.0
            )
            if costo_p > 0 and precio_p > 0:
                ganancia = precio_p - costo_p
                pct = (ganancia / costo_p) * 100
                color = "🟢" if pct > 0 else "🔴"
                st.caption(f"{color} Ganancia: {f'${ganancia:,.0f}'.replace(',', '.')} ({pct:.1f}%)")

    st.markdown("")
    if st.button("💾 Guardar Producto", type="primary", use_container_width=True):
        if nom_p and precio_p > 0:
            try:
                with engine.begin() as conn:
                    conn.execute(text("""
                        INSERT INTO Productos
                        (usuario_id, nombre, descripcion, codigo_barras, codigo_ref,
                         categoria, stock_actual, stock_minimo, costo_compra, precio_venta)
                        VALUES (:uid, :nom, :desc, :cod, :ref, :cat, :stk, :stk_min, :costo, :pvp)
                    """), {
                        "uid": user_id, "nom": nom_p, "desc": desc_p or None,
                        "cod": cod_barras or None, "ref": cod_ref or None,
                        "cat": categoria_p, "stk": int(stock_inicial),
                        "stk_min": int(stock_min), "costo": float(costo_p),
                        "pvp": float(precio_p)
                    })
                invalidar_cache_productos()
                st.success(f"✅ Producto '{nom_p}' registrado con precio {f'${precio_p:,.0f}'.replace(',', '.')}.")
                st.rerun()
            except Exception as e:
                st.error(f"Error al guardar: {e}")
        else:
            st.warning("El nombre y el precio de venta son obligatorios.")

# ==========================================
# TAB 3: ENTRADAS DE MERCANCÍA
# ==========================================
with tab_entradas:
    st.subheader("Registrar Entrada de Mercancía")
    st.caption("Sube la foto o PDF de la factura como comprobante y registra los productos recibidos.")

    proveedores = obtener_proveedores(user_id)
    df_productos_entrada = obtener_todos_productos(user_id)

    if df_productos_entrada.empty:
        st.info("Primero registra productos en el inventario.")
    else:
        dict_proveedores = {p[1]: p[0] for p in proveedores} if proveedores else {}
        dict_productos = {
            f"{r['nombre']} ({r['codigo_barras'] or r['codigo_ref'] or 'sin código'})": r['id']
            for _, r in df_productos_entrada.iterrows()
        }

        col_izq_e, col_der_e = st.columns([1, 1])

        with col_izq_e:
            st.markdown("**1. Sube la factura del proveedor**")
            factura_img = st.file_uploader(
                "Foto o PDF de la factura",
                type=["jpg", "jpeg", "png", "pdf"],
                help="Se guarda como comprobante de la entrada."
            )
            if factura_img:
                if factura_img.type != "application/pdf":
                    st.image(factura_img, caption="Factura del proveedor", use_container_width=True)
                else:
                    st.success(f"📄 PDF cargado: {factura_img.name}")

        with col_der_e:
            st.markdown("**2. Datos de la entrada**")
            if dict_proveedores:
                proveedor_sel = st.selectbox(
                    "Proveedor (opcional)",
                    ["-- Sin proveedor --"] + list(dict_proveedores.keys())
                )
            else:
                proveedor_sel = "-- Sin proveedor --"
                st.caption("Sin proveedores registrados.")
            num_factura = st.text_input("Número de Factura (opcional)")
            notas_entrada = st.text_area("Notas (opcional)", height=68)

        st.markdown("---")
        st.markdown("**3. Productos recibidos** — completa mirando la factura:")

        # Tabla dinámica de productos
        if "items_entrada" not in st.session_state:
            st.session_state.items_entrada = [
                {"producto": "-- Seleccionar --", "cantidad": 1, "costo": 0.0}
            ]

        col_add, col_clear = st.columns([1, 1])
        with col_add:
            if st.button("➕ Agregar fila", use_container_width=True):
                st.session_state.items_entrada.append(
                    {"producto": "-- Seleccionar --", "cantidad": 1, "costo": 0.0}
                )
                st.rerun()
        with col_clear:
            if st.button("🗑️ Limpiar filas", use_container_width=True):
                st.session_state.items_entrada = [
                    {"producto": "-- Seleccionar --", "cantidad": 1, "costo": 0.0}
                ]
                st.rerun()

        items_validos = []
        total_entrada = 0

        opciones_productos = ["-- Seleccionar --"] + list(dict_productos.keys())

        for i, item in enumerate(st.session_state.items_entrada):
            col_i1, col_i2, col_i3, col_i4 = st.columns([3, 1, 2, 1])
            with col_i1:
                prod_sel = st.selectbox(
                    f"Producto {i+1}",
                    options=opciones_productos,
                    key=f"ep_{i}",
                    label_visibility="collapsed" if i > 0 else "visible"
                )
            with col_i2:
                cant = st.number_input(
                    "Cant.", min_value=1, value=item["cantidad"],
                    step=1, key=f"ec_{i}",
                    label_visibility="collapsed" if i > 0 else "visible"
                )
            with col_i3:
                costo = st.number_input(
                    "Costo unit. ($)", min_value=0.0,
                    value=item["costo"], step=1000.0,
                    key=f"ek_{i}",
                    label_visibility="collapsed" if i > 0 else "visible"
                )
            with col_i4:
                if prod_sel != "-- Seleccionar --" and cant > 0:
                    subtotal_i = cant * costo
                    st.write(f"**{f'${subtotal_i:,.0f}'.replace(',', '.')}**")
                    total_entrada += subtotal_i
                    items_validos.append({
                        "producto_id": dict_productos[prod_sel],
                        "cantidad": cant,
                        "costo_unitario": costo,
                        "subtotal": subtotal_i
                    })

        if total_entrada > 0:
            st.info(f"**Total de la entrada: ${total_entrada:,.0f}**".replace(",", "."))

        st.markdown("")
        if st.button("✅ Registrar Entrada", type="primary", use_container_width=True):
            if items_validos:
                try:
                    proveedor_id = dict_proveedores.get(proveedor_sel) if proveedor_sel != "-- Sin proveedor --" else None

                    # Guardar imagen de factura si se subió
                    factura_path = None
                    if factura_img:
                        facturas_dir = os.path.join(
                            os.path.dirname(os.path.abspath(__file__)), "..", "facturas"
                        )
                        os.makedirs(facturas_dir, exist_ok=True)
                        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
                        ext = factura_img.name.split(".")[-1]
                        factura_path = os.path.join(facturas_dir, f"factura_{user_id}_{timestamp}.{ext}")
                        with open(factura_path, "wb") as f:
                            f.write(factura_img.getbuffer())

                    with engine.begin() as conn:
                        is_sqlite = "sqlite" in str(engine.url)

                        if is_sqlite:
                            cur = conn.execute(text("""
                                INSERT INTO Entradas_Inventario
                                (usuario_id, proveedor_id, numero_factura, total_compra, notas)
                                VALUES (:uid, :pid, :nf, :total, :notas)
                            """), {
                                "uid": user_id, "pid": proveedor_id,
                                "nf": num_factura or None,
                                "total": total_entrada,
                                "notas": notas_entrada or None
                            })
                            entrada_id = cur.lastrowid
                        else:
                            res = conn.execute(text("""
                                INSERT INTO Entradas_Inventario
                                (usuario_id, proveedor_id, numero_factura, total_compra, notas)
                                VALUES (:uid, :pid, :nf, :total, :notas)
                                RETURNING id
                            """), {
                                "uid": user_id, "pid": proveedor_id,
                                "nf": num_factura or None,
                                "total": total_entrada,
                                "notas": notas_entrada or None
                            })
                            entrada_id = res.scalar()

                        for item in items_validos:
                            conn.execute(text("""
                                INSERT INTO Detalles_Entrada
                                (entrada_id, producto_id, cantidad, costo_unitario, subtotal)
                                VALUES (:eid, :pid, :cant, :costo, :sub)
                            """), {
                                "eid": entrada_id,
                                "pid": item["producto_id"],
                                "cant": item["cantidad"],
                                "costo": item["costo_unitario"],
                                "sub": item["subtotal"]
                            })
                            conn.execute(text("""
                                UPDATE Productos
                                SET stock_actual = stock_actual + :cant,
                                    costo_compra = :costo
                                WHERE id = :pid AND usuario_id = :uid
                            """), {
                                "cant": item["cantidad"],
                                "costo": item["costo_unitario"],
                                "pid": item["producto_id"],
                                "uid": user_id
                            })

                    invalidar_cache_productos()
                    st.session_state.items_entrada = [
                        {"producto": "-- Seleccionar --", "cantidad": 1, "costo": 0.0}
                    ]
                    st.success(f"✅ Entrada #{entrada_id} registrada. {len(items_validos)} producto(s) actualizados en stock.")
                    if factura_path:
                        st.caption(f"📎 Factura guardada como comprobante.")
                    st.rerun()
                except Exception as e:
                    st.error(f"Error al registrar entrada: {e}")
            else:
                st.warning("Selecciona al menos un producto con cantidad mayor a 0.")

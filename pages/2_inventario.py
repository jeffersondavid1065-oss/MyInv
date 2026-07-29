import streamlit as st
import pandas as pd
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

    with st.form("form_nuevo_producto", clear_on_submit=True):
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

        with col_n2:
            stock_inicial = st.number_input("Stock Inicial", min_value=0, value=0, step=1)
            stock_min = st.number_input("Stock Mínimo (alerta)", min_value=0, value=2, step=1)
            costo_p = st.number_input("Costo de Compra ($)", min_value=0.0, step=1000.0)
            precio_p = st.number_input("Precio de Venta ($) *", min_value=0.0, step=1000.0)

        st.markdown("")
        if st.form_submit_button("Guardar Producto", type="primary"):
            if nom_p and precio_p > 0:
                try:
                    with engine.begin() as conn:
                        is_sqlite = "sqlite" in str(engine.url)
                        if is_sqlite:
                            conn.execute(text("""
                                INSERT INTO Productos
                                (usuario_id, nombre, descripcion, codigo_barras, codigo_ref,
                                 categoria, stock_actual, stock_minimo, costo_compra, precio_venta)
                                VALUES (:uid, :nom, :desc, :cod, :ref, :cat, :stk, :stk_min, :costo, :pvp)
                            """), {
                                "uid": user_id, "nom": nom_p, "desc": desc_p,
                                "cod": cod_barras or None, "ref": cod_ref or None,
                                "cat": categoria_p, "stk": int(stock_inicial),
                                "stk_min": int(stock_min), "costo": float(costo_p),
                                "pvp": float(precio_p)
                            })
                        else:
                            conn.execute(text("""
                                INSERT INTO Productos
                                (usuario_id, nombre, descripcion, codigo_barras, codigo_ref,
                                 categoria, stock_actual, stock_minimo, costo_compra, precio_venta)
                                VALUES (:uid, :nom, :desc, :cod, :ref, :cat, :stk, :stk_min, :costo, :pvp)
                            """), {
                                "uid": user_id, "nom": nom_p, "desc": desc_p,
                                "cod": cod_barras or None, "ref": cod_ref or None,
                                "cat": categoria_p, "stk": int(stock_inicial),
                                "stk_min": int(stock_min), "costo": float(costo_p),
                                "pvp": float(precio_p)
                            })
                    invalidar_cache_productos()
                    st.success(f"Producto '{nom_p}' registrado exitosamente.")
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
    st.caption("Registra las compras a proveedores. El stock se actualiza automáticamente.")

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

        with st.form("form_entrada", clear_on_submit=True):
            col_e1, col_e2 = st.columns(2)
            with col_e1:
                if dict_proveedores:
                    proveedor_sel = st.selectbox(
                        "Proveedor (opcional)",
                        ["-- Sin proveedor --"] + list(dict_proveedores.keys())
                    )
                else:
                    proveedor_sel = "-- Sin proveedor --"
                    st.info("No tienes proveedores. Ve a Proveedores para agregar uno.")
                num_factura = st.text_input("Número de Factura (opcional)")

            with col_e2:
                notas_entrada = st.text_area("Notas (opcional)", height=80)

            st.markdown("**Productos recibidos:**")
            st.caption("Agrega hasta 10 productos por entrada.")

            items_entrada = []
            total_entrada = 0

            for i in range(10):
                col_i1, col_i2, col_i3 = st.columns([3, 1, 1])
                with col_i1:
                    prod_sel = st.selectbox(
                        f"Producto {i+1}",
                        ["-- Seleccionar --"] + list(dict_productos.keys()),
                        key=f"entrada_prod_{i}",
                        label_visibility="collapsed" if i > 0 else "visible"
                    )
                with col_i2:
                    cant_entrada = st.number_input(
                        "Cantidad", min_value=0, value=0, step=1,
                        key=f"entrada_cant_{i}",
                        label_visibility="collapsed" if i > 0 else "visible"
                    )
                with col_i3:
                    costo_entrada = st.number_input(
                        "Costo unitario ($)", min_value=0.0, step=1000.0,
                        key=f"entrada_costo_{i}",
                        label_visibility="collapsed" if i > 0 else "visible"
                    )

                if prod_sel != "-- Seleccionar --" and cant_entrada > 0:
                    subtotal_item = cant_entrada * costo_entrada
                    total_entrada += subtotal_item
                    items_entrada.append({
                        "producto_id": dict_productos[prod_sel],
                        "cantidad": cant_entrada,
                        "costo_unitario": costo_entrada,
                        "subtotal": subtotal_item
                    })

            if total_entrada > 0:
                st.info(f"Total de la entrada: {formato_cop(total_entrada)}")

            if st.form_submit_button("Registrar Entrada", type="primary"):
                if items_entrada:
                    try:
                        proveedor_id = dict_proveedores.get(proveedor_sel) if proveedor_sel != "-- Sin proveedor --" else None

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

                            for item in items_entrada:
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
                                # Actualizar stock y costo de compra
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
                        st.success(f"Entrada #{entrada_id} registrada. Stock actualizado.")
                        st.rerun()
                    except Exception as e:
                        st.error(f"Error al registrar entrada: {e}")
                else:
                    st.warning("Selecciona al menos un producto con cantidad mayor a 0.")

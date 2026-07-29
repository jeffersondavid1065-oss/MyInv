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

tab_stock, tab_nuevo, tab_entradas, tab_factura_ia = st.tabs([
    "Stock Actual",
    "Agregar Producto",
    "Entradas de Mercancía",
    "🤖 Leer Factura con IA"
])

# ==========================================
# TAB 1: STOCK ACTUAL
# ==========================================
with tab_stock:
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

    col_f1, col_f2, col_f3 = st.columns(3)
    with col_f1:
        busqueda_inv = st.text_input("Buscar por nombre o código",
                                      placeholder="Escanea o escribe...", key="busq_inv")
    with col_f2:
        filtro_estado = st.selectbox("Estado de stock",
                                      ["Todos", "Agotados", "Por agotarse", "Con stock"])
    with col_f3:
        filtro_categoria = st.text_input("Categoría", placeholder="Ej: Ferretería")

    df_inv = obtener_todos_productos(user_id)

    if not df_inv.empty:
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
                df_inv[['id', 'nombre', 'codigo_barras', 'codigo_ref',
                        'categoria', 'stock_actual', 'stock_minimo',
                        'costo_compra', 'precio_venta']],
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
                                "nom": row['nombre'], "cod": row['codigo_barras'],
                                "ref": row['codigo_ref'], "cat": row['categoria'],
                                "st_act": int(row['stock_actual']),
                                "st_min": int(row['stock_minimo']),
                                "costo": float(row['costo_compra']),
                                "pvp": float(row['precio_venta']),
                                "id": int(row['id']), "uid": user_id
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
        cod_barras = st.text_input("Código de Barras",
                                    placeholder="Escanea con el lector o escribe manualmente")
        cod_ref = st.text_input("Referencia interna (opcional)")
        categoria_p = st.text_input("Categoría", value="General")
        stock_inicial = st.number_input("Stock Inicial", min_value=0, value=0, step=1)
        stock_min = st.number_input("Stock Mínimo (alerta)", min_value=0, value=2, step=1)

    with col_n2:
        st.markdown("**💰 Precio de Venta**")
        costo_p = st.number_input("Costo de Compra ($) *", min_value=0.0,
                                   step=1000.0, key="costo_nuevo")
        modo_precio = st.radio("Calcular precio por:",
                                ["Porcentaje de ganancia", "Precio fijo"], horizontal=True)

        if modo_precio == "Porcentaje de ganancia":
            porcentaje = st.slider("% de ganancia", min_value=0, max_value=300,
                                   value=30, step=1)
            if costo_p > 0:
                precio_calculado = costo_p * (1 + porcentaje / 100)
                ganancia_pesos = precio_calculado - costo_p
                with st.container(border=True):
                    st.markdown(f"**Costo:** {formato_cop(costo_p)}")
                    st.markdown(f"**Ganancia ({porcentaje}%):** {formato_cop(ganancia_pesos)}")
                    st.markdown(f"### Precio de Venta: {formato_cop(precio_calculado)}")
                precio_p = precio_calculado
                ajuste = st.number_input("Ajuste fino al precio ($)",
                                          min_value=-precio_calculado, value=0.0, step=100.0)
                precio_p = max(0, precio_calculado + ajuste)
                if ajuste != 0:
                    pct_real = ((precio_p - costo_p) / costo_p * 100) if costo_p > 0 else 0
                    st.caption(f"Precio ajustado: {formato_cop(precio_p)} ({pct_real:.1f}% de ganancia)")
            else:
                st.info("Ingresa el costo de compra para calcular el precio.")
                precio_p = 0.0
        else:
            precio_p = st.number_input("Precio de Venta ($) *", min_value=0.0, step=1000.0)
            if costo_p > 0 and precio_p > 0:
                ganancia = precio_p - costo_p
                pct = (ganancia / costo_p) * 100
                color = "🟢" if pct > 0 else "🔴"
                st.caption(f"{color} Ganancia: {formato_cop(ganancia)} ({pct:.1f}%)")

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
                st.success(f"✅ Producto '{nom_p}' registrado con precio {formato_cop(precio_p)}.")
                st.rerun()
            except Exception as e:
                st.error(f"Error al guardar: {e}")
        else:
            st.warning("El nombre y el precio de venta son obligatorios.")

# ==========================================
# TAB 3: ENTRADAS DE MERCANCÍA (MANUAL)
# ==========================================
with tab_entradas:
    st.subheader("Registrar Entrada de Mercancía")
    st.caption("Registra los productos recibidos manualmente.")

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
            st.markdown("**Comprobante (opcional)**")
            factura_img = st.file_uploader("Foto o PDF de la factura",
                                            type=["jpg", "jpeg", "png", "pdf"])
            if factura_img:
                if factura_img.type != "application/pdf":
                    st.image(factura_img, use_container_width=True)
                else:
                    st.success(f"📄 {factura_img.name}")

        with col_der_e:
            st.markdown("**Datos de la entrada**")
            if dict_proveedores:
                proveedor_sel = st.selectbox("Proveedor",
                                              ["-- Sin proveedor --"] + list(dict_proveedores.keys()))
            else:
                proveedor_sel = "-- Sin proveedor --"
            num_factura = st.text_input("Número de Factura (opcional)")
            notas_entrada = st.text_area("Notas (opcional)", height=68)

        st.markdown("---")
        st.markdown("**Productos recibidos:**")

        if "items_entrada" not in st.session_state:
            st.session_state.items_entrada = [{"producto": "-- Seleccionar --", "cantidad": 1, "costo": 0.0}]

        col_add, col_clear = st.columns(2)
        with col_add:
            if st.button("➕ Agregar fila", use_container_width=True):
                st.session_state.items_entrada.append({"producto": "-- Seleccionar --", "cantidad": 1, "costo": 0.0})
                st.rerun()
        with col_clear:
            if st.button("🗑️ Limpiar filas", use_container_width=True):
                st.session_state.items_entrada = [{"producto": "-- Seleccionar --", "cantidad": 1, "costo": 0.0}]
                st.rerun()

        items_validos = []
        total_entrada = 0
        opciones_productos = ["-- Seleccionar --"] + list(dict_productos.keys())

        for i, item in enumerate(st.session_state.items_entrada):
            col_i1, col_i2, col_i3, col_i4 = st.columns([3, 1, 2, 1])
            with col_i1:
                prod_sel = st.selectbox(f"Producto {i+1}", options=opciones_productos,
                                         key=f"ep_{i}", label_visibility="collapsed" if i > 0 else "visible")
            with col_i2:
                cant = st.number_input("Cant.", min_value=1, value=item["cantidad"],
                                        step=1, key=f"ec_{i}",
                                        label_visibility="collapsed" if i > 0 else "visible")
            with col_i3:
                costo = st.number_input("Costo unit. ($)", min_value=0.0,
                                         value=item["costo"], step=1000.0, key=f"ek_{i}",
                                         label_visibility="collapsed" if i > 0 else "visible")
            with col_i4:
                if prod_sel != "-- Seleccionar --" and cant > 0:
                    subtotal_i = cant * costo
                    st.write(f"**{formato_cop(subtotal_i)}**")
                    total_entrada += subtotal_i
                    items_validos.append({
                        "producto_id": dict_productos[prod_sel],
                        "cantidad": cant, "costo_unitario": costo, "subtotal": subtotal_i
                    })

        if total_entrada > 0:
            st.info(f"**Total de la entrada: {formato_cop(total_entrada)}**")

        if st.button("✅ Registrar Entrada", type="primary", use_container_width=True):
            if items_validos:
                try:
                    proveedor_id = dict_proveedores.get(proveedor_sel) if proveedor_sel != "-- Sin proveedor --" else None
                    factura_path = None
                    if factura_img:
                        facturas_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "facturas")
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
                            """), {"uid": user_id, "pid": proveedor_id,
                                   "nf": num_factura or None, "total": total_entrada,
                                   "notas": notas_entrada or None})
                            entrada_id = cur.lastrowid
                        else:
                            res = conn.execute(text("""
                                INSERT INTO Entradas_Inventario
                                (usuario_id, proveedor_id, numero_factura, total_compra, notas)
                                VALUES (:uid, :pid, :nf, :total, :notas) RETURNING id
                            """), {"uid": user_id, "pid": proveedor_id,
                                   "nf": num_factura or None, "total": total_entrada,
                                   "notas": notas_entrada or None})
                            entrada_id = res.scalar()

                        for item in items_validos:
                            conn.execute(text("""
                                INSERT INTO Detalles_Entrada
                                (entrada_id, producto_id, cantidad, costo_unitario, subtotal)
                                VALUES (:eid, :pid, :cant, :costo, :sub)
                            """), {"eid": entrada_id, "pid": item["producto_id"],
                                   "cant": item["cantidad"], "costo": item["costo_unitario"],
                                   "sub": item["subtotal"]})
                            conn.execute(text("""
                                UPDATE Productos SET stock_actual = stock_actual + :cant,
                                    costo_compra = :costo
                                WHERE id = :pid AND usuario_id = :uid
                            """), {"cant": item["cantidad"], "costo": item["costo_unitario"],
                                   "pid": item["producto_id"], "uid": user_id})

                    invalidar_cache_productos()
                    st.session_state.items_entrada = [{"producto": "-- Seleccionar --", "cantidad": 1, "costo": 0.0}]
                    st.success(f"✅ Entrada #{entrada_id} registrada. Stock actualizado.")
                    st.rerun()
                except Exception as e:
                    st.error(f"Error al registrar entrada: {e}")
            else:
                st.warning("Selecciona al menos un producto con cantidad mayor a 0.")

# ==========================================
# TAB 4: LEER FACTURA CON IA (GEMINI)
# ==========================================
with tab_factura_ia:
    st.subheader("🤖 Leer Factura con Inteligencia Artificial")
    st.caption("Sube la foto o PDF de la factura del proveedor y Gemini extrae los productos automáticamente.")

    # Verificar si Gemini está configurado
    gemini_ok = False
    try:
        import google.generativeai as genai
        api_key = st.secrets["gemini"]["api_key"]
        genai.configure(api_key=api_key)
        gemini_ok = True
    except Exception:
        st.error("⚠️ Gemini no está configurado. Agrega tu API key en Streamlit Secrets: `[gemini] api_key = 'tu-key'`")

    if gemini_ok:
        proveedores_ia = obtener_proveedores(user_id)
        dict_proveedores_ia = {p[1]: p[0] for p in proveedores_ia} if proveedores_ia else {}

        col_ia1, col_ia2 = st.columns([1, 1])
        with col_ia1:
            archivo_factura = st.file_uploader(
                "📸 Sube la foto o PDF de la factura",
                type=["jpg", "jpeg", "png", "pdf"],
                help="Toma una foto clara de la factura o sube el PDF directo."
            )
            if archivo_factura:
                if archivo_factura.type != "application/pdf":
                    st.image(archivo_factura, caption="Factura cargada", use_container_width=True)
                else:
                    st.success(f"📄 PDF cargado: {archivo_factura.name}")

        with col_ia2:
            st.markdown("**Datos opcionales:**")
            if dict_proveedores_ia:
                prov_ia = st.selectbox("Proveedor", ["-- Sin proveedor --"] + list(dict_proveedores_ia.keys()), key="prov_ia")
            else:
                prov_ia = "-- Sin proveedor --"
            nf_ia = st.text_input("Número de factura (opcional)", key="nf_ia")
            notas_ia = st.text_area("Notas (opcional)", height=68, key="notas_ia")

        if archivo_factura:
            if st.button("🤖 Analizar Factura con Gemini", type="primary", use_container_width=True):
                with st.spinner("Gemini está leyendo la factura... esto tarda unos segundos..."):
                    try:
                        from gemini_utils import leer_factura_imagen, leer_factura_pdf

                        archivo_bytes = archivo_factura.read()

                        if archivo_factura.type == "application/pdf":
                            datos = leer_factura_pdf(archivo_bytes)
                        else:
                            datos = leer_factura_imagen(archivo_bytes)

                        if datos and "productos" in datos:
                            st.session_state.factura_ia_datos = datos
                            st.session_state.factura_ia_productos = datos["productos"]
                            st.success(f"✅ Gemini detectó **{len(datos['productos'])} producto(s)** en la factura.")

                            # Mostrar info del proveedor detectado
                            if datos.get("proveedor"):
                                st.info(f"📦 Proveedor detectado: **{datos['proveedor']}**")
                            if datos.get("numero_factura"):
                                st.info(f"📄 Factura N°: **{datos['numero_factura']}**")
                            if datos.get("total_factura"):
                                st.info(f"💰 Total detectado: **{formato_cop(datos['total_factura'])}**")
                        else:
                            st.error("No se pudieron extraer productos. Intenta con una imagen más clara.")

                    except Exception as e:
                        st.error(f"Error al analizar: {e}")

        # Mostrar tabla editable con productos detectados
        if "factura_ia_productos" in st.session_state and st.session_state.factura_ia_productos:
            st.markdown("---")
            st.markdown("### 📋 Productos detectados — revisa y corrige si es necesario")
            st.caption("Puedes editar cualquier valor antes de confirmar. Agrega o elimina filas según necesites.")

            import json as _json
            df_ia = pd.DataFrame(st.session_state.factura_ia_productos)

            for col in ['nombre', 'cantidad', 'costo_unitario', 'subtotal']:
                if col not in df_ia.columns:
                    df_ia[col] = 0 if col != 'nombre' else ""

            df_ia_edit = st.data_editor(
                df_ia[['nombre', 'cantidad', 'costo_unitario', 'subtotal']],
                column_config={
                    "nombre": st.column_config.TextColumn("Producto", width="large"),
                    "cantidad": st.column_config.NumberColumn("Cantidad", min_value=0, step=1),
                    "costo_unitario": st.column_config.NumberColumn("Costo Unitario ($)", min_value=0, format="$%d"),
                    "subtotal": st.column_config.NumberColumn("Subtotal ($)", min_value=0, format="$%d"),
                },
                use_container_width=True,
                hide_index=True,
                num_rows="dynamic",
                key="editor_factura_ia"
            )

            total_ia = df_ia_edit['subtotal'].sum()
            st.info(f"**Total de la entrada: {formato_cop(total_ia)}**")

            st.markdown("---")
            st.markdown("**Asociar productos al inventario existente:**")
            st.caption("Selecciona a qué producto del inventario corresponde cada ítem de la factura.")

            df_productos_ia = obtener_todos_productos(user_id)
            dict_prods_ia = {
                f"{r['nombre']}": r['id']
                for _, r in df_productos_ia.iterrows()
            } if not df_productos_ia.empty else {}

            items_ia_validos = []

            if not dict_prods_ia:
                st.warning("No tienes productos en el inventario. Los productos de la factura se ignorarán al registrar.")
            else:
                for i, row in df_ia_edit.iterrows():
                    if not row['nombre'] or row['cantidad'] <= 0:
                        continue

                    col_ia_p1, col_ia_p2 = st.columns([2, 2])
                    with col_ia_p1:
                        st.write(f"**{row['nombre']}** — Cant: {int(row['cantidad'])} — {formato_cop(row['subtotal'])}")
                    with col_ia_p2:
                        prod_match = st.selectbox(
                            "Asociar a producto del inventario",
                            ["-- Crear nuevo --"] + list(dict_prods_ia.keys()),
                            key=f"ia_match_{i}"
                        )

                    items_ia_validos.append({
                        "nombre_factura": row['nombre'],
                        "cantidad": int(row['cantidad']),
                        "costo_unitario": float(row['costo_unitario']),
                        "subtotal": float(row['subtotal']),
                        "producto_id": dict_prods_ia.get(prod_match) if prod_match != "-- Crear nuevo --" else None,
                        "crear_nuevo": prod_match == "-- Crear nuevo --"
                    })

            col_conf1, col_conf2 = st.columns(2)
            with col_conf1:
                if st.button("✅ Confirmar y Registrar Entrada", type="primary", use_container_width=True):
                    items_para_registrar = [i for i in items_ia_validos if i["producto_id"] is not None]

                    if items_para_registrar:
                        try:
                            proveedor_id_ia = dict_proveedores_ia.get(prov_ia) if prov_ia != "-- Sin proveedor --" else None

                            with engine.begin() as conn:
                                is_sqlite = "sqlite" in str(engine.url)
                                if is_sqlite:
                                    cur = conn.execute(text("""
                                        INSERT INTO Entradas_Inventario
                                        (usuario_id, proveedor_id, numero_factura, total_compra, notas)
                                        VALUES (:uid, :pid, :nf, :total, :notas)
                                    """), {"uid": user_id, "pid": proveedor_id_ia,
                                           "nf": nf_ia or None, "total": float(total_ia),
                                           "notas": notas_ia or "Registrado con IA (Gemini)"})
                                    entrada_id = cur.lastrowid
                                else:
                                    res = conn.execute(text("""
                                        INSERT INTO Entradas_Inventario
                                        (usuario_id, proveedor_id, numero_factura, total_compra, notas)
                                        VALUES (:uid, :pid, :nf, :total, :notas) RETURNING id
                                    """), {"uid": user_id, "pid": proveedor_id_ia,
                                           "nf": nf_ia or None, "total": float(total_ia),
                                           "notas": notas_ia or "Registrado con IA (Gemini)"})
                                    entrada_id = res.scalar()

                                for item in items_para_registrar:
                                    conn.execute(text("""
                                        INSERT INTO Detalles_Entrada
                                        (entrada_id, producto_id, cantidad, costo_unitario, subtotal)
                                        VALUES (:eid, :pid, :cant, :costo, :sub)
                                    """), {"eid": entrada_id, "pid": item["producto_id"],
                                           "cant": item["cantidad"],
                                           "costo": item["costo_unitario"],
                                           "sub": item["subtotal"]})
                                    conn.execute(text("""
                                        UPDATE Productos
                                        SET stock_actual = stock_actual + :cant,
                                            costo_compra = :costo
                                        WHERE id = :pid AND usuario_id = :uid
                                    """), {"cant": item["cantidad"],
                                           "costo": item["costo_unitario"],
                                           "pid": item["producto_id"],
                                           "uid": user_id})

                            invalidar_cache_productos()
                            del st.session_state.factura_ia_productos
                            del st.session_state.factura_ia_datos
                            st.success(f"✅ Entrada #{entrada_id} registrada. {len(items_para_registrar)} producto(s) actualizados.")
                            nuevos = [i for i in items_ia_validos if i["crear_nuevo"]]
                            if nuevos:
                                st.warning(f"⚠️ {len(nuevos)} producto(s) marcados como 'Crear nuevo' no se registraron. Agrégalos en 'Agregar Producto'.")
                            st.rerun()
                        except Exception as e:
                            st.error(f"Error al registrar: {e}")
                    else:
                        st.warning("Asocia al menos un producto del inventario para registrar la entrada.")

            with col_conf2:
                if st.button("❌ Cancelar", use_container_width=True):
                    if "factura_ia_productos" in st.session_state:
                        del st.session_state.factura_ia_productos
                    if "factura_ia_datos" in st.session_state:
                        del st.session_state.factura_ia_datos
                    st.rerun()

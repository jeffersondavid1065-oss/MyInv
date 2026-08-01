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

UNIDADES = ["Unidad", "kg", "g", "lb", "m", "cm", "vara", "pie",
            "L", "mL", "galón", "Docena", "Caja", "Bulto", "Rollo",
            "Paquete", "m²", "m³"]

UNIDADES_DECIMALES = {"kg", "g", "lb", "m", "cm", "vara", "pie",
                      "L", "mL", "galón", "m²", "m³"}

def formato_cop(numero):
    return f"${float(numero):,.0f}".replace(",", ".")

def formato_cant(numero, unidad="Unidad"):
    if unidad in UNIDADES_DECIMALES:
        s = f"{float(numero):.3f}".rstrip('0').rstrip('.')
        return f"{s} {unidad}"
    return f"{int(float(numero))} {unidad}"

st.title("Inventario y Almacén")
st.markdown(f"Control de stock para: **{nombre_negocio}**")
st.markdown("---")

tab_stock, tab_nuevo, tab_entradas = st.tabs([
    "Stock Actual",
    "Agregar Producto",
    "Entradas de Mercancía 🤖"
])

# ==========================================
# TAB 1: STOCK ACTUAL
# ==========================================
with tab_stock:
    metricas = obtener_metricas_inventario(user_id)
    if metricas:
        col1, col2, col3, col4, col5, col6 = st.columns(6)
        col1.metric("Total Productos", int(metricas[2]))
        col2.metric("Inversión (Costo)", formato_cop(metricas[0]))
        col3.metric("Valor Comercial", formato_cop(metricas[1]))
        ganancia_inv = float(metricas[1]) - float(metricas[0])
        col4.metric("Ganancia Potencial", formato_cop(ganancia_inv))
        col5.metric("Agotados", int(metricas[3]),
                    delta="⚠️" if int(metricas[3]) > 0 else None, delta_color="inverse")
        col6.metric("Por Agotarse", int(metricas[4]),
                    delta="⚠️" if int(metricas[4]) > 0 else None, delta_color="inverse")

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
            st.caption("💡 Puedes editar **código de barras** y **unidad de medida** directamente aquí.")

            df_show = df_inv.copy()
            df_show['codigo_barras'] = df_show['codigo_barras'].fillna("").astype(str).replace("None", "")
            df_show['codigo_ref'] = df_show['codigo_ref'].fillna("").astype(str).replace("None", "")
            df_show['categoria'] = df_show['categoria'].fillna("General")

            if 'unidad_medida' not in df_show.columns:
                df_show['unidad_medida'] = 'Unidad'
            else:
                df_show['unidad_medida'] = df_show['unidad_medida'].fillna("Unidad")

            # Calcular ganancia
            df_show['ganancia'] = df_show['precio_venta'] - df_show['costo_compra']
            df_show['pct_ganancia'] = df_show.apply(
                lambda r: round((r['ganancia'] / r['costo_compra']) * 100, 1)
                if r['costo_compra'] > 0 else 0.0, axis=1
            )

            cols_mostrar = ['id', 'nombre', 'codigo_barras', 'codigo_ref',
                            'categoria', 'unidad_medida', 'stock_actual',
                            'stock_minimo', 'costo_compra', 'precio_venta',
                            'ganancia', 'pct_ganancia']
            cols_disponibles = [c for c in cols_mostrar if c in df_show.columns]

            df_edit = st.data_editor(
                df_show[cols_disponibles],
                hide_index=True,
                use_container_width=True,
                disabled=["id", "ganancia", "pct_ganancia"],
                column_config={
                    "id": None,
                    "nombre": "Producto",
                    "codigo_barras": st.column_config.TextColumn("Código Barras"),
                    "codigo_ref": "Referencia",
                    "categoria": "Categoría",
                    "unidad_medida": st.column_config.SelectboxColumn(
                        "Unidad", options=UNIDADES,
                        help="Ej: kg para granel, m para cable, Unidad para repuestos"
                    ),
                    "stock_actual": st.column_config.NumberColumn(
                        "Stock", min_value=0, step=0.001, format="%.3f"
                    ),
                    "stock_minimo": st.column_config.NumberColumn(
                        "Stock Mín.", min_value=0, step=0.001, format="%.3f"
                    ),
                    "costo_compra": st.column_config.NumberColumn("Costo ($)", format="$%d"),
                    "precio_venta": st.column_config.NumberColumn("Precio Venta ($)", format="$%d"),
                    "ganancia": st.column_config.NumberColumn("Ganancia ($)", format="$%d"),
                    "pct_ganancia": st.column_config.NumberColumn("% Ganancia", format="%.1f%%"),
                },
                key=f"editor_inv_{busqueda_inv}_{filtro_estado}"
            )

            if st.button("💾 Guardar Cambios", type="primary"):
                try:
                    with engine.begin() as conn:
                        for _, row in df_edit.iterrows():
                            um = row.get('unidad_medida', 'Unidad') if 'unidad_medida' in row else 'Unidad'
                            conn.execute(text("""
                                UPDATE Productos
                                SET nombre = :nom, codigo_barras = :cod,
                                    codigo_ref = :ref, categoria = :cat,
                                    unidad_medida = :um,
                                    stock_actual = :st_act, stock_minimo = :st_min,
                                    costo_compra = :costo, precio_venta = :pvp
                                WHERE id = :id AND usuario_id = :uid
                            """), {
                                "nom": row['nombre'],
                                "cod": row['codigo_barras'] or None,
                                "ref": row['codigo_ref'] or None,
                                "cat": row['categoria'],
                                "um": um,
                                "st_act": float(row['stock_actual']),
                                "st_min": float(row['stock_minimo']),
                                "costo": float(row['costo_compra']),
                                "pvp": float(row['precio_venta']),
                                # ganancia y pct_ganancia son calculadas, NO se guardan
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
        cod_barras = st.text_input("Código de Barras",
                                    placeholder="Escanea con el lector o escribe manualmente")
        cod_ref = st.text_input("Referencia interna (opcional)")
        categoria_p = st.text_input("Categoría", value="General")

        unidad_p = st.selectbox("Unidad de Medida", options=UNIDADES,
                                 help="Cómo se mide/vende este producto")
        hints = {
            "kg": "Ej: puntillas, tornillos a granel, cemento",
            "m": "Ej: cable eléctrico, tubería, manguera",
            "L": "Ej: pintura, aceite, solvente",
            "vara": "Ej: varilla de construcción",
            "Bulto": "Ej: cemento, harina",
        }
        if unidad_p in hints:
            st.caption(f"💡 {hints[unidad_p]}")

    with col_n2:
        es_decimal = unidad_p in UNIDADES_DECIMALES
        stock_inicial = st.number_input(
            f"Stock Inicial ({unidad_p})",
            min_value=0.0 if es_decimal else 0,
            value=1.0 if es_decimal else 1,
            step=0.5 if es_decimal else 1
        )
        stock_min = st.number_input(
            f"Stock Mínimo ({unidad_p})",
            min_value=0.0 if es_decimal else 0,
            value=0.5 if es_decimal else 2,
            step=0.5 if es_decimal else 1
        )

        st.markdown(f"**💰 Precio de Venta (por {unidad_p})**")
        costo_p = st.number_input(f"Costo de Compra ($ por {unidad_p}) *",
                                   min_value=0.0, step=1000.0, key="costo_nuevo")
        modo_precio = st.radio("Calcular precio por:",
                                ["Porcentaje de ganancia", "Precio fijo"], horizontal=True)

        if modo_precio == "Porcentaje de ganancia":
            porcentaje = st.slider("% de ganancia", min_value=0, max_value=300, value=30, step=1)
            if costo_p > 0:
                precio_calculado = costo_p * (1 + porcentaje / 100)
                ganancia_pesos = precio_calculado - costo_p
                with st.container(border=True):
                    st.markdown(f"**Costo:** {formato_cop(costo_p)}")
                    st.markdown(f"**Ganancia ({porcentaje}%):** {formato_cop(ganancia_pesos)}")
                    st.markdown(f"### Precio de Venta: {formato_cop(precio_calculado)}")
                precio_p = precio_calculado
                ajuste = st.number_input("Ajuste fino ($)",
                                          min_value=-precio_calculado, value=0.0, step=100.0)
                precio_p = max(0, precio_calculado + ajuste)
                if ajuste != 0:
                    pct_real = ((precio_p - costo_p) / costo_p * 100) if costo_p > 0 else 0
                    st.caption(f"Precio ajustado: {formato_cop(precio_p)} ({pct_real:.1f}%)")
            else:
                st.info("Ingresa el costo para calcular el precio.")
                precio_p = 0.0
        else:
            precio_p = st.number_input(f"Precio de Venta ($ por {unidad_p}) *",
                                        min_value=0.0, step=1000.0)
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
                         categoria, unidad_medida, stock_actual, stock_minimo,
                         costo_compra, precio_venta)
                        VALUES (:uid, :nom, :desc, :cod, :ref, :cat, :um, :stk, :stk_min, :costo, :pvp)
                    """), {
                        "uid": user_id, "nom": nom_p, "desc": desc_p or None,
                        "cod": cod_barras or None, "ref": cod_ref or None,
                        "cat": categoria_p, "um": unidad_p,
                        "stk": float(stock_inicial), "stk_min": float(stock_min),
                        "costo": float(costo_p), "pvp": float(precio_p)
                    })
                invalidar_cache_productos()
                st.success(f"✅ '{nom_p}' registrado — {formato_cant(stock_inicial, unidad_p)} en stock a {formato_cop(precio_p)}/{unidad_p}.")
                st.rerun()
            except Exception as e:
                st.error(f"Error al guardar: {e}")
        else:
            st.warning("El nombre y el precio de venta son obligatorios.")

# ==========================================
# TAB 3: ENTRADAS DE MERCANCÍA CON IA
# ==========================================
with tab_entradas:
    st.subheader("Registrar Entrada de Mercancía")
    st.caption("Sube la factura para que la IA detecte los productos, o agrégalos manualmente.")

    proveedores = obtener_proveedores(user_id)
    dict_proveedores = {p[1]: p[0] for p in proveedores} if proveedores else {}

    col_cab1, col_cab2 = st.columns(2)
    with col_cab1:
        factura_img = st.file_uploader(
            "📸 Foto o PDF de la factura",
            type=["jpg", "jpeg", "png", "pdf"],
            help="Sube la factura y la IA detecta los productos automáticamente."
        )
        if factura_img:
            if factura_img.type != "application/pdf":
                st.image(factura_img, use_container_width=True)
            else:
                st.success(f"📄 {factura_img.name}")

        if factura_img:
            if st.button("🤖 Analizar con IA", type="primary",
                         use_container_width=True, key="btn_analizar_ia"):
                with st.spinner("Gemini está leyendo la factura..."):
                    try:
                        from gemini_utils import leer_factura_imagen, leer_factura_pdf
                        archivo_bytes = factura_img.read()
                        datos = leer_factura_pdf(archivo_bytes) if factura_img.type == "application/pdf" \
                                else leer_factura_imagen(archivo_bytes)
                        if datos and "productos" in datos and datos["productos"]:
                            st.session_state.items_entrada = [
                                {
                                    "nombre": p.get("nombre", ""),
                                    "cantidad": float(p.get("cantidad", 1)),
                                    "costo": float(p.get("costo_unitario", 0)),
                                    "subtotal": float(p.get("subtotal", 0)),
                                    "unidad_medida": "Unidad",
                                    "ia": True
                                }
                                for p in datos["productos"]
                            ]
                            if datos.get("numero_factura"):
                                st.session_state.num_factura_ia = datos["numero_factura"]
                            st.success(f"✅ IA detectó **{len(datos['productos'])} producto(s)**. Revisa abajo.")
                            st.rerun()
                        else:
                            st.error("No se detectaron productos. Intenta con imagen más clara.")
                    except Exception as e:
                        st.error(f"Error: {e}")

    with col_cab2:
        if dict_proveedores:
            prov_sel = st.selectbox("Proveedor", ["-- Sin proveedor --"] + list(dict_proveedores.keys()),
                                     key="prov_entrada")
        else:
            prov_sel = "-- Sin proveedor --"
            st.caption("Sin proveedores registrados.")

        num_factura = st.text_input("Número de Factura",
                                     value=st.session_state.get("num_factura_ia", ""))
        notas_entrada = st.text_area("Notas", height=68)

    st.markdown("---")

    if "items_entrada" not in st.session_state:
        st.session_state.items_entrada = []

    col_btn1, col_btn2 = st.columns(2)
    with col_btn1:
        if st.button("➕ Agregar producto manualmente",
                     use_container_width=True, key="btn_agregar_manual"):
            st.session_state.items_entrada.append({
                "nombre": "", "cantidad": 1.0, "costo": 0.0,
                "subtotal": 0.0, "unidad_medida": "Unidad", "ia": False
            })
            st.rerun()
    with col_btn2:
        if st.button("🗑️ Limpiar todo", use_container_width=True, key="btn_limpiar_entrada"):
            st.session_state.items_entrada = []
            if "num_factura_ia" in st.session_state:
                del st.session_state.num_factura_ia
            st.rerun()

    if not st.session_state.items_entrada:
        st.info("Sube una factura para que la IA detecte los productos, o agrega uno manualmente.")
    else:
        df_inv_entrada = obtener_todos_productos(user_id)
        st.markdown(f"**{len(st.session_state.items_entrada)} producto(s) en esta entrada:**")

        items_a_eliminar = []
        total_entrada = 0.0

        for i, item in enumerate(st.session_state.items_entrada):
            with st.container(border=True):
                nombre_item = item.get("nombre", "")
                producto_match = None
                if nombre_item and not df_inv_entrada.empty:
                    matches = df_inv_entrada[
                        df_inv_entrada['nombre'].str.lower().str.contains(
                            nombre_item.lower()[:10], na=False
                        )
                    ]
                    if not matches.empty:
                        producto_match = matches.iloc[0]

                col_h1, col_h2 = st.columns([4, 1])
                with col_h1:
                    if producto_match is not None:
                        um_match = producto_match.get('unidad_medida', 'Unidad') \
                                   if 'unidad_medida' in producto_match else 'Unidad'
                        st.success(f"✅ Encontrado: **{producto_match['nombre']}** "
                                   f"(Stock: {formato_cant(producto_match['stock_actual'], um_match)})")
                    else:
                        st.warning("⚠️ Producto nuevo — se creará en el inventario")
                with col_h2:
                    if st.button("❌", key=f"del_item_{i}"):
                        items_a_eliminar.append(i)

                col_f1, col_f2, col_f3, col_f4 = st.columns([3, 1, 1, 1])
                with col_f1:
                    nombre_nuevo = st.text_input("Nombre", value=nombre_item, key=f"nom_{i}")
                    st.session_state.items_entrada[i]["nombre"] = nombre_nuevo
                with col_f2:
                    um_actual = item.get("unidad_medida", "Unidad")
                    if producto_match is not None and 'unidad_medida' in producto_match:
                        um_actual = producto_match['unidad_medida'] or "Unidad"
                    um_idx = UNIDADES.index(um_actual) if um_actual in UNIDADES else 0
                    um_sel = st.selectbox("Unidad", UNIDADES, index=um_idx, key=f"um_{i}")
                    st.session_state.items_entrada[i]["unidad_medida"] = um_sel
                with col_f3:
                    es_dec = um_sel in UNIDADES_DECIMALES
                    cant_nueva = st.number_input(
                        f"Cant. ({um_sel})",
                        min_value=0.0 if es_dec else 1,
                        value=float(item.get("cantidad", 1)),
                        step=0.5 if es_dec else 1,
                        key=f"cant_{i}"
                    )
                    st.session_state.items_entrada[i]["cantidad"] = cant_nueva
                with col_f4:
                    costo_nuevo = st.number_input(
                        f"Costo/$/{um_sel}", min_value=0.0,
                        value=float(item.get("costo", 0)),
                        step=1000.0, key=f"costo_{i}"
                    )
                    st.session_state.items_entrada[i]["costo"] = costo_nuevo
                    subtotal_i = cant_nueva * costo_nuevo
                    st.session_state.items_entrada[i]["subtotal"] = subtotal_i
                    st.caption(f"= {formato_cop(subtotal_i)}")

                if producto_match is not None:
                    col_e1, col_e2 = st.columns(2)
                    with col_e1:
                        pvp_actual = float(producto_match['precio_venta'])
                        st.caption(f"PVP actual: {formato_cop(pvp_actual)}/{um_sel}")
                        actualizar_precio = st.checkbox("Actualizar precio de venta", key=f"upd_precio_{i}")
                    with col_e2:
                        if actualizar_precio:
                            precio_nuevo = st.number_input("Nuevo PVP ($)", min_value=0.0,
                                                           value=pvp_actual, step=1000.0, key=f"pvp_{i}")
                            st.session_state.items_entrada[i]["precio_venta"] = precio_nuevo
                        else:
                            st.session_state.items_entrada[i]["precio_venta"] = pvp_actual
                    st.session_state.items_entrada[i]["producto_id"] = int(producto_match['id'])
                    st.session_state.items_entrada[i]["es_nuevo"] = False
                else:
                    col_n1, col_n2, col_n3 = st.columns(3)
                    with col_n1:
                        pvp_nuevo = st.number_input(
                            f"Precio venta ($/{um_sel}) *", min_value=0.0,
                            value=float(costo_nuevo * 1.3) if costo_nuevo > 0 else 0.0,
                            step=1000.0, key=f"pvp_nuevo_{i}"
                        )
                        st.session_state.items_entrada[i]["precio_venta"] = pvp_nuevo
                    with col_n2:
                        cod_barras = st.text_input("Código de barras",
                                                    placeholder="Escanea o escribe",
                                                    key=f"cod_{i}")
                        st.session_state.items_entrada[i]["codigo_barras"] = cod_barras
                    with col_n3:
                        categoria = st.text_input("Categoría", value="General", key=f"cat_{i}")
                        st.session_state.items_entrada[i]["categoria"] = categoria
                    st.session_state.items_entrada[i]["es_nuevo"] = True
                    st.session_state.items_entrada[i]["producto_id"] = None

                total_entrada += subtotal_i

        for idx in sorted(items_a_eliminar, reverse=True):
            st.session_state.items_entrada.pop(idx)
        if items_a_eliminar:
            st.rerun()

        if total_entrada > 0:
            st.markdown("---")
            st.info(f"**Total de la entrada: {formato_cop(total_entrada)}**")

        if st.session_state.items_entrada and st.button(
            "✅ Registrar Entrada", type="primary",
            use_container_width=True, key="btn_registrar_entrada"
        ):
            items_validos = [i for i in st.session_state.items_entrada
                             if i.get("nombre") and i.get("cantidad", 0) > 0]
            if not items_validos:
                st.warning("Agrega al menos un producto válido.")
            else:
                try:
                    proveedor_id = dict_proveedores.get(prov_sel) if prov_sel != "-- Sin proveedor --" else None

                    with engine.begin() as conn:
                        is_sqlite = "sqlite" in str(engine.url)

                        if is_sqlite:
                            cur = conn.execute(text("""
                                INSERT INTO Entradas_Inventario
                                (usuario_id, proveedor_id, numero_factura, total_compra, notas)
                                VALUES (:uid, :pid, :nf, :total, :notas)
                            """), {"uid": user_id, "pid": proveedor_id,
                                   "nf": num_factura or None, "total": float(total_entrada),
                                   "notas": notas_entrada or None})
                            entrada_id = cur.lastrowid
                        else:
                            res = conn.execute(text("""
                                INSERT INTO Entradas_Inventario
                                (usuario_id, proveedor_id, numero_factura, total_compra, notas)
                                VALUES (:uid, :pid, :nf, :total, :notas) RETURNING id
                            """), {"uid": user_id, "pid": proveedor_id,
                                   "nf": num_factura or None, "total": float(total_entrada),
                                   "notas": notas_entrada or None})
                            entrada_id = res.scalar()

                        nuevos = actualizados = 0

                        for item in items_validos:
                            pid = item.get("producto_id")
                            pvp = float(item.get("precio_venta", 0))
                            costo = float(item.get("costo", 0))
                            cantidad = float(item.get("cantidad", 1))
                            um = item.get("unidad_medida", "Unidad")
                            cod = item.get("codigo_barras") or None
                            cat = item.get("categoria", "General")

                            if item.get("es_nuevo") or not pid:
                                pvp_sug = costo * 1.30 if costo > 0 else 1000
                                pvp_final = pvp if pvp > 0 else pvp_sug
                                if is_sqlite:
                                    cur_p = conn.execute(text("""
                                        INSERT INTO Productos
                                        (usuario_id, nombre, codigo_barras, categoria, unidad_medida,
                                         stock_actual, stock_minimo, costo_compra, precio_venta)
                                        VALUES (:uid, :nom, :cod, :cat, :um, :stk, 2, :costo, :pvp)
                                    """), {"uid": user_id, "nom": item["nombre"],
                                           "cod": cod, "cat": cat, "um": um,
                                           "stk": cantidad, "costo": costo, "pvp": pvp_final})
                                    pid = cur_p.lastrowid
                                else:
                                    res_p = conn.execute(text("""
                                        INSERT INTO Productos
                                        (usuario_id, nombre, codigo_barras, categoria, unidad_medida,
                                         stock_actual, stock_minimo, costo_compra, precio_venta)
                                        VALUES (:uid, :nom, :cod, :cat, :um, :stk, 2, :costo, :pvp)
                                        RETURNING id
                                    """), {"uid": user_id, "nom": item["nombre"],
                                           "cod": cod, "cat": cat, "um": um,
                                           "stk": cantidad, "costo": costo, "pvp": pvp_final})
                                    pid = res_p.scalar()
                                nuevos += 1
                            else:
                                conn.execute(text("""
                                    UPDATE Productos
                                    SET stock_actual = stock_actual + :cant,
                                        costo_compra = :costo, precio_venta = :pvp,
                                        unidad_medida = :um
                                    WHERE id = :pid AND usuario_id = :uid
                                """), {"cant": cantidad, "costo": costo, "pvp": pvp,
                                       "um": um, "pid": pid, "uid": user_id})
                                actualizados += 1

                            conn.execute(text("""
                                INSERT INTO Detalles_Entrada
                                (entrada_id, producto_id, cantidad, costo_unitario, subtotal)
                                VALUES (:eid, :pid, :cant, :costo, :sub)
                            """), {"eid": entrada_id, "pid": pid,
                                   "cant": cantidad, "costo": costo,
                                   "sub": float(item.get("subtotal", 0))})

                    invalidar_cache_productos()
                    st.session_state.items_entrada = []
                    if "num_factura_ia" in st.session_state:
                        del st.session_state.num_factura_ia

                    st.success(f"✅ Entrada #{entrada_id}: **{nuevos}** nuevo(s), **{actualizados}** actualizado(s).")
                    if nuevos > 0:
                        st.info("💡 Productos nuevos creados con 30% de ganancia sugerida. Ajusta precios en Stock Actual.")
                    st.rerun()
                except Exception as e:
                    st.error(f"Error al registrar: {e}")

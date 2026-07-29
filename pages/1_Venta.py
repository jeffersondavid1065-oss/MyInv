import streamlit as st
import pandas as pd
from datetime import datetime, date
from sqlalchemy import text
from db import obtener_conexion
from queries import (
    obtener_productos_activos,
    buscar_producto_por_codigo,
    obtener_clientes,
    invalidar_cache_ventas,
    invalidar_cache_creditos,
    invalidar_cache_productos,
)
from utils import aplicar_estilos, verificar_auth

st.set_page_config(page_title="Punto de Venta", layout="wide")
aplicar_estilos()
user_id, nombre_negocio = verificar_auth()

engine = obtener_conexion()

def formato_cop(numero):
    return f"${float(numero):,.0f}".replace(",", ".")

st.title("Punto de Venta")
st.markdown(f"Caja para: **{nombre_negocio}**")
st.markdown("---")

# ==========================================
# IDENTIFICACIÓN DEL CAJERO
# ==========================================
if "cajero_activo_id" not in st.session_state:
    st.session_state.cajero_activo_id = None
if "cajero_activo_nombre" not in st.session_state:
    st.session_state.cajero_activo_nombre = None

try:
    with engine.connect() as conn:
        cajeros = conn.execute(text("""
            SELECT id, nombre, pin FROM Cajeros
            WHERE usuario_id = :uid AND activo = 1
            ORDER BY nombre ASC
        """), {"uid": user_id}).fetchall()
except Exception:
    cajeros = []

if cajeros:
    if not st.session_state.cajero_activo_id:
        with st.container(border=True):
            st.subheader("👤 Identificación del Cajero")
            col_id1, col_id2, col_id3 = st.columns([2, 1, 1])
            with col_id1:
                dict_cajeros = {c[1]: (c[0], c[2]) for c in cajeros}
                cajero_sel_nombre = st.selectbox("Selecciona tu nombre", options=list(dict_cajeros.keys()))
            with col_id2:
                pin_input = st.text_input("Tu PIN", type="password", max_chars=4)
            with col_id3:
                st.write("")
                st.write("")
                if st.button("Entrar a Caja", type="primary", use_container_width=True):
                    cajero_id_sel, pin_guardado = dict_cajeros[cajero_sel_nombre]
                    import hashlib
                    if hashlib.sha256(pin_input.encode()).hexdigest() == pin_guardado:
                        st.session_state.cajero_activo_id = cajero_id_sel
                        st.session_state.cajero_activo_nombre = cajero_sel_nombre
                        st.rerun()
                    else:
                        st.error("PIN incorrecto.")
        st.stop()
    else:
        col_caj1, col_caj2 = st.columns([3, 1])
        with col_caj1:
            st.success(f"👤 Cajero activo: **{st.session_state.cajero_activo_nombre}**")
        with col_caj2:
            if st.button("Cambiar cajero"):
                st.session_state.cajero_activo_id = None
                st.session_state.cajero_activo_nombre = None
                st.rerun()
        st.markdown("")

cajero_id_actual = st.session_state.get("cajero_activo_id")
if "limpiar_buscador" not in st.session_state:
    st.session_state.limpiar_buscador = False
if "carrito" not in st.session_state:
    st.session_state.carrito = []
if "ultima_venta_id" not in st.session_state:
    st.session_state.ultima_venta_id = None

def agregar_al_carrito(producto_id, nombre, codigo_barras, precio, stock_actual, cantidad=1):
    for item in st.session_state.carrito:
        if item["producto_id"] == producto_id:
            nueva_cant = item["cantidad"] + cantidad
            if nueva_cant > stock_actual:
                st.warning(f"Solo hay {stock_actual} unidades de '{nombre}'.")
                return
            item["cantidad"] = nueva_cant
            item["subtotal"] = nueva_cant * item["precio_unitario"]
            return
    if stock_actual <= 3:
        st.warning(f"⚠️ Stock bajo: solo quedan **{stock_actual}** unidades de '{nombre}'.")
    st.session_state.carrito.append({
        "producto_id": producto_id,
        "nombre": nombre,
        "codigo_barras": codigo_barras,
        "precio_unitario": float(precio),
        "descuento_item": 0.0,
        "descuento_pct_item": 0.0,
        "cantidad": cantidad,
        "subtotal": float(precio) * cantidad,
        "stock_max": stock_actual,
    })

def limpiar_carrito():
    st.session_state.carrito = []

# ==========================================
# TABS
# ==========================================
tab_pos, tab_historial, tab_devolucion = st.tabs([
    "🛒 Nueva Venta",
    "📋 Historial del Día",
    "↩️ Devoluciones"
])

# ==========================================
# TAB 1: NUEVA VENTA
# ==========================================
with tab_pos:
    col_izq, col_der = st.columns([3, 2])

    with col_izq:
        st.subheader("🔍 Buscar Producto")
        st.caption("Escanea el código de barras o escribe el nombre.")

        # Limpiar buscador si viene de un escaneo exitoso
        if st.session_state.get("limpiar_buscador", False):
            st.session_state.limpiar_buscador = False
            st.session_state.buscador_pos = ""

        busqueda = st.text_input(
            "Código o nombre", placeholder="Escanea o escribe aquí...",
            key="buscador_pos", label_visibility="collapsed"
        )

        if busqueda:
            busqueda = busqueda.strip()
            producto_encontrado = buscar_producto_por_codigo(user_id, busqueda)

            if producto_encontrado:
                agregar_al_carrito(
                    producto_id=producto_encontrado[0],
                    nombre=producto_encontrado[1],
                    codigo_barras=producto_encontrado[2],
                    precio=producto_encontrado[4],
                    stock_actual=int(producto_encontrado[3]),
                )
                # Limpiar campo automáticamente para siguiente escaneo
                st.session_state.limpiar_buscador = True
                st.rerun()
            else:
                df_productos = obtener_productos_activos(user_id)
                if not df_productos.empty:
                    df_filtrado = df_productos[
                        df_productos['nombre'].str.contains(busqueda, case=False, na=False)
                    ]
                    if not df_filtrado.empty:
                        st.markdown(f"**{len(df_filtrado)} resultado(s):**")
                        for _, prod in df_filtrado.iterrows():
                            c1, c2, c3 = st.columns([3, 1, 1])
                            with c1:
                                st.write(f"**{prod['nombre']}**")
                                if prod['codigo_barras']:
                                    st.caption(f"Cód: {prod['codigo_barras']}")
                            with c2:
                                st.write(formato_cop(prod['precio_venta']))
                                color = "🔴" if prod['stock_actual'] <= 0 else "🟡" if prod['stock_actual'] <= prod['stock_minimo'] else "🟢"
                                st.caption(f"{color} {prod['stock_actual']} uds.")
                            with c3:
                                if st.button("Agregar", key=f"add_{prod['id']}"):
                                    agregar_al_carrito(
                                        producto_id=int(prod['id']),
                                        nombre=prod['nombre'],
                                        codigo_barras=prod['codigo_barras'] or "",
                                        precio=float(prod['precio_venta']),
                                        stock_actual=int(prod['stock_actual']),
                                    )
                                    st.rerun()
                    else:
                        st.warning(f"No se encontró '{busqueda}'.")

        st.markdown("---")
        st.subheader("🛒 Carrito")

        if not st.session_state.carrito:
            st.info("El carrito está vacío.")
        else:
            # ==========================================
            # TIPO DE DESCUENTO POR ÍTEM
            # ==========================================
            tipo_desc_item = st.radio(
                "Descuento por ítem:",
                ["$ Valor fijo", "% Porcentaje"],
                horizontal=True,
                key="tipo_desc_item"
            )

            # Headers carrito
            h1, h2, h3, h4, h5, h6 = st.columns([3, 1, 1, 1, 1, 1])
            h1.markdown("**Producto**")
            h2.markdown("**Precio**")
            h3.markdown("**Cant.**")
            if tipo_desc_item == "$ Valor fijo":
                h4.markdown("**Desc.$**")
            else:
                h4.markdown("**Desc.%**")
            h5.markdown("**Total**")
            h6.markdown("**❌**")

            items_a_eliminar = []
            total_carrito = 0

            for i, item in enumerate(st.session_state.carrito):
                c1, c2, c3, c4, c5, c6 = st.columns([3, 1, 1, 1, 1, 1])
                with c1:
                    st.write(item["nombre"])
                with c2:
                    st.write(formato_cop(item["precio_unitario"]))
                with c3:
                    nueva_cant = st.number_input(
                        "", min_value=1,
                        max_value=item.get("stock_max", 9999),
                        value=item["cantidad"], step=1,
                        key=f"cant_{i}", label_visibility="collapsed"
                    )
                    if nueva_cant != item["cantidad"]:
                        st.session_state.carrito[i]["cantidad"] = nueva_cant
                        desc = item.get("descuento_item", 0)
                        precio_neto = item["precio_unitario"] - desc
                        st.session_state.carrito[i]["subtotal"] = nueva_cant * max(0, precio_neto)
                        st.rerun()
                with c4:
                    if tipo_desc_item == "$ Valor fijo":
                        desc_item = st.number_input(
                            "", min_value=0.0,
                            value=float(item.get("descuento_item", 0)),
                            step=500.0, key=f"desc_{i}", label_visibility="collapsed"
                        )
                        if desc_item != item.get("descuento_item", 0):
                            st.session_state.carrito[i]["descuento_item"] = desc_item
                            st.session_state.carrito[i]["descuento_pct_item"] = 0.0
                            precio_neto = item["precio_unitario"] - desc_item
                            st.session_state.carrito[i]["subtotal"] = item["cantidad"] * max(0, precio_neto)
                            st.rerun()
                    else:
                        pct_item = st.number_input(
                            "", min_value=0.0, max_value=100.0,
                            value=float(item.get("descuento_pct_item", 0)),
                            step=5.0, key=f"pct_{i}", label_visibility="collapsed"
                        )
                        if pct_item != item.get("descuento_pct_item", 0):
                            desc_calculado = item["precio_unitario"] * (pct_item / 100)
                            st.session_state.carrito[i]["descuento_pct_item"] = pct_item
                            st.session_state.carrito[i]["descuento_item"] = desc_calculado
                            precio_neto = item["precio_unitario"] - desc_calculado
                            st.session_state.carrito[i]["subtotal"] = item["cantidad"] * max(0, precio_neto)
                            st.rerun()
                with c5:
                    st.write(formato_cop(item["subtotal"]))
                with c6:
                    if st.button("❌", key=f"del_{i}"):
                        items_a_eliminar.append(i)

                total_carrito += item["subtotal"]

            for idx in sorted(items_a_eliminar, reverse=True):
                st.session_state.carrito.pop(idx)
            if items_a_eliminar:
                st.rerun()

            st.markdown("---")

            # ==========================================
            # DESCUENTO GLOBAL (SOBRE EL TOTAL)
            # ==========================================
            col_desc_tipo, col_desc_val = st.columns([1, 2])
            with col_desc_tipo:
                tipo_desc_global = st.radio(
                    "Descuento global:",
                    ["$ Fijo", "% Porcentaje"],
                    horizontal=True,
                    key="tipo_desc_global"
                )
            with col_desc_val:
                if tipo_desc_global == "$ Fijo":
                    desc_global = st.number_input(
                        "Descuento sobre total ($)",
                        min_value=0.0, step=1000.0,
                        key="descuento_pos"
                    )
                else:
                    pct_global = st.number_input(
                        "Descuento sobre total (%)",
                        min_value=0.0, max_value=100.0,
                        step=5.0, key="descuento_pct_global"
                    )
                    desc_global = total_carrito * (pct_global / 100)
                    if pct_global > 0:
                        st.caption(f"= {formato_cop(desc_global)}")

            total_final = max(0, total_carrito - desc_global)

            col_t1, col_t2 = st.columns([2, 1])
            with col_t2:
                st.markdown(f"### Total: {formato_cop(total_final)}")
                if desc_global > 0:
                    st.caption(f"Ahorro: {formato_cop(desc_global)}")
            with col_t1:
                if st.button("🗑️ Limpiar carrito", use_container_width=True):
                    limpiar_carrito()
                    st.rerun()

    with col_der:
        st.subheader("💰 Cobrar")

        if not st.session_state.carrito:
            st.info("Agrega productos al carrito para cobrar.")
        else:
            total_carrito = sum(i["subtotal"] for i in st.session_state.carrito)
            desc_global = st.session_state.get("descuento_pos", 0)
            # Si el descuento es por porcentaje, recalcular
            pct_g = st.session_state.get("descuento_pct_global", 0)
            if st.session_state.get("tipo_desc_global") == "% Porcentaje" and pct_g > 0:
                desc_global = total_carrito * (pct_g / 100)
            total_final = max(0, total_carrito - desc_global)

            st.markdown(f"**Total: {formato_cop(total_final)}**")
            if desc_global > 0:
                st.caption(f"Descuento aplicado: {formato_cop(desc_global)}")
            st.markdown("---")

            tipo_pago = st.selectbox(
                "Tipo de Pago",
                ["Efectivo", "Transferencia", "Credito", "Mixto"],
                format_func=lambda x: "Crédito" if x == "Credito" else x,
                key="tipo_pago_sel"
            )

            monto_efectivo = 0
            monto_transferencia = 0
            cambio = 0
            cliente_id = None
            tipo_cuota = "Libre"
            valor_cuota = 0
            fecha_limite = date.today()

            if tipo_pago == "Efectivo":
                monto_efectivo_input = st.number_input(
                    "Monto recibido ($)", min_value=0.0,
                    value=float(total_final), step=1000.0
                )
                cambio = max(0, monto_efectivo_input - total_final)
                monto_efectivo = min(monto_efectivo_input, total_final)
                if cambio > 0:
                    st.success(f"💵 Cambio: {formato_cop(cambio)}")

            elif tipo_pago == "Transferencia":
                monto_transferencia = total_final
                st.info(f"Total a transferir: {formato_cop(total_final)}")

            elif tipo_pago == "Credito":
                clientes = obtener_clientes(user_id)
                if clientes:
                    busq_cli = st.text_input("Buscar cliente", placeholder="Nombre o teléfono...")
                    clientes_f = [c for c in clientes if busq_cli.lower() in c[1].lower()] if busq_cli else clientes
                    dict_clientes = {c[1]: c[0] for c in clientes_f}
                    if dict_clientes:
                        cliente_sel = st.selectbox("Cliente", options=list(dict_clientes.keys()))
                        cliente_id = dict_clientes[cliente_sel]
                    tipo_cuota = st.selectbox("Tipo de cuota", ["Libre", "Semanal", "Quincenal", "Mensual"])
                    if tipo_cuota != "Libre":
                        valor_cuota = st.number_input("Valor por cuota ($)", min_value=0.0, step=10000.0)
                    fecha_limite = st.date_input("Fecha límite", value=date.today())
                else:
                    st.warning("No tienes clientes registrados.")
                    tipo_pago = None

            elif tipo_pago == "Mixto":
                monto_efectivo = st.number_input(
                    "Monto Efectivo ($)", min_value=0.0,
                    step=1000.0, max_value=float(total_final)
                )
                monto_transferencia = total_final - monto_efectivo
                st.info(f"Transferencia: {formato_cop(monto_transferencia)}")

            st.markdown("---")

            if tipo_pago and st.button("✅ Confirmar Venta", type="primary", use_container_width=True):
                try:
                    with engine.begin() as conn:
                        is_sqlite = "sqlite" in str(engine.url)
                        estado_venta = "Credito" if tipo_pago == "Credito" else "Pagada"

                        if is_sqlite:
                            cur = conn.execute(text("""
                                INSERT INTO Ventas
                                (usuario_id, cliente_id, subtotal, descuento, total,
                                 tipo_pago, monto_efectivo, monto_transferencia, cambio, estado, cajero_id)
                                VALUES (:uid, :cid, :sub, :desc, :total,
                                        :tipo, :efec, :trans, :cambio, :est, :cajero)
                            """), {
                                "uid": user_id, "cid": cliente_id,
                                "sub": total_carrito, "desc": desc_global,
                                "total": total_final, "tipo": tipo_pago,
                                "efec": monto_efectivo, "trans": monto_transferencia,
                                "cambio": cambio, "est": estado_venta,
                                "cajero": cajero_id_actual
                            })
                            venta_id = cur.lastrowid
                        else:
                            res = conn.execute(text("""
                                INSERT INTO Ventas
                                (usuario_id, cliente_id, subtotal, descuento, total,
                                 tipo_pago, monto_efectivo, monto_transferencia, cambio, estado, cajero_id)
                                VALUES (:uid, :cid, :sub, :desc, :total,
                                        :tipo, :efec, :trans, :cambio, :est, :cajero)
                                RETURNING id
                            """), {
                                "uid": user_id, "cid": cliente_id,
                                "sub": total_carrito, "desc": desc_global,
                                "total": total_final, "tipo": tipo_pago,
                                "efec": monto_efectivo, "trans": monto_transferencia,
                                "cambio": cambio, "est": estado_venta,
                                "cajero": cajero_id_actual
                            })
                            venta_id = res.scalar()

                        for item in st.session_state.carrito:
                            precio_neto = item["precio_unitario"] - item.get("descuento_item", 0)
                            conn.execute(text("""
                                INSERT INTO Detalles_Venta
                                (venta_id, producto_id, nombre_producto, codigo_barras,
                                 cantidad, precio_unitario, subtotal)
                                VALUES (:vid, :pid, :nom, :cod, :cant, :pvp, :sub)
                            """), {
                                "vid": venta_id, "pid": item["producto_id"],
                                "nom": item["nombre"], "cod": item["codigo_barras"],
                                "cant": item["cantidad"], "pvp": max(0, precio_neto),
                                "sub": item["subtotal"]
                            })
                            resultado = conn.execute(text("""
                                UPDATE Productos SET stock_actual = stock_actual - :cant
                                WHERE id = :pid AND stock_actual >= :cant
                            """), {"cant": item["cantidad"], "pid": item["producto_id"]})
                            if resultado.rowcount == 0:
                                raise ValueError(f"Stock insuficiente para '{item['nombre']}'.")

                        if tipo_pago == "Credito" and cliente_id:
                            conn.execute(text("""
                                INSERT INTO Creditos
                                (usuario_id, venta_id, cliente_id, total, saldo_pendiente,
                                 fecha_inicio, fecha_limite, tipo_cuota, valor_cuota, estado)
                                VALUES (:uid, :vid, :cid, :total, :saldo,
                                        :f_ini, :f_lim, :tipo_c, :val_c, 'Activo')
                            """), {
                                "uid": user_id, "vid": venta_id, "cid": cliente_id,
                                "total": total_final, "saldo": total_final,
                                "f_ini": date.today().strftime('%Y-%m-%d'),
                                "f_lim": fecha_limite.strftime('%Y-%m-%d'),
                                "tipo_c": tipo_cuota, "val_c": valor_cuota,
                            })
                            invalidar_cache_creditos()

                    invalidar_cache_ventas()
                    invalidar_cache_productos()

                    st.session_state.ultima_venta_id = venta_id
                    st.session_state.ultima_venta_total = total_final
                    st.session_state.ultima_venta_cambio = cambio
                    st.session_state.ultima_venta_tipo = tipo_pago

                    limpiar_carrito()
                    st.success(f"✅ Venta #{venta_id} registrada.")
                    if cambio > 0:
                        st.info(f"💵 Cambio: {formato_cop(cambio)}")
                    st.rerun()

                except ValueError as ve:
                    st.error(str(ve))
                except Exception as e:
                    st.error(f"Error: {e}")

            # Última venta + ticket
            if st.session_state.ultima_venta_id:
                st.markdown("---")
                st.subheader("🧾 Última Venta")
                vid = st.session_state.ultima_venta_id

                with engine.connect() as conn:
                    detalles = pd.read_sql_query(text("""
                        SELECT nombre_producto, cantidad, precio_unitario, subtotal
                        FROM Detalles_Venta WHERE venta_id = :vid
                    """), con=conn, params={"vid": vid})
                    venta_info = conn.execute(text("""
                        SELECT subtotal, descuento, total, tipo_pago,
                               monto_efectivo, cambio, fecha
                        FROM Ventas WHERE id = :vid
                    """), {"vid": vid}).fetchone()

                st.write(f"**Venta #{vid}** — {st.session_state.ultima_venta_tipo}")
                if not detalles.empty:
                    st.dataframe(detalles.rename(columns={
                        "nombre_producto": "Producto", "cantidad": "Cant.",
                        "precio_unitario": "Precio", "subtotal": "Subtotal"
                    }), hide_index=True, use_container_width=True)
                st.success(f"**Total: {formato_cop(st.session_state.ultima_venta_total)}**")
                if st.session_state.ultima_venta_cambio > 0:
                    st.info(f"Cambio: {formato_cop(st.session_state.ultima_venta_cambio)}")

                try:
                    from pdf_utils import generar_ticket_venta
                    cfg = st.session_state.get("taller_config", {})
                    logo_path = cfg.get("logo_path")
                    if not logo_path:
                        with engine.connect() as conn_logo:
                            lr = conn_logo.execute(
                                text("SELECT logo_path, nit, telefono, direccion FROM Usuarios WHERE id = :uid"),
                                {"uid": user_id}
                            ).fetchone()
                            if lr:
                                logo_path = lr[0]
                                cfg = {"logo_path": lr[0], "nit": lr[1] or "", "telefono": lr[2] or "", "direccion": lr[3] or ""}

                    if venta_info:
                        pdf_bytes = generar_ticket_venta(
                            negocio_nombre=nombre_negocio,
                            negocio_nit=cfg.get("nit", ""),
                            negocio_telefono=cfg.get("telefono", ""),
                            negocio_direccion=cfg.get("direccion", ""),
                            negocio_logo_path=logo_path,
                            venta_id=vid,
                            fecha=venta_info[6],
                            cliente="",
                            tipo_pago=venta_info[3],
                            monto_efectivo=float(venta_info[4] or 0),
                            cambio=float(venta_info[5] or 0),
                            df_items=detalles,
                            subtotal=float(venta_info[0] or 0),
                            descuento=float(venta_info[1] or 0),
                            total=float(venta_info[2] or 0),
                        )
                        st.download_button(
                            label="🧾 Descargar Ticket PDF",
                            data=pdf_bytes,
                            file_name=f"Ticket_{vid}.pdf",
                            mime="application/pdf",
                            use_container_width=True
                        )
                except Exception as e:
                    st.caption(f"PDF no disponible: {e}")

                if st.button("🛒 Nueva Venta", use_container_width=True, type="primary"):
                    st.session_state.ultima_venta_id = None
                    st.rerun()

# ==========================================
# TAB 2: HISTORIAL DEL DÍA
# ==========================================
with tab_historial:
    st.subheader(f"Ventas de Hoy — {date.today().strftime('%d/%m/%Y')}")

    with engine.connect() as conn:
        df_hoy = pd.read_sql_query(text("""
            SELECT v.id, v.fecha, COALESCE(cl.nombre, 'Directa') as cliente,
                   v.total, v.tipo_pago, v.estado
            FROM Ventas v
            LEFT JOIN Clientes cl ON v.cliente_id = cl.id
            WHERE v.usuario_id = :uid
            AND DATE(v.fecha) = :hoy
            AND v.estado != 'Anulada'
            ORDER BY v.fecha DESC
        """), con=conn, params={"uid": user_id, "hoy": date.today().strftime('%Y-%m-%d')})

    if not df_hoy.empty:
        total_dia = df_hoy['total'].sum()
        cant_ventas = len(df_hoy)

        col_h1, col_h2, col_h3 = st.columns(3)
        col_h1.metric("Ventas del día", cant_ventas)
        col_h2.metric("Total del día", formato_cop(total_dia))
        col_h3.metric("Ticket promedio", formato_cop(total_dia / cant_ventas))

        st.markdown("---")
        st.dataframe(
            df_hoy.rename(columns={
                'id': 'N°', 'fecha': 'Hora', 'cliente': 'Cliente',
                'total': 'Total', 'tipo_pago': 'Pago', 'estado': 'Estado'
            }),
            use_container_width=True, hide_index=True
        )

        st.markdown("---")
        st.markdown("**🖨️ Reimprimir ticket:**")
        dict_ventas = {
            f"Venta #{r['id']} — {formato_cop(r['total'])} — {r['cliente']}": r['id']
            for _, r in df_hoy.iterrows()
        }
        venta_reimp = st.selectbox("Selecciona la venta", options=list(dict_ventas.keys()))
        venta_id_reimp = dict_ventas[venta_reimp]

        with engine.connect() as conn:
            detalles_reimp = pd.read_sql_query(text("""
                SELECT nombre_producto, cantidad, precio_unitario, subtotal
                FROM Detalles_Venta WHERE venta_id = :vid
            """), con=conn, params={"vid": venta_id_reimp})
            info_reimp = conn.execute(text("""
                SELECT subtotal, descuento, total, tipo_pago,
                       monto_efectivo, cambio, fecha
                FROM Ventas WHERE id = :vid
            """), {"vid": venta_id_reimp}).fetchone()

        if not detalles_reimp.empty:
            st.dataframe(detalles_reimp.rename(columns={
                "nombre_producto": "Producto", "cantidad": "Cant.",
                "precio_unitario": "Precio", "subtotal": "Subtotal"
            }), hide_index=True, use_container_width=True)

        try:
            from pdf_utils import generar_ticket_venta
            cfg = st.session_state.get("taller_config", {})
            logo_path = cfg.get("logo_path")
            if not logo_path:
                with engine.connect() as conn_logo:
                    lr = conn_logo.execute(
                        text("SELECT logo_path, nit, telefono, direccion FROM Usuarios WHERE id = :uid"),
                        {"uid": user_id}
                    ).fetchone()
                    if lr:
                        logo_path = lr[0]
                        cfg = {"logo_path": lr[0], "nit": lr[1] or "", "telefono": lr[2] or "", "direccion": lr[3] or ""}

            pdf_reimp = generar_ticket_venta(
                negocio_nombre=nombre_negocio,
                negocio_nit=cfg.get("nit", ""),
                negocio_telefono=cfg.get("telefono", ""),
                negocio_direccion=cfg.get("direccion", ""),
                negocio_logo_path=logo_path,
                venta_id=venta_id_reimp,
                fecha=info_reimp[6] if info_reimp else "",
                cliente="",
                tipo_pago=info_reimp[3] if info_reimp else "",
                monto_efectivo=float(info_reimp[4] or 0) if info_reimp else 0,
                cambio=float(info_reimp[5] or 0) if info_reimp else 0,
                df_items=detalles_reimp,
                subtotal=float(info_reimp[0] or 0) if info_reimp else 0,
                descuento=float(info_reimp[1] or 0) if info_reimp else 0,
                total=float(info_reimp[2] or 0) if info_reimp else 0,
            )
            st.download_button(
                label="🖨️ Reimprimir Ticket PDF",
                data=pdf_reimp,
                file_name=f"Ticket_{venta_id_reimp}.pdf",
                mime="application/pdf",
                use_container_width=True
            )
        except Exception as e:
            st.caption(f"PDF no disponible: {e}")
    else:
        st.info("No hay ventas registradas hoy todavía.")

# ==========================================
# TAB 3: DEVOLUCIONES
# ==========================================
with tab_devolucion:
    st.subheader("Devoluciones y Anulaciones")
    st.caption("Busca la venta por número para anularla. El stock se restaura automáticamente.")

    col_d1, col_d2 = st.columns(2)
    with col_d1:
        venta_num = st.text_input("Número de venta", placeholder="Ej: 25")
    with col_d2:
        motivo = st.text_input("Motivo (opcional)", placeholder="Ej: Producto defectuoso")

    if venta_num and venta_num.isdigit():
        vid_dev = int(venta_num)

        with engine.connect() as conn:
            venta_dev = conn.execute(text("""
                SELECT v.id, v.total, v.estado, v.tipo_pago,
                       DATE(v.fecha) as fecha,
                       COALESCE(cl.nombre, 'Venta directa') as cliente
                FROM Ventas v
                LEFT JOIN Clientes cl ON v.cliente_id = cl.id
                WHERE v.id = :vid AND v.usuario_id = :uid
            """), {"vid": vid_dev, "uid": user_id}).fetchone()

            if venta_dev:
                detalles_dev = pd.read_sql_query(text("""
                    SELECT id, producto_id, nombre_producto, cantidad,
                           precio_unitario, subtotal
                    FROM Detalles_Venta WHERE venta_id = :vid
                """), con=conn, params={"vid": vid_dev})

        if venta_dev:
            if venta_dev[2] == "Anulada":
                st.warning(f"La venta #{vid_dev} ya fue anulada.")
            else:
                with st.container(border=True):
                    c1, c2, c3 = st.columns(3)
                    c1.metric("Venta #", vid_dev)
                    c2.metric("Total", formato_cop(venta_dev[1]))
                    c3.metric("Estado", venta_dev[2])
                    st.write(f"**Cliente:** {venta_dev[5]} | **Fecha:** {venta_dev[4]} | **Pago:** {venta_dev[3]}")

                if not detalles_dev.empty:
                    st.dataframe(
                        detalles_dev[['nombre_producto', 'cantidad', 'precio_unitario', 'subtotal']].rename(columns={
                            'nombre_producto': 'Producto', 'cantidad': 'Cant.',
                            'precio_unitario': 'Precio', 'subtotal': 'Subtotal'
                        }),
                        hide_index=True, use_container_width=True
                    )

                st.markdown("---")
                col_b1, col_b2 = st.columns(2)

                with col_b1:
                    if st.button("🚫 Anular Venta Completa", use_container_width=True, type="primary"):
                        try:
                            with engine.begin() as conn:
                                conn.execute(text("""
                                    UPDATE Ventas SET estado = 'Anulada', notas = :notas
                                    WHERE id = :vid AND usuario_id = :uid
                                """), {
                                    "vid": vid_dev, "uid": user_id,
                                    "notas": f"Anulada: {motivo}" if motivo else "Anulada"
                                })
                                for _, item in detalles_dev.iterrows():
                                    if item['producto_id']:
                                        conn.execute(text("""
                                            UPDATE Productos
                                            SET stock_actual = stock_actual + :cant
                                            WHERE id = :pid AND usuario_id = :uid
                                        """), {
                                            "cant": int(item['cantidad']),
                                            "pid": int(item['producto_id']),
                                            "uid": user_id
                                        })
                                conn.execute(text("""
                                    UPDATE Creditos SET estado = 'Pagado'
                                    WHERE venta_id = :vid AND usuario_id = :uid
                                """), {"vid": vid_dev, "uid": user_id})

                            invalidar_cache_ventas()
                            invalidar_cache_productos()
                            st.success(f"✅ Venta #{vid_dev} anulada. Stock restaurado.")
                            st.rerun()
                        except Exception as e:
                            st.error(f"Error al anular: {e}")

                with col_b2:
                    st.info("💡 Al anular se restaura el stock y la venta queda marcada como anulada en los reportes.")
        else:
            st.warning(f"No se encontró la venta #{vid_dev}.")

import streamlit as st
import pandas as pd
from datetime import datetime, date, timedelta
from sqlalchemy import text
from db import obtener_conexion
from queries import (
    obtener_productos_activos,
    buscar_producto_por_codigo,
    obtener_clientes,
    invalidar_cache_ventas,
    invalidar_cache_creditos,
    invalidar_cache_productos,
    obtener_facturas_periodo,
    obtener_historial_devoluciones,
)
from utils import aplicar_estilos, verificar_auth
from tz_utils import hoy_bogota, ahora_bogota_naive
from alegra_utils import facturar_venta, anular_factura_venta, emitir_factura_dian_venta

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
            st.subheader("Identificación del Cajero")
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
            st.success(f"Cajero activo: **{st.session_state.cajero_activo_nombre}**")
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

def agregar_al_carrito(producto_id, nombre, codigo_barras, precio, stock_actual, costo=0, cantidad=1, iva_porcentaje=0):
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
        st.warning(f"Stock bajo: solo quedan **{stock_actual}** unidades de '{nombre}'.")
    st.session_state.carrito.append({
        "producto_id": producto_id,
        "nombre": nombre,
        "codigo_barras": codigo_barras,
        "precio_unitario": float(precio),
        "costo_unitario": float(costo or 0),
        "descuento_item": 0.0,
        "descuento_pct_item": 0.0,
        "cantidad": cantidad,
        "subtotal": float(precio) * cantidad,
        "stock_max": stock_actual,
        "iva_porcentaje": float(iva_porcentaje or 0),
    })

def limpiar_carrito():
    st.session_state.carrito = []

def calcular_desglose_iva(carrito, total_final, total_bruto):
    """
    A partir del carrito (precios ya con IVA incluido), calcula el subtotal
    sin IVA y el IVA total, prorrateando el descuento global proporcionalmente.
    """
    subtotal_sin_iva = 0.0
    iva_total = 0.0
    for item in carrito:
        cant = item["cantidad"]
        if cant <= 0:
            continue
        iva_pct = item.get("iva_porcentaje", 0) or 0
        precio_neto_unit = item["subtotal"] / cant
        base_unit = precio_neto_unit / (1 + iva_pct / 100)
        subtotal_sin_iva += base_unit * cant
        iva_total += (precio_neto_unit - base_unit) * cant

    factor = (total_final / total_bruto) if total_bruto else 1.0
    return subtotal_sin_iva * factor, iva_total * factor

def calcular_iva_desde_detalles(df_detalles, total_venta):
    """Misma idea que calcular_desglose_iva, pero a partir del DataFrame de Detalles_Venta ya guardado."""
    if df_detalles is None or df_detalles.empty or 'iva_porcentaje' not in df_detalles.columns:
        return 0.0, 0.0
    subtotal_sin_iva = 0.0
    iva_total = 0.0
    suma_lineas = 0.0
    for _, row in df_detalles.iterrows():
        linea = float(row.get('subtotal', 0) or 0)
        iva_pct = float(row.get('iva_porcentaje', 0) or 0)
        base = linea / (1 + iva_pct / 100)
        subtotal_sin_iva += base
        iva_total += (linea - base)
        suma_lineas += linea
    factor = (float(total_venta) / suma_lineas) if suma_lineas else 1.0
    return subtotal_sin_iva * factor, iva_total * factor

# ==========================================
# TABS
# ==========================================
tab_pos, tab_historial, tab_devolucion = st.tabs([
    "Nueva Venta",
    "Historial del Día",
    "Devoluciones"
])

# ==========================================
# TAB 1: NUEVA VENTA
# ==========================================
with tab_pos:
    col_izq, col_der = st.columns([3, 2])

    with col_izq:
        st.subheader("Buscar Producto")
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
                    costo=producto_encontrado[5],
                    iva_porcentaje=producto_encontrado[6] if len(producto_encontrado) > 6 else 0,
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
                                        costo=float(prod['costo_compra'] or 0),
                                        iva_porcentaje=float(prod.get('iva_porcentaje', 0) or 0),
                                    )
                                    st.rerun()
                    else:
                        st.warning(f"No se encontró '{busqueda}'.")

        st.markdown("---")
        st.subheader("Carrito")

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
            subtotal_sin_iva_cart, iva_total_cart = calcular_desglose_iva(
                st.session_state.carrito, total_final, total_carrito
            )

            col_t1, col_t2 = st.columns([2, 1])
            with col_t2:
                st.markdown(f"### Total: {formato_cop(total_final)}")
                if desc_global > 0:
                    st.caption(f"Ahorro: {formato_cop(desc_global)}")
                if iva_total_cart > 1:
                    st.caption(f"Subtotal (sin IVA): {formato_cop(subtotal_sin_iva_cart)} | IVA: {formato_cop(iva_total_cart)}")
            with col_t1:
                if st.button("Limpiar carrito", use_container_width=True):
                    limpiar_carrito()
                    st.rerun()

    with col_der:
        st.subheader("Cobrar")

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
            _, iva_total_cobrar = calcular_desglose_iva(st.session_state.carrito, total_final, total_carrito)

            st.markdown(f"**Total: {formato_cop(total_final)}**")
            if desc_global > 0:
                st.caption(f"Descuento aplicado: {formato_cop(desc_global)}")
            if iva_total_cobrar > 1:
                st.caption(f"Incluye IVA: {formato_cop(iva_total_cobrar)}")
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
            fecha_limite = hoy_bogota()

            if tipo_pago in ("Efectivo", "Transferencia", "Mixto"):
                clientes_fact = obtener_clientes(user_id)
                if clientes_fact:
                    dict_clientes_fact = {"Sin cliente (venta directa)": None}
                    for c in clientes_fact:
                        cid_c, nombre_c, tel_c = c[0], c[1], c[2]
                        etiqueta = f"{nombre_c} — {tel_c}" if tel_c else nombre_c
                        dict_clientes_fact[etiqueta] = cid_c
                    cliente_fact_sel = st.selectbox(
                        "Cliente (opcional, para poder facturar electrónicamente)",
                        options=list(dict_clientes_fact.keys()),
                        key="venta_cliente_opcional_sel",
                        help="Solo se puede emitir factura electrónica si la venta queda ligada a un cliente registrado con documento.",
                    )
                    cliente_id = dict_clientes_fact[cliente_fact_sel]

            if tipo_pago == "Efectivo":
                monto_efectivo_input = st.number_input(
                    "Monto recibido ($)", min_value=0.0,
                    value=float(total_final), step=1000.0
                )
                cambio = max(0, monto_efectivo_input - total_final)
                monto_efectivo = min(monto_efectivo_input, total_final)
                if cambio > 0:
                    st.success(f"Cambio: {formato_cop(cambio)}")

            elif tipo_pago == "Transferencia":
                monto_transferencia = total_final
                st.info(f"Total a transferir: {formato_cop(total_final)}")

            elif tipo_pago == "Credito":
                clientes = obtener_clientes(user_id)
                if clientes:
                    # IMPORTANTE: las opciones del selectbox deben ser una lista
                    # ESTABLE (no filtrada dinámicamente por texto de búsqueda).
                    # Antes, el campo de búsqueda cambiaba la lista de "options"
                    # en cada tecla, y como el selectbox no tenía un "key" fijo,
                    # Streamlit lo trataba como un widget nuevo y volvía a
                    # seleccionar el índice 0 (el primer cliente en orden
                    # alfabético) sin importar lo que el cajero hubiera elegido.
                    # Por eso todo crédito terminaba guardándose al mismo cliente.
                    # Se usa el buscador nativo del selectbox (escribir para
                    # filtrar) y se incluye el teléfono en la etiqueta para
                    # poder buscar también por teléfono.
                    dict_clientes = {}
                    for c in clientes:
                        cid_c, nombre_c, tel_c = c[0], c[1], c[2]
                        etiqueta = f"{nombre_c} — {tel_c}" if tel_c else nombre_c
                        dict_clientes[etiqueta] = cid_c

                    cliente_sel = st.selectbox(
                        "Cliente",
                        options=list(dict_clientes.keys()),
                        key="venta_credito_cliente_sel",
                        help="Escribe el nombre o teléfono para buscar en la lista.",
                    )
                    cliente_id = dict_clientes[cliente_sel]
                    cliente_nombre_sel = cliente_sel.split(" — ")[0]
                    st.caption(f"🧾 Este crédito quedará registrado a nombre de: **{cliente_nombre_sel}**")

                    tipo_cuota = st.selectbox(
                        "Tipo de cuota", ["Libre", "Semanal", "Quincenal", "Mensual"],
                        key="venta_credito_tipo_cuota"
                    )
                    if tipo_cuota != "Libre":
                        valor_cuota = st.number_input(
                            "Valor por cuota ($)", min_value=0.0, step=10000.0,
                            key="venta_credito_valor_cuota"
                        )
                    fecha_limite = st.date_input(
                        "Fecha límite", value=hoy_bogota(),
                        key="venta_credito_fecha_limite"
                    )
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

            col_venta1, col_venta2 = st.columns(2)
            confirmar_venta = tipo_pago and col_venta1.button(
                "Registrar Venta", type="primary", use_container_width=True
            )
            confirmar_venta_factura = tipo_pago and col_venta2.button(
                "Registrar Venta con Factura Electrónica", use_container_width=True
            )

            if confirmar_venta_factura and not cliente_id:
                st.warning(
                    "Selecciona un cliente registrado (con documento) en 'Cliente' "
                    "para poder facturar electrónicamente."
                )
            elif confirmar_venta or confirmar_venta_factura:
                try:
                    # Subtotal bruto (antes de cualquier descuento) y descuento total
                    # (suma de descuentos por ítem + descuento global), para que el
                    # descuento real quede reflejado en Ventas.descuento y no se
                    # pierda "escondido" dentro del subtotal.
                    subtotal_bruto = sum(
                        item["precio_unitario"] * item["cantidad"]
                        for item in st.session_state.carrito
                    )
                    descuento_items_total = sum(
                        item.get("descuento_item", 0) * item["cantidad"]
                        for item in st.session_state.carrito
                    )
                    descuento_total = descuento_items_total + desc_global

                    with engine.begin() as conn:
                        is_sqlite = "sqlite" in str(engine.url)
                        estado_venta = "Credito" if tipo_pago == "Credito" else "Pagada"

                        fecha_venta = ahora_bogota_naive()

                        if is_sqlite:
                            cur = conn.execute(text("""
                                INSERT INTO Ventas
                                (usuario_id, cliente_id, subtotal, descuento, total,
                                 tipo_pago, monto_efectivo, monto_transferencia, cambio, estado, cajero_id, fecha)
                                VALUES (:uid, :cid, :sub, :desc, :total,
                                        :tipo, :efec, :trans, :cambio, :est, :cajero, :fecha)
                            """), {
                                "uid": user_id, "cid": cliente_id,
                                "sub": subtotal_bruto, "desc": descuento_total,
                                "total": total_final, "tipo": tipo_pago,
                                "efec": monto_efectivo, "trans": monto_transferencia,
                                "cambio": cambio, "est": estado_venta,
                                "cajero": cajero_id_actual, "fecha": fecha_venta
                            })
                            venta_id = cur.lastrowid
                        else:
                            res = conn.execute(text("""
                                INSERT INTO Ventas
                                (usuario_id, cliente_id, subtotal, descuento, total,
                                 tipo_pago, monto_efectivo, monto_transferencia, cambio, estado, cajero_id, fecha)
                                VALUES (:uid, :cid, :sub, :desc, :total,
                                        :tipo, :efec, :trans, :cambio, :est, :cajero, :fecha)
                                RETURNING id
                            """), {
                                "uid": user_id, "cid": cliente_id,
                                "sub": subtotal_bruto, "desc": descuento_total,
                                "total": total_final, "tipo": tipo_pago,
                                "efec": monto_efectivo, "trans": monto_transferencia,
                                "cambio": cambio, "est": estado_venta,
                                "cajero": cajero_id_actual, "fecha": fecha_venta
                            })
                            venta_id = res.scalar()

                        for item in st.session_state.carrito:
                            descuento_linea = item.get("descuento_item", 0) * item["cantidad"]
                            conn.execute(text("""
                                INSERT INTO Detalles_Venta
                                (venta_id, producto_id, nombre_producto, codigo_barras,
                                 cantidad, precio_unitario, costo_unitario, descuento, subtotal, iva_porcentaje)
                                VALUES (:vid, :pid, :nom, :cod, :cant, :pvp, :costo, :desc, :sub, :iva)
                            """), {
                                "vid": venta_id, "pid": item["producto_id"],
                                "nom": item["nombre"], "cod": item["codigo_barras"],
                                "cant": item["cantidad"], "pvp": item["precio_unitario"],
                                "costo": item.get("costo_unitario", 0),
                                "desc": descuento_linea,
                                "sub": item["subtotal"],
                                "iva": item.get("iva_porcentaje", 0) or 0
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
                                "f_ini": hoy_bogota().strftime('%Y-%m-%d'),
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
                    st.success(f"Venta #{venta_id} registrada.")
                    if cambio > 0:
                        st.info(f"Cambio: {formato_cop(cambio)}")

                    if confirmar_venta_factura:
                        with st.spinner("Creando factura en Alegra..."):
                            ok_f, msg_f = facturar_venta(user_id, venta_id)
                        if ok_f:
                            st.success(msg_f)
                        else:
                            st.warning(msg_f)

                    st.rerun()

                except ValueError as ve:
                    st.error(str(ve))
                except Exception as e:
                    st.error(f"Error: {e}")

            # Última venta + ticket
            if st.session_state.ultima_venta_id:
                st.markdown("---")
                st.subheader("Última Venta")
                vid = st.session_state.ultima_venta_id

                with engine.connect() as conn:
                    detalles = pd.read_sql_query(text("""
                        SELECT nombre_producto, cantidad, precio_unitario, descuento, subtotal, iva_porcentaje
                        FROM Detalles_Venta WHERE venta_id = :vid
                    """), con=conn, params={"vid": vid})
                    venta_info = conn.execute(text("""
                        SELECT subtotal, descuento, total, tipo_pago,
                               monto_efectivo, cambio, fecha
                        FROM Ventas WHERE id = :vid
                    """), {"vid": vid}).fetchone()

                st.write(f"**Venta #{vid}** — {st.session_state.ultima_venta_tipo}")
                if not detalles.empty:
                    st.dataframe(detalles.drop(columns=["iva_porcentaje"]).rename(columns={
                        "nombre_producto": "Producto", "cantidad": "Cant.",
                        "precio_unitario": "Precio", "descuento": "Descuento", "subtotal": "Subtotal"
                    }), hide_index=True, use_container_width=True,
                    column_config={
                        "Precio": st.column_config.NumberColumn("Precio", format="$%,d"),
                        "Descuento": st.column_config.NumberColumn("Descuento", format="$%,d"),
                        "Subtotal": st.column_config.NumberColumn("Subtotal", format="$%,d"),
                    })
                if venta_info and float(venta_info[1] or 0) > 0:
                    st.caption(f"Descuento total aplicado: {formato_cop(venta_info[1])}")
                _, iva_ultima_venta = calcular_iva_desde_detalles(detalles, st.session_state.ultima_venta_total)
                if iva_ultima_venta > 1:
                    st.caption(f"IVA incluido: {formato_cop(iva_ultima_venta)}")
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
                            total_iva=iva_ultima_venta,
                        )
                        st.download_button(
                            label="Descargar Ticket PDF",
                            data=pdf_bytes,
                            file_name=f"Ticket_{vid}.pdf",
                            mime="application/pdf",
                            use_container_width=True
                        )
                except Exception as e:
                    st.caption(f"PDF no disponible: {e}")

                st.markdown("---")
                venta_factura = conn_factura = None
                with engine.connect() as conn_factura:
                    venta_factura = conn_factura.execute(text("""
                        SELECT cliente_id, factura_estado, factura_alegra_id, factura_pdf_url, factura_xml_url
                        FROM Ventas WHERE id = :vid
                    """), {"vid": vid}).fetchone()

                if venta_factura and venta_factura[1] == "emitida":
                    st.success(f"Factura electrónica emitida ante la DIAN (Alegra #{venta_factura[2]}).")
                    col_fpdf, col_fxml = st.columns(2)
                    if venta_factura[3]:
                        col_fpdf.link_button("Ver PDF de la factura", venta_factura[3], use_container_width=True)
                    if venta_factura[4]:
                        col_fxml.link_button("Descargar XML (DIAN)", venta_factura[4], use_container_width=True)
                elif venta_factura and venta_factura[1] == "abierta":
                    st.info(f"Factura creada en Alegra (#{venta_factura[2]}) — todavía no se ha emitido ante la DIAN.")
                    if venta_factura[3]:
                        st.link_button("Ver PDF (borrador)", venta_factura[3], use_container_width=True)
                    if st.button("Emitir a la DIAN", use_container_width=True, type="primary"):
                        with st.spinner("Emitiendo ante la DIAN..."):
                            ok_dian, msg_dian = emitir_factura_dian_venta(user_id, vid)
                        if ok_dian:
                            st.success(msg_dian)
                        else:
                            st.warning(msg_dian)
                        st.rerun()
                elif not venta_factura or not venta_factura[0]:
                    st.caption("Esta venta no tiene cliente asociado — no se puede facturar electrónicamente.")
                else:
                    if st.button("Emitir factura electrónica", use_container_width=True):
                        with st.spinner("Creando factura en Alegra..."):
                            ok, msg = facturar_venta(user_id, vid)
                        if ok:
                            st.success(msg)
                        else:
                            st.warning(msg)
                        st.rerun()

                if st.button("Nueva Venta", use_container_width=True, type="primary"):
                    st.session_state.ultima_venta_id = None
                    st.rerun()

# ==========================================
# TAB 2: HISTORIAL DEL DÍA
# ==========================================
with tab_historial:
    st.subheader(f"Ventas de Hoy — {hoy_bogota().strftime('%d/%m/%Y')}")

    df_hoy = obtener_facturas_periodo(user_id, hoy_bogota(), hoy_bogota())
    df_hoy = df_hoy.rename(columns={'estado_venta': 'estado'})

    if not df_hoy.empty:
        df_hoy_activas = df_hoy[df_hoy['estado'] != 'Anulada']
        total_dia = df_hoy_activas['total'].sum()
        cant_ventas = len(df_hoy_activas)

        col_h1, col_h2, col_h3 = st.columns(3)
        col_h1.metric("Ventas del día", cant_ventas)
        col_h2.metric("Total del día", formato_cop(total_dia))
        col_h3.metric("Ticket promedio", formato_cop(total_dia / cant_ventas if cant_ventas else 0))

        st.markdown("---")
        df_hoy = df_hoy.copy()
        df_hoy['numero_factura_texto'] = (
            df_hoy['factura_prefijo'].fillna('').astype(str) + df_hoy['factura_numero'].fillna('').astype(str)
        )
        df_hoy['fe_texto'] = df_hoy['factura_estado'].fillna('Sin facturar').replace({
            'emitida': 'Emitida', 'abierta': 'Abierta (sin timbrar)', 'error': 'Error', 'anulada': 'Anulada (N.C.)'
        })
        st.dataframe(
            df_hoy[['id', 'fecha', 'cliente', 'total', 'tipo_pago', 'estado', 'fe_texto', 'numero_factura_texto',
                    'factura_pdf_url', 'factura_xml_url']].rename(columns={
                'id': 'N°', 'fecha': 'Hora', 'cliente': 'Cliente',
                'total': 'Total', 'tipo_pago': 'Pago', 'estado': 'Estado',
                'fe_texto': 'Factura Electrónica', 'numero_factura_texto': 'N° Factura',
                'factura_pdf_url': 'Factura PDF', 'factura_xml_url': 'Factura XML',
            }),
            use_container_width=True, hide_index=True,
            column_config={
                "Total": st.column_config.NumberColumn("Total", format="$%,d"),
                "Factura PDF": st.column_config.LinkColumn(display_text="Abrir"),
                "Factura XML": st.column_config.LinkColumn(display_text="Abrir"),
            }
        )

        st.markdown("---")
        st.markdown("**Reimprimir ticket:**")
        dict_ventas = {
            f"Venta #{r['id']} — {formato_cop(r['total'])} — {r['cliente']}": r['id']
            for _, r in df_hoy.iterrows()
        }
        venta_reimp = st.selectbox("Selecciona la venta", options=list(dict_ventas.keys()))
        venta_id_reimp = dict_ventas[venta_reimp]

        with engine.connect() as conn:
            detalles_reimp = pd.read_sql_query(text("""
                SELECT nombre_producto, cantidad, precio_unitario, descuento, subtotal, iva_porcentaje
                FROM Detalles_Venta WHERE venta_id = :vid
            """), con=conn, params={"vid": venta_id_reimp})
            info_reimp = conn.execute(text("""
                SELECT subtotal, descuento, total, tipo_pago,
                       monto_efectivo, cambio, fecha
                FROM Ventas WHERE id = :vid
            """), {"vid": venta_id_reimp}).fetchone()

        if not detalles_reimp.empty:
            st.dataframe(detalles_reimp.drop(columns=["iva_porcentaje"]).rename(columns={
                "nombre_producto": "Producto", "cantidad": "Cant.",
                "precio_unitario": "Precio", "descuento": "Descuento", "subtotal": "Subtotal"
            }), hide_index=True, use_container_width=True,
            column_config={
                "Precio": st.column_config.NumberColumn("Precio", format="$%,d"),
                "Descuento": st.column_config.NumberColumn("Descuento", format="$%,d"),
                "Subtotal": st.column_config.NumberColumn("Subtotal", format="$%,d"),
            })
        if info_reimp and float(info_reimp[1] or 0) > 0:
            st.caption(f"Descuento total aplicado: {formato_cop(info_reimp[1])}")
        _, iva_reimp = calcular_iva_desde_detalles(detalles_reimp, float(info_reimp[2]) if info_reimp else 0)
        if iva_reimp > 1:
            st.caption(f"IVA incluido: {formato_cop(iva_reimp)}")

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
                total_iva=iva_reimp,
            )
            st.download_button(
                label="Reimprimir Ticket PDF",
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

    # ==========================================
    # HISTORIAL DE DEVOLUCIONES (NOTAS CRÉDITO)
    # ==========================================
    st.markdown("**Historial de devoluciones:**")
    hoy_hist_dev = hoy_bogota()
    hace_30_hist_dev = hoy_hist_dev - timedelta(days=30)
    fechas_hist_dev = st.date_input("Período", [hace_30_hist_dev, hoy_hist_dev], key="fechas_historial_devoluciones")

    if len(fechas_hist_dev) == 2:
        f_ini_hd, f_fin_hd = fechas_hist_dev
        df_devoluciones = obtener_historial_devoluciones(user_id, f_ini_hd, f_fin_hd)

        if not df_devoluciones.empty:
            df_devoluciones = df_devoluciones.copy()
            df_devoluciones['numero_factura_texto'] = (
                df_devoluciones['factura_prefijo'].fillna('').astype(str)
                + df_devoluciones['factura_numero'].fillna('').astype(str)
            )
            df_devoluciones['numero_nc_texto'] = (
                df_devoluciones['nota_credito_prefijo'].fillna('').astype(str)
                + df_devoluciones['nota_credito_numero'].fillna('').astype(str)
            )

            def _estado_nc(row):
                if row['nota_credito_alegra_id']:
                    return 'Emitida'
                if row['factura_estado'] == 'emitida':
                    return 'Pendiente'
                if row['factura_estado'] == 'abierta':
                    return 'No aplica (factura nunca se emitió a la DIAN)'
                return 'No aplica (sin FE)'

            df_devoluciones['nc_estado_texto'] = df_devoluciones.apply(_estado_nc, axis=1)

            st.dataframe(
                df_devoluciones[['id', 'fecha', 'cliente', 'total', 'numero_factura_texto',
                                  'nc_estado_texto', 'numero_nc_texto', 'nota_credito_pdf_url',
                                  'nota_credito_xml_url', 'notas']].rename(columns={
                    'id': 'Venta #', 'fecha': 'Fecha', 'cliente': 'Cliente', 'total': 'Total',
                    'numero_factura_texto': 'N° Factura', 'nc_estado_texto': 'Nota Crédito',
                    'numero_nc_texto': 'N° Nota Crédito',
                    'nota_credito_pdf_url': 'N.C. PDF', 'nota_credito_xml_url': 'N.C. XML',
                    'notas': 'Motivo',
                }),
                use_container_width=True, hide_index=True,
                column_config={
                    "Total": st.column_config.NumberColumn(format="$%,d"),
                    "N.C. PDF": st.column_config.LinkColumn(display_text="Abrir"),
                    "N.C. XML": st.column_config.LinkColumn(display_text="Abrir"),
                }
            )

            pendientes_nc = df_devoluciones[df_devoluciones['nc_estado_texto'] == 'Pendiente']
            if not pendientes_nc.empty:
                st.warning(f"{len(pendientes_nc)} devolución(es) con factura electrónica sin nota crédito confirmada.")
                dict_reintento_nc = {
                    f"Venta #{r['id']} — {r['cliente']} — {formato_cop(r['total'])}": r['id']
                    for _, r in pendientes_nc.iterrows()
                }
                reintento_nc_sel = st.selectbox(
                    "Reintentar nota crédito de una devolución", options=list(dict_reintento_nc.keys()),
                    key="reintento_nc_historial_sel"
                )
                if st.button("Reintentar nota crédito", use_container_width=True, key="btn_reintento_nc_historial"):
                    with st.spinner("Emitiendo nota crédito ante Alegra..."):
                        ok_nc_h, msg_nc_h = anular_factura_venta(user_id, dict_reintento_nc[reintento_nc_sel])
                    if ok_nc_h:
                        st.success(msg_nc_h)
                        st.rerun()
                    else:
                        st.error(msg_nc_h)
        else:
            st.caption("No hay devoluciones en este período.")

    st.markdown("---")
    st.caption("Busca la venta por número para anularla. El stock se restaura automáticamente.")

    with engine.connect() as conn:
        df_hoy_dev = pd.read_sql_query(text("""
            SELECT v.id, v.fecha, COALESCE(cl.nombre, 'Venta directa') as cliente,
                   v.total, v.tipo_pago
            FROM Ventas v
            LEFT JOIN Clientes cl ON v.cliente_id = cl.id
            WHERE v.usuario_id = :uid
            AND DATE(v.fecha) = :hoy
            AND v.estado != 'Anulada'
            ORDER BY v.fecha DESC
        """), con=conn, params={"uid": user_id, "hoy": hoy_bogota().strftime('%Y-%m-%d')})

    if not df_hoy_dev.empty:
        st.markdown("**Ventas de hoy** (para identificar el número de venta):")
        st.dataframe(
            df_hoy_dev.rename(columns={
                'id': 'N°', 'fecha': 'Hora', 'cliente': 'Cliente',
                'total': 'Total', 'tipo_pago': 'Pago'
            }),
            hide_index=True, use_container_width=True,
            column_config={
                "Total": st.column_config.NumberColumn("Total", format="$%,d"),
            }
        )
    else:
        st.caption("No hay ventas registradas hoy todavía.")

    st.markdown("---")
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
                       COALESCE(cl.nombre, 'Venta directa') as cliente,
                       v.factura_estado, v.factura_alegra_id, v.factura_prefijo,
                       v.factura_numero, v.nota_credito_alegra_id, v.nota_credito_pdf_url,
                       v.nota_credito_xml_url
                FROM Ventas v
                LEFT JOIN Clientes cl ON v.cliente_id = cl.id
                WHERE v.id = :vid AND v.usuario_id = :uid
            """), {"vid": vid_dev, "uid": user_id}).fetchone()

            if venta_dev:
                detalles_dev = pd.read_sql_query(text("""
                    SELECT id, producto_id, nombre_producto, cantidad,
                           precio_unitario, descuento, subtotal
                    FROM Detalles_Venta WHERE venta_id = :vid
                """), con=conn, params={"vid": vid_dev})

        if venta_dev:
            if venta_dev[2] == "Anulada":
                st.warning(f"La venta #{vid_dev} ya fue anulada.")
                if venta_dev[6] == "anulada" and venta_dev[10]:
                    numero_factura_dev = f"{venta_dev[8] or ''}{venta_dev[9] or ''}"
                    st.info(
                        f"Factura electrónica {numero_factura_dev} anulada mediante "
                        f"nota crédito (Alegra #{venta_dev[10]})."
                    )
                    col_ncpdf, col_ncxml = st.columns(2)
                    if venta_dev[11]:
                        col_ncpdf.link_button("Ver PDF de la Nota Crédito", venta_dev[11], use_container_width=True)
                    if venta_dev[12]:
                        col_ncxml.link_button("Descargar XML (DIAN)", venta_dev[12], use_container_width=True)
                elif venta_dev[7]:
                    st.warning(
                        "Esta venta tenía factura electrónica emitida pero no se confirmó la nota crédito."
                    )
                    if st.button("Reintentar nota crédito", use_container_width=True):
                        with st.spinner("Emitiendo nota crédito ante Alegra..."):
                            ok_nc_r, msg_nc_r = anular_factura_venta(user_id, vid_dev)
                        if ok_nc_r:
                            st.success(msg_nc_r)
                            st.rerun()
                        else:
                            st.error(msg_nc_r)
            else:
                with st.container(border=True):
                    c1, c2, c3 = st.columns(3)
                    c1.metric("Venta #", vid_dev)
                    c2.metric("Total", formato_cop(venta_dev[1]))
                    c3.metric("Estado", venta_dev[2])
                    st.write(f"**Cliente:** {venta_dev[5]} | **Fecha:** {venta_dev[4]} | **Pago:** {venta_dev[3]}")

                if not detalles_dev.empty:
                    st.dataframe(
                        detalles_dev[['nombre_producto', 'cantidad', 'precio_unitario', 'descuento', 'subtotal']].rename(columns={
                            'nombre_producto': 'Producto', 'cantidad': 'Cant.',
                            'precio_unitario': 'Precio', 'descuento': 'Descuento', 'subtotal': 'Subtotal'
                        }),
                        hide_index=True, use_container_width=True,
                        column_config={
                            "Precio": st.column_config.NumberColumn("Precio", format="$%,d"),
                            "Descuento": st.column_config.NumberColumn("Descuento", format="$%,d"),
                            "Subtotal": st.column_config.NumberColumn("Subtotal", format="$%,d"),
                        }
                    )

                st.markdown("---")
                col_b1, col_b2 = st.columns(2)

                with col_b1:
                    if st.button("Anular Venta Completa", use_container_width=True, type="primary"):
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
                            st.success(f"Venta #{vid_dev} anulada. Stock restaurado.")

                            with st.spinner("Verificando si necesita nota crédito electrónica..."):
                                ok_nc, msg_nc = anular_factura_venta(user_id, vid_dev)
                            if ok_nc:
                                if "nota crédito" in msg_nc.lower():
                                    st.success(msg_nc)
                            else:
                                st.warning(f"La venta se anuló en MyInv, pero: {msg_nc}")

                            st.rerun()
                        except Exception as e:
                            st.error(f"Error al anular: {e}")

                with col_b2:
                    st.info("Al anular se restaura el stock y la venta queda marcada como anulada en los reportes.")
        else:
            st.warning(f"No se encontró la venta #{vid_dev}.")

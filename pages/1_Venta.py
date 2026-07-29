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
# INICIALIZAR CARRITO EN SESSION STATE
# ==========================================
if "carrito" not in st.session_state:
    st.session_state.carrito = []

def agregar_al_carrito(producto_id, nombre, codigo_barras, precio, cantidad=1):
    """Agrega un producto al carrito o aumenta la cantidad si ya existe."""
    for item in st.session_state.carrito:
        if item["producto_id"] == producto_id:
            item["cantidad"] += cantidad
            item["subtotal"] = item["cantidad"] * item["precio_unitario"]
            return
    st.session_state.carrito.append({
        "producto_id": producto_id,
        "nombre": nombre,
        "codigo_barras": codigo_barras,
        "precio_unitario": float(precio),
        "cantidad": cantidad,
        "subtotal": float(precio) * cantidad,
    })

def limpiar_carrito():
    st.session_state.carrito = []

# ==========================================
# LAYOUT: DOS COLUMNAS
# Izquierda: Búsqueda + Carrito
# Derecha: Cobro
# ==========================================
col_izq, col_der = st.columns([3, 2])

with col_izq:
    # ==========================================
    # BUSCADOR (lector de barras o texto)
    # ==========================================
    st.subheader("🔍 Buscar Producto")
    st.caption("Escanea el código de barras o escribe el nombre del producto.")

    busqueda = st.text_input(
        "Código de barras o nombre",
        placeholder="Escanea o escribe aquí...",
        key="buscador_pos",
        label_visibility="collapsed"
    )

    if busqueda:
        busqueda = busqueda.strip()

        # 1. Buscar por código de barras exacto (lector físico)
        producto_encontrado = buscar_producto_por_codigo(user_id, busqueda)

        if producto_encontrado:
            # Encontrado por código — agregar directo al carrito
            agregar_al_carrito(
                producto_id=producto_encontrado[0],
                nombre=producto_encontrado[1],
                codigo_barras=producto_encontrado[2],
                precio=producto_encontrado[4],
            )
            st.success(f"✅ {producto_encontrado[1]} agregado al carrito.")
            st.session_state.buscador_pos = ""
            st.rerun()
        else:
            # 2. Buscar por nombre (escritura manual)
            df_productos = obtener_productos_activos(user_id)
            if not df_productos.empty:
                df_filtrado = df_productos[
                    df_productos['nombre'].str.contains(busqueda, case=False, na=False)
                ]

                if not df_filtrado.empty:
                    st.markdown(f"**{len(df_filtrado)} resultado(s) encontrado(s):**")
                    for _, prod in df_filtrado.iterrows():
                        col_p1, col_p2, col_p3 = st.columns([3, 1, 1])
                        with col_p1:
                            st.write(f"**{prod['nombre']}**")
                            if prod['codigo_barras']:
                                st.caption(f"Código: {prod['codigo_barras']}")
                        with col_p2:
                            st.write(formato_cop(prod['precio_venta']))
                            st.caption(f"Stock: {prod['stock_actual']}")
                        with col_p3:
                            if st.button("Agregar", key=f"add_{prod['id']}"):
                                agregar_al_carrito(
                                    producto_id=int(prod['id']),
                                    nombre=prod['nombre'],
                                    codigo_barras=prod['codigo_barras'] or "",
                                    precio=float(prod['precio_venta']),
                                )
                                st.rerun()
                else:
                    st.warning(f"No se encontró '{busqueda}'. Verifica el nombre o código.")

    st.markdown("---")

    # ==========================================
    # CARRITO DE COMPRAS
    # ==========================================
    st.subheader("🛒 Carrito")

    if not st.session_state.carrito:
        st.info("El carrito está vacío. Busca un producto para comenzar.")
    else:
        # Headers
        h1, h2, h3, h4, h5 = st.columns([3, 1, 1, 1, 1])
        h1.markdown("**Producto**")
        h2.markdown("**Precio**")
        h3.markdown("**Cant.**")
        h4.markdown("**Subtotal**")
        h5.markdown("**Quitar**")

        total_carrito = 0
        items_a_eliminar = []

        for i, item in enumerate(st.session_state.carrito):
            c1, c2, c3, c4, c5 = st.columns([3, 1, 1, 1, 1])
            with c1:
                st.write(item["nombre"])
            with c2:
                st.write(formato_cop(item["precio_unitario"]))
            with c3:
                nueva_cant = st.number_input(
                    "", min_value=1, value=item["cantidad"],
                    step=1, key=f"cant_{i}",
                    label_visibility="collapsed"
                )
                if nueva_cant != item["cantidad"]:
                    st.session_state.carrito[i]["cantidad"] = nueva_cant
                    st.session_state.carrito[i]["subtotal"] = nueva_cant * item["precio_unitario"]
                    st.rerun()
            with c4:
                st.write(formato_cop(item["subtotal"]))
            with c5:
                if st.button("❌", key=f"del_{i}"):
                    items_a_eliminar.append(i)

            total_carrito += item["subtotal"]

        # Eliminar items marcados
        for idx in sorted(items_a_eliminar, reverse=True):
            st.session_state.carrito.pop(idx)
        if items_a_eliminar:
            st.rerun()

        st.markdown("---")
        col_total1, col_total2 = st.columns([2, 1])
        with col_total2:
            # Descuento
            descuento = st.number_input(
                "Descuento ($)", min_value=0.0,
                step=1000.0, key="descuento_pos"
            )
            total_final = max(0, total_carrito - descuento)
            st.markdown(f"### Total: {formato_cop(total_final)}")

        with col_total1:
            if st.button("🗑️ Limpiar carrito", use_container_width=True):
                limpiar_carrito()
                st.rerun()

with col_der:
    # ==========================================
    # PANEL DE COBRO
    # ==========================================
    st.subheader("💰 Cobrar")

    if not st.session_state.carrito:
        st.info("Agrega productos al carrito para cobrar.")
    else:
        total_carrito = sum(i["subtotal"] for i in st.session_state.carrito)
        descuento = st.session_state.get("descuento_pos", 0)
        total_final = max(0, total_carrito - descuento)

        st.markdown(f"**Total a cobrar: {formato_cop(total_final)}**")
        st.markdown("---")

        tipo_pago = st.selectbox(
            "Tipo de Pago",
            ["Efectivo", "Transferencia", "Crédito", "Mixto"],
            key="tipo_pago_sel"
        )

        monto_efectivo = 0
        monto_transferencia = 0
        cambio = 0
        cliente_id = None

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

        elif tipo_pago == "Crédito":
            clientes = obtener_clientes(user_id)
            if clientes:
                dict_clientes = {c[1]: c[0] for c in clientes}
                cliente_sel = st.selectbox("Cliente", options=list(dict_clientes.keys()))
                cliente_id = dict_clientes[cliente_sel]

                tipo_cuota = st.selectbox(
                    "Tipo de cuota",
                    ["Libre", "Semanal", "Quincenal", "Mensual"]
                )
                valor_cuota = 0
                if tipo_cuota != "Libre":
                    valor_cuota = st.number_input(
                        "Valor por cuota ($)", min_value=0.0, step=10000.0
                    )
                fecha_limite = st.date_input(
                    "Fecha límite de pago", value=date.today()
                )
            else:
                st.warning("No tienes clientes registrados. Ve a Clientes y Créditos para agregar uno.")
                tipo_pago = None

        elif tipo_pago == "Mixto":
            monto_efectivo = st.number_input(
                "Monto en Efectivo ($)", min_value=0.0,
                step=1000.0, max_value=float(total_final)
            )
            monto_transferencia = total_final - monto_efectivo
            st.info(f"Transferencia: {formato_cop(monto_transferencia)}")

        st.markdown("---")

        if tipo_pago and st.button(
            "✅ Confirmar Venta",
            type="primary",
            use_container_width=True
        ):
            try:
                with engine.begin() as conn:
                    is_sqlite = "sqlite" in str(engine.url)

                    estado_venta = "Credito" if tipo_pago == "Crédito" else "Pagada"

                    # 1. Crear venta
                    if is_sqlite:
                        cur = conn.execute(text("""
                            INSERT INTO Ventas
                            (usuario_id, cliente_id, subtotal, descuento, total,
                             tipo_pago, monto_efectivo, monto_transferencia, cambio, estado)
                            VALUES (:uid, :cid, :sub, :desc, :total,
                                    :tipo, :efec, :trans, :cambio, :est)
                        """), {
                            "uid": user_id, "cid": cliente_id,
                            "sub": total_carrito, "desc": descuento,
                            "total": total_final, "tipo": tipo_pago,
                            "efec": monto_efectivo, "trans": monto_transferencia,
                            "cambio": cambio, "est": estado_venta
                        })
                        venta_id = cur.lastrowid
                    else:
                        res = conn.execute(text("""
                            INSERT INTO Ventas
                            (usuario_id, cliente_id, subtotal, descuento, total,
                             tipo_pago, monto_efectivo, monto_transferencia, cambio, estado)
                            VALUES (:uid, :cid, :sub, :desc, :total,
                                    :tipo, :efec, :trans, :cambio, :est)
                            RETURNING id
                        """), {
                            "uid": user_id, "cid": cliente_id,
                            "sub": total_carrito, "desc": descuento,
                            "total": total_final, "tipo": tipo_pago,
                            "efec": monto_efectivo, "trans": monto_transferencia,
                            "cambio": cambio, "est": estado_venta
                        })
                        venta_id = res.scalar()

                    # 2. Insertar detalles y descontar stock
                    for item in st.session_state.carrito:
                        conn.execute(text("""
                            INSERT INTO Detalles_Venta
                            (venta_id, producto_id, nombre_producto, codigo_barras,
                             cantidad, precio_unitario, subtotal)
                            VALUES (:vid, :pid, :nom, :cod, :cant, :pvp, :sub)
                        """), {
                            "vid": venta_id,
                            "pid": item["producto_id"],
                            "nom": item["nombre"],
                            "cod": item["codigo_barras"],
                            "cant": item["cantidad"],
                            "pvp": item["precio_unitario"],
                            "sub": item["subtotal"]
                        })
                        # Descontar stock con verificación de cantidad suficiente
                        resultado = conn.execute(text("""
                            UPDATE Productos
                            SET stock_actual = stock_actual - :cant
                            WHERE id = :pid AND stock_actual >= :cant
                        """), {"cant": item["cantidad"], "pid": item["producto_id"]})
                        if resultado.rowcount == 0:
                            raise ValueError(f"Stock insuficiente para '{item['nombre']}'.")

                    # 3. Si es crédito, crear el registro de deuda
                    if tipo_pago == "Crédito" and cliente_id:
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
                            "tipo_c": tipo_cuota if tipo_pago == "Crédito" else "Libre",
                            "val_c": valor_cuota if tipo_pago == "Crédito" else 0,
                        })
                        invalidar_cache_creditos()

                invalidar_cache_ventas()

                # Guardar venta_id para mostrar comprobante
                st.session_state.ultima_venta_id = venta_id
                st.session_state.ultima_venta_total = total_final
                st.session_state.ultima_venta_cambio = cambio
                st.session_state.ultima_venta_tipo = tipo_pago

                limpiar_carrito()
                st.success(f"✅ Venta #{venta_id} registrada exitosamente.")
                if cambio > 0:
                    st.info(f"💵 Cambio al cliente: {formato_cop(cambio)}")
                st.rerun()

            except ValueError as ve:
                st.error(str(ve))
            except Exception as e:
                st.error(f"Error al procesar la venta: {e}")

        # ==========================================
        # COMPROBANTE DE ÚLTIMA VENTA
        # ==========================================
        if "ultima_venta_id" in st.session_state and st.session_state.ultima_venta_id:
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
                           monto_efectivo, cambio, fecha, cliente_id
                    FROM Ventas WHERE id = :vid
                """), {"vid": vid}).fetchone()

            st.write(f"**Venta #{vid}** — {st.session_state.ultima_venta_tipo}")
            if not detalles.empty:
                st.dataframe(
                    detalles.rename(columns={
                        "nombre_producto": "Producto",
                        "cantidad": "Cant.",
                        "precio_unitario": "Precio",
                        "subtotal": "Subtotal"
                    }),
                    hide_index=True,
                    use_container_width=True
                )
            st.success(f"**Total: {formato_cop(st.session_state.ultima_venta_total)}**")
            if st.session_state.ultima_venta_cambio > 0:
                st.info(f"Cambio: {formato_cop(st.session_state.ultima_venta_cambio)}")

            # Botón de descarga PDF
            try:
                from pdf_utils import generar_ticket_venta
                cfg = st.session_state.get("taller_config", {})

                # Cargar logo_path desde BD si no está en session_state
                logo_path = cfg.get("logo_path")
                if not logo_path:
                    with engine.connect() as conn_logo:
                        logo_row = conn_logo.execute(
                            text("SELECT logo_path, nit, telefono, direccion FROM Usuarios WHERE id = :uid"),
                            {"uid": user_id}
                        ).fetchone()
                        if logo_row:
                            logo_path = logo_row[0]
                            cfg = {
                                "logo_path": logo_row[0],
                                "nit": logo_row[1] or "",
                                "telefono": logo_row[2] or "",
                                "direccion": logo_row[3] or "",
                            }

                if venta_info:
                    pdf_ticket = generar_ticket_venta(
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
                        data=pdf_ticket,
                        file_name=f"Ticket_{vid}.pdf",
                        mime="application/pdf",
                        use_container_width=True
                    )
            except Exception as e:
                st.caption(f"PDF no disponible: {e}")

            if st.button("🖨️ Nueva Venta", use_container_width=True):
                st.session_state.ultima_venta_id = None
                st.rerun()

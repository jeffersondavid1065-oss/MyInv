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
    tiene_fe_habilitada,
    tiene_iva_habilitado,
    crear_cotizacion,
    obtener_cotizaciones,
    obtener_cotizacion_detalle,
    convertir_cotizacion_a_venta,
    eliminar_cotizacion,
    mapa_numeros_venta,
    numero_venta_negocio,
    id_venta_desde_numero,
    invalidar_cache_clientes,
    restaurar_stock_items,
    acreditar_venta,
)
from utils import aplicar_estilos, verificar_auth
from tz_utils import hoy_bogota, ahora_bogota_naive
from factus_utils import (
    facturar_venta, anular_factura_venta, emitir_nota_credito,
    CORRECCION_DEVOLUCION_PARCIAL, CORRECCION_REBAJA, CORRECCION_AJUSTE_PRECIO, CORRECCION_OTROS,
    refrescar_url_factura, refrescar_url_nota_credito, mostrar_documento,
    obtener_credenciales, consultar_adquiriente_dian,
)
from iva_utils import calcular_desglose_iva, calcular_iva_desde_detalles, texto_tasa_iva

st.set_page_config(page_title="Punto de Venta", layout="wide")
aplicar_estilos()
user_id, nombre_negocio = verificar_auth()

engine = obtener_conexion()
fe_activa = tiene_fe_habilitada(user_id)
iva_activo = tiene_iva_habilitado(user_id)
numeros_venta = mapa_numeros_venta(user_id)


def num_venta(vid):
    """Número de venta propio de este negocio (1, 2, 3...) — es lo único que
    se le muestra al usuario. El ID interno de Ventas.id (compartido por
    todos los negocios de la plataforma) queda oculto pero sigue siendo la
    llave real por debajo. Usa numero_venta_negocio() como respaldo para una
    venta recién creada en este mismo rerun, que todavía no está en el mapa
    calculado al inicio del script."""
    if vid is None or (isinstance(vid, float) and pd.isna(vid)):
        return None
    vid = int(vid)
    return numeros_venta.get(vid) or numero_venta_negocio(user_id, vid)


def formato_cop(numero):
    return f"${float(numero):,.0f}".replace(",", ".")

st.title("Punto de Venta")
st.markdown(f"Caja para: **{nombre_negocio}**")
st.markdown("---")

# ==========================================
# CAJERO ACTIVO
# ==========================================
# La identidad (dueño o cajero) ya se resolvió en el login de app.py — aquí
# solo se lee. Si la identidad cambió desde el último carrito guardado en esta
# misma pestaña (ej. un cajero cerró sesión y otro entró), se limpia el carrito
# para que no quede visible ni cobrable bajo el nombre de otra persona.
cajero_id_actual = st.session_state.auth.get("cajero_id")
cajero_nombre_actual = st.session_state.auth.get("cajero_nombre")
identidad_actual = f"cajero:{cajero_id_actual}" if cajero_id_actual else f"admin:{user_id}"

if st.session_state.get("_identidad_carrito") != identidad_actual:
    st.session_state.carrito = []
    st.session_state.carrito_cotizacion = []
    st.session_state.descuento_pos = 0
    st.session_state.descuento_pct_global = 0
    st.session_state.tipo_desc_global = "$ Fijo"
    st.session_state.limpiar_buscador = True
    st.session_state._identidad_carrito = identidad_actual

if cajero_nombre_actual:
    st.success(f"Cajero activo: **{cajero_nombre_actual}**")
    st.markdown("")

if "limpiar_buscador" not in st.session_state:
    st.session_state.limpiar_buscador = False
if "carrito" not in st.session_state:
    st.session_state.carrito = []

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

def agregar_item_libre_al_carrito(nombre, precio, cantidad=1, costo=0, iva_porcentaje=0):
    """Agrega al carrito un ítem que NO está en el inventario (Venta Libre):
    algo que se consiguió por fuera para no perder la venta. producto_id
    queda en None -- Detalles_Venta.producto_id ya admite NULL y tanto el
    descuento de stock como la facturación electrónica (que arma la factura
    solo con los datos de Detalles_Venta) ya lo manejan correctamente. No se
    fusiona con otros ítems del carrito (a diferencia de agregar_al_carrito)
    porque dos ítems libres distintos comparten producto_id=None."""
    st.session_state.carrito.append({
        "producto_id": None,
        "nombre": nombre,
        "codigo_barras": "",
        "precio_unitario": float(precio),
        "costo_unitario": float(costo or 0),
        "descuento_item": 0.0,
        "descuento_pct_item": 0.0,
        "cantidad": cantidad,
        "subtotal": float(precio) * cantidad,
        "stock_max": None,
        "iva_porcentaje": float(iva_porcentaje or 0),
        "es_libre": True,
    })

def limpiar_carrito():
    st.session_state.carrito = []

TIPOS_DOC_POS = ["CC", "NIT", "CE", "PAS", "TI"]
REGIMENES_POS = {"No responsable de IVA": "SIMPLIFIED_REGIME", "Responsable de IVA": "COMMON_REGIME"}
OPCIONES_IVA_POS = [0, 5, 19]


def registrar_cliente_rapido_pos(key_suffix):
    """Expander para dar de alta un cliente sin salir del Punto de Venta,
    con la misma consulta a la DIAN que en Clientes y Créditos para
    autocompletar nombre y correo a partir del documento. Al guardar, deja
    en session_state el nombre/teléfono del cliente recién creado (bajo
    '_cliente_pos_creado') para que el selector que llamó a esta función lo
    preseleccione."""
    with st.expander("+ Registrar cliente nuevo"):
        creds_dian = obtener_credenciales(user_id)
        if creds_dian[0]:
            st.caption("Escribe el documento y consulta a la DIAN para traer nombre y correo automáticamente.")
            col_d1, col_d2, col_d3 = st.columns([1, 2, 1])
            with col_d1:
                tipo_doc_d = st.selectbox("Tipo de documento", TIPOS_DOC_POS, key=f"pos_tipo_doc_dian_{key_suffix}")
            with col_d2:
                num_doc_d = st.text_input("Número de documento", key=f"pos_num_doc_dian_{key_suffix}")
            with col_d3:
                st.markdown("<div style='height: 1.9em'></div>", unsafe_allow_html=True)
                buscar_d = st.button("Buscar en la DIAN", key=f"pos_buscar_dian_{key_suffix}", use_container_width=True)
            if buscar_d:
                if num_doc_d.strip():
                    with st.spinner("Consultando la DIAN..."):
                        ok_d, res_d = consultar_adquiriente_dian(*creds_dian, tipo_doc_d, num_doc_d.strip())
                    if ok_d:
                        st.session_state[f"pos_rn_{key_suffix}"] = res_d.get("nombre") or ""
                        st.session_state[f"pos_rt_{key_suffix}"] = tipo_doc_d
                        st.session_state[f"pos_rd_{key_suffix}"] = num_doc_d.strip()
                        st.session_state[f"pos_re_{key_suffix}"] = res_d.get("email") or ""
                        st.success("Datos encontrados en la DIAN. Revisa y completa lo que falte abajo.")
                        st.rerun()
                    else:
                        st.error(res_d)
                else:
                    st.warning("Escribe el número de documento.")
            st.markdown("---")

        with st.form(f"pos_form_nuevo_cliente_{key_suffix}", clear_on_submit=True):
            col_c1, col_c2 = st.columns(2)
            with col_c1:
                nombre_c = st.text_input("Nombre completo *", value=st.session_state.get(f"pos_rn_{key_suffix}", ""))
                telefono_c = st.text_input("Teléfono")
                email_c = st.text_input("Email (opcional)", value=st.session_state.get(f"pos_re_{key_suffix}", ""))
            with col_c2:
                doc_c = st.text_input("CC / NIT", value=st.session_state.get(f"pos_rd_{key_suffix}", ""))
                tipo_prev = st.session_state.get(f"pos_rt_{key_suffix}", "CC")
                tipo_doc_c = st.selectbox(
                    "Tipo de documento", options=TIPOS_DOC_POS,
                    index=TIPOS_DOC_POS.index(tipo_prev) if tipo_prev in TIPOS_DOC_POS else 0,
                )
                dv_c = st.text_input("Dígito de verificación", help="Solo aplica para NIT.")
                regimen_sel = st.selectbox("Régimen tributario", options=list(REGIMENES_POS.keys()))

            if st.form_submit_button("Guardar Cliente", type="primary"):
                if nombre_c:
                    try:
                        with engine.begin() as conn:
                            conn.execute(text("""
                                INSERT INTO Clientes
                                (usuario_id, nombre, telefono, email, documento, tipo_documento, regimen, digito_verificacion)
                                VALUES (:uid, :nom, :tel, :email, :doc, :tipo_doc, :regimen, :dv)
                            """), {
                                "uid": user_id, "nom": nombre_c,
                                "tel": telefono_c or None,
                                "email": email_c or None,
                                "doc": doc_c or None,
                                "tipo_doc": tipo_doc_c,
                                "regimen": REGIMENES_POS[regimen_sel],
                                "dv": dv_c or None,
                            })
                        invalidar_cache_clientes()
                        for k in (f"pos_rn_{key_suffix}", f"pos_rt_{key_suffix}", f"pos_rd_{key_suffix}", f"pos_re_{key_suffix}"):
                            st.session_state.pop(k, None)
                        st.session_state["_cliente_pos_creado"] = (nombre_c, telefono_c)
                        st.success(f"Cliente '{nombre_c}' registrado.")
                        st.rerun()
                    except Exception as e:
                        st.error(f"Error al guardar: {e}")
                else:
                    st.warning("El nombre es obligatorio.")

# ==========================================
# TABS
# ==========================================
tab_pos, tab_historial, tab_devolucion, tab_cotizacion = st.tabs([
    "Nueva Venta",
    "Historial del Día",
    "Devoluciones",
    "Cotización",
])

# ==========================================
# TAB 1: NUEVA VENTA
# ==========================================
with tab_pos:
    @st.fragment
    def _seccion_pos():
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
                            st.markdown("""
                                <style>
                                .st-key-resultados_busqueda_pos [data-testid="stVerticalBlock"] {
                                    gap: 0.3rem !important;
                                }
                                .st-key-resultados_busqueda_pos [data-testid="stVerticalBlockBorderWrapper"] {
                                    gap: 0.3rem !important;
                                }
                                </style>
                            """, unsafe_allow_html=True)
                            with st.container(key="resultados_busqueda_pos"):
                                for _, prod in df_filtrado.iterrows():
                                    c1, c2, c3 = st.columns([3, 1, 1])
                                    with c1:
                                        st.write(f"**{prod['nombre']}**")
                                        detalle_parts = []
                                        if prod['categoria']:
                                            detalle_parts.append(f"📂 {prod['categoria']}")
                                        if prod['codigo_barras']:
                                            detalle_parts.append(f"Cód: {prod['codigo_barras']}")
                                        if detalle_parts:
                                            st.caption(" · ".join(detalle_parts))
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

            with st.expander("Venta libre (algo que no está en el inventario)"):
                st.caption(
                    "Para cuando no tienes el producto y lo consigues por fuera para no "
                    "perder el cliente. Se agrega al carrito y se puede facturar "
                    "electrónicamente igual que cualquier otra venta, pero no descuenta "
                    "stock de Productos."
                )
                with st.form("form_venta_libre", clear_on_submit=True):
                    nombre_libre = st.text_input("Nombre / descripción *", placeholder="Ej: Filtro de aceite Toyota")
                    col_vl1, col_vl2, col_vl3 = st.columns(3)
                    with col_vl1:
                        precio_libre = st.number_input("Precio de venta ($) *", min_value=0.0, step=1000.0)
                    with col_vl2:
                        cantidad_libre = st.number_input("Cantidad", min_value=1, value=1, step=1)
                    with col_vl3:
                        costo_libre = st.number_input(
                            "Costo (opcional, $)", min_value=0.0, step=1000.0,
                            help="Lo que te costó a ti, si lo sabes. Solo afecta los reportes de ganancia."
                        )
                    if iva_activo:
                        iva_libre = st.selectbox("IVA%", OPCIONES_IVA_POS, index=0)
                    else:
                        iva_libre = 0
                    if st.form_submit_button("Agregar venta libre al carrito", use_container_width=True):
                        if not nombre_libre.strip():
                            st.warning("Escribe el nombre o descripción del ítem.")
                        elif precio_libre <= 0:
                            st.warning("El precio debe ser mayor a 0.")
                        else:
                            agregar_item_libre_al_carrito(
                                nombre=nombre_libre.strip(), precio=precio_libre,
                                cantidad=int(cantidad_libre), costo=costo_libre,
                                iva_porcentaje=iva_libre,
                            )
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
                st.subheader("Carrito")

                # ==========================================
                # TIPO DE AJUSTE POR ÍTEM (DESCUENTO O AUMENTO)
                # ==========================================
                tipo_desc_item = st.radio(
                    "Ajuste por ítem:",
                    ["Desc. $", "Desc. %", "▲ Aumento $", "▲ Aumento %"],
                    horizontal=True,
                    key="tipo_desc_item",
                    help="Aumento: cobra más caro que el precio de lista solo en esta venta, "
                         "sin modificar el precio del producto en el inventario."
                )

                # Headers carrito
                anchos_carrito = [3, 1, 1, 1, 0.7, 1, 0.5] if iva_activo else [3, 1, 1, 1, 1, 0.5]
                cols_header = st.columns(anchos_carrito)
                if iva_activo:
                    h1, h2, h3, h4, h5, h6, h7 = cols_header
                else:
                    h1, h2, h3, h4, h6, h7 = cols_header
                h1.markdown("**Producto**")
                h2.markdown("**Precio**")
                h3.markdown("**Cant.**")
                h4.markdown(f"**{tipo_desc_item}**")
                if iva_activo:
                    h5.markdown("**IVA%**")
                h6.markdown("**Total**")
                h7.markdown("**❌**")

                items_a_eliminar = []
                total_carrito = 0

                for i, item in enumerate(st.session_state.carrito):
                    cols_fila = st.columns(anchos_carrito)
                    if iva_activo:
                        c1, c2, c3, c4, c5, c6, c7 = cols_fila
                    else:
                        c1, c2, c3, c4, c6, c7 = cols_fila
                    with c1:
                        st.write(f"(Libre) {item['nombre']}" if item.get("es_libre") else item["nombre"])
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
                        ajuste_actual = item.get("descuento_item", 0)
                        if tipo_desc_item == "Desc. $":
                            valor_actual = max(0.0, ajuste_actual)
                            desc_item = st.number_input(
                                "", min_value=0.0,
                                value=float(valor_actual),
                                step=500.0, key=f"desc_{i}", label_visibility="collapsed"
                            )
                            if desc_item != valor_actual:
                                st.session_state.carrito[i]["descuento_item"] = desc_item
                                st.session_state.carrito[i]["descuento_pct_item"] = 0.0
                                st.session_state.carrito[i]["aumento_pct_item"] = 0.0
                                precio_neto = item["precio_unitario"] - desc_item
                                st.session_state.carrito[i]["subtotal"] = item["cantidad"] * max(0, precio_neto)
                                st.rerun()
                        elif tipo_desc_item == "Desc. %":
                            pct_item = st.number_input(
                                "", min_value=0.0, max_value=100.0,
                                value=float(item.get("descuento_pct_item", 0)),
                                step=5.0, key=f"pct_{i}", label_visibility="collapsed"
                            )
                            if pct_item != item.get("descuento_pct_item", 0):
                                desc_calculado = item["precio_unitario"] * (pct_item / 100)
                                st.session_state.carrito[i]["descuento_pct_item"] = pct_item
                                st.session_state.carrito[i]["aumento_pct_item"] = 0.0
                                st.session_state.carrito[i]["descuento_item"] = desc_calculado
                                precio_neto = item["precio_unitario"] - desc_calculado
                                st.session_state.carrito[i]["subtotal"] = item["cantidad"] * max(0, precio_neto)
                                st.rerun()
                        elif tipo_desc_item == "▲ Aumento $":
                            valor_actual = max(0.0, -ajuste_actual)
                            aum_item = st.number_input(
                                "", min_value=0.0,
                                value=float(valor_actual),
                                step=500.0, key=f"aum_{i}", label_visibility="collapsed"
                            )
                            if aum_item != valor_actual:
                                st.session_state.carrito[i]["descuento_item"] = -aum_item
                                st.session_state.carrito[i]["descuento_pct_item"] = 0.0
                                st.session_state.carrito[i]["aumento_pct_item"] = 0.0
                                precio_neto = item["precio_unitario"] + aum_item
                                st.session_state.carrito[i]["subtotal"] = item["cantidad"] * max(0, precio_neto)
                                st.rerun()
                        else:  # ▲ Aumento %
                            pct_item = st.number_input(
                                "", min_value=0.0, max_value=300.0,
                                value=float(item.get("aumento_pct_item", 0)),
                                step=5.0, key=f"aumpct_{i}", label_visibility="collapsed"
                            )
                            if pct_item != item.get("aumento_pct_item", 0):
                                aum_calculado = item["precio_unitario"] * (pct_item / 100)
                                st.session_state.carrito[i]["aumento_pct_item"] = pct_item
                                st.session_state.carrito[i]["descuento_pct_item"] = 0.0
                                st.session_state.carrito[i]["descuento_item"] = -aum_calculado
                                precio_neto = item["precio_unitario"] + aum_calculado
                                st.session_state.carrito[i]["subtotal"] = item["cantidad"] * max(0, precio_neto)
                                st.rerun()
                    if iva_activo:
                        with c5:
                            iva_pct_item = item.get("iva_porcentaje", 0) or 0
                            st.write(f"{iva_pct_item:.0f}%" if iva_pct_item == int(iva_pct_item) else f"{iva_pct_item}%")
                    with c6:
                        st.write(formato_cop(item["subtotal"]))
                    with c7:
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

                cliente_creado_pos = st.session_state.pop("_cliente_pos_creado", None)

                if tipo_pago in ("Efectivo", "Transferencia", "Mixto"):
                    clientes_fact = obtener_clientes(user_id)
                    dict_clientes_fact = {"Sin cliente (venta directa)": None}
                    for c in clientes_fact:
                        cid_c, nombre_c, tel_c = c[0], c[1], c[2]
                        etiqueta = f"{nombre_c} — {tel_c}" if tel_c else nombre_c
                        dict_clientes_fact[etiqueta] = cid_c
                    opciones_fact = list(dict_clientes_fact.keys())
                    if cliente_creado_pos:
                        nombre_cc, tel_cc = cliente_creado_pos
                        etiqueta_cc = f"{nombre_cc} — {tel_cc}" if tel_cc else nombre_cc
                        if etiqueta_cc in opciones_fact:
                            st.session_state["venta_cliente_opcional_sel"] = etiqueta_cc
                    cliente_fact_sel = st.selectbox(
                        "Cliente (opcional, para poder facturar electrónicamente)",
                        options=opciones_fact,
                        key="venta_cliente_opcional_sel",
                        help="Solo se puede emitir factura electrónica si la venta queda ligada a un cliente registrado con documento.",
                    )
                    cliente_id = dict_clientes_fact[cliente_fact_sel]
                    registrar_cliente_rapido_pos("efectivo")

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

                        opciones_credito = list(dict_clientes.keys())
                        if cliente_creado_pos:
                            nombre_cc, tel_cc = cliente_creado_pos
                            etiqueta_cc = f"{nombre_cc} — {tel_cc}" if tel_cc else nombre_cc
                            if etiqueta_cc in opciones_credito:
                                st.session_state["venta_credito_cliente_sel"] = etiqueta_cc

                        cliente_sel = st.selectbox(
                            "Cliente",
                            options=opciones_credito,
                            key="venta_credito_cliente_sel",
                            help="Escribe el nombre o teléfono para buscar en la lista.",
                        )
                        cliente_id = dict_clientes[cliente_sel]
                        cliente_nombre_sel = cliente_sel.split(" — ")[0]
                        st.caption(f"🧾 Este crédito quedará registrado a nombre de: **{cliente_nombre_sel}**")
                        registrar_cliente_rapido_pos("credito")

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
                        st.warning("No tienes clientes registrados. Registra uno abajo para poder venderle a crédito.")
                        registrar_cliente_rapido_pos("credito")
                        tipo_pago = None

                elif tipo_pago == "Mixto":
                    monto_efectivo = st.number_input(
                        "Monto Efectivo ($)", min_value=0.0,
                        step=1000.0, max_value=float(total_final)
                    )
                    monto_transferencia = total_final - monto_efectivo
                    st.info(f"Transferencia: {formato_cop(monto_transferencia)}")

                st.markdown("---")

                if fe_activa:
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
                        confirmar_venta_factura = False
                else:
                    confirmar_venta = tipo_pago and st.button(
                        "Registrar Venta", type="primary", use_container_width=True
                    )
                    confirmar_venta_factura = False

                if confirmar_venta or confirmar_venta_factura:
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
                                if item["producto_id"]:
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

                        limpiar_carrito()
                        st.success(f"Venta #{num_venta(venta_id)} registrada.")
                        if cambio > 0:
                            st.info(f"Cambio: {formato_cop(cambio)}")

                        if confirmar_venta_factura:
                            with st.spinner("Emitiendo factura electrónica ante la DIAN..."):
                                ok_f, msg_f = facturar_venta(user_id, venta_id)
                            if ok_f:
                                st.success(msg_f)
                            else:
                                st.warning(msg_f)

                        # scope="app": el Historial del Día (otra pestaña, fuera
                        # de este fragment) también debe mostrar la venta nueva.
                        st.rerun(scope="app")

                    except ValueError as ve:
                        st.error(str(ve))
                    except Exception as e:
                        st.error(f"Error: {e}")

    _seccion_pos()

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
            'emitida': 'Emitida', 'error': 'Error', 'anulada': 'Anulada (N.C.)'
        })
        # numero_venta reemplaza el ID interno (compartido por todos los
        # negocios de la plataforma) por el número propio de este negocio.
        df_hoy['numero_venta'] = df_hoy['id'].map(numeros_venta)
        cols_hoy = ['numero_venta', 'fecha', 'cliente', 'total']
        rename_hoy = {
            'numero_venta': 'N°', 'fecha': 'Hora', 'cliente': 'Cliente', 'total': 'Total',
            'tipo_pago': 'Pago', 'estado': 'Estado',
            'fe_texto': 'Factura Electrónica', 'numero_factura_texto': 'N° Factura',
            'factura_pdf_url': 'Factura PDF', 'factura_xml_url': 'Factura XML',
        }
        config_hoy = {
            "Total": st.column_config.NumberColumn("Total", format="$%,d"),
        }
        if iva_activo:
            cols_hoy += ['iva_tasa_texto', 'iva_valor']
            rename_hoy.update({'iva_tasa_texto': 'Impuesto', 'iva_valor': 'Valor Imp'})
            config_hoy["Valor Imp"] = st.column_config.NumberColumn("Valor Imp", format="$%,d")
        cols_hoy += ['tipo_pago', 'estado', 'fe_texto', 'numero_factura_texto']

        df_hoy_mostrar = df_hoy[cols_hoy].rename(columns=rename_hoy)
        st.dataframe(
            df_hoy_mostrar,
            use_container_width=True, hide_index=True,
            column_config=config_hoy,
        )

        if not df_hoy.empty:
            st.markdown("**Descargar factura de una venta específica:**")
            dict_desc_hoy = {
                f"Venta #{num_venta(r['id'])} — {formato_cop(r['total'])} — {r['cliente']}": i
                for i, r in df_hoy.iterrows()
            }
            desc_sel_hoy_str = st.selectbox(
                "Selecciona la venta", options=list(dict_desc_hoy.keys()),
                index=None, placeholder="Selecciona una venta...", key="desc_sel_hoy"
            )
            if desc_sel_hoy_str:
                fila_desc_hoy = df_hoy.loc[dict_desc_hoy[desc_sel_hoy_str]]
                col_dh1, col_dh2 = st.columns(2)
                mostrar_documento(col_dh1, "Descargar PDF", fila_desc_hoy['factura_pdf_url'], f"Factura_Venta_{num_venta(fila_desc_hoy['id'])}.pdf", "application/pdf")
                mostrar_documento(col_dh2, "Descargar XML", fila_desc_hoy['factura_xml_url'], f"Factura_Venta_{num_venta(fila_desc_hoy['id'])}.xml", "application/xml")

        st.markdown("---")
        st.markdown("**Reimprimir ticket:**")
        dict_ventas = {
            f"Venta #{num_venta(r['id'])} — {formato_cop(r['total'])} — {r['cliente']}": r['id']
            for _, r in df_hoy.iterrows()
        }
        venta_reimp = st.selectbox(
            "Selecciona la venta", options=list(dict_ventas.keys()),
            index=None, placeholder="Selecciona una venta..."
        )

        if venta_reimp:
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
                detalles_reimp_mostrar = detalles_reimp if iva_activo else detalles_reimp.drop(columns=["iva_porcentaje"])
                config_reimp = {
                    "Precio": st.column_config.NumberColumn("Precio", format="$%,d"),
                    "Descuento": st.column_config.NumberColumn("Descuento", format="$%,d"),
                    "Subtotal": st.column_config.NumberColumn("Subtotal", format="$%,d"),
                }
                if iva_activo:
                    config_reimp["IVA%"] = st.column_config.NumberColumn("IVA%", format="%.0f%%")
                st.dataframe(detalles_reimp_mostrar.rename(columns={
                    "nombre_producto": "Producto", "cantidad": "Cant.",
                    "precio_unitario": "Precio", "descuento": "Descuento", "subtotal": "Subtotal",
                    "iva_porcentaje": "IVA%",
                }), hide_index=True, use_container_width=True,
                column_config=config_reimp)
            if info_reimp and float(info_reimp[1] or 0) > 0:
                st.caption(f"Descuento total aplicado: {formato_cop(info_reimp[1])}")
            _, iva_reimp = calcular_iva_desde_detalles(detalles_reimp, float(info_reimp[2]) if info_reimp else 0)
            if iva_reimp > 1:
                st.caption(f"IVA incluido ({texto_tasa_iva(detalles_reimp)}): {formato_cop(iva_reimp)}")

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
                    venta_id=num_venta(venta_id_reimp),
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
                    file_name=f"Ticket_{num_venta(venta_id_reimp)}.pdf",
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
    if st.session_state.auth.get("rol") == "cajero":
        st.warning("🔒 Las devoluciones y anulaciones las debe hacer el dueño del negocio.")
        st.stop()

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
                if pd.notna(row['nota_credito_alegra_id']):
                    return 'Emitida'
                if row['factura_estado'] == 'emitida':
                    return 'Pendiente'
                return 'No aplica (sin FE)'

            df_devoluciones['nc_estado_texto'] = df_devoluciones.apply(_estado_nc, axis=1)
            df_devoluciones['numero_venta'] = df_devoluciones['id'].map(numeros_venta)

            df_dev_mostrar = df_devoluciones[[
                'numero_venta', 'fecha', 'cliente', 'total', 'numero_factura_texto',
                'nc_estado_texto', 'numero_nc_texto',
                'notas']].rename(columns={
                'numero_venta': 'Venta #', 'fecha': 'Fecha', 'cliente': 'Cliente', 'total': 'Total',
                'numero_factura_texto': 'N° Factura', 'nc_estado_texto': 'Nota Crédito',
                'numero_nc_texto': 'N° Nota Crédito',
                'notas': 'Motivo',
            })
            st.dataframe(
                df_dev_mostrar,
                use_container_width=True, hide_index=True,
                column_config={"Total": st.column_config.NumberColumn(format="$%,d")},
            )

            nc_con_documento = df_devoluciones[df_devoluciones['nota_credito_alegra_id'].notna()]
            if not nc_con_documento.empty:
                st.markdown("**Descargar nota crédito de una devolución específica:**")
                dict_desc_nc = {
                    f"Venta #{num_venta(r['id'])} — {formato_cop(r['total'])} — {r['cliente']}": i
                    for i, r in nc_con_documento.iterrows()
                }
                desc_sel_nc_str = st.selectbox(
                    "Selecciona la devolución", options=list(dict_desc_nc.keys()),
                    index=None, placeholder="Selecciona una devolución...", key="desc_sel_nc"
                )
                if desc_sel_nc_str:
                    fila_desc_nc = nc_con_documento.loc[dict_desc_nc[desc_sel_nc_str]]
                    col_dn1, col_dn2 = st.columns(2)
                    mostrar_documento(col_dn1, "Descargar PDF", fila_desc_nc['nota_credito_pdf_url'], f"NotaCredito_Venta_{num_venta(fila_desc_nc['id'])}.pdf", "application/pdf")
                    mostrar_documento(col_dn2, "Descargar XML", fila_desc_nc['nota_credito_xml_url'], f"NotaCredito_Venta_{num_venta(fila_desc_nc['id'])}.xml", "application/xml")

            pendientes_nc = df_devoluciones[df_devoluciones['nc_estado_texto'] == 'Pendiente']
            if not pendientes_nc.empty:
                st.warning(f"{len(pendientes_nc)} devolución(es) con factura electrónica sin nota crédito confirmada.")
                dict_reintento_nc = {
                    f"Venta #{num_venta(r['id'])} — {r['cliente']} — {formato_cop(r['total'])}": r['id']
                    for _, r in pendientes_nc.iterrows()
                }
                reintento_nc_sel = st.selectbox(
                    "Reintentar nota crédito de una devolución", options=list(dict_reintento_nc.keys()),
                    index=None, placeholder="Selecciona una devolución...", key="reintento_nc_historial_sel"
                )
                if st.button("Reintentar nota crédito", use_container_width=True, key="btn_reintento_nc_historial", disabled=not reintento_nc_sel):
                    with st.spinner("Emitiendo nota crédito..."):
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
        df_hoy_dev = df_hoy_dev.copy()
        df_hoy_dev['numero_venta'] = df_hoy_dev['id'].map(numeros_venta)
        st.markdown("**Ventas de hoy** (para identificar el número de venta):")
        st.dataframe(
            df_hoy_dev[['numero_venta', 'fecha', 'cliente', 'total', 'tipo_pago']].rename(columns={
                'numero_venta': 'N°', 'fecha': 'Hora', 'cliente': 'Cliente',
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
        venta_num = st.text_input("Número de venta", placeholder="Ej: 3")
    with col_d2:
        motivo = st.text_input("Motivo (opcional)", placeholder="Ej: Producto defectuoso")

    if venta_num and venta_num.isdigit():
        # El usuario escribe el número de venta propio del negocio (el que ve
        # en pantalla), no el ID interno — se traduce al ID real por debajo.
        numero_tipeado_dev = int(venta_num)
        vid_dev = id_venta_desde_numero(user_id, numero_tipeado_dev)

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
                           precio_unitario, descuento, subtotal, iva_porcentaje
                    FROM Detalles_Venta WHERE venta_id = :vid
                """), con=conn, params={"vid": vid_dev})

        if venta_dev:
            if venta_dev[2] == "Anulada":
                st.warning(f"La venta #{num_venta(vid_dev)} ya fue anulada.")
                if venta_dev[6] == "anulada" and venta_dev[10]:
                    numero_factura_dev = f"{venta_dev[8] or ''}{venta_dev[9] or ''}"
                    st.info(
                        f"Factura electrónica {numero_factura_dev} anulada mediante "
                        f"nota crédito (#{venta_dev[10]})."
                    )
                    pdf_mostrar_nc, xml_mostrar_nc = refrescar_url_nota_credito(user_id, vid_dev)
                    col_ncpdf, col_ncxml = st.columns(2)
                    mostrar_documento(col_ncpdf, "Ver PDF de la Nota Crédito", pdf_mostrar_nc, f"NotaCredito_Venta_{num_venta(vid_dev)}.pdf", "application/pdf")
                    mostrar_documento(col_ncxml, "Descargar XML (DIAN)", xml_mostrar_nc, f"NotaCredito_Venta_{num_venta(vid_dev)}.xml", "application/xml")
                elif venta_dev[7]:
                    st.warning(
                        "Esta venta tenía factura electrónica emitida pero no se confirmó la nota crédito."
                    )
                    if st.button("Reintentar nota crédito", use_container_width=True):
                        with st.spinner("Emitiendo nota crédito..."):
                            ok_nc_r, msg_nc_r = anular_factura_venta(user_id, vid_dev)
                        if ok_nc_r:
                            st.success(msg_nc_r)
                            st.rerun()
                        else:
                            st.error(msg_nc_r)
            else:
                with st.container(border=True):
                    c1, c2, c3 = st.columns(3)
                    c1.metric("Venta #", num_venta(vid_dev))
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
                tipo_correccion = st.radio(
                    "Tipo de corrección",
                    ["Anulación completa", "Devolución parcial de productos",
                     "Rebaja / descuento", "Ajuste de precio", "Otro"],
                    key=f"tipo_correccion_{vid_dev}",
                    help="Define qué concepto de corrección DIAN se envía en la nota crédito "
                         "electrónica (si la venta tiene factura emitida).",
                )

                @st.fragment
                def _seccion_correccion_venta():
                    if tipo_correccion == "Anulación completa":
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
                                    st.success(f"Venta #{num_venta(vid_dev)} anulada. Stock restaurado.")

                                    with st.spinner("Verificando si necesita nota crédito electrónica..."):
                                        ok_nc, msg_nc = anular_factura_venta(user_id, vid_dev)
                                    if ok_nc:
                                        if "nota crédito" in msg_nc.lower():
                                            st.success(msg_nc)
                                    else:
                                        st.warning(f"La venta se anuló en MyInv, pero: {msg_nc}")

                                    # scope="app": el estado de la venta mostrado
                                    # arriba (fuera de este fragment) debe reflejar
                                    # la anulación.
                                    st.rerun(scope="app")
                                except Exception as e:
                                    st.error(f"Error al anular: {e}")

                        with col_b2:
                            st.info("Al anular se restaura el stock y la venta queda marcada como anulada en los reportes.")

                    elif tipo_correccion == "Devolución parcial de productos":
                        st.caption(
                            "Indica cuántas unidades de cada producto devuelve el cliente. "
                            "Solo se restaura el stock de esas cantidades y se acredita su valor."
                        )
                        df_dev_base = detalles_dev.copy()
                        df_dev_base['cantidad_devolver'] = 0.0
                        df_dev_editado = st.data_editor(
                            df_dev_base[['nombre_producto', 'cantidad', 'precio_unitario', 'cantidad_devolver']],
                            hide_index=True, use_container_width=True,
                            disabled=['nombre_producto', 'cantidad', 'precio_unitario'],
                            column_config={
                                'nombre_producto': 'Producto',
                                'cantidad': 'Cant. vendida',
                                'precio_unitario': st.column_config.NumberColumn("Precio", format="$%,d"),
                                'cantidad_devolver': st.column_config.NumberColumn(
                                    "Cant. a devolver", min_value=0.0, step=1.0
                                ),
                            },
                            key=f"editor_dev_parcial_{vid_dev}",
                        )
                        if st.button("Registrar devolución y nota crédito", type="primary", use_container_width=True):
                            try:
                                items_stock, items_credito = [], []
                                monto_total = 0.0
                                for idx, fila in df_dev_editado.iterrows():
                                    cant_dev = min(float(fila['cantidad_devolver'] or 0), float(fila['cantidad']))
                                    if cant_dev <= 0:
                                        continue
                                    original = detalles_dev.loc[idx]
                                    precio_unit = float(original['precio_unitario'])
                                    cant_original = float(original['cantidad'])
                                    desc_unit = float(original['descuento'] or 0) / cant_original if cant_original else 0.0
                                    monto_linea = cant_dev * (precio_unit - desc_unit)
                                    monto_total += monto_linea
                                    if original['producto_id']:
                                        items_stock.append({"producto_id": int(original['producto_id']), "cantidad": cant_dev})
                                    items_credito.append({
                                        "producto_id": original['producto_id'],
                                        "nombre_producto": original['nombre_producto'],
                                        "cantidad": cant_dev,
                                        "precio_unitario": precio_unit,
                                        "descuento": desc_unit * cant_dev,
                                        "iva_porcentaje": original['iva_porcentaje'],
                                    })

                                if not items_credito:
                                    st.warning("Indica al menos una cantidad a devolver mayor a 0.")
                                elif monto_total > float(venta_dev[1]):
                                    st.warning("El monto a acreditar no puede superar el total de la venta.")
                                else:
                                    if items_stock:
                                        restaurar_stock_items(user_id, items_stock)
                                    acreditar_venta(user_id, vid_dev, monto_total,
                                                     motivo=f"Devolución parcial: {motivo}" if motivo else "Devolución parcial")
                                    st.success(f"Devolución registrada. Se acreditaron {formato_cop(monto_total)} "
                                               f"y se restauró el stock devuelto.")

                                    with st.spinner("Verificando si necesita nota crédito electrónica..."):
                                        ok_nc, msg_nc = emitir_nota_credito(
                                            user_id, vid_dev, CORRECCION_DEVOLUCION_PARCIAL, items_credito, motivo
                                        )
                                    if ok_nc:
                                        if "nota crédito" in msg_nc.lower():
                                            st.success(msg_nc)
                                    else:
                                        st.warning(f"La devolución se registró en MyInv, pero: {msg_nc}")
                                    # scope="app": el saldo/estado de la venta
                                    # mostrado arriba (fuera de este fragment)
                                    # debe reflejar la devolución.
                                    st.rerun(scope="app")
                            except Exception as e:
                                st.error(f"Error al registrar la devolución: {e}")

                    else:
                        concepto_map = {
                            "Rebaja / descuento": CORRECCION_REBAJA,
                            "Ajuste de precio": CORRECCION_AJUSTE_PRECIO,
                            "Otro": CORRECCION_OTROS,
                        }
                        st.caption(
                            "Indica el monto a acreditar por producto (no cambia el stock, solo "
                            "el total cobrado de la venta)."
                        )
                        df_credito_base = detalles_dev.copy()
                        df_credito_base['monto_credito'] = 0.0
                        df_credito_editado = st.data_editor(
                            df_credito_base[['nombre_producto', 'subtotal', 'monto_credito']],
                            hide_index=True, use_container_width=True,
                            disabled=['nombre_producto', 'subtotal'],
                            column_config={
                                'nombre_producto': 'Producto',
                                'subtotal': st.column_config.NumberColumn("Subtotal vendido", format="$%,d"),
                                'monto_credito': st.column_config.NumberColumn(
                                    "Monto a acreditar ($)", min_value=0.0, step=500.0
                                ),
                            },
                            key=f"editor_credito_{vid_dev}_{tipo_correccion}",
                        )
                        motivo_requerido = tipo_correccion == "Otro"
                        if motivo_requerido and not motivo:
                            st.info("Para 'Otro' es obligatorio indicar el motivo arriba.")
                        if st.button(f"Registrar {tipo_correccion.lower()} y nota crédito",
                                     type="primary", use_container_width=True,
                                     disabled=motivo_requerido and not motivo):
                            try:
                                items_credito = []
                                monto_total = 0.0
                                for idx, fila in df_credito_editado.iterrows():
                                    monto_linea = min(float(fila['monto_credito'] or 0), float(fila['subtotal']))
                                    if monto_linea <= 0:
                                        continue
                                    original = detalles_dev.loc[idx]
                                    monto_total += monto_linea
                                    items_credito.append({
                                        "producto_id": original['producto_id'],
                                        "nombre_producto": original['nombre_producto'],
                                        "cantidad": float(original['cantidad']),
                                        "precio_unitario": float(original['precio_unitario']),
                                        "descuento": monto_linea,
                                        "iva_porcentaje": original['iva_porcentaje'],
                                    })

                                if not items_credito:
                                    st.warning("Indica al menos un monto a acreditar mayor a 0.")
                                elif monto_total > float(venta_dev[1]):
                                    st.warning("El monto a acreditar no puede superar el total de la venta.")
                                else:
                                    acreditar_venta(user_id, vid_dev, monto_total,
                                                     motivo=f"{tipo_correccion}: {motivo}" if motivo else tipo_correccion)
                                    st.success(f"Se acreditaron {formato_cop(monto_total)} sobre la venta #{num_venta(vid_dev)}.")

                                    with st.spinner("Verificando si necesita nota crédito electrónica..."):
                                        ok_nc, msg_nc = emitir_nota_credito(
                                            user_id, vid_dev, concepto_map[tipo_correccion], items_credito, motivo
                                        )
                                    if ok_nc:
                                        if "nota crédito" in msg_nc.lower():
                                            st.success(msg_nc)
                                    else:
                                        st.warning(f"El crédito se registró en MyInv, pero: {msg_nc}")
                                    # scope="app": el saldo/estado de la venta
                                    # mostrado arriba (fuera de este fragment)
                                    # debe reflejar el ajuste.
                                    st.rerun(scope="app")
                            except Exception as e:
                                st.error(f"Error al registrar el crédito: {e}")

                _seccion_correccion_venta()
        else:
            st.warning(f"No se encontró la venta #{numero_tipeado_dev}.")

# ==========================================
# TAB 4: COTIZACIÓN
# ==========================================
with tab_cotizacion:
    @st.fragment
    def _seccion_cotizacion_builder():
        if "carrito_cotizacion" not in st.session_state:
            st.session_state.carrito_cotizacion = []

        def agregar_a_cotizacion(producto_id, nombre, codigo_barras, precio, stock_actual, costo=0, cantidad=1, iva_porcentaje=0):
            for item in st.session_state.carrito_cotizacion:
                if item["producto_id"] == producto_id:
                    item["cantidad"] += cantidad
                    item["subtotal"] = item["cantidad"] * item["precio_unitario"]
                    return
            st.session_state.carrito_cotizacion.append({
                "producto_id": producto_id,
                "nombre": nombre,
                "codigo_barras": codigo_barras,
                "precio_unitario": float(precio),
                "costo_unitario": float(costo or 0),
                "descuento_item": 0.0,
                "cantidad": cantidad,
                "subtotal": float(precio) * cantidad,
                "stock_max": stock_actual,
                "iva_porcentaje": float(iva_porcentaje or 0),
            })

        def agregar_item_libre_a_cotizacion(nombre, precio, cantidad=1, costo=0, iva_porcentaje=0):
            """Igual que agregar_item_libre_al_carrito pero para la cotización:
            un ítem que NO está en el inventario. producto_id queda en None --
            Detalles_Cotizacion.producto_id admite NULL y tanto crear_cotizacion
            como convertir_cotizacion_a_venta ya lo pasan tal cual a Detalles_Venta."""
            st.session_state.carrito_cotizacion.append({
                "producto_id": None,
                "nombre": nombre,
                "codigo_barras": "",
                "precio_unitario": float(precio),
                "costo_unitario": float(costo or 0),
                "descuento_item": 0.0,
                "cantidad": cantidad,
                "subtotal": float(precio) * cantidad,
                "stock_max": None,
                "iva_porcentaje": float(iva_porcentaje or 0),
                "es_libre": True,
            })

        st.subheader("Nueva Cotización")
        st.caption(
            "Cotiza productos para un cliente sin comprometer stock ni forma de pago todavía. "
            "Podrás descargarla en PDF y, cuando el cliente la apruebe, convertirla en una venta real "
            "desde 'Cotizaciones Guardadas' más abajo."
        )

        col_cot_izq, col_cot_der = st.columns([3, 2])

        with col_cot_izq:
            st.markdown("**Buscar Producto**")
            busqueda_cot = st.text_input(
                "Código o nombre", placeholder="Escanea o escribe aquí...",
                key="buscador_cotizacion", label_visibility="collapsed"
            )

            if busqueda_cot:
                busqueda_cot = busqueda_cot.strip()
                prod_encontrado_cot = buscar_producto_por_codigo(user_id, busqueda_cot)

                if prod_encontrado_cot:
                    agregar_a_cotizacion(
                        producto_id=prod_encontrado_cot[0],
                        nombre=prod_encontrado_cot[1],
                        codigo_barras=prod_encontrado_cot[2],
                        precio=prod_encontrado_cot[4],
                        stock_actual=int(prod_encontrado_cot[3]),
                        costo=prod_encontrado_cot[5],
                        iva_porcentaje=prod_encontrado_cot[6] if len(prod_encontrado_cot) > 6 else 0,
                    )
                    st.rerun()
                else:
                    df_productos_cot = obtener_productos_activos(user_id)
                    if not df_productos_cot.empty:
                        df_filtrado_cot = df_productos_cot[
                            df_productos_cot['nombre'].str.contains(busqueda_cot, case=False, na=False)
                        ]
                        if not df_filtrado_cot.empty:
                            st.markdown(f"**{len(df_filtrado_cot)} resultado(s):**")
                            for _, prod in df_filtrado_cot.iterrows():
                                c1, c2, c3 = st.columns([3, 1, 1])
                                with c1:
                                    st.write(f"**{prod['nombre']}**")
                                with c2:
                                    st.write(formato_cop(prod['precio_venta']))
                                with c3:
                                    if st.button("Agregar", key=f"add_cot_{prod['id']}"):
                                        agregar_a_cotizacion(
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
                            st.warning(f"No se encontró '{busqueda_cot}'.")

            with st.expander("Venta libre (algo que no está en el inventario)"):
                st.caption(
                    "Para cotizar algo que no tienes en el inventario o que conseguirías por "
                    "fuera. Se agrega a la cotización igual que cualquier producto, pero no "
                    "descuenta stock de Productos."
                )
                with st.form("form_libre_cotizacion", clear_on_submit=True):
                    nombre_libre_cot = st.text_input("Nombre / descripción *", placeholder="Ej: Filtro de aceite Toyota")
                    col_lc1, col_lc2, col_lc3 = st.columns(3)
                    with col_lc1:
                        precio_libre_cot = st.number_input("Precio de venta ($) *", min_value=0.0, step=1000.0)
                    with col_lc2:
                        cantidad_libre_cot = st.number_input("Cantidad", min_value=1, value=1, step=1)
                    with col_lc3:
                        costo_libre_cot = st.number_input(
                            "Costo (opcional, $)", min_value=0.0, step=1000.0,
                            help="Lo que te costó a ti, si lo sabes. Solo afecta los reportes de ganancia."
                        )
                    if iva_activo:
                        iva_libre_cot = st.selectbox("IVA%", OPCIONES_IVA_POS, index=0, key="iva_libre_cot")
                    else:
                        iva_libre_cot = 0
                    if st.form_submit_button("Agregar venta libre a la cotización", use_container_width=True):
                        if not nombre_libre_cot.strip():
                            st.warning("Escribe el nombre o descripción del ítem.")
                        elif precio_libre_cot <= 0:
                            st.warning("El precio debe ser mayor a 0.")
                        else:
                            agregar_item_libre_a_cotizacion(
                                nombre=nombre_libre_cot.strip(), precio=precio_libre_cot,
                                cantidad=int(cantidad_libre_cot), costo=costo_libre_cot,
                                iva_porcentaje=iva_libre_cot,
                            )
                            st.rerun()

            st.markdown("---")
            st.markdown("**Ítems de la Cotización**")

            if not st.session_state.carrito_cotizacion:
                st.info("Aún no se han agregado ítems.")
            else:
                items_a_quitar_cot = []
                total_cotizacion = 0

                for i, item in enumerate(st.session_state.carrito_cotizacion):
                    cc1, cc2, cc3, cc4, cc5 = st.columns([3, 1, 1, 1, 0.5])
                    with cc1:
                        st.write(f"(Libre) {item['nombre']}" if item.get("es_libre") else item["nombre"])
                    with cc2:
                        st.write(formato_cop(item["precio_unitario"]))
                    with cc3:
                        nueva_cant_cot = st.number_input(
                            "", min_value=1, value=item["cantidad"], step=1,
                            key=f"cant_cot_{i}", label_visibility="collapsed"
                        )
                        if nueva_cant_cot != item["cantidad"]:
                            st.session_state.carrito_cotizacion[i]["cantidad"] = nueva_cant_cot
                            st.session_state.carrito_cotizacion[i]["subtotal"] = nueva_cant_cot * item["precio_unitario"]
                            st.rerun()
                    with cc4:
                        st.write(formato_cop(item["subtotal"]))
                    with cc5:
                        if st.button("Quitar", key=f"del_cot_{i}"):
                            items_a_quitar_cot.append(i)

                    total_cotizacion += item["subtotal"]

                for idx in sorted(items_a_quitar_cot, reverse=True):
                    st.session_state.carrito_cotizacion.pop(idx)
                if items_a_quitar_cot:
                    st.rerun()

                st.markdown("---")
                st.markdown(f"### Total: {formato_cop(total_cotizacion)}")

        with col_cot_der:
            st.markdown("**Guardar Cotización**")

            if not st.session_state.carrito_cotizacion:
                st.info("Agrega productos para poder guardar la cotización.")
            else:
                clientes_cot = obtener_clientes(user_id)
                dict_clientes_cot = {"Sin cliente": None}
                if clientes_cot:
                    for c in clientes_cot:
                        cid_c, nombre_c, tel_c = c[0], c[1], c[2]
                        etiqueta = f"{nombre_c} — {tel_c}" if tel_c else nombre_c
                        dict_clientes_cot[etiqueta] = cid_c

                cliente_cot_sel = st.selectbox(
                    "Cliente (opcional)", options=list(dict_clientes_cot.keys()), key="cliente_cotizacion_sel",
                    help="Solo se requiere si vas a convertir esta cotización en una venta a crédito.",
                )
                cliente_id_cot = dict_clientes_cot[cliente_cot_sel]

                total_cot_guardar = sum(i["subtotal"] for i in st.session_state.carrito_cotizacion)
                st.markdown(f"**Total: {formato_cop(total_cot_guardar)}**")

                if st.button("Guardar Cotización", type="primary", use_container_width=True):
                    try:
                        cajero_id_cot = st.session_state.auth.get("cajero_id")
                        cot_id_nueva = crear_cotizacion(
                            user_id, cliente_id_cot, cajero_id_cot,
                            st.session_state.carrito_cotizacion,
                            subtotal=total_cot_guardar, descuento=0, total=total_cot_guardar,
                        )
                        st.session_state.carrito_cotizacion = []
                        st.success(
                            f"Cotización #{cot_id_nueva} guardada. Descárgala o conviértela en venta "
                            "desde 'Cotizaciones Guardadas' más abajo."
                        )
                        # scope="app": la lista de "Cotizaciones Guardadas" (fuera
                        # de este fragment) también debe mostrar la nueva.
                        st.rerun(scope="app")
                    except Exception as e:
                        st.error(f"Error al guardar la cotización: {e}")


    _seccion_cotizacion_builder()
    st.markdown("---")
    st.subheader("Cotizaciones Guardadas")

    df_cotizaciones = obtener_cotizaciones(user_id)

    if df_cotizaciones.empty:
        st.info("Aún no se ha guardado ninguna cotización.")
    else:
        pendientes_cot = df_cotizaciones[df_cotizaciones['convertida_a_venta_id'].isna()]
        convertidas_cot = df_cotizaciones[df_cotizaciones['convertida_a_venta_id'].notna()]
        st.markdown(f"**{len(pendientes_cot)}** pendiente(s) · **{len(convertidas_cot)}** ya convertida(s) en venta.")
        st.markdown("---")

        for _, cot in df_cotizaciones.iterrows():
            cot_id_actual = int(cot['id'])
            with st.container(border=True):
                col_h1, col_h2, col_h3 = st.columns([3, 2, 2])
                with col_h1:
                    st.markdown(f"**Cotización #{cot_id_actual}** — {cot['cliente']}")
                    st.caption(f"Total: {formato_cop(cot['total'])}")
                with col_h2:
                    st.caption(f"Creada: {cot['fecha']}")
                with col_h3:
                    if pd.notna(cot['convertida_a_venta_id']):
                        st.success(f"Convertida en Venta #{num_venta(int(cot['convertida_a_venta_id']))}")
                    else:
                        st.warning("Pendiente")

                with st.expander("Ver detalle, descargar PDF o convertir a venta"):
                    encabezado, df_items_cot = obtener_cotizacion_detalle(user_id, cot_id_actual)

                    if df_items_cot is None or df_items_cot.empty:
                        st.info("Esta cotización no tiene ítems.")
                    else:
                        st.dataframe(
                            df_items_cot[['nombre_producto', 'cantidad', 'precio_unitario', 'subtotal']].rename(columns={
                                'nombre_producto': 'Producto', 'cantidad': 'Cant.',
                                'precio_unitario': 'Precio', 'subtotal': 'Subtotal',
                            }),
                            hide_index=True, use_container_width=True,
                            column_config={
                                "Precio": st.column_config.NumberColumn(format="$%,d"),
                                "Subtotal": st.column_config.NumberColumn(format="$%,d"),
                            }
                        )

                        st.markdown("---")
                        st.markdown("#### Descargar PDF")

                        cfg_cot = st.session_state.get("taller_config", {})
                        logo_path_cot = cfg_cot.get("logo_path")
                        if not logo_path_cot:
                            with engine.connect() as conn_logo_cot:
                                lr_cot = conn_logo_cot.execute(
                                    text("SELECT logo_path, nit, telefono, direccion FROM Usuarios WHERE id = :uid"),
                                    {"uid": user_id}
                                ).fetchone()
                                if lr_cot:
                                    logo_path_cot = lr_cot[0]
                                    cfg_cot = {
                                        "logo_path": lr_cot[0], "nit": lr_cot[1] or "",
                                        "telefono": lr_cot[2] or "", "direccion": lr_cot[3] or "",
                                    }

                        from pdf_utils import generar_ticket_venta as _generar_pdf_cotizacion
                        pdf_bytes_cot = _generar_pdf_cotizacion(
                            negocio_nombre=nombre_negocio,
                            negocio_nit=cfg_cot.get("nit", ""),
                            negocio_telefono=cfg_cot.get("telefono", ""),
                            negocio_direccion=cfg_cot.get("direccion", ""),
                            negocio_logo_path=logo_path_cot,
                            venta_id=cot_id_actual,
                            fecha=str(encabezado.fecha)[:10],
                            cliente=encabezado.cliente or "Sin cliente",
                            tipo_pago="",
                            df_items=df_items_cot,
                            subtotal=float(encabezado.subtotal),
                            descuento=float(encabezado.descuento or 0),
                            total=float(encabezado.total),
                            es_cotizacion=True,
                        )
                        st.download_button(
                            "Descargar Cotización en PDF", data=pdf_bytes_cot,
                            file_name=f"Cotizacion_{cot_id_actual}.pdf", mime="application/pdf",
                            key=f"pdf_cot_{cot_id_actual}", use_container_width=True,
                        )

                        if pd.isna(cot['convertida_a_venta_id']):
                            st.markdown("---")
                            st.markdown("#### Convertir en Venta Real")

                            tipo_pago_conv = st.selectbox(
                                "Tipo de Pago", ["Efectivo", "Transferencia", "Credito", "Mixto"],
                                format_func=lambda x: "Crédito" if x == "Credito" else x,
                                key=f"tipo_pago_conv_{cot_id_actual}"
                            )

                            tipo_cuota_conv, valor_cuota_conv = "Libre", 0
                            fecha_limite_conv = hoy_bogota()
                            monto_efectivo_conv, monto_transferencia_conv = 0, 0
                            credito_bloqueado = False

                            if tipo_pago_conv == "Credito":
                                if not encabezado.cliente_id:
                                    st.warning(
                                        "Esta cotización no tiene cliente asignado — no se puede convertir "
                                        "a crédito sin uno. Elimínala y crea una nueva con cliente, o cóbrala "
                                        "de otra forma."
                                    )
                                    credito_bloqueado = True
                                else:
                                    tipo_cuota_conv = st.selectbox(
                                        "Tipo de cuota", ["Libre", "Semanal", "Quincenal", "Mensual"],
                                        key=f"tipo_cuota_conv_{cot_id_actual}"
                                    )
                                    if tipo_cuota_conv != "Libre":
                                        valor_cuota_conv = st.number_input(
                                            "Valor por cuota ($)", min_value=0.0, step=10000.0,
                                            key=f"valor_cuota_conv_{cot_id_actual}"
                                        )
                                    fecha_limite_conv = st.date_input(
                                        "Fecha límite", value=hoy_bogota(),
                                        key=f"fecha_limite_conv_{cot_id_actual}"
                                    )
                            elif tipo_pago_conv == "Efectivo":
                                monto_efectivo_conv = float(encabezado.total)
                            elif tipo_pago_conv == "Transferencia":
                                monto_transferencia_conv = float(encabezado.total)
                            elif tipo_pago_conv == "Mixto":
                                monto_efectivo_conv = st.number_input(
                                    "Monto Efectivo ($)", min_value=0.0, max_value=float(encabezado.total),
                                    step=1000.0, key=f"monto_efectivo_conv_{cot_id_actual}"
                                )
                                monto_transferencia_conv = float(encabezado.total) - monto_efectivo_conv

                            if st.button(
                                "Convertir en Venta", type="primary", use_container_width=True,
                                key=f"btn_convertir_{cot_id_actual}", disabled=credito_bloqueado
                            ):
                                try:
                                    cajero_id_conv = st.session_state.auth.get("cajero_id")
                                    venta_id_nueva = convertir_cotizacion_a_venta(
                                        user_id, cot_id_actual, tipo_pago_conv, cajero_id=cajero_id_conv,
                                        monto_efectivo=monto_efectivo_conv,
                                        monto_transferencia=monto_transferencia_conv,
                                        tipo_cuota=tipo_cuota_conv, valor_cuota=valor_cuota_conv,
                                        fecha_limite=fecha_limite_conv,
                                    )
                                    st.success(f"Cotización convertida en la Venta #{num_venta(venta_id_nueva)}.")
                                    st.rerun()
                                except ValueError as e:
                                    st.error(str(e))
                                except Exception as e:
                                    st.error(f"Error al convertir: {e}")

                            st.markdown("---")
                            if st.button("Eliminar Cotización", key=f"del_cot_btn_{cot_id_actual}"):
                                try:
                                    eliminar_cotizacion(user_id, cot_id_actual)
                                    st.success("Cotización eliminada.")
                                    st.rerun()
                                except Exception as e:
                                    st.error(str(e))

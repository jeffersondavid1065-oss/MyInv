import streamlit as st
import pandas as pd
from datetime import date, timedelta
from sqlalchemy import text
from db import obtener_conexion
from queries import (
    obtener_clientes,
    obtener_creditos_cliente,
    obtener_creditos_pendientes,
    invalidar_cache_clientes,
    invalidar_cache_creditos,
    obtener_historial_ventas_cliente,
    obtener_historial_abonos_cliente,
)
from utils import aplicar_estilos, verificar_auth, bloquear_si_cajero
from tz_utils import hoy_bogota
from alegra_utils import registrar_abono_credito, refrescar_url_factura, refrescar_url_nota_credito, mostrar_documento

st.set_page_config(page_title="Clientes y Créditos", layout="wide")
aplicar_estilos()
user_id, nombre_negocio = verificar_auth()
bloquear_si_cajero()

engine = obtener_conexion()

def formato_cop(numero):
    return f"${float(numero):,.0f}".replace(",", ".")

st.title("Clientes y Créditos")
st.markdown(f"Gestión de cartera para: **{nombre_negocio}**")
st.markdown("---")

tab_creditos, tab_clientes, tab_estado_cuenta, tab_nuevo = st.tabs([
    "Créditos y Abonos",
    "Directorio de Clientes",
    "Estado de Cuenta",
    "Registrar Cliente"
])

# ==========================================
# TAB 1: CRÉDITOS Y ABONOS
# ==========================================
with tab_creditos:
    st.subheader("Cartera Activa")

    df_creditos = obtener_creditos_pendientes(user_id)

    if not df_creditos.empty:
        # Métricas rápidas
        total_deuda = df_creditos['saldo_pendiente'].sum()
        vencidos = df_creditos[df_creditos['vencido'] == True]
        por_vencer = df_creditos[
            (df_creditos['vencido'] == False) &
            (pd.to_datetime(df_creditos['fecha_limite']) <= pd.Timestamp(hoy_bogota() + timedelta(days=7)))
        ]

        col_m1, col_m2, col_m3 = st.columns(3)
        col_m1.metric("Total en Cartera", formato_cop(total_deuda))
        col_m2.metric("Créditos Vencidos", len(vencidos),
                      delta="Cobrar ya" if len(vencidos) > 0 else None,
                      delta_color="inverse")
        col_m3.metric("Vencen esta semana", len(por_vencer),
                      delta="Avisar" if len(por_vencer) > 0 else None,
                      delta_color="inverse")

        st.markdown("---")

        # Alertas de vencidos
        if not vencidos.empty:
            st.error(f"**{len(vencidos)} crédito(s) vencido(s):**")
            for _, v in vencidos.iterrows():
                st.write(f"• **{v['cliente']}** — Saldo: {formato_cop(v['saldo_pendiente'])} — Tel: {v['telefono'] or 'N/A'} — Venció: {v['fecha_limite']}")

        st.markdown("---")

        # Tabla completa de créditos
        st.markdown("**Todos los créditos activos:**")
        df_mostrar = df_creditos.copy()
        df_mostrar['numero_factura_texto'] = (
            df_mostrar['factura_prefijo'].fillna('').astype(str) + df_mostrar['factura_numero'].fillna('').astype(str)
        )
        df_mostrar['factura_estado'] = df_mostrar['factura_estado'].fillna('Sin facturar').replace({
            'emitida': 'Facturada', 'abierta': 'Abierta (sin timbrar)', 'error': 'Error factura', 'anulada': 'Anulada (N.C.)'
        })
        # 'pedir': None cuando no hace falta pedir nada (sin FE, o ya tiene su
        # copia guardada) para que la casilla ni aparezca ahí.
        df_mostrar['pedir'] = None
        falta_copia = df_mostrar['factura_alegra_id'].notna() & ~df_mostrar['factura_pdf_url'].fillna('').str.startswith('data:')
        df_mostrar.loc[falta_copia, 'pedir'] = False
        df_mostrar = df_mostrar[[
            'venta_id', 'cliente', 'total', 'saldo_pendiente', 'fecha_limite', 'tipo_cuota',
            'estado', 'vencido', 'factura_estado', 'numero_factura_texto', 'pedir'
        ]].rename(columns={
            'venta_id': 'Venta #',
            'cliente': 'Cliente', 'total': 'Total Original', 'saldo_pendiente': 'Saldo Pendiente',
            'fecha_limite': 'Fecha Límite', 'tipo_cuota': 'Tipo Cuota', 'estado': 'Estado',
            'vencido': 'Vencido', 'factura_estado': 'Facturación', 'numero_factura_texto': 'N° Factura', 'pedir': 'Pedir',
        })
        df_creditos_editada = st.data_editor(
            df_mostrar,
            use_container_width=True,
            hide_index=True,
            disabled=[c for c in df_mostrar.columns if c != 'Pedir'],
            key="editor_cartera",
            column_config={
                "Total Original": st.column_config.NumberColumn(format="$%,d"),
                "Saldo Pendiente": st.column_config.NumberColumn(format="$%,d"),
                "Pedir": st.column_config.CheckboxColumn(
                    "Pedir", help='Marca la(s) venta(s) y pulsa el botón de abajo para guardar su factura.'
                ),
            }
        )
        st.caption("Para descargar la factura de una venta específica, selecciónala abajo en \"Registrar Abono\".")
        seleccionadas_cartera = df_creditos_editada[df_creditos_editada['Pedir'] == True]
        if not seleccionadas_cartera.empty:
            if st.button(f"Guardar factura de {len(seleccionadas_cartera)} venta(s) marcada(s)", key="btn_pedir_cartera"):
                with st.spinner("Guardando..."):
                    for vid_sel in seleccionadas_cartera['Venta #'].tolist():
                        refrescar_url_factura(user_id, int(vid_sel))
                st.rerun()

        st.markdown("---")

        # ==========================================
        # REGISTRAR ABONO
        # ==========================================
        st.subheader("Registrar Abono")

        clientes = obtener_clientes(user_id)
        if clientes:
            dict_clientes = {c[1]: c[0] for c in clientes}
            cliente_abono = st.selectbox(
                "Selecciona el cliente",
                options=list(dict_clientes.keys()),
                key="cliente_abono_sel"
            )
            cliente_id_abono = dict_clientes[cliente_abono]

            df_cred_cliente = obtener_creditos_cliente(user_id, cliente_id_abono)
            creditos_activos = df_cred_cliente[df_cred_cliente['estado'] == 'Activo'] if not df_cred_cliente.empty else pd.DataFrame()

            if not creditos_activos.empty:
                # Mostrar deudas del cliente
                st.markdown(f"**Deudas activas de {cliente_abono}:**")
                for _, cred in creditos_activos.iterrows():
                    factura_txt = " 🧾" if cred.get('factura_estado') == 'emitida' else ""
                    st.write(
                        f"• Crédito #{cred['id']} — "
                        f"Total: {formato_cop(cred['total'])} — "
                        f"Saldo: {formato_cop(cred['saldo_pendiente'])} — "
                        f"Vence: {cred['fecha_limite']}{factura_txt}"
                    )

                # Seleccionar crédito a abonar
                dict_creditos = {
                    f"Crédito #{r['id']} — Saldo: {formato_cop(r['saldo_pendiente'])}": r['id']
                    for _, r in creditos_activos.iterrows()
                }
                credito_sel = st.selectbox(
                    "Selecciona el crédito a abonar",
                    options=list(dict_creditos.keys())
                )
                credito_id_abono = dict_creditos[credito_sel]
                fila_credito = creditos_activos[creditos_activos['id'] == credito_id_abono].iloc[0]
                saldo_actual = fila_credito['saldo_pendiente']
                venta_id_credito = int(fila_credito['venta_id'])
                tiene_factura = fila_credito.get('factura_estado') == 'emitida'

                if tiene_factura:
                    st.caption("🧾 Esta venta tiene factura electrónica emitida — el abono se sincronizará automáticamente.")
                    col_fd1, col_fd2 = st.columns(2)
                    mostrar_documento(col_fd1, "Factura PDF", fila_credito.get('factura_pdf_url'), f"Factura_Venta_{venta_id_credito}.pdf", "application/pdf")
                    mostrar_documento(col_fd2, "Factura XML", fila_credito.get('factura_xml_url'), f"Factura_Venta_{venta_id_credito}.xml", "application/xml")

                col_ab1, col_ab2, col_ab3 = st.columns(3)
                with col_ab1:
                    min_abono = min(1000.0, float(saldo_actual))
                    monto_abono = st.number_input(
                        "Monto del abono ($)",
                        min_value=min_abono,
                        max_value=float(saldo_actual),
                        value=min_abono,
                        step=min_abono
                    )
                with col_ab2:
                    metodo_abono = st.radio("Método", ["Efectivo", "Transferencia"], horizontal=True)
                with col_ab3:
                    notas_abono = st.text_input("Notas (opcional)")

                nuevo_saldo = float(saldo_actual) - float(monto_abono)
                st.info(f"Saldo actual: {formato_cop(saldo_actual)} → Nuevo saldo: {formato_cop(nuevo_saldo)}")

                if st.button("Registrar Abono", type="primary", use_container_width=True):
                    try:
                        with engine.begin() as conn:
                            conn.execute(text("""
                                INSERT INTO Abonos (credito_id, usuario_id, monto, notas)
                                VALUES (:cid, :uid, :monto, :notas)
                            """), {
                                "cid": int(credito_id_abono),
                                "uid": user_id,
                                "monto": float(monto_abono),
                                "notas": notas_abono or None
                            })

                            nuevo_estado = "Pagado" if nuevo_saldo <= 0 else "Activo"
                            conn.execute(text("""
                                UPDATE Creditos
                                SET saldo_pendiente = :saldo, estado = :estado
                                WHERE id = :cid AND usuario_id = :uid
                            """), {
                                "saldo": float(max(0, nuevo_saldo)),
                                "estado": nuevo_estado,
                                "cid": int(credito_id_abono),
                                "uid": user_id
                            })

                        invalidar_cache_creditos()
                        if nuevo_estado == "Pagado":
                            st.success(f"Abono registrado. ¡Crédito #{credito_id_abono} pagado completamente!")
                        else:
                            st.success(f"Abono de {formato_cop(monto_abono)} registrado. Saldo pendiente: {formato_cop(nuevo_saldo)}")

                        if tiene_factura:
                            with st.spinner("Sincronizando abono..."):
                                ok_al, msg_al = registrar_abono_credito(user_id, venta_id_credito, monto_abono, metodo_abono)
                            if not ok_al:
                                st.warning(f"El abono quedó registrado en MyInv, pero: {msg_al}")

                        st.rerun()
                    except Exception as e:
                        st.error(f"Error al registrar abono: {e}")

                # Historial de abonos
                with st.expander("Ver historial de abonos de este crédito"):
                    with engine.connect() as conn:
                        df_abonos = pd.read_sql_query(text("""
                            SELECT monto, fecha, notas
                            FROM Abonos
                            WHERE credito_id = :cid
                            ORDER BY fecha DESC
                        """), con=conn, params={"cid": credito_id_abono})

                    if not df_abonos.empty:
                        df_abonos['monto'] = df_abonos['monto'].apply(formato_cop)
                        df_abonos.columns = ['Monto', 'Fecha', 'Notas']
                        st.dataframe(df_abonos, hide_index=True, use_container_width=True)
                    else:
                        st.info("Sin abonos registrados para este crédito.")
            else:
                st.success(f"{cliente_abono} no tiene créditos activos. ¡Está al día!")
    else:
        st.success("No hay créditos activos. ¡Cartera limpia!")

# ==========================================
# TAB 2: DIRECTORIO DE CLIENTES
# ==========================================
with tab_clientes:
    st.subheader("Directorio de Clientes")

    with engine.connect() as conn:
        df_todos_clientes = pd.read_sql_query(text("""
            SELECT c.id, c.nombre, c.telefono, c.documento, c.email,
                   c.direccion, c.cupo_credito, c.activo,
                   COALESCE(SUM(cr.saldo_pendiente), 0) as deuda_actual
            FROM Clientes c
            LEFT JOIN Creditos cr ON cr.cliente_id = c.id AND cr.estado = 'Activo'
            WHERE c.usuario_id = :uid
            GROUP BY c.id, c.nombre, c.telefono, c.documento,
                     c.email, c.direccion, c.cupo_credito, c.activo
            ORDER BY c.nombre ASC
        """), con=conn, params={"uid": user_id})

    if not df_todos_clientes.empty:
        busq_cliente = st.text_input("Buscar cliente", placeholder="Nombre, documento o teléfono...")

        if busq_cliente:
            mask = (
                df_todos_clientes['nombre'].str.contains(busq_cliente, case=False, na=False) |
                df_todos_clientes['documento'].astype(str).str.contains(busq_cliente, case=False, na=False) |
                df_todos_clientes['telefono'].astype(str).str.contains(busq_cliente, case=False, na=False)
            )
            df_todos_clientes = df_todos_clientes[mask]

        st.dataframe(
            df_todos_clientes.drop(columns=['id']).rename(columns={
                'nombre': 'Nombre', 'telefono': 'Teléfono',
                'documento': 'Documento', 'email': 'Email',
                'direccion': 'Dirección', 'cupo_credito': 'Cupo Crédito',
                'activo': 'Activo', 'deuda_actual': 'Deuda Actual'
            }),
            use_container_width=True,
            hide_index=True,
            column_config={
                "Cupo Crédito": st.column_config.NumberColumn("Cupo Crédito", format="$%,d"),
                "Deuda Actual": st.column_config.NumberColumn("Deuda Actual", format="$%,d"),
            }
        )
    else:
        st.info("No tienes clientes registrados todavía.")

# ==========================================
# TAB 3: ESTADO DE CUENTA
# ==========================================
with tab_estado_cuenta:
    st.subheader("Estado de Cuenta por Cliente")

    clientes_ec = obtener_clientes(user_id)
    if not clientes_ec:
        st.info("No tienes clientes registrados todavía.")
    else:
        dict_clientes_ec = {c[1]: c[0] for c in clientes_ec}
        cliente_ec_sel = st.selectbox(
            "Selecciona el cliente", options=list(dict_clientes_ec.keys()), key="cliente_estado_cuenta_sel"
        )
        cliente_id_ec = dict_clientes_ec[cliente_ec_sel]

        df_compras = obtener_historial_ventas_cliente(user_id, cliente_id_ec)
        df_abonos_ec = obtener_historial_abonos_cliente(user_id, cliente_id_ec)

        df_compras_activas = df_compras[df_compras['estado'] != 'Anulada']
        total_comprado = df_compras_activas['total'].sum()
        total_pagado = df_abonos_ec['monto'].sum() if not df_abonos_ec.empty else 0
        with engine.connect() as conn:
            saldo_pendiente_cliente = conn.execute(text("""
                SELECT COALESCE(SUM(saldo_pendiente), 0) FROM Creditos
                WHERE usuario_id = :uid AND cliente_id = :cid AND estado = 'Activo'
            """), {"uid": user_id, "cid": cliente_id_ec}).scalar()

        col_ec1, col_ec2, col_ec3, col_ec4 = st.columns(4)
        col_ec1.metric("Total comprado", formato_cop(total_comprado))
        col_ec2.metric("Total pagado (abonos)", formato_cop(total_pagado))
        col_ec3.metric("Saldo pendiente", formato_cop(saldo_pendiente_cliente))
        col_ec4.metric("N° de compras", len(df_compras_activas))

        st.markdown("---")
        st.markdown("**Historial de compras:**")
        if not df_compras.empty:
            df_compras_mostrar = df_compras.copy()
            df_compras_mostrar['numero_factura_texto'] = (
                df_compras_mostrar['factura_prefijo'].fillna('').astype(str)
                + df_compras_mostrar['factura_numero'].fillna('').astype(str)
            )
            df_compras_mostrar['fe_texto'] = df_compras_mostrar['factura_estado'].fillna('Sin facturar').replace({
                'emitida': 'Emitida', 'abierta': 'Abierta (sin timbrar)', 'error': 'Error', 'anulada': 'Anulada (N.C.)'
            })
            # 'pedir': None cuando no hace falta pedir nada (sin factura ni
            # nota crédito, o ya tienen su copia guardada).
            df_compras_mostrar['pedir'] = None
            falta_factura = (
                df_compras_mostrar['factura_alegra_id'].notna()
                & ~df_compras_mostrar['factura_pdf_url'].fillna('').str.startswith('data:')
            )
            falta_nc = (
                df_compras_mostrar['nota_credito_alegra_id'].notna()
                & ~df_compras_mostrar['nota_credito_pdf_url'].fillna('').str.startswith('data:')
            )
            df_compras_mostrar.loc[falta_factura | falta_nc, 'pedir'] = False
            df_compras_display = df_compras_mostrar[[
                'id', 'fecha', 'total', 'tipo_pago', 'estado', 'fe_texto', 'numero_factura_texto', 'pedir'
            ]].rename(columns={
                'id': 'Venta #', 'fecha': 'Fecha', 'total': 'Total',
                'tipo_pago': 'Pago', 'estado': 'Estado',
                'fe_texto': 'Factura Electrónica', 'numero_factura_texto': 'N° Factura', 'pedir': 'Pedir',
            })
            df_compras_editada = st.data_editor(
                df_compras_display,
                use_container_width=True, hide_index=True,
                disabled=[c for c in df_compras_display.columns if c != 'Pedir'],
                key="editor_estado_cuenta",
                column_config={
                    "Total": st.column_config.NumberColumn(format="$%,d"),
                    "Pedir": st.column_config.CheckboxColumn(
                        "Pedir", help='Marca la(s) venta(s) y pulsa el botón de abajo para guardar su factura o nota crédito.'
                    ),
                }
            )
            seleccionadas_ec = df_compras_editada[df_compras_editada['Pedir'] == True]
            if not seleccionadas_ec.empty:
                if st.button(f"Guardar documentos de {len(seleccionadas_ec)} venta(s) marcada(s)", key="btn_pedir_ec"):
                    with st.spinner("Guardando..."):
                        for vid_sel in seleccionadas_ec['Venta #'].tolist():
                            refrescar_url_factura(user_id, int(vid_sel))
                            refrescar_url_nota_credito(user_id, int(vid_sel))
                    st.rerun()

            con_documento_ec = df_compras_mostrar[
                df_compras_mostrar['factura_alegra_id'].notna() | df_compras_mostrar['nota_credito_alegra_id'].notna()
            ]
            if not con_documento_ec.empty:
                st.markdown("**Descargar factura o nota crédito de una compra específica:**")
                dict_desc_ec = {
                    f"Venta #{r['id']} — {formato_cop(r['total'])} — {r['fecha']}": i
                    for i, r in con_documento_ec.iterrows()
                }
                desc_sel_ec_str = st.selectbox("Selecciona la compra", options=list(dict_desc_ec.keys()), key="desc_sel_ec")
                fila_desc_ec = con_documento_ec.loc[dict_desc_ec[desc_sel_ec_str]]
                col_de1, col_de2, col_de3, col_de4 = st.columns(4)
                mostrar_documento(col_de1, "Factura PDF", fila_desc_ec['factura_pdf_url'], f"Factura_Venta_{fila_desc_ec['id']}.pdf", "application/pdf")
                mostrar_documento(col_de2, "Factura XML", fila_desc_ec['factura_xml_url'], f"Factura_Venta_{fila_desc_ec['id']}.xml", "application/xml")
                mostrar_documento(col_de3, "N.C. PDF", fila_desc_ec['nota_credito_pdf_url'], f"NotaCredito_Venta_{fila_desc_ec['id']}.pdf", "application/pdf")
                mostrar_documento(col_de4, "N.C. XML", fila_desc_ec['nota_credito_xml_url'], f"NotaCredito_Venta_{fila_desc_ec['id']}.xml", "application/xml")
        else:
            st.caption("Este cliente todavía no tiene compras registradas.")

        st.markdown("---")
        st.markdown("**Historial de pagos (abonos):**")
        if not df_abonos_ec.empty:
            st.dataframe(
                df_abonos_ec[['fecha', 'monto', 'venta_id', 'notas']].rename(columns={
                    'fecha': 'Fecha', 'monto': 'Monto', 'venta_id': 'Venta #', 'notas': 'Notas'
                }),
                use_container_width=True, hide_index=True,
                column_config={"Monto": st.column_config.NumberColumn(format="$%,d")}
            )
        else:
            st.caption("Este cliente todavía no ha hecho abonos.")

# ==========================================
# TAB 4: REGISTRAR CLIENTE NUEVO
# ==========================================
with tab_nuevo:
    st.subheader("Registrar Nuevo Cliente")

    with st.form("form_nuevo_cliente", clear_on_submit=True):
        col_c1, col_c2 = st.columns(2)
        with col_c1:
            nombre_c = st.text_input("Nombre completo *")
            telefono_c = st.text_input("Teléfono")
            email_c = st.text_input("Email (opcional)")
        with col_c2:
            doc_c = st.text_input("CC / NIT")
            tipo_doc_c = st.selectbox(
                "Tipo de documento",
                options=["CC", "NIT", "CE", "PAS", "TI"],
                help="Necesario para poder facturar electrónicamente a este cliente"
            )
            dir_c = st.text_input("Dirección")
            cupo_c = st.number_input(
                "Cupo de crédito ($)",
                min_value=0.0, step=50000.0,
                help="Monto máximo que puede deber este cliente"
            )

        st.markdown("")
        if st.form_submit_button("Guardar Cliente", type="primary"):
            if nombre_c:
                try:
                    with engine.begin() as conn:
                        conn.execute(text("""
                            INSERT INTO Clientes
                            (usuario_id, nombre, telefono, email, documento, tipo_documento, direccion, cupo_credito)
                            VALUES (:uid, :nom, :tel, :email, :doc, :tipo_doc, :dir, :cupo)
                        """), {
                            "uid": user_id, "nom": nombre_c,
                            "tel": telefono_c or None,
                            "email": email_c or None,
                            "doc": doc_c or None,
                            "tipo_doc": tipo_doc_c,
                            "dir": dir_c or None,
                            "cupo": float(cupo_c)
                        })
                    invalidar_cache_clientes()
                    st.success(f"Cliente '{nombre_c}' registrado exitosamente.")
                    st.rerun()
                except Exception as e:
                    st.error(f"Error al guardar: {e}")
            else:
                st.warning("El nombre es obligatorio.")

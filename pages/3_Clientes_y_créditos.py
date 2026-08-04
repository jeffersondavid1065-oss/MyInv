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
)
from utils import aplicar_estilos, verificar_auth
from tz_utils import hoy_bogota

st.set_page_config(page_title="Clientes y Créditos", layout="wide")
aplicar_estilos()
user_id, nombre_negocio = verificar_auth()

engine = obtener_conexion()

def formato_cop(numero):
    return f"${float(numero):,.0f}".replace(",", ".")

st.title("Clientes y Créditos")
st.markdown(f"Gestión de cartera para: **{nombre_negocio}**")
st.markdown("---")

tab_creditos, tab_clientes, tab_nuevo = st.tabs([
    "Créditos y Abonos",
    "Directorio de Clientes",
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
        df_mostrar = df_creditos[[
            'cliente', 'total', 'saldo_pendiente',
            'fecha_limite', 'tipo_cuota', 'estado', 'vencido'
        ]].copy()
        df_mostrar.columns = [
            'Cliente', 'Total Original', 'Saldo Pendiente',
            'Fecha Límite', 'Tipo Cuota', 'Estado', 'Vencido'
        ]
        st.dataframe(
            df_mostrar.style.format({
                'Total Original': lambda x: formato_cop(x),
                'Saldo Pendiente': lambda x: formato_cop(x),
            }),
            use_container_width=True,
            hide_index=True
        )

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
                    st.write(
                        f"• Crédito #{cred['id']} — "
                        f"Total: {formato_cop(cred['total'])} — "
                        f"Saldo: {formato_cop(cred['saldo_pendiente'])} — "
                        f"Vence: {cred['fecha_limite']}"
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
                saldo_actual = creditos_activos[
                    creditos_activos['id'] == credito_id_abono
                ]['saldo_pendiente'].values[0]

                col_ab1, col_ab2 = st.columns(2)
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
# TAB 3: REGISTRAR CLIENTE NUEVO
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

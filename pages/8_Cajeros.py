import streamlit as st
import pandas as pd
import hashlib
from datetime import date, timedelta
from sqlalchemy import text
from db import obtener_conexion
from utils import aplicar_estilos, verificar_auth
from tz_utils import hoy_bogota

st.set_page_config(page_title="Cajeros", layout="wide")
aplicar_estilos()
user_id, nombre_negocio = verificar_auth()

engine = obtener_conexion()

def formato_cop(numero):
    return f"${float(numero):,.0f}".replace(",", ".")

def hash_pin(pin):
    return hashlib.sha256(pin.encode()).hexdigest()

st.title("Control de Cajeros")
st.markdown(f"Gestión de empleados en caja para: **{nombre_negocio}**")
st.markdown("---")

tab_cajeros, tab_nuevo, tab_rendimiento = st.tabs([
    "Cajeros Registrados",
    "Registrar Cajero",
    "Rendimiento por Cajero"
])

# ==========================================
# TAB 1: LISTA DE CAJEROS
# ==========================================
with tab_cajeros:
    st.subheader("Cajeros del Negocio")

    try:
        with engine.connect() as conn:
            df_cajeros = pd.read_sql_query(text("""
                SELECT c.id, c.nombre, c.activo, c.fecha_registro,
                       COUNT(DISTINCT DATE(v.fecha)) as dias_trabajados,
                       COUNT(v.id) as total_ventas,
                       COALESCE(SUM(v.total), 0) as total_vendido
                FROM Cajeros c
                LEFT JOIN Ventas v ON v.cajero_id = c.id
                    AND v.estado != 'Anulada'
                WHERE c.usuario_id = :uid
                GROUP BY c.id, c.nombre, c.activo, c.fecha_registro
                ORDER BY c.nombre ASC
            """), con=conn, params={"uid": user_id})
    except Exception:
        df_cajeros = pd.DataFrame()
        st.warning("La tabla de cajeros aún no está lista. Haz Reboot app en Streamlit Cloud.")

    if not df_cajeros.empty:
        for _, caj in df_cajeros.iterrows():
            with st.container(border=True):
                col_c1, col_c2, col_c3, col_c4 = st.columns([3, 2, 2, 1])
                with col_c1:
                    estado_icon = "🟢" if caj['activo'] else "🔴"
                    st.markdown(f"### {estado_icon} {caj['nombre']}")
                    st.caption(f"Registrado: {str(caj['fecha_registro'])[:10]}")
                with col_c2:
                    st.metric("Días trabajados", int(caj['dias_trabajados']))
                with col_c3:
                    st.metric("Ventas", int(caj['total_ventas']))
                with col_c4:
                    nuevo_estado = not caj['activo']
                    btn_label = "Desactivar" if caj['activo'] else "Activar"
                    if st.button(btn_label, key=f"toggle_{caj['id']}"):
                        try:
                            with engine.begin() as conn:
                                conn.execute(text("""
                                    UPDATE Cajeros SET activo = :estado
                                    WHERE id = :cid AND usuario_id = :uid
                                """), {"estado": nuevo_estado, "cid": int(caj['id']), "uid": user_id})
                            st.rerun()
                        except Exception as e:
                            st.error(f"Error: {e}")

        st.markdown("---")
        st.subheader("Cambiar PIN de un cajero")
        cajeros_activos = df_cajeros[df_cajeros['activo'] == True]
        if not cajeros_activos.empty:
            dict_caj = {r['nombre']: r['id'] for _, r in cajeros_activos.iterrows()}
            caj_sel = st.selectbox("Selecciona el cajero", options=list(dict_caj.keys()))
            caj_id_sel = dict_caj[caj_sel]

            col_pin1, col_pin2 = st.columns(2)
            with col_pin1:
                nuevo_pin = st.text_input("Nuevo PIN (4 dígitos)", type="password", max_chars=4)
            with col_pin2:
                confirmar_pin = st.text_input("Confirmar PIN", type="password", max_chars=4)

            if st.button("Actualizar PIN", use_container_width=True):
                if nuevo_pin and len(nuevo_pin) == 4 and nuevo_pin.isdigit():
                    if nuevo_pin == confirmar_pin:
                        try:
                            with engine.begin() as conn:
                                conn.execute(text("""
                                    UPDATE Cajeros SET pin = :pin
                                    WHERE id = :cid AND usuario_id = :uid
                                """), {"pin": hash_pin(nuevo_pin), "cid": caj_id_sel, "uid": user_id})
                            st.success(f"PIN de {caj_sel} actualizado.")
                        except Exception as e:
                            st.error(f"Error: {e}")
                    else:
                        st.error("Los PINs no coinciden.")
                else:
                    st.warning("El PIN debe ser de exactamente 4 dígitos numéricos.")
    else:
        st.info("No tienes cajeros registrados. Ve a 'Registrar Cajero' para agregar uno.")

# ==========================================
# TAB 2: REGISTRAR CAJERO NUEVO
# ==========================================
with tab_nuevo:
    st.subheader("Registrar Nuevo Cajero")
    st.caption("El cajero usará su nombre y PIN para identificarse en las ventas.")

    with st.form("form_nuevo_cajero", clear_on_submit=True):
        col_n1, col_n2 = st.columns(2)
        with col_n1:
            nombre_caj = st.text_input("Nombre del Cajero *")
        with col_n2:
            pin_caj = st.text_input("PIN (4 dígitos) *", type="password", max_chars=4,
                                     help="Solo números, exactamente 4 dígitos")

        st.markdown("")
        if st.form_submit_button("Registrar Cajero", type="primary"):
            if nombre_caj and pin_caj:
                if len(pin_caj) == 4 and pin_caj.isdigit():
                    try:
                        with engine.begin() as conn:
                            conn.execute(text("""
                                INSERT INTO Cajeros (usuario_id, nombre, pin)
                                VALUES (:uid, :nom, :pin)
                            """), {
                                "uid": user_id,
                                "nom": nombre_caj,
                                "pin": hash_pin(pin_caj)
                            })
                        st.success(f"Cajero '{nombre_caj}' registrado exitosamente.")
                        st.rerun()
                    except Exception as e:
                        st.error(f"Error al registrar: {e}")
                else:
                    st.error("El PIN debe ser de exactamente 4 dígitos numéricos.")
            else:
                st.warning("El nombre y el PIN son obligatorios.")

    st.markdown("---")
    st.info("""
    **¿Cómo funciona el control de cajeros?**
    
    • Cada cajero tiene un **nombre** y un **PIN de 4 dígitos**
    • Al abrir el Punto de Venta, el cajero selecciona su nombre e ingresa su PIN
    • Las ventas quedan registradas con el cajero que las realizó
    • El dueño puede ver el rendimiento de cada cajero en "Rendimiento"
    • Si un cajero ya no trabaja, puedes **desactivarlo** sin borrarlo
    """)

# ==========================================
# TAB 3: RENDIMIENTO POR CAJERO
# ==========================================
with tab_rendimiento:
    st.subheader("Rendimiento por Cajero")

    col_f1, col_f2 = st.columns(2)
    with col_f1:
        hoy = hoy_bogota()
        hace_30 = hoy - timedelta(days=30)
        fechas_rend = st.date_input("Período", [hace_30, hoy], key="fechas_rend")
    with col_f2:
        st.write("")

    if len(fechas_rend) == 2:
        f_ini_r, f_fin_r = fechas_rend

        try:
            with engine.connect() as conn:
                df_rend = pd.read_sql_query(text("""
                    SELECT
                        COALESCE(caj.nombre, 'Sin cajero') as cajero,
                        COUNT(v.id) as total_ventas,
                        COALESCE(SUM(v.total), 0) as total_vendido,
                        COALESCE(AVG(v.total), 0) as ticket_promedio,
                        COUNT(CASE WHEN v.tipo_pago = 'Efectivo' THEN 1 END) as ventas_efectivo,
                        COUNT(CASE WHEN v.tipo_pago = 'Credito' THEN 1 END) as ventas_credito
                    FROM Ventas v
                    LEFT JOIN Cajeros caj ON v.cajero_id = caj.id
                    WHERE v.usuario_id = :uid
                    AND DATE(v.fecha) >= :f_ini
                    AND DATE(v.fecha) <= :f_fin
                    AND v.estado != 'Anulada'
                    GROUP BY caj.id, caj.nombre
                    ORDER BY total_vendido DESC
                """), con=conn, params={
                    "uid": user_id,
                    "f_ini": f_ini_r.strftime('%Y-%m-%d'),
                    "f_fin": f_fin_r.strftime('%Y-%m-%d')
                })
        except Exception:
            df_rend = pd.DataFrame()

        if not df_rend.empty:
            for _, row in df_rend.iterrows():
                with st.container(border=True):
                    col_r1, col_r2, col_r3, col_r4 = st.columns(4)
                    col_r1.markdown(f"### 👤 {row['cajero']}")
                    col_r2.metric("Ventas", int(row['total_ventas']))
                    col_r3.metric("Total Vendido", formato_cop(row['total_vendido']))
                    col_r4.metric("Ticket Promedio", formato_cop(row['ticket_promedio']))

            st.markdown("---")
            st.bar_chart(
                df_rend.set_index('cajero')['total_vendido'],
                height=300
            )
        else:
            st.info("No hay datos de rendimiento en este período.")

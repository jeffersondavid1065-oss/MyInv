import streamlit as st
import pandas as pd
from datetime import date, timedelta
from sqlalchemy import text
from db import obtener_conexion
from utils import aplicar_estilos
from tz_utils import hoy_bogota

st.set_page_config(page_title="Administración - MyAlmacén", layout="wide")
aplicar_estilos()

# --------------------------------------------------------------------------------
# AUTENTICACIÓN Y VERIFICACIÓN DE ADMINISTRADOR
# --------------------------------------------------------------------------------
CORREO_ADMIN = "jefferson.david1065@gmail.com"

if "auth" not in st.session_state:
    st.session_state.auth = {"logged": False, "user_id": None, "nombre_negocio": None, "email": None, "token": None}

if not st.session_state.auth["logged"]:
    st.warning("Debes iniciar sesión para acceder a este módulo.")
    st.stop()

email_actual = st.session_state.auth.get("email")

if email_actual != CORREO_ADMIN:
    st.error("Acceso denegado. Esta sección es de uso exclusivo para la administración del sistema.")
    st.stop()

engine = obtener_conexion()

# ==========================================
# CONSULTA CACHEADA
# ==========================================
@st.cache_data(ttl=30)
def obtener_almacenes():
    engine = obtener_conexion()
    with engine.connect() as conn:
        return pd.read_sql_query(
            text("SELECT id, nombre_negocio, nombre_dueno, email, activo, fecha_pago_limite FROM Usuarios ORDER BY id DESC"),
            con=conn
        )

st.title("Panel de Administración")
st.markdown("Gestión de suscripciones y accesos de MyAlmacén.")
st.markdown("---")

df_almacenes = obtener_almacenes()

if not df_almacenes.empty:
    st.subheader("Almacenes Registrados")
    st.dataframe(df_almacenes, use_container_width=True, hide_index=True)

    st.markdown("---")
    st.subheader("Gestionar Suscripción")

    dict_almacenes = {
        f"ID {r['id']} - {r['nombre_negocio']} ({r['email']})": r['id']
        for _, r in df_almacenes.iterrows()
    }

    sel = st.selectbox("Selecciona el almacén:", options=list(dict_almacenes.keys()))

    if sel:
        almacen_id = dict_almacenes[sel]
        row = df_almacenes[df_almacenes['id'] == almacen_id].iloc[0]

        col1, col2 = st.columns(2)
        with col1:
            st.info(
                f"**Propietario:** {row['nombre_dueno']}\n\n"
                f"**Estado:** {'Activa' if row['activo'] else 'Inactiva'}\n\n"
                f"**Fecha de corte:** {row['fecha_pago_limite'] or 'Sin fecha'}"
            )

        with col2:
            st.markdown("### Acciones")

            col_a1, col_a2 = st.columns(2)

            with col_a1:
                if st.button("Activar 30 días", type="primary", use_container_width=True):
                    nueva_fecha = hoy_bogota() + timedelta(days=30)
                    try:
                        with engine.begin() as conn:
                            conn.execute(
                                text("UPDATE Usuarios SET fecha_pago_limite = :f, activo = TRUE WHERE id = :id"),
                                {"f": nueva_fecha, "id": almacen_id}
                            )
                        obtener_almacenes.clear()
                        st.success(f"Activado hasta: {nueva_fecha}")
                        st.rerun()
                    except Exception as e:
                        st.error(f"Error: {e}")

            with col_a2:
                if st.button("Suspender", use_container_width=True):
                    fecha_vencida = hoy_bogota() - timedelta(days=1)
                    try:
                        with engine.begin() as conn:
                            conn.execute(
                                text("UPDATE Usuarios SET fecha_pago_limite = :f, activo = FALSE WHERE id = :id"),
                                {"f": fecha_vencida, "id": almacen_id}
                            )
                        obtener_almacenes.clear()
                        st.warning(f"Acceso suspendido para '{row['nombre_negocio']}'.")
                        st.rerun()
                    except Exception as e:
                        st.error(f"Error: {e}")

            # Extender fecha personalizada
            st.markdown("**O elige una fecha específica:**")
            fecha_custom = st.date_input("Fecha de corte", value=hoy_bogota() + timedelta(days=30))
            if st.button("Guardar Fecha", use_container_width=True):
                try:
                    with engine.begin() as conn:
                        conn.execute(
                            text("UPDATE Usuarios SET fecha_pago_limite = :f, activo = TRUE WHERE id = :id"),
                            {"f": fecha_custom, "id": almacen_id}
                        )
                    obtener_almacenes.clear()
                    st.success(f"Fecha actualizada: {fecha_custom}")
                    st.rerun()
                except Exception as e:
                    st.error(f"Error: {e}")
else:
    st.info("No hay almacenes registrados todavía.")

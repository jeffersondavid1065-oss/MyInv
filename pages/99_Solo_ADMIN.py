import streamlit as st
import pandas as pd
from datetime import date, timedelta
from sqlalchemy import text
from db import obtener_conexion
from utils import aplicar_estilos
from queries import establecer_fe_habilitada
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
            text("""
                SELECT id, nombre_negocio, nombre_dueno, email, activo, fecha_pago_limite,
                       COALESCE(fe_habilitada, FALSE) as fe_habilitada
                FROM Usuarios ORDER BY id DESC
            """),
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

    st.markdown("---")
    st.subheader("Mantenimiento: Recalcular Costo de Ventas Antiguas")
    st.caption(
        "Antes de esta actualización, las ventas no guardaban el costo del producto vendido, "
        "por lo que 'Costo de Ventas' y la ganancia quedaban en $0 para esas ventas. "
        "Esta acción rellena el costo faltante usando el **costo de compra actual** de cada "
        "producto como mejor aproximación (no es el costo exacto histórico si el precio de compra "
        "cambió desde entonces). Es segura de ejecutar varias veces: solo toca ventas con costo en $0."
    )

    with engine.connect() as conn:
        pendientes = conn.execute(text("""
            SELECT COUNT(*) FROM Detalles_Venta dv
            JOIN Productos p ON p.id = dv.producto_id
            WHERE (dv.costo_unitario IS NULL OR dv.costo_unitario = 0)
        """)).scalar()

    if pendientes and pendientes > 0:
        st.warning(f"Hay **{pendientes}** línea(s) de venta sin costo registrado en todos los almacenes.")
        if st.button("Recalcular costos faltantes ahora", type="primary"):
            try:
                with engine.begin() as conn:
                    resultado = conn.execute(text("""
                        UPDATE Detalles_Venta
                        SET costo_unitario = (
                            SELECT p.costo_compra FROM Productos p WHERE p.id = Detalles_Venta.producto_id
                        )
                        WHERE (costo_unitario IS NULL OR costo_unitario = 0)
                        AND producto_id IS NOT NULL
                        AND EXISTS (SELECT 1 FROM Productos p WHERE p.id = Detalles_Venta.producto_id)
                    """))
                from queries import (
                    obtener_ganancia_dia, obtener_ganancia_por_producto_dia,
                    obtener_ganancia_acumulada, obtener_metricas_mes,
                )
                obtener_ganancia_dia.clear()
                obtener_ganancia_por_producto_dia.clear()
                obtener_ganancia_acumulada.clear()
                obtener_metricas_mes.clear()
                st.success(f"Costos recalculados en {resultado.rowcount} línea(s) de venta.")
                st.rerun()
            except Exception as e:
                st.error(f"Error: {e}")
    else:
        st.success("Todas las ventas ya tienen su costo registrado.")

    st.markdown("---")
    st.subheader("Mantenimiento: Corregir valores $NaN en Productos")
    st.caption(
        "Si se guardó un producto con el costo, precio de venta o cantidad en blanco "
        "(incluida una factura mal leída por la IA en Entradas de Mercancía), ese valor "
        "puede quedar como NaN en la base de datos, haciendo que 'Inversión', "
        "'Valor Comercial' y 'Ganancia Potencial' en Inventario se muestren como $NaN. "
        "Esta acción resetea esos valores corruptos a 0."
    )

    is_sqlite = "sqlite" in str(engine.url)
    condicion_nan = (
        "(costo_compra != costo_compra OR precio_venta != precio_venta OR stock_actual != stock_actual)"
        if is_sqlite else
        "(costo_compra::text = 'NaN' OR precio_venta::text = 'NaN' OR stock_actual::text = 'NaN')"
    )

    with engine.connect() as conn:
        nan_pendientes = conn.execute(text(
            f"SELECT COUNT(*) FROM Productos WHERE {condicion_nan}"
        )).scalar()

    if nan_pendientes and nan_pendientes > 0:
        st.warning(f"Hay **{nan_pendientes}** producto(s) con valores $NaN.")
        if st.button("Corregir valores $NaN ahora", type="primary"):
            try:
                with engine.begin() as conn:
                    if is_sqlite:
                        resultado = conn.execute(text("""
                            UPDATE Productos
                            SET costo_compra = CASE WHEN costo_compra != costo_compra THEN 0 ELSE costo_compra END,
                                precio_venta = CASE WHEN precio_venta != precio_venta THEN 0 ELSE precio_venta END,
                                stock_actual = CASE WHEN stock_actual != stock_actual THEN 0 ELSE stock_actual END
                            WHERE costo_compra != costo_compra OR precio_venta != precio_venta
                               OR stock_actual != stock_actual
                        """))
                    else:
                        resultado = conn.execute(text("""
                            UPDATE Productos
                            SET costo_compra = CASE WHEN costo_compra::text = 'NaN' THEN 0 ELSE costo_compra END,
                                precio_venta = CASE WHEN precio_venta::text = 'NaN' THEN 0 ELSE precio_venta END,
                                stock_actual = CASE WHEN stock_actual::text = 'NaN' THEN 0 ELSE stock_actual END
                            WHERE costo_compra::text = 'NaN' OR precio_venta::text = 'NaN'
                               OR stock_actual::text = 'NaN'
                        """))
                from queries import invalidar_cache_productos
                invalidar_cache_productos()
                st.success(f"Valores corregidos en {resultado.rowcount} producto(s). "
                           f"Si algún producto quedó en stock 0, revisa y ajusta la cantidad real.")
                st.rerun()
            except Exception as e:
                st.error(f"Error: {e}")
    else:
        st.success("No hay productos con valores $NaN.")

    st.markdown("---")

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

        st.markdown("---")
        st.markdown("### Facturación Electrónica")
        st.caption(
            "Controla qué negocios pueden ver y usar la integración de facturación electrónica "
            "(configurar credenciales, emitir facturas/notas crédito ante la DIAN). "
            "Nuevos almacenes empiezan sin acceso."
        )
        fe_activa = bool(row['fe_habilitada'])
        st.write(f"Estado actual: {'✅ Habilitada' if fe_activa else '🔒 Deshabilitada'}")
        col_fe1, col_fe2 = st.columns(2)
        with col_fe1:
            if st.button("Habilitar FE", type="primary", use_container_width=True, disabled=fe_activa):
                establecer_fe_habilitada(almacen_id, True)
                obtener_almacenes.clear()
                st.success(f"Facturación Electrónica habilitada para '{row['nombre_negocio']}'.")
                st.rerun()
        with col_fe2:
            if st.button("Deshabilitar FE", use_container_width=True, disabled=not fe_activa):
                establecer_fe_habilitada(almacen_id, False)
                obtener_almacenes.clear()
                st.warning(f"Facturación Electrónica deshabilitada para '{row['nombre_negocio']}'.")
                st.rerun()

else:
    st.info("No hay almacenes registrados todavía.")

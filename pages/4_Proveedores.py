import streamlit as st
import pandas as pd
from datetime import date, timedelta
from sqlalchemy import text
from db import obtener_conexion
from queries import obtener_proveedores, invalidar_cache_proveedores
from utils import aplicar_estilos, verificar_auth, bloquear_si_cajero
from tz_utils import hoy_bogota

st.set_page_config(page_title="Proveedores", layout="wide")
aplicar_estilos()
user_id, nombre_negocio = verificar_auth()
bloquear_si_cajero()

engine = obtener_conexion()

def formato_cop(numero):
    return f"${float(numero):,.0f}".replace(",", ".")

st.title("Proveedores")
st.markdown(f"Gestión de proveedores para: **{nombre_negocio}**")
st.markdown("---")

tab_lista, tab_nuevo, tab_historial = st.tabs([
    "Directorio de Proveedores",
    "Registrar Proveedor",
    "Historial de Compras"
])

# ==========================================
# TAB 1: DIRECTORIO DE PROVEEDORES
# ==========================================
with tab_lista:
    st.subheader("Proveedores Registrados")

    with engine.connect() as conn:
        df_proveedores = pd.read_sql_query(text("""
            SELECT p.id, p.nombre, p.telefono, p.nit, p.email, p.contacto,
                   COALESCE(COUNT(e.id), 0) as total_entradas,
                   COALESCE(SUM(e.total_compra), 0) as total_comprado
            FROM Proveedores p
            LEFT JOIN Entradas_Inventario e ON e.proveedor_id = p.id
            WHERE p.usuario_id = :uid AND p.activo = TRUE
            GROUP BY p.id, p.nombre, p.telefono, p.nit, p.email, p.contacto
            ORDER BY p.nombre ASC
        """), con=conn, params={"uid": user_id})

    if not df_proveedores.empty:
        busq_prov = st.text_input(
            "Buscar proveedor",
            placeholder="Nombre, NIT o teléfono..."
        )
        if busq_prov:
            mask = (
                df_proveedores['nombre'].str.contains(busq_prov, case=False, na=False) |
                df_proveedores['nit'].astype(str).str.contains(busq_prov, case=False, na=False) |
                df_proveedores['telefono'].astype(str).str.contains(busq_prov, case=False, na=False)
            )
            df_proveedores = df_proveedores[mask]

        for _, prov in df_proveedores.iterrows():
            with st.container(border=True):
                col_p1, col_p2, col_p3 = st.columns([3, 2, 1])
                with col_p1:
                    st.markdown(f"#### {prov['nombre']}")
                    if prov['contacto']:
                        st.caption(f"Contacto: {prov['contacto']}")
                with col_p2:
                    st.write(f"{prov['telefono'] or 'Sin teléfono'}")
                    if prov['nit']:
                        st.write(f"NIT: {prov['nit']}")
                    if prov['email']:
                        st.write(f"{prov['email']}")
                with col_p3:
                    st.metric("Compras", int(prov['total_entradas']))
                    st.metric("Total", formato_cop(prov['total_comprado']))
    else:
        st.info("No tienes proveedores registrados. Ve a 'Registrar Proveedor' para agregar uno.")

# ==========================================
# TAB 2: REGISTRAR PROVEEDOR NUEVO
# ==========================================
with tab_nuevo:
    st.subheader("Registrar Nuevo Proveedor")

    with st.form("form_nuevo_proveedor", clear_on_submit=True):
        col_n1, col_n2 = st.columns(2)
        with col_n1:
            nombre_p = st.text_input("Nombre del Proveedor / Empresa *")
            contacto_p = st.text_input("Nombre del Contacto")
            telefono_p = st.text_input("Teléfono")
        with col_n2:
            nit_p = st.text_input("NIT / Documento")
            email_p = st.text_input("Email")

        st.markdown("")
        if st.form_submit_button("Guardar Proveedor", type="primary"):
            if nombre_p:
                try:
                    with engine.begin() as conn:
                        conn.execute(text("""
                            INSERT INTO Proveedores
                            (usuario_id, nombre, contacto, telefono, nit, email)
                            VALUES (:uid, :nom, :cont, :tel, :nit, :email)
                        """), {
                            "uid": user_id,
                            "nom": nombre_p,
                            "cont": contacto_p or None,
                            "tel": telefono_p or None,
                            "nit": nit_p or None,
                            "email": email_p or None,
                        })
                    invalidar_cache_proveedores()
                    st.success(f"Proveedor '{nombre_p}' registrado exitosamente.")
                    st.rerun()
                except Exception as e:
                    st.error(f"Error al guardar: {e}")
            else:
                st.warning("El nombre del proveedor es obligatorio.")

# ==========================================
# TAB 3: HISTORIAL DE COMPRAS
# ==========================================
with tab_historial:
    st.subheader("Historial de Entradas por Proveedor")

    proveedores = obtener_proveedores(user_id)

    col_h1, col_h2 = st.columns(2)
    with col_h1:
        hoy = hoy_bogota()
        hace_30 = hoy - timedelta(days=30)
        fechas = st.date_input("Rango de fechas", [hace_30, hoy])
    with col_h2:
        if proveedores:
            dict_prov_filtro = {"-- Todos --": None}
            dict_prov_filtro.update({p[1]: p[0] for p in proveedores})
            prov_filtro = st.selectbox(
                "Filtrar por proveedor",
                options=list(dict_prov_filtro.keys())
            )
            prov_id_filtro = dict_prov_filtro[prov_filtro]
        else:
            prov_id_filtro = None
            st.info("Sin proveedores registrados.")

    if len(fechas) == 2:
        fecha_ini, fecha_fin = fechas

        condicion_prov = ""
        params_h = {
            "uid": user_id,
            "f_ini": fecha_ini.strftime('%Y-%m-%d'),
            "f_fin": fecha_fin.strftime('%Y-%m-%d')
        }
        if prov_id_filtro:
            condicion_prov = "AND e.proveedor_id = :prov_id"
            params_h["prov_id"] = prov_id_filtro

        with engine.connect() as conn:
            df_historial = pd.read_sql_query(text(f"""
                SELECT e.id as entrada_id,
                       DATE(e.fecha) as fecha,
                       COALESCE(p.nombre, 'Sin proveedor') as proveedor,
                       e.numero_factura,
                       e.total_compra,
                       e.notas
                FROM Entradas_Inventario e
                LEFT JOIN Proveedores p ON e.proveedor_id = p.id
                WHERE e.usuario_id = :uid
                AND DATE(e.fecha) >= :f_ini
                AND DATE(e.fecha) <= :f_fin
                {condicion_prov}
                ORDER BY e.fecha DESC
            """), con=conn, params=params_h)

        if not df_historial.empty:
            total_periodo = df_historial['total_compra'].sum()
            st.success(f"Total comprado en el período: {formato_cop(total_periodo)}")

            st.dataframe(
                df_historial.rename(columns={
                    'entrada_id': 'ID',
                    'fecha': 'Fecha',
                    'proveedor': 'Proveedor',
                    'numero_factura': 'Factura',
                    'total_compra': 'Total ($)',
                    'notas': 'Notas'
                }),
                use_container_width=True,
                hide_index=True,
                column_config={
                    "Total ($)": st.column_config.NumberColumn("Total ($)", format="$%,d"),
                }
            )

            # Detalle de una entrada
            st.markdown("---")
            st.markdown("**Ver detalle de una entrada:**")
            dict_entradas = {
                f"Entrada #{r['entrada_id']} — {r['proveedor']} — {formato_cop(r['total_compra'])}": r['entrada_id']
                for _, r in df_historial.iterrows()
            }
            entrada_sel = st.selectbox(
                "Selecciona la entrada",
                options=list(dict_entradas.keys())
            )
            entrada_id_sel = dict_entradas[entrada_sel]

            with engine.connect() as conn:
                df_detalle = pd.read_sql_query(text("""
                    SELECT pr.nombre as producto, de.cantidad,
                           de.costo_unitario, de.subtotal
                    FROM Detalles_Entrada de
                    JOIN Productos pr ON de.producto_id = pr.id
                    WHERE de.entrada_id = :eid
                """), con=conn, params={"eid": entrada_id_sel})

            if not df_detalle.empty:
                st.dataframe(
                    df_detalle.rename(columns={
                        'producto': 'Producto',
                        'cantidad': 'Cantidad',
                        'costo_unitario': 'Costo Unitario ($)',
                        'subtotal': 'Subtotal ($)'
                    }),
                    use_container_width=True,
                    hide_index=True,
                    column_config={
                        "Costo Unitario ($)": st.column_config.NumberColumn("Costo Unitario ($)", format="$%,d"),
                        "Subtotal ($)": st.column_config.NumberColumn("Subtotal ($)", format="$%,d"),
                    }
                )
        else:
            st.info("No hay entradas en este período.")

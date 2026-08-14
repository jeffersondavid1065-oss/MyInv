import streamlit as st
import pandas as pd
from datetime import date, timedelta
from sqlalchemy import text
from db import obtener_conexion
from utils import verificar_auth, bloquear_si_cajero
from queries import (
    obtener_ganancia_dia, obtener_ganancia_por_producto_dia, obtener_ganancia_acumulada,
    obtener_gastos_dia, mapa_numeros_venta,
)
from tz_utils import hoy_bogota, ahora_bogota_naive

st.set_page_config(page_title="Cierre de Caja", layout="wide")
user_id, nombre_negocio = verificar_auth()
bloquear_si_cajero()

engine = obtener_conexion()
numeros_venta = mapa_numeros_venta(user_id)

def formato_cop(numero):
    return f"${float(numero):,.0f}".replace(",", ".")

st.title("Cierre de Caja")
st.markdown(f"Control de caja para: **{nombre_negocio}**")
st.markdown("---")

tab_cierre, tab_historial = st.tabs(["Hacer Cierre de Hoy", "Historial de Cierres"])

# ==========================================
# TAB 1: CIERRE DEL DÍA
# ==========================================
with tab_cierre:
    fecha_cierre = st.date_input("Fecha del cierre", value=hoy_bogota())

    # Verificar si ya hay cierre para esta fecha
    with engine.connect() as conn:
        cierre_existente = conn.execute(text("""
            SELECT id, total_efectivo, total_transferencias,
                   efectivo_contado, diferencia, notas, fecha_cierre, total_descuentos,
                   total_gastos
            FROM Cierres_Caja
            WHERE usuario_id = :uid AND fecha = :fecha
        """), {"uid": user_id, "fecha": fecha_cierre.strftime('%Y-%m-%d')}).fetchone()

    if cierre_existente:
        st.warning(f"Ya existe un cierre para el {fecha_cierre.strftime('%d/%m/%Y')}.")
        with st.container(border=True):
            col1, col2, col3, col4, col5 = st.columns(5)
            col1.metric("Efectivo Sistema", formato_cop(cierre_existente[1]))
            col2.metric("Transferencias", formato_cop(cierre_existente[2]))
            col3.metric("Efectivo Contado", formato_cop(cierre_existente[3]))
            col4.metric("Descuentos", formato_cop(cierre_existente[7] or 0))
            col5.metric("Gastos del Día", formato_cop(cierre_existente[8] or 0))
            diff = cierre_existente[4]
            color = "🔴" if diff < 0 else "🟢" if diff == 0 else "🟡"
            st.metric(f"{color} Diferencia", formato_cop(diff))
            if cierre_existente[5]:
                st.info(f"Notas: {cierre_existente[5]}")
            st.caption(f"Cierre registrado: {cierre_existente[6]}")
    else:
        # Calcular ventas del día
        with engine.connect() as conn:
            resumen = conn.execute(text("""
                SELECT
                    COUNT(*) as total_ventas,
                    COALESCE(SUM(CASE WHEN estado != 'Anulada' THEN total ELSE 0 END), 0) as total_general,
                    COALESCE(SUM(CASE WHEN tipo_pago IN ('Efectivo','Mixto') AND estado != 'Anulada' THEN monto_efectivo ELSE 0 END), 0) as total_efectivo,
                    COALESCE(SUM(CASE WHEN tipo_pago IN ('Transferencia','Mixto') AND estado != 'Anulada' THEN monto_transferencia ELSE 0 END), 0) as total_transferencias,
                    COALESCE(SUM(CASE WHEN tipo_pago = 'Credito' AND estado = 'Credito' THEN total ELSE 0 END), 0) as total_creditos,
                    COALESCE(SUM(CASE WHEN estado != 'Anulada' THEN descuento ELSE 0 END), 0) as total_descuentos,
                    COUNT(CASE WHEN estado = 'Anulada' THEN 1 END) as ventas_anuladas
                FROM Ventas
                WHERE usuario_id = :uid AND DATE(fecha) = :fecha
            """), {"uid": user_id, "fecha": fecha_cierre.strftime('%Y-%m-%d')}).fetchone()

        total_ventas = int(resumen[0])
        total_general = float(resumen[1])
        total_efectivo = float(resumen[2])
        total_transferencias = float(resumen[3])
        total_creditos = float(resumen[4])
        total_descuentos = float(resumen[5])
        ventas_anuladas = int(resumen[6])

        gastos_dia_info = obtener_gastos_dia(user_id, fecha_cierre.strftime('%Y-%m-%d'))
        gastos_efectivo_dia = gastos_dia_info["total_efectivo"]
        gastos_transferencia_dia = gastos_dia_info["total_transferencia"]
        gastos_total_dia = gastos_dia_info["total"]
        efectivo_esperado = total_efectivo - gastos_efectivo_dia

        st.subheader(f"Resumen del {fecha_cierre.strftime('%d/%m/%Y')}")

        col_r1, col_r2, col_r3, col_r4, col_r5, col_r6 = st.columns(6)
        col_r1.metric("Ventas Realizadas", total_ventas - ventas_anuladas)
        col_r2.metric("Total General", formato_cop(total_general))
        col_r3.metric("Efectivo Sistema", formato_cop(total_efectivo))
        col_r4.metric("Transferencias", formato_cop(total_transferencias))
        col_r5.metric("Créditos", formato_cop(total_creditos))
        col_r6.metric("Descuentos", formato_cop(total_descuentos))

        if ventas_anuladas > 0:
            st.warning(f"{ventas_anuladas} venta(s) anulada(s) hoy.")

        if gastos_total_dia > 0:
            col_gs1, col_gs2, col_gs3 = st.columns(3)
            col_gs1.metric("Gastos en Efectivo Hoy", formato_cop(gastos_efectivo_dia))
            col_gs2.metric("Gastos por Transferencia Hoy", formato_cop(gastos_transferencia_dia))
            col_gs3.metric("Efectivo Esperado en Caja", formato_cop(efectivo_esperado),
                           help="Efectivo por ventas menos los gastos pagados en efectivo hoy. "
                                "Ve a Gastos para ver o editar el detalle.")

        ingresos_dia, costos_dia, ganancia_dia, iva_dia, gastos_dia_calc, utilidad_neta_dia = obtener_ganancia_dia(
            user_id, fecha_cierre.strftime('%Y-%m-%d')
        )
        ganancia_acumulada = obtener_ganancia_acumulada(user_id)

        col_g1, col_g2, col_g3, col_g4 = st.columns(4)
        col_g1.metric("IVA Recaudado Hoy", formato_cop(iva_dia))
        col_g2.metric("Ganancia Bruta del Día", formato_cop(ganancia_dia),
                      delta_color="inverse" if ganancia_dia < 0 else "normal",
                      help="Ingresos netos de IVA menos costo de mercancía vendida (sin restar gastos).")
        col_g3.metric("Utilidad Neta del Día", formato_cop(utilidad_neta_dia),
                      delta_color="inverse" if utilidad_neta_dia < 0 else "normal",
                      help="Ganancia bruta menos los gastos operativos registrados hoy.")
        col_g4.metric("Utilidad Neta Acumulada", formato_cop(ganancia_acumulada),
                      delta_color="inverse" if ganancia_acumulada < 0 else "normal")
        st.caption(
            f"Ingresos del día (con IVA): {formato_cop(ingresos_dia)} — "
            f"Costo de mercancía vendida: {formato_cop(costos_dia)} — "
            f"Gastos de hoy: {formato_cop(gastos_total_dia)}"
        )

        # Ganancia por producto vendido en el día
        with st.expander("Ver ganancia por producto vendido hoy", expanded=(ganancia_dia != 0)):
            df_prod = obtener_ganancia_por_producto_dia(user_id, fecha_cierre.strftime('%Y-%m-%d'))
            if not df_prod.empty:
                st.dataframe(
                    df_prod.rename(columns={
                        'producto': 'Producto', 'cantidad': 'Cant.',
                        'costo_unitario': 'Costo Unitario', 'precio_unitario': 'Precio Venta Unitario',
                        'costo_total': 'Costo Total', 'venta_total': 'Venta Total',
                        'iva_total': 'IVA', 'ganancia': 'Ganancia',
                    }),
                    use_container_width=True, hide_index=True,
                    column_config={
                        "Costo Unitario": st.column_config.NumberColumn("Costo Unitario", format="$%,d"),
                        "Precio Venta Unitario": st.column_config.NumberColumn("Precio Venta Unitario", format="$%,d"),
                        "Costo Total": st.column_config.NumberColumn("Costo Total", format="$%,d"),
                        "Venta Total": st.column_config.NumberColumn("Venta Total", format="$%,d"),
                        "IVA": st.column_config.NumberColumn("IVA", format="$%,d"),
                        "Ganancia": st.column_config.NumberColumn("Ganancia", format="$%,d"),
                    }
                )
            else:
                st.info("Sin productos vendidos en esta fecha.")

        # Ver detalle de ventas del día
        with st.expander("Ver detalle de ventas del día"):
            with engine.connect() as conn:
                df_ventas = pd.read_sql_query(text("""
                    SELECT v.id, DATE(v.fecha) as fecha,
                           COALESCE(cl.nombre, 'Directa') as cliente,
                           v.subtotal, v.descuento, v.total, v.tipo_pago, v.estado
                    FROM Ventas v
                    LEFT JOIN Clientes cl ON v.cliente_id = cl.id
                    WHERE v.usuario_id = :uid AND DATE(v.fecha) = :fecha
                    ORDER BY v.fecha DESC
                """), con=conn, params={"uid": user_id, "fecha": fecha_cierre.strftime('%Y-%m-%d')})
            if not df_ventas.empty:
                df_ventas = df_ventas.copy()
                df_ventas['id'] = df_ventas['id'].map(numeros_venta)
                st.dataframe(df_ventas.rename(columns={
                    'id': 'N°', 'fecha': 'Fecha', 'cliente': 'Cliente',
                    'subtotal': 'Subtotal', 'descuento': 'Descuento',
                    'total': 'Total', 'tipo_pago': 'Pago', 'estado': 'Estado'
                }), hide_index=True, use_container_width=True,
                column_config={
                    "Subtotal": st.column_config.NumberColumn("Subtotal", format="$%,d"),
                    "Descuento": st.column_config.NumberColumn("Descuento", format="$%,d"),
                    "Total": st.column_config.NumberColumn("Total", format="$%,d"),
                })
            else:
                st.info("Sin ventas en esta fecha.")

        st.markdown("---")
        st.subheader("Conteo de Efectivo")
        st.caption("Cuenta el dinero físico en caja y compara con el sistema.")

        col_c1, col_c2 = st.columns(2)
        with col_c1:
            efectivo_contado = st.number_input(
                "Efectivo físico contado ($)",
                min_value=0.0, step=1000.0,
                help="Cuenta los billetes y monedas en caja y escribe el total aquí."
            )
            notas_cierre = st.text_area("Notas del cierre (opcional)", height=80)

            try:
                with engine.connect() as conn:
                    cajeros_negocio = conn.execute(text("""
                        SELECT id, nombre FROM Cajeros WHERE usuario_id = :uid AND activo = 1 ORDER BY nombre ASC
                    """), {"uid": user_id}).fetchall()
            except Exception:
                cajeros_negocio = []

            OTRO_DUENO = "Otro / Dueño"
            opciones_cierre = [c[1] for c in cajeros_negocio] + [OTRO_DUENO]
            # El cierre de caja solo lo puede hacer el dueño (los cajeros no
            # llegan a esta página), así que por defecto se preselecciona "Otro / Dueño".
            idx_default = len(opciones_cierre) - 1

            if cajeros_negocio:
                cerrado_por_sel = st.selectbox("Cerrado por", options=opciones_cierre, index=idx_default)
                if cerrado_por_sel == OTRO_DUENO:
                    cerrado_por = st.text_input("Nombre de quien cierra", placeholder="Ej: nombre del dueño")
                    cajero_id_cierre = None
                else:
                    cerrado_por = cerrado_por_sel
                    cajero_id_cierre = next(c[0] for c in cajeros_negocio if c[1] == cerrado_por_sel)
            else:
                cerrado_por = st.text_input("Cerrado por", placeholder="Nombre del cajero o dueño")
                cajero_id_cierre = None

        with col_c2:
            diferencia = efectivo_contado - efectivo_esperado
            st.markdown("**Resumen del cierre:**")
            with st.container(border=True):
                st.write(f"**Efectivo por ventas:** {formato_cop(total_efectivo)}")
                if gastos_efectivo_dia > 0:
                    st.write(f"**Gastos en efectivo hoy:** −{formato_cop(gastos_efectivo_dia)}")
                st.write(f"**Efectivo esperado en caja:** {formato_cop(efectivo_esperado)}")
                st.write(f"**Efectivo físico contado:** {formato_cop(efectivo_contado)}")
                st.markdown("---")
                if diferencia == 0:
                    st.success(f"**Cuadra perfectamente: {formato_cop(diferencia)}**")
                elif diferencia > 0:
                    st.warning(f"**Sobrante: +{formato_cop(diferencia)}**")
                else:
                    st.error(f"**Faltante: {formato_cop(diferencia)}**")

        st.markdown("")
        if st.button("Registrar Cierre de Caja", type="primary", use_container_width=True):
            if total_ventas == 0 and total_efectivo == 0 and gastos_total_dia == 0:
                st.warning("No hay ventas ni gastos para cerrar en esta fecha.")
            else:
                try:
                    with engine.begin() as conn:
                        is_sqlite = "sqlite" in str(engine.url)
                        conn.execute(text("""
                            INSERT INTO Cierres_Caja
                            (usuario_id, fecha, total_ventas, total_efectivo,
                             total_transferencias, total_creditos, total_descuentos, total_gastos,
                             efectivo_contado, diferencia, notas, cerrado_por, cajero_id, fecha_cierre)
                            VALUES (:uid, :fecha, :tv, :tef, :ttr, :tcr, :tdesc, :tgas,
                                    :ec, :dif, :notas, :por, :cajero_id, :fecha_cierre)
                        """), {
                            "uid": user_id,
                            "fecha": fecha_cierre.strftime('%Y-%m-%d'),
                            "tv": total_ventas - ventas_anuladas,
                            "tef": total_efectivo,
                            "ttr": total_transferencias,
                            "tcr": total_creditos,
                            "tdesc": total_descuentos,
                            "tgas": gastos_total_dia,
                            "ec": efectivo_contado,
                            "dif": diferencia,
                            "notas": notas_cierre or None,
                            "por": cerrado_por or None,
                            "cajero_id": cajero_id_cierre,
                            "fecha_cierre": ahora_bogota_naive(),
                        })
                    st.success(f"Cierre de caja del {fecha_cierre.strftime('%d/%m/%Y')} registrado.")
                    if diferencia == 0:
                        st.balloons()
                    st.rerun()
                except Exception as e:
                    st.error(f"Error al registrar cierre: {e}")

# ==========================================
# TAB 2: HISTORIAL DE CIERRES
# ==========================================
with tab_historial:
    st.subheader("Historial de Cierres de Caja")

    col_f1, col_f2 = st.columns(2)
    with col_f1:
        hoy = hoy_bogota()
        fecha_ini_h = hoy - timedelta(days=30)
        fechas_h = st.date_input("Rango de fechas", [fecha_ini_h, hoy], key="fechas_hist_caja")
    with col_f2:
        st.write("")

    if len(fechas_h) == 2:
        f_ini, f_fin = fechas_h
        with engine.connect() as conn:
            df_hist = pd.read_sql_query(text("""
                SELECT fecha, total_ventas,
                       total_efectivo, total_transferencias, total_creditos, total_descuentos,
                       total_gastos, efectivo_contado, diferencia, cerrado_por, notas
                FROM Cierres_Caja
                WHERE usuario_id = :uid
                AND fecha >= :f_ini AND fecha <= :f_fin
                ORDER BY fecha DESC
            """), con=conn, params={
                "uid": user_id,
                "f_ini": f_ini.strftime('%Y-%m-%d'),
                "f_fin": f_fin.strftime('%Y-%m-%d')
            })
            if 'total_gastos' not in df_hist.columns:
                df_hist['total_gastos'] = 0

        if not df_hist.empty:
            # Métricas del período
            col_m1, col_m2, col_m3, col_m4, col_m5 = st.columns(5)
            col_m1.metric("Días con cierre", len(df_hist))
            col_m2.metric("Total período", formato_cop(df_hist['total_efectivo'].sum() + df_hist['total_transferencias'].sum()))
            col_m3.metric("Descuentos período", formato_cop(df_hist['total_descuentos'].sum()))
            col_m4.metric("Gastos período", formato_cop(df_hist['total_gastos'].sum()))
            col_m5.metric("Diferencias acumuladas", formato_cop(df_hist['diferencia'].sum()))

            st.markdown("---")
            st.dataframe(
                df_hist.rename(columns={
                    'fecha': 'Fecha', 'total_ventas': 'Ventas',
                    'total_efectivo': 'Efectivo Sistema',
                    'total_transferencias': 'Transferencias',
                    'total_creditos': 'Créditos',
                    'total_descuentos': 'Descuentos',
                    'total_gastos': 'Gastos',
                    'efectivo_contado': 'Efectivo Contado',
                    'diferencia': 'Diferencia',
                    'cerrado_por': 'Cerrado por',
                    'notas': 'Notas'
                }),
                use_container_width=True, hide_index=True,
                column_config={
                    "Efectivo Sistema": st.column_config.NumberColumn("Efectivo Sistema", format="$%,d"),
                    "Transferencias": st.column_config.NumberColumn("Transferencias", format="$%,d"),
                    "Créditos": st.column_config.NumberColumn("Créditos", format="$%,d"),
                    "Descuentos": st.column_config.NumberColumn("Descuentos", format="$%,d"),
                    "Gastos": st.column_config.NumberColumn("Gastos", format="$%,d"),
                    "Efectivo Contado": st.column_config.NumberColumn("Efectivo Contado", format="$%,d"),
                    "Diferencia": st.column_config.NumberColumn("Diferencia", format="$%,d"),
                }
            )
        else:
            st.info("No hay cierres de caja en este período.")

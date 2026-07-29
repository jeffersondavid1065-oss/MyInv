import streamlit as st
import pandas as pd
from sqlalchemy import text
from db import obtener_conexion
from datetime import datetime, date


# ==========================================
# MÉTRICAS DEL DASHBOARD
# ==========================================
@st.cache_data(ttl=30)
def obtener_metricas_dashboard(uid):
    """
    Métricas principales del dashboard en una sola query.
    - Ventas de hoy (cantidad y total)
    - Caja del día (efectivo)
    - Productos agotados
    - Créditos vencidos
    - Clientes con deuda activa
    """
    engine = obtener_conexion()
    hoy = date.today().strftime('%Y-%m-%d')
    with engine.connect() as conn:
        ventas_hoy = conn.execute(text("""
            SELECT 
                COUNT(*) as cantidad,
                COALESCE(SUM(total), 0) as total_ventas,
                COALESCE(SUM(monto_efectivo), 0) as caja_efectivo,
                COALESCE(SUM(monto_transferencia), 0) as total_transferencia
            FROM Ventas
            WHERE usuario_id = :uid
            AND estado != 'Anulada'
            AND DATE(fecha) = :hoy
        """), {"uid": uid, "hoy": hoy}).fetchone()

        agotados = conn.execute(text("""
            SELECT COUNT(*) FROM Productos
            WHERE usuario_id = :uid AND stock_actual <= 0 AND activo = TRUE
        """), {"uid": uid}).scalar()

        por_agotarse = conn.execute(text("""
            SELECT COUNT(*) FROM Productos
            WHERE usuario_id = :uid 
            AND stock_actual > 0 
            AND stock_actual <= stock_minimo 
            AND activo = TRUE
        """), {"uid": uid}).scalar()

        creditos_vencidos = conn.execute(text("""
            SELECT COUNT(*) FROM Creditos
            WHERE usuario_id = :uid
            AND estado = 'Activo'
            AND fecha_limite < :hoy
        """), {"uid": uid, "hoy": hoy}).scalar()

        total_deuda = conn.execute(text("""
            SELECT COALESCE(SUM(saldo_pendiente), 0)
            FROM Creditos
            WHERE usuario_id = :uid AND estado = 'Activo'
        """), {"uid": uid}).scalar()

    return {
        "ventas_hoy": int(ventas_hoy[0]) if ventas_hoy else 0,
        "total_ventas_hoy": float(ventas_hoy[1]) if ventas_hoy else 0,
        "caja_efectivo": float(ventas_hoy[2]) if ventas_hoy else 0,
        "total_transferencia": float(ventas_hoy[3]) if ventas_hoy else 0,
        "agotados": int(agotados) if agotados else 0,
        "por_agotarse": int(por_agotarse) if por_agotarse else 0,
        "creditos_vencidos": int(creditos_vencidos) if creditos_vencidos else 0,
        "total_deuda": float(total_deuda) if total_deuda else 0,
    }


# ==========================================
# PRODUCTOS
# ==========================================
@st.cache_data(ttl=60)
def obtener_productos_activos(uid):
    """Lista de productos con stock > 0 para el POS."""
    engine = obtener_conexion()
    with engine.connect() as conn:
        return pd.read_sql_query(
            text("""
                SELECT id, nombre, codigo_barras, codigo_ref, categoria,
                       stock_actual, precio_venta, costo_compra, stock_minimo
                FROM Productos
                WHERE usuario_id = :uid AND activo = TRUE AND stock_actual > 0
                ORDER BY nombre ASC
            """),
            con=conn, params={"uid": uid}
        )


@st.cache_data(ttl=60)
def obtener_todos_productos(uid):
    """Lista completa de productos para inventario."""
    engine = obtener_conexion()
    with engine.connect() as conn:
        return pd.read_sql_query(
            text("""
                SELECT id, nombre, descripcion, codigo_barras, codigo_ref,
                       categoria, stock_actual, stock_minimo,
                       costo_compra, precio_venta, activo
                FROM Productos
                WHERE usuario_id = :uid
                ORDER BY nombre ASC
            """),
            con=conn, params={"uid": uid}
        )


@st.cache_data(ttl=30)
def buscar_producto_por_codigo(uid, codigo):
    """Busca un producto por código de barras o referencia (para lector)."""
    engine = obtener_conexion()
    with engine.connect() as conn:
        resultado = conn.execute(text("""
            SELECT id, nombre, codigo_barras, stock_actual, precio_venta, costo_compra
            FROM Productos
            WHERE usuario_id = :uid
            AND activo = TRUE
            AND (codigo_barras = :codigo OR codigo_ref = :codigo)
            LIMIT 1
        """), {"uid": uid, "codigo": codigo}).fetchone()
    return resultado


@st.cache_data(ttl=60)
def obtener_metricas_inventario(uid):
    """Métricas agregadas del inventario en una sola query."""
    engine = obtener_conexion()
    with engine.connect() as conn:
        row = conn.execute(text("""
            SELECT
                COALESCE(SUM(stock_actual * costo_compra), 0) as valor_costo,
                COALESCE(SUM(stock_actual * precio_venta), 0) as valor_venta,
                COUNT(*) as total_productos,
                COALESCE(SUM(CASE WHEN stock_actual <= 0 THEN 1 ELSE 0 END), 0) as agotados,
                COALESCE(SUM(CASE WHEN stock_actual > 0 AND stock_actual <= stock_minimo THEN 1 ELSE 0 END), 0) as por_agotarse
            FROM Productos
            WHERE usuario_id = :uid AND activo = TRUE
        """), {"uid": uid}).fetchone()
    return row


# ==========================================
# CLIENTES
# ==========================================
@st.cache_data(ttl=60)
def obtener_clientes(uid):
    """Lista de clientes activos."""
    engine = obtener_conexion()
    with engine.connect() as conn:
        return conn.execute(text("""
            SELECT id, nombre, telefono, documento, cupo_credito
            FROM Clientes
            WHERE usuario_id = :uid AND activo = TRUE
            ORDER BY nombre ASC
        """), {"uid": uid}).fetchall()


@st.cache_data(ttl=30)
def obtener_creditos_cliente(uid, cliente_id):
    """Créditos activos de un cliente específico."""
    engine = obtener_conexion()
    with engine.connect() as conn:
        return pd.read_sql_query(text("""
            SELECT c.id, v.id as venta_id, c.total, c.saldo_pendiente,
                   c.fecha_inicio, c.fecha_limite, c.tipo_cuota,
                   c.valor_cuota, c.estado
            FROM Creditos c
            JOIN Ventas v ON c.venta_id = v.id
            WHERE c.usuario_id = :uid AND c.cliente_id = :cid
            ORDER BY c.fecha_inicio DESC
        """), con=conn, params={"uid": uid, "cid": cliente_id})


@st.cache_data(ttl=30)
def obtener_creditos_pendientes(uid):
    """Todos los créditos activos con info del cliente."""
    engine = obtener_conexion()
    with engine.connect() as conn:
        return pd.read_sql_query(text("""
            SELECT cr.id, cl.nombre as cliente, cl.telefono,
                   cr.total, cr.saldo_pendiente,
                   cr.fecha_limite, cr.tipo_cuota, cr.estado,
                   CASE WHEN cr.fecha_limite < CURRENT_DATE THEN TRUE ELSE FALSE END as vencido
            FROM Creditos cr
            JOIN Clientes cl ON cr.cliente_id = cl.id
            WHERE cr.usuario_id = :uid AND cr.estado = 'Activo'
            ORDER BY cr.fecha_limite ASC
        """), con=conn, params={"uid": uid})


# ==========================================
# PROVEEDORES
# ==========================================
@st.cache_data(ttl=120)
def obtener_proveedores(uid):
    """Lista de proveedores activos."""
    engine = obtener_conexion()
    with engine.connect() as conn:
        return conn.execute(text("""
            SELECT id, nombre, telefono, nit
            FROM Proveedores
            WHERE usuario_id = :uid AND activo = TRUE
            ORDER BY nombre ASC
        """), {"uid": uid}).fetchall()


# ==========================================
# VENTAS Y REPORTES
# ==========================================
@st.cache_data(ttl=30)
def obtener_ventas_periodo(uid, fecha_inicio, fecha_fin):
    """Ventas en un rango de fechas para reportes."""
    engine = obtener_conexion()
    with engine.connect() as conn:
        return pd.read_sql_query(text("""
            SELECT v.id, DATE(v.fecha) as fecha, cl.nombre as cliente,
                   v.total, v.tipo_pago, v.estado,
                   v.monto_efectivo, v.monto_transferencia, v.cambio
            FROM Ventas v
            LEFT JOIN Clientes cl ON v.cliente_id = cl.id
            WHERE v.usuario_id = :uid
            AND DATE(v.fecha) >= :f_ini AND DATE(v.fecha) <= :f_fin
            AND v.estado != 'Anulada'
            ORDER BY v.fecha DESC
        """), con=conn, params={
            "uid": uid,
            "f_ini": fecha_inicio.strftime('%Y-%m-%d'),
            "f_fin": fecha_fin.strftime('%Y-%m-%d')
        })


@st.cache_data(ttl=30)
def obtener_top_productos(uid, fecha_inicio, fecha_fin, limite=10):
    """Top productos más vendidos en un período."""
    engine = obtener_conexion()
    with engine.connect() as conn:
        return pd.read_sql_query(text("""
            SELECT dv.nombre_producto,
                   SUM(dv.cantidad) as unidades_vendidas,
                   SUM(dv.subtotal) as total_vendido
            FROM Detalles_Venta dv
            JOIN Ventas v ON dv.venta_id = v.id
            WHERE v.usuario_id = :uid
            AND DATE(v.fecha) >= :f_ini AND DATE(v.fecha) <= :f_fin
            AND v.estado != 'Anulada'
            GROUP BY dv.nombre_producto
            ORDER BY unidades_vendidas DESC
            LIMIT :limite
        """), con=conn, params={
            "uid": uid,
            "f_ini": fecha_inicio.strftime('%Y-%m-%d'),
            "f_fin": fecha_fin.strftime('%Y-%m-%d'),
            "limite": limite
        })


@st.cache_data(ttl=60)
def obtener_metricas_mes(uid, año, mes):
    """Ingresos, costos y margen del mes."""
    engine = obtener_conexion()
    with engine.connect() as conn:
        ingresos = conn.execute(text("""
            SELECT COALESCE(SUM(total), 0)
            FROM Ventas
            WHERE usuario_id = :uid
            AND estado != 'Anulada'
            AND EXTRACT(YEAR FROM fecha) = :año
            AND EXTRACT(MONTH FROM fecha) = :mes
        """), {"uid": uid, "año": año, "mes": mes}).scalar()

        costos = conn.execute(text("""
            SELECT COALESCE(SUM(dv.costo_unitario * dv.cantidad), 0)
            FROM Detalles_Venta dv
            JOIN Ventas v ON dv.venta_id = v.id
            WHERE v.usuario_id = :uid
            AND v.estado != 'Anulada'
            AND EXTRACT(YEAR FROM v.fecha) = :año
            AND EXTRACT(MONTH FROM v.fecha) = :mes
        """), {"uid": uid, "año": año, "mes": mes}).scalar()

    ingresos = float(ingresos) if ingresos else 0
    costos = float(costos) if costos else 0
    return ingresos, costos, ingresos - costos


# ==========================================
# FUNCIONES DE INVALIDACIÓN DE CACHE
# ==========================================
def invalidar_cache_productos():
    """Llamar después de crear, editar o eliminar un producto."""
    obtener_productos_activos.clear()
    obtener_todos_productos.clear()
    obtener_metricas_inventario.clear()
    obtener_metricas_dashboard.clear()
    buscar_producto_por_codigo.clear()


def invalidar_cache_ventas():
    """Llamar después de registrar o anular una venta."""
    obtener_metricas_dashboard.clear()
    obtener_ventas_periodo.clear()
    obtener_top_productos.clear()
    obtener_metricas_mes.clear()
    obtener_productos_activos.clear()
    obtener_metricas_inventario.clear()


def invalidar_cache_clientes():
    """Llamar después de crear o editar un cliente."""
    obtener_clientes.clear()


def invalidar_cache_creditos():
    """Llamar después de registrar un crédito o abono."""
    obtener_creditos_cliente.clear()
    obtener_creditos_pendientes.clear()
    obtener_metricas_dashboard.clear()


def invalidar_cache_proveedores():
    """Llamar después de crear o editar un proveedor."""
    obtener_proveedores.clear()

import calendar
import streamlit as st
import pandas as pd
from sqlalchemy import text
from db import obtener_conexion
from datetime import datetime, date
from tz_utils import hoy_bogota


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
    hoy = hoy_bogota().strftime('%Y-%m-%d')
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
                       stock_actual, stock_minimo, precio_venta, costo_compra,
                       iva_porcentaje
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
                       categoria, unidad_medida, stock_actual, stock_minimo,
                       costo_compra, precio_venta, activo, iva_porcentaje
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
            SELECT id, nombre, codigo_barras, stock_actual, precio_venta, costo_compra,
                   iva_porcentaje
            FROM Productos
            WHERE usuario_id = :uid
            AND activo = TRUE
            AND (codigo_barras = :codigo OR codigo_ref = :codigo)
            LIMIT 1
        """), {"uid": uid, "codigo": codigo}).fetchone()
    return resultado


def obtener_datos_facturacion_producto(uid, producto_id):
    """Datos del producto necesarios para facturar electrónicamente (sin cache, siempre al día)."""
    engine = obtener_conexion()
    with engine.connect() as conn:
        return conn.execute(text("""
            SELECT id, nombre, precio_venta, alegra_item_id
            FROM Productos
            WHERE usuario_id = :uid AND id = :producto_id
        """), {"uid": uid, "producto_id": producto_id}).fetchone()


def guardar_alegra_item_id(producto_id, alegra_item_id):
    """Guarda el id de ítem en Alegra la primera vez que se crea, para reutilizarlo después."""
    engine = obtener_conexion()
    with engine.begin() as conn:
        conn.execute(text("""
            UPDATE Productos SET alegra_item_id = :alegra_id WHERE id = :producto_id
        """), {"alegra_id": alegra_item_id, "producto_id": producto_id})
    invalidar_cache_productos()


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


def obtener_datos_facturacion_cliente(uid, cliente_id):
    """Datos del cliente necesarios para facturar electrónicamente (sin cache, siempre al día)."""
    engine = obtener_conexion()
    with engine.connect() as conn:
        return conn.execute(text("""
            SELECT id, nombre, documento, tipo_documento, email, alegra_contact_id
            FROM Clientes
            WHERE usuario_id = :uid AND id = :cliente_id
        """), {"uid": uid, "cliente_id": cliente_id}).fetchone()


def guardar_alegra_contact_id(cliente_id, alegra_contact_id):
    """Guarda el id de contacto en Alegra la primera vez que se crea, para reutilizarlo después."""
    engine = obtener_conexion()
    with engine.begin() as conn:
        conn.execute(text("""
            UPDATE Clientes SET alegra_contact_id = :alegra_id WHERE id = :cliente_id
        """), {"alegra_id": alegra_contact_id, "cliente_id": cliente_id})
    invalidar_cache_clientes()


@st.cache_data(ttl=30)
def obtener_creditos_cliente(uid, cliente_id):
    """Créditos activos de un cliente específico."""
    engine = obtener_conexion()
    with engine.connect() as conn:
        return pd.read_sql_query(text("""
            SELECT c.id, v.id as venta_id, c.total, c.saldo_pendiente,
                   c.fecha_inicio, c.fecha_limite, c.tipo_cuota,
                   c.valor_cuota, c.estado, v.factura_estado, v.factura_pdf_url, v.factura_xml_url,
                   v.factura_prefijo, v.factura_numero
            FROM Creditos c
            JOIN Ventas v ON c.venta_id = v.id
            WHERE c.usuario_id = :uid AND c.cliente_id = :cid
            ORDER BY c.fecha_inicio DESC
        """), con=conn, params={"uid": uid, "cid": cliente_id})


def obtener_historial_ventas_cliente(uid, cliente_id):
    """Historial completo de compras de un cliente (todas sus ventas, con su
    estado de facturación electrónica), para el estado de cuenta."""
    engine = obtener_conexion()
    with engine.connect() as conn:
        return pd.read_sql_query(text("""
            SELECT v.id, v.fecha, v.total, v.tipo_pago, v.estado,
                   v.factura_estado, v.factura_prefijo, v.factura_numero,
                   v.factura_pdf_url, v.factura_xml_url,
                   v.nota_credito_alegra_id, v.nota_credito_pdf_url, v.nota_credito_xml_url
            FROM Ventas v
            WHERE v.usuario_id = :uid AND v.cliente_id = :cid
            ORDER BY v.fecha DESC
        """), con=conn, params={"uid": uid, "cid": cliente_id})


def obtener_historial_abonos_cliente(uid, cliente_id):
    """Historial completo de abonos (pagos) de un cliente, sobre todos sus créditos,
    para el estado de cuenta."""
    engine = obtener_conexion()
    with engine.connect() as conn:
        return pd.read_sql_query(text("""
            SELECT a.fecha, a.monto, a.notas, a.credito_id, cr.venta_id
            FROM Abonos a
            JOIN Creditos cr ON a.credito_id = cr.id
            WHERE a.usuario_id = :uid AND cr.cliente_id = :cid
            ORDER BY a.fecha DESC
        """), con=conn, params={"uid": uid, "cid": cliente_id})


@st.cache_data(ttl=30)
def obtener_creditos_pendientes(uid):
    """Todos los créditos activos con info del cliente."""
    engine = obtener_conexion()
    with engine.connect() as conn:
        return pd.read_sql_query(text("""
            SELECT cr.id, cr.venta_id, cl.nombre as cliente, cl.telefono,
                   cr.total, cr.saldo_pendiente,
                   cr.fecha_limite, cr.tipo_cuota, cr.estado,
                   CASE WHEN cr.fecha_limite < :hoy THEN TRUE ELSE FALSE END as vencido,
                   v.factura_estado, v.factura_prefijo, v.factura_numero,
                   v.factura_pdf_url, v.factura_xml_url
            FROM Creditos cr
            JOIN Clientes cl ON cr.cliente_id = cl.id
            JOIN Ventas v ON cr.venta_id = v.id
            WHERE cr.usuario_id = :uid AND cr.estado = 'Activo'
            ORDER BY cr.fecha_limite ASC
        """), con=conn, params={"uid": uid, "hoy": hoy_bogota().strftime('%Y-%m-%d')})


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
                   v.subtotal, v.descuento, v.total, v.tipo_pago, v.estado,
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
    f_ini = date(año, mes, 1).strftime('%Y-%m-%d')
    f_fin = date(año, mes, calendar.monthrange(año, mes)[1]).strftime('%Y-%m-%d')
    with engine.connect() as conn:
        ingresos = conn.execute(text("""
            SELECT COALESCE(SUM(total), 0)
            FROM Ventas
            WHERE usuario_id = :uid
            AND estado != 'Anulada'
            AND DATE(fecha) >= :f_ini AND DATE(fecha) <= :f_fin
        """), {"uid": uid, "f_ini": f_ini, "f_fin": f_fin}).scalar()

        costos = conn.execute(text("""
            SELECT COALESCE(SUM(dv.costo_unitario * dv.cantidad), 0)
            FROM Detalles_Venta dv
            JOIN Ventas v ON dv.venta_id = v.id
            WHERE v.usuario_id = :uid
            AND v.estado != 'Anulada'
            AND DATE(v.fecha) >= :f_ini AND DATE(v.fecha) <= :f_fin
        """), {"uid": uid, "f_ini": f_ini, "f_fin": f_fin}).scalar()

    ingresos = float(ingresos) if ingresos else 0
    costos = float(costos) if costos else 0
    return ingresos, costos, ingresos - costos


@st.cache_data(ttl=60)
def obtener_ganancia_dia(uid, fecha):
    """Ingresos, costos y ganancia de un día específico (para Cierre de Caja)."""
    engine = obtener_conexion()
    with engine.connect() as conn:
        ingresos = conn.execute(text("""
            SELECT COALESCE(SUM(total), 0)
            FROM Ventas
            WHERE usuario_id = :uid
            AND estado != 'Anulada'
            AND DATE(fecha) = :fecha
        """), {"uid": uid, "fecha": fecha}).scalar()

        costos = conn.execute(text("""
            SELECT COALESCE(SUM(dv.costo_unitario * dv.cantidad), 0)
            FROM Detalles_Venta dv
            JOIN Ventas v ON dv.venta_id = v.id
            WHERE v.usuario_id = :uid
            AND v.estado != 'Anulada'
            AND DATE(v.fecha) = :fecha
        """), {"uid": uid, "fecha": fecha}).scalar()

    ingresos = float(ingresos) if ingresos else 0
    costos = float(costos) if costos else 0
    return ingresos, costos, ingresos - costos


@st.cache_data(ttl=60)
def obtener_ganancia_por_producto_dia(uid, fecha):
    """Ganancia detallada por producto vendido en una fecha (para Cierre de Caja)."""
    engine = obtener_conexion()
    with engine.connect() as conn:
        df = pd.read_sql_query(text("""
            SELECT dv.nombre_producto AS producto,
                   SUM(dv.cantidad) AS cantidad,
                   SUM(dv.costo_unitario * dv.cantidad) / SUM(dv.cantidad) AS costo_unitario,
                   SUM(dv.subtotal) / SUM(dv.cantidad) AS precio_unitario,
                   SUM(dv.costo_unitario * dv.cantidad) AS costo_total,
                   SUM(dv.subtotal) AS venta_total,
                   SUM(dv.subtotal) - SUM(dv.costo_unitario * dv.cantidad) AS ganancia
            FROM Detalles_Venta dv
            JOIN Ventas v ON dv.venta_id = v.id
            WHERE v.usuario_id = :uid
            AND v.estado != 'Anulada'
            AND DATE(v.fecha) = :fecha
            GROUP BY dv.nombre_producto
            ORDER BY ganancia DESC
        """), con=conn, params={"uid": uid, "fecha": fecha})
    return df


@st.cache_data(ttl=120)
def obtener_ganancia_acumulada(uid):
    """Ganancia bruta acumulada histórica: ingresos - costos de todas las ventas no anuladas."""
    engine = obtener_conexion()
    with engine.connect() as conn:
        ingresos = conn.execute(text("""
            SELECT COALESCE(SUM(total), 0)
            FROM Ventas
            WHERE usuario_id = :uid AND estado != 'Anulada'
        """), {"uid": uid}).scalar()

        costos = conn.execute(text("""
            SELECT COALESCE(SUM(dv.costo_unitario * dv.cantidad), 0)
            FROM Detalles_Venta dv
            JOIN Ventas v ON dv.venta_id = v.id
            WHERE v.usuario_id = :uid AND v.estado != 'Anulada'
        """), {"uid": uid}).scalar()

    ingresos = float(ingresos) if ingresos else 0
    costos = float(costos) if costos else 0
    return ingresos - costos


# ==========================================
# FACTURACIÓN ELECTRÓNICA
# ==========================================
def obtener_credenciales_alegra(uid):
    """Credenciales de Alegra configuradas por este negocio (o None si no ha configurado nada)."""
    engine = obtener_conexion()
    with engine.connect() as conn:
        return conn.execute(text("""
            SELECT alegra_email, alegra_token
            FROM Usuarios WHERE id = :uid
        """), {"uid": uid}).fetchone()


def guardar_credenciales_alegra(uid, email, token):
    """Guarda (o actualiza) las credenciales de Alegra de este negocio."""
    engine = obtener_conexion()
    with engine.begin() as conn:
        conn.execute(text("""
            UPDATE Usuarios SET alegra_email = :email, alegra_token = :token WHERE id = :uid
        """), {"email": email, "token": token, "uid": uid})


def eliminar_credenciales_alegra(uid):
    """Desconecta la cuenta de Alegra de este negocio."""
    engine = obtener_conexion()
    with engine.begin() as conn:
        conn.execute(text("""
            UPDATE Usuarios SET alegra_email = NULL, alegra_token = NULL WHERE id = :uid
        """), {"uid": uid})


def obtener_venta_id_de_credito(credito_id):
    """Devuelve el venta_id ligado a un crédito, para poder sincronizar abonos con Alegra."""
    engine = obtener_conexion()
    with engine.connect() as conn:
        return conn.execute(text("""
            SELECT venta_id FROM Creditos WHERE id = :cid
        """), {"cid": credito_id}).scalar()


@st.cache_data(ttl=30)
def obtener_facturas_periodo(uid, fecha_inicio, fecha_fin):
    """Ventas del período con su estado de facturación electrónica, para el reporte
    y para el historial de ventas. Incluye ventas anuladas (a diferencia del reporte
    de ventas normal) porque interesa ver si a una venta anulada ya se le emitió la
    nota crédito o no."""
    engine = obtener_conexion()
    with engine.connect() as conn:
        return pd.read_sql_query(text("""
            SELECT v.id, v.fecha, COALESCE(cl.nombre, 'Sin cliente') as cliente,
                   cl.documento as cliente_documento,
                   v.total, v.tipo_pago, v.estado as estado_venta, v.factura_estado, v.factura_alegra_id,
                   v.factura_prefijo, v.factura_numero,
                   v.factura_cufe, v.factura_pdf_url, v.factura_xml_url,
                   v.nota_credito_alegra_id, v.nota_credito_pdf_url, v.nota_credito_xml_url
            FROM Ventas v
            LEFT JOIN Clientes cl ON v.cliente_id = cl.id
            WHERE v.usuario_id = :uid
            AND DATE(v.fecha) >= :f_ini AND DATE(v.fecha) <= :f_fin
            ORDER BY v.fecha DESC
        """), con=conn, params={
            "uid": uid,
            "f_ini": fecha_inicio.strftime('%Y-%m-%d'),
            "f_fin": fecha_fin.strftime('%Y-%m-%d')
        })


def obtener_venta_para_facturar(uid, venta_id):
    """Datos base de una venta necesarios para emitirla como factura electrónica."""
    engine = obtener_conexion()
    with engine.connect() as conn:
        return conn.execute(text("""
            SELECT id, cliente_id, total, tipo_pago, factura_alegra_id,
                   factura_estado, nota_credito_alegra_id,
                   factura_cufe, factura_pdf_url, factura_xml_url,
                   nota_credito_pdf_url, nota_credito_xml_url
            FROM Ventas
            WHERE id = :vid AND usuario_id = :uid
        """), {"vid": venta_id, "uid": uid}).fetchone()


def obtener_credito_de_venta(venta_id):
    """Términos de crédito (fecha límite, periodicidad) ligados a una venta a crédito."""
    engine = obtener_conexion()
    with engine.connect() as conn:
        return conn.execute(text("""
            SELECT fecha_limite, tipo_cuota
            FROM Creditos
            WHERE venta_id = :vid
        """), {"vid": venta_id}).fetchone()


def guardar_nota_credito(venta_id, nota_credito_alegra_id, pdf_url=None, xml_url=None):
    """Guarda el id (y PDF/XML) de la nota crédito emitida en Alegra para una venta anulada."""
    engine = obtener_conexion()
    with engine.begin() as conn:
        conn.execute(text("""
            UPDATE Ventas SET nota_credito_alegra_id = :ncid, nota_credito_pdf_url = :pdf_url,
                   nota_credito_xml_url = :xml_url, factura_estado = 'anulada'
            WHERE id = :vid
        """), {"ncid": nota_credito_alegra_id, "pdf_url": pdf_url, "xml_url": xml_url, "vid": venta_id})
    invalidar_cache_ventas()


def obtener_items_venta(venta_id):
    """Renglones de una venta (producto, cantidad, precio, descuento, IVA) para armar la factura."""
    engine = obtener_conexion()
    with engine.connect() as conn:
        return conn.execute(text("""
            SELECT producto_id, nombre_producto, cantidad, precio_unitario,
                   descuento, iva_porcentaje
            FROM Detalles_Venta
            WHERE venta_id = :vid
        """), {"vid": venta_id}).fetchall()


def guardar_resultado_factura(venta_id, alegra_id=None, cufe=None, pdf_url=None, xml_url=None,
                               estado="emitida", prefijo=None, numero=None):
    """Guarda el resultado de emitir (o intentar emitir) la factura electrónica de una venta."""
    engine = obtener_conexion()
    with engine.begin() as conn:
        conn.execute(text("""
            UPDATE Ventas
            SET factura_alegra_id = :alegra_id, factura_cufe = :cufe,
                factura_pdf_url = :pdf_url, factura_xml_url = :xml_url, factura_estado = :estado,
                factura_prefijo = :prefijo, factura_numero = :numero
            WHERE id = :vid
        """), {
            "alegra_id": alegra_id, "cufe": cufe, "pdf_url": pdf_url, "xml_url": xml_url,
            "estado": estado, "prefijo": prefijo, "numero": numero, "vid": venta_id
        })
    invalidar_cache_ventas()


def actualizar_datos_factura(venta_id, cufe=None, pdf_url=None, xml_url=None, prefijo=None, numero=None):
    """Completa CUFE/PDF/XML/prefijo/número de una factura ya emitida sin pisar lo que ya
    estaba guardado. Se usa al refrescar facturas cuya validación DIAN no estuvo lista
    al momento de emitirlas."""
    engine = obtener_conexion()
    with engine.begin() as conn:
        conn.execute(text("""
            UPDATE Ventas
            SET factura_cufe = COALESCE(:cufe, factura_cufe),
                factura_pdf_url = COALESCE(:pdf_url, factura_pdf_url),
                factura_xml_url = COALESCE(:xml_url, factura_xml_url),
                factura_prefijo = COALESCE(:prefijo, factura_prefijo),
                factura_numero = COALESCE(:numero, factura_numero)
            WHERE id = :vid
        """), {
            "cufe": cufe, "pdf_url": pdf_url, "xml_url": xml_url,
            "prefijo": prefijo, "numero": numero, "vid": venta_id
        })
    invalidar_cache_ventas()


def actualizar_pdf_nota_credito(venta_id, pdf_url, xml_url=None):
    """Completa el PDF/XML de una nota crédito ya emitida, cuando no llegaron en la respuesta de creación."""
    engine = obtener_conexion()
    with engine.begin() as conn:
        conn.execute(text("""
            UPDATE Ventas
            SET nota_credito_pdf_url = COALESCE(:pdf_url, nota_credito_pdf_url),
                nota_credito_xml_url = COALESCE(:xml_url, nota_credito_xml_url)
            WHERE id = :vid
        """), {"pdf_url": pdf_url, "xml_url": xml_url, "vid": venta_id})
    invalidar_cache_ventas()


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
    obtener_ganancia_dia.clear()
    obtener_ganancia_por_producto_dia.clear()
    obtener_ganancia_acumulada.clear()
    obtener_facturas_periodo.clear()


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

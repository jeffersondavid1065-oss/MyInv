import calendar
import streamlit as st
import pandas as pd
from sqlalchemy import text
from db import obtener_conexion
from datetime import datetime, date
from tz_utils import hoy_bogota
from iva_utils import calcular_iva_desde_detalles, texto_tasa_iva


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

        facturas_abiertas = conn.execute(text("""
            SELECT COUNT(*) FROM Ventas
            WHERE usuario_id = :uid AND factura_estado = 'abierta'
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
        "facturas_abiertas": int(facturas_abiertas) if facturas_abiertas else 0,
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
            SELECT id, nombre, documento, tipo_documento, email, regimen, digito_verificacion
            FROM Clientes
            WHERE usuario_id = :uid AND id = :cliente_id
        """), {"uid": uid, "cliente_id": cliente_id}).fetchone()


@st.cache_data(ttl=30)
def obtener_creditos_cliente(uid, cliente_id):
    """Créditos activos de un cliente específico."""
    engine = obtener_conexion()
    with engine.connect() as conn:
        return pd.read_sql_query(text("""
            SELECT c.id, v.id as venta_id, c.total, c.saldo_pendiente,
                   c.fecha_inicio, c.fecha_limite, c.tipo_cuota,
                   c.valor_cuota, c.estado, v.factura_estado, v.factura_alegra_id,
                   v.factura_pdf_url, v.factura_xml_url,
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
                   v.factura_estado, v.factura_alegra_id, v.factura_prefijo, v.factura_numero,
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
                   v.factura_estado, v.factura_alegra_id, v.factura_prefijo, v.factura_numero,
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
def _iva_recaudado_rango(uid, f_ini=None, f_fin=None):
    """
    IVA recaudado (ya incluido en los precios) de las ventas no anuladas de un
    negocio, opcionalmente acotado a un rango de fechas (f_ini/f_fin como
    strings 'YYYY-MM-DD'; None = sin límite por ese lado). Se calcula por
    venta (respetando el descuento global de cada una, igual que en el
    ticket) y se suma, en vez de sacarlo directo del subtotal de los
    renglones, para no sobrestimarlo en ventas con descuento.
    """
    try:
        engine = obtener_conexion()
        filtro_fecha_v = ""
        filtro_fecha_d = ""
        params = {"uid": uid}
        if f_ini:
            filtro_fecha_v += " AND DATE(fecha) >= :f_ini"
            filtro_fecha_d += " AND DATE(v.fecha) >= :f_ini"
            params["f_ini"] = f_ini
        if f_fin:
            filtro_fecha_v += " AND DATE(fecha) <= :f_fin"
            filtro_fecha_d += " AND DATE(v.fecha) <= :f_fin"
            params["f_fin"] = f_fin

        with engine.connect() as conn:
            df_ventas = pd.read_sql_query(text(f"""
                SELECT id, total FROM Ventas
                WHERE usuario_id = :uid AND estado != 'Anulada' {filtro_fecha_v}
            """), con=conn, params=params)

            if df_ventas.empty:
                return 0.0

            df_det = pd.read_sql_query(text(f"""
                SELECT dv.venta_id, dv.subtotal, dv.iva_porcentaje
                FROM Detalles_Venta dv
                JOIN Ventas v ON dv.venta_id = v.id
                WHERE v.usuario_id = :uid AND v.estado != 'Anulada' {filtro_fecha_d}
            """), con=conn, params=params)

        if df_det.empty:
            return 0.0

        totales_por_venta = dict(zip(df_ventas['id'], df_ventas['total']))
        iva_total = 0.0
        for vid, grupo in df_det.groupby('venta_id'):
            total_venta = float(totales_por_venta.get(vid, 0) or 0)
            _, iva_valor = calcular_iva_desde_detalles(grupo, total_venta)
            iva_total += iva_valor
        return iva_total
    except Exception as e:
        # Nunca debe tumbar Dashboard/Cierre de Caja/Reportes por un problema
        # transitorio calculando IVA (ej. migración de columna todavía no
        # corrida en este proceso) - se degrada a 0 y queda en los logs.
        print(f"[_iva_recaudado_rango] Error: {e}")
        return 0.0


@st.cache_data(ttl=30)
def obtener_iva_periodo(uid, fecha_inicio, fecha_fin):
    """IVA recaudado (ya incluido en los precios) de las ventas no anuladas de un período."""
    return _iva_recaudado_rango(uid, fecha_inicio.strftime('%Y-%m-%d'), fecha_fin.strftime('%Y-%m-%d'))


@st.cache_data(ttl=30)
def obtener_iva_por_tasa_periodo(uid, fecha_inicio, fecha_fin):
    """
    Desglose del IVA generado en ventas no anuladas de un período, agrupado
    por tasa (base gravable + IVA de cada una). Sirve como insumo para la
    casilla de IVA Generado de la Declaración de IVA (Formulario 300) - no
    incluye IVA descontable de compras, que MyInv todavía no registra.
    Prorratea el descuento global de cada venta antes de separar base/IVA,
    igual que calcular_iva_desde_detalles.
    """
    vacio = pd.DataFrame(columns=['tasa', 'base_gravable', 'iva'])
    try:
        engine = obtener_conexion()
        f_ini = fecha_inicio.strftime('%Y-%m-%d')
        f_fin = fecha_fin.strftime('%Y-%m-%d')
        with engine.connect() as conn:
            df_ventas = pd.read_sql_query(text("""
                SELECT id, total FROM Ventas
                WHERE usuario_id = :uid AND estado != 'Anulada'
                AND DATE(fecha) >= :f_ini AND DATE(fecha) <= :f_fin
            """), con=conn, params={"uid": uid, "f_ini": f_ini, "f_fin": f_fin})

            if df_ventas.empty:
                return vacio

            df_det = pd.read_sql_query(text("""
                SELECT dv.venta_id, dv.subtotal, dv.iva_porcentaje
                FROM Detalles_Venta dv
                JOIN Ventas v ON dv.venta_id = v.id
                WHERE v.usuario_id = :uid AND v.estado != 'Anulada'
                AND DATE(v.fecha) >= :f_ini AND DATE(v.fecha) <= :f_fin
            """), con=conn, params={"uid": uid, "f_ini": f_ini, "f_fin": f_fin})

        if df_det.empty:
            return vacio

        totales_por_venta = dict(zip(df_ventas['id'], df_ventas['total']))
        resultado = {}
        for vid, grupo in df_det.groupby('venta_id'):
            total_venta = float(totales_por_venta.get(vid, 0) or 0)
            suma_lineas = float(grupo['subtotal'].sum())
            factor = (total_venta / suma_lineas) if suma_lineas else 1.0
            for _, row in grupo.iterrows():
                pct = float(row['iva_porcentaje'] or 0)
                linea = float(row['subtotal'] or 0) * factor
                base = linea / (1 + pct / 100) if pct else linea
                iva_linea = linea - base
                if pct not in resultado:
                    resultado[pct] = {'base_gravable': 0.0, 'iva': 0.0}
                resultado[pct]['base_gravable'] += base
                resultado[pct]['iva'] += iva_linea

        filas = [
            {'tasa': tasa, 'base_gravable': v['base_gravable'], 'iva': v['iva']}
            for tasa, v in sorted(resultado.items())
        ]
        return pd.DataFrame(filas)
    except Exception as e:
        print(f"[obtener_iva_por_tasa_periodo] Error: {e}")
        return vacio


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
    """Ingresos (con IVA), costos, gastos operativos, margen bruto y utilidad
    neta del mes. El margen bruto se calcula sobre ingresos netos de IVA menos
    costo de mercancía vendida - el IVA recaudado no es utilidad del negocio.
    La utilidad neta resta además los gastos operativos del período (Gastos)."""
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

        gastos = conn.execute(text("""
            SELECT COALESCE(SUM(monto), 0) FROM Gastos
            WHERE usuario_id = :uid AND DATE(fecha) >= :f_ini AND DATE(fecha) <= :f_fin
        """), {"uid": uid, "f_ini": f_ini, "f_fin": f_fin}).scalar()

    ingresos = float(ingresos) if ingresos else 0
    costos = float(costos) if costos else 0
    gastos = float(gastos) if gastos else 0
    iva = _iva_recaudado_rango(uid, f_ini, f_fin)
    margen = (ingresos - iva) - costos
    utilidad_neta = margen - gastos
    return ingresos, costos, margen, iva, gastos, utilidad_neta


@st.cache_data(ttl=60)
def obtener_ganancia_dia(uid, fecha):
    """Ingresos (con IVA), costos, gastos, ganancia bruta y utilidad neta de un
    día específico (para Cierre de Caja). La ganancia bruta se calcula sobre
    ingresos netos de IVA; la utilidad neta le resta además los gastos del día."""
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

        gastos = conn.execute(text("""
            SELECT COALESCE(SUM(monto), 0) FROM Gastos
            WHERE usuario_id = :uid AND DATE(fecha) = :fecha
        """), {"uid": uid, "fecha": fecha}).scalar()

    ingresos = float(ingresos) if ingresos else 0
    costos = float(costos) if costos else 0
    gastos = float(gastos) if gastos else 0
    iva = _iva_recaudado_rango(uid, fecha, fecha)
    ganancia = (ingresos - iva) - costos
    utilidad_neta = ganancia - gastos
    return ingresos, costos, ganancia, iva, gastos, utilidad_neta


@st.cache_data(ttl=60)
def obtener_ganancia_por_producto_dia(uid, fecha):
    """Ganancia detallada por producto vendido en una fecha (para Cierre de
    Caja). La ganancia se calcula sobre la venta neta de IVA (el IVA de cada
    renglón no es utilidad del negocio)."""
    engine = obtener_conexion()
    with engine.connect() as conn:
        df = pd.read_sql_query(text("""
            SELECT dv.nombre_producto AS producto,
                   SUM(dv.cantidad) AS cantidad,
                   SUM(dv.costo_unitario * dv.cantidad) / SUM(dv.cantidad) AS costo_unitario,
                   SUM(dv.subtotal) / SUM(dv.cantidad) AS precio_unitario,
                   SUM(dv.costo_unitario * dv.cantidad) AS costo_total,
                   SUM(dv.subtotal) AS venta_total,
                   SUM(dv.subtotal - dv.subtotal / (1 + dv.iva_porcentaje / 100.0)) AS iva_total,
                   SUM(dv.subtotal / (1 + dv.iva_porcentaje / 100.0)) - SUM(dv.costo_unitario * dv.cantidad) AS ganancia
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
    """Utilidad neta acumulada histórica: ingresos netos de IVA - costos -
    gastos operativos, de todas las ventas no anuladas y todos los gastos
    registrados. El IVA recaudado no es utilidad del negocio."""
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

        gastos = conn.execute(text("""
            SELECT COALESCE(SUM(monto), 0) FROM Gastos WHERE usuario_id = :uid
        """), {"uid": uid}).scalar()

    ingresos = float(ingresos) if ingresos else 0
    costos = float(costos) if costos else 0
    gastos = float(gastos) if gastos else 0
    iva = _iva_recaudado_rango(uid)
    return (ingresos - iva) - costos - gastos


# ==========================================
# FACTURACIÓN ELECTRÓNICA
# ==========================================
def obtener_credenciales_factus(uid):
    """Credenciales de Factus configuradas por este negocio (o None si no ha configurado nada)."""
    engine = obtener_conexion()
    with engine.connect() as conn:
        return conn.execute(text("""
            SELECT factus_client_id, factus_client_secret, factus_username, factus_password
            FROM Usuarios WHERE id = :uid
        """), {"uid": uid}).fetchone()


def guardar_credenciales_factus(uid, client_id, client_secret, username, password):
    """Guarda (o actualiza) las credenciales de Factus de este negocio."""
    engine = obtener_conexion()
    with engine.begin() as conn:
        conn.execute(text("""
            UPDATE Usuarios
            SET factus_client_id = :cid, factus_client_secret = :csecret,
                factus_username = :user, factus_password = :pwd
            WHERE id = :uid
        """), {"cid": client_id, "csecret": client_secret, "user": username, "pwd": password, "uid": uid})


def eliminar_credenciales_factus(uid):
    """Desconecta la cuenta de Factus de este negocio."""
    engine = obtener_conexion()
    with engine.begin() as conn:
        conn.execute(text("""
            UPDATE Usuarios
            SET factus_client_id = NULL, factus_client_secret = NULL,
                factus_username = NULL, factus_password = NULL
            WHERE id = :uid
        """), {"uid": uid})


def obtener_municipio_taller(uid):
    """Código DIVIPOLA del municipio configurado para este negocio (o None)."""
    engine = obtener_conexion()
    with engine.connect() as conn:
        return conn.execute(
            text("SELECT municipio_code_taller FROM Usuarios WHERE id = :uid"), {"uid": uid}
        ).scalar()


def guardar_municipio_taller(uid, municipio_code):
    """Guarda el código DIVIPOLA del municipio de este negocio, usado en la factura electrónica."""
    engine = obtener_conexion()
    with engine.begin() as conn:
        conn.execute(
            text("UPDATE Usuarios SET municipio_code_taller = :code WHERE id = :uid"),
            {"code": municipio_code, "uid": uid}
        )


@st.cache_data(ttl=60)
def tiene_fe_habilitada(uid):
    """Indica si el administrador habilitó la Facturación Electrónica para este
    negocio. Controla el acceso a toda la funcionalidad de Alegra/DIAN."""
    engine = obtener_conexion()
    with engine.connect() as conn:
        valor = conn.execute(
            text("SELECT fe_habilitada FROM Usuarios WHERE id = :uid"), {"uid": uid}
        ).scalar()
    return bool(valor)


def establecer_fe_habilitada(uid, habilitada):
    """Habilita o deshabilita la Facturación Electrónica para un negocio (solo admin)."""
    engine = obtener_conexion()
    with engine.begin() as conn:
        conn.execute(
            text("UPDATE Usuarios SET fe_habilitada = :val WHERE id = :uid"),
            {"val": bool(habilitada), "uid": uid}
        )
    tiene_fe_habilitada.clear()


@st.cache_data(ttl=60)
def tiene_iva_habilitado(uid):
    """Indica si el negocio declara IVA. Controla si se muestran/permiten
    campos de IVA en Inventario, Venta y Reportes - y si se le puede cobrar
    IVA a un cliente en la factura electrónica. Si algo falla (ej. la
    migración de la columna todavía no corrió), se asume que no declara -
    es el lado seguro (nunca cobrar IVA por accidente)."""
    try:
        engine = obtener_conexion()
        with engine.connect() as conn:
            valor = conn.execute(
                text("SELECT declara_iva FROM Usuarios WHERE id = :uid"), {"uid": uid}
            ).scalar()
        return bool(valor)
    except Exception as e:
        print(f"[tiene_iva_habilitado] Error: {e}")
        return False


def establecer_declara_iva(uid, declara):
    """Guarda si el negocio declara IVA o no (lo define el propio dueño en Configuración)."""
    engine = obtener_conexion()
    with engine.begin() as conn:
        conn.execute(
            text("UPDATE Usuarios SET declara_iva = :val WHERE id = :uid"),
            {"val": bool(declara), "uid": uid}
        )
    tiene_iva_habilitado.clear()


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
        df = pd.read_sql_query(text("""
            SELECT v.id, v.fecha, COALESCE(cl.nombre, 'Sin cliente') as cliente,
                   cl.documento as cliente_documento, cl.email as cliente_email,
                   v.total, v.tipo_pago, v.estado as estado_venta, v.factura_estado, v.factura_alegra_id,
                   v.factura_prefijo, v.factura_numero, v.factura_correo_enviado,
                   v.factura_cufe, v.factura_pdf_url, v.factura_xml_url,
                   v.nota_credito_alegra_id, v.nota_credito_pdf_url, v.nota_credito_xml_url,
                   v.nota_credito_prefijo, v.nota_credito_numero, v.nota_credito_correo_enviado
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
    # Red de seguridad: si la migración de columnas nuevas (init_db) todavía no
    # corrió en este proceso cuando se hizo esta consulta, no se debe romper la
    # página con un KeyError - se completan como vacías y ya.
    for col in ('factura_xml_url', 'nota_credito_xml_url', 'nota_credito_prefijo', 'nota_credito_numero',
                'factura_correo_enviado', 'nota_credito_correo_enviado'):
        if col not in df.columns:
            df[col] = None

    # IVA por venta: se calcula desde los renglones de Detalles_Venta (misma
    # lógica que el ticket individual) en un solo query por período, no uno
    # por venta, para no multiplicar consultas en listados largos.
    # Igual que con las columnas nuevas de arriba: si algo falla (ej. la
    # migración de iva_porcentaje todavía no corrió en este proceso), no se
    # debe romper la página - se deja en 0/"0%" y ya.
    df['iva_valor'] = 0.0
    df['iva_tasa_texto'] = "0%"
    try:
        if not df.empty:
            with engine.connect() as conn:
                df_det = pd.read_sql_query(text("""
                    SELECT dv.venta_id, dv.subtotal, dv.iva_porcentaje
                    FROM Detalles_Venta dv
                    JOIN Ventas v ON dv.venta_id = v.id
                    WHERE v.usuario_id = :uid
                    AND DATE(v.fecha) >= :f_ini AND DATE(v.fecha) <= :f_fin
                """), con=conn, params={
                    "uid": uid,
                    "f_ini": fecha_inicio.strftime('%Y-%m-%d'),
                    "f_fin": fecha_fin.strftime('%Y-%m-%d')
                })
            if not df_det.empty:
                totales_por_venta = dict(zip(df['id'], df['total']))
                for vid, grupo in df_det.groupby('venta_id'):
                    total_venta = float(totales_por_venta.get(vid, 0) or 0)
                    _, iva_valor = calcular_iva_desde_detalles(grupo, total_venta)
                    df.loc[df['id'] == vid, 'iva_valor'] = iva_valor
                    df.loc[df['id'] == vid, 'iva_tasa_texto'] = texto_tasa_iva(grupo)
    except Exception as e:
        print(f"[obtener_facturas_periodo] Error calculando IVA: {e}")

    return df


@st.cache_data(ttl=30)
def mapa_numeros_venta(uid):
    """Mapea cada Ventas.id de este negocio a su número de venta propio
    (1, 2, 3... en el orden en que se registraron). Ventas.id es una sola
    secuencia AUTOINCREMENT compartida por TODOS los negocios de la
    plataforma, así que el ID interno de la primera venta real de un negocio
    nuevo puede ser un número grande sin relación con cuántas ventas tiene
    ese negocio — este mapa da el número que sí tiene sentido mostrarle al
    negocio. El ID interno nunca se muestra en pantalla, pero sigue siendo
    la llave real usada por dentro (Detalles_Venta, Creditos, reference_code
    de Factus)."""
    engine = obtener_conexion()
    with engine.connect() as conn:
        ids = conn.execute(text("""
            SELECT id FROM Ventas WHERE usuario_id = :uid ORDER BY id ASC
        """), {"uid": uid}).scalars().all()
    return {vid: i for i, vid in enumerate(ids, start=1)}


def numero_venta_negocio(uid, venta_id):
    """Número de venta propio del negocio para una sola venta (ver
    mapa_numeros_venta). Se consulta directo en vez de pasar por el mapa
    cacheado — útil justo después de crear/anular una venta, sin esperar a
    que se refresque la caché de 30s."""
    if venta_id is None:
        return None
    engine = obtener_conexion()
    with engine.connect() as conn:
        return conn.execute(text("""
            SELECT COUNT(*) FROM Ventas WHERE usuario_id = :uid AND id <= :vid
        """), {"uid": uid, "vid": venta_id}).scalar()


def id_venta_desde_numero(uid, numero_venta):
    """Inverso de numero_venta_negocio(): dado el número de venta propio del
    negocio (el único que ve el usuario), devuelve el Ventas.id real. Se usa
    cuando el usuario escribe/busca una venta por ese número (ej. en
    Devoluciones o en el buscador de Facturación Electrónica)."""
    if not numero_venta or numero_venta < 1:
        return None
    engine = obtener_conexion()
    with engine.connect() as conn:
        return conn.execute(text("""
            SELECT id FROM Ventas WHERE usuario_id = :uid ORDER BY id ASC
            LIMIT 1 OFFSET :offset
        """), {"uid": uid, "offset": numero_venta - 1}).scalar()


@st.cache_data(ttl=30)
def obtener_historial_devoluciones(uid, fecha_inicio, fecha_fin):
    """Ventas anuladas (devoluciones) del período, con la factura que originaron
    y la nota crédito que las anuló (si Alegra la emitió), para la pestaña de
    Devoluciones."""
    engine = obtener_conexion()
    with engine.connect() as conn:
        df = pd.read_sql_query(text("""
            SELECT v.id, v.fecha, COALESCE(cl.nombre, 'Venta directa') as cliente,
                   v.total, v.notas,
                   v.factura_alegra_id, v.factura_prefijo, v.factura_numero,
                   v.factura_estado, v.nota_credito_alegra_id,
                   v.nota_credito_prefijo, v.nota_credito_numero,
                   v.nota_credito_pdf_url, v.nota_credito_xml_url
            FROM Ventas v
            LEFT JOIN Clientes cl ON v.cliente_id = cl.id
            WHERE v.usuario_id = :uid AND v.estado = 'Anulada'
            AND DATE(v.fecha) >= :f_ini AND DATE(v.fecha) <= :f_fin
            ORDER BY v.fecha DESC
        """), con=conn, params={
            "uid": uid,
            "f_ini": fecha_inicio.strftime('%Y-%m-%d'),
            "f_fin": fecha_fin.strftime('%Y-%m-%d')
        })
    for col in ('nota_credito_pdf_url', 'nota_credito_xml_url', 'nota_credito_prefijo', 'nota_credito_numero'):
        if col not in df.columns:
            df[col] = None
    return df


def obtener_venta_para_facturar(uid, venta_id):
    """Datos base de una venta necesarios para emitirla como factura electrónica."""
    engine = obtener_conexion()
    with engine.connect() as conn:
        return conn.execute(text("""
            SELECT id, cliente_id, total, tipo_pago, notas, factura_alegra_id,
                   factura_estado, factura_numero, nota_credito_alegra_id,
                   factura_cufe, factura_pdf_url, factura_xml_url,
                   nota_credito_pdf_url, nota_credito_xml_url,
                   nota_credito_prefijo, nota_credito_numero
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


def guardar_nota_credito(venta_id, reference_code, pdf_url=None, xml_url=None, prefijo=None, numero=None,
                          anular_venta=True, correo_enviado=None):
    """Guarda el reference_code, número (prefijo+consecutivo) y PDF/XML de la
    nota crédito emitida en Factus para una venta. anular_venta=True (caso de
    anulación completa, concepto DIAN 2) también marca factura_estado =
    'anulada'; para los demás conceptos (devolución parcial, rebaja, ajuste
    de precio, otros) la factura original sigue siendo válida, así que
    factura_estado no se toca. correo_enviado: None si no se intentó mandar
    el correo (ej. cliente sin email registrado), True/False según si Factus
    confirmó el envío."""
    engine = obtener_conexion()
    with engine.begin() as conn:
        if anular_venta:
            conn.execute(text("""
                UPDATE Ventas SET nota_credito_alegra_id = :ncid, nota_credito_pdf_url = :pdf_url,
                       nota_credito_xml_url = :xml_url, nota_credito_prefijo = :prefijo,
                       nota_credito_numero = :numero, nota_credito_correo_enviado = :correo_enviado,
                       factura_estado = 'anulada'
                WHERE id = :vid
            """), {
                "ncid": reference_code, "pdf_url": pdf_url, "xml_url": xml_url,
                "prefijo": prefijo, "numero": numero, "correo_enviado": correo_enviado, "vid": venta_id
            })
        else:
            conn.execute(text("""
                UPDATE Ventas SET nota_credito_alegra_id = :ncid, nota_credito_pdf_url = :pdf_url,
                       nota_credito_xml_url = :xml_url, nota_credito_prefijo = :prefijo,
                       nota_credito_numero = :numero, nota_credito_correo_enviado = :correo_enviado
                WHERE id = :vid
            """), {
                "ncid": reference_code, "pdf_url": pdf_url, "xml_url": xml_url,
                "prefijo": prefijo, "numero": numero, "correo_enviado": correo_enviado, "vid": venta_id
            })
    invalidar_cache_ventas()


def restaurar_stock_items(uid, items):
    """Devuelve al stock las cantidades indicadas (para devoluciones
    parciales). items: iterable de dicts/objetos con producto_id y
    cantidad."""
    engine = obtener_conexion()
    with engine.begin() as conn:
        for item in items:
            pid = item["producto_id"] if isinstance(item, dict) else item.producto_id
            cant = item["cantidad"] if isinstance(item, dict) else item.cantidad
            if pid:
                conn.execute(text("""
                    UPDATE Productos SET stock_actual = stock_actual + :cant
                    WHERE id = :pid AND usuario_id = :uid
                """), {"cant": cant, "pid": pid, "uid": uid})
    invalidar_cache_productos()


def acreditar_venta(uid, venta_id, monto, motivo=None):
    """Reduce el total de una venta por un monto acreditado (devolución
    parcial, rebaja, ajuste de precio u otros) sin cambiar su estado -- la
    venta sigue activa, solo con un total menor. El motivo, si se da, se
    anexa a las notas de la venta."""
    engine = obtener_conexion()
    with engine.begin() as conn:
        conn.execute(text("""
            UPDATE Ventas
            SET total = total - :monto,
                notas = CASE
                    WHEN :motivo IS NULL OR :motivo = '' THEN notas
                    WHEN notas IS NULL OR notas = '' THEN :motivo
                    ELSE notas || ' | ' || :motivo
                END
            WHERE id = :vid AND usuario_id = :uid
        """), {"monto": monto, "motivo": motivo, "vid": venta_id, "uid": uid})
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


def guardar_resultado_factura(venta_id, reference_code=None, cufe=None, pdf_url=None, xml_url=None,
                               estado="emitida", prefijo=None, numero=None, correo_enviado=None):
    """Guarda el resultado de emitir (o intentar emitir) la factura electrónica de una venta.
    correo_enviado: None si no se intentó mandar el correo (ej. cliente sin
    email registrado), True/False según si Factus confirmó el envío."""
    engine = obtener_conexion()
    with engine.begin() as conn:
        conn.execute(text("""
            UPDATE Ventas
            SET factura_alegra_id = :reference_code, factura_cufe = :cufe,
                factura_pdf_url = :pdf_url, factura_xml_url = :xml_url, factura_estado = :estado,
                factura_prefijo = :prefijo, factura_numero = :numero, factura_correo_enviado = :correo_enviado
            WHERE id = :vid
        """), {
            "reference_code": reference_code, "cufe": cufe, "pdf_url": pdf_url, "xml_url": xml_url,
            "estado": estado, "prefijo": prefijo, "numero": numero, "correo_enviado": correo_enviado, "vid": venta_id
        })
    invalidar_cache_ventas()


def actualizar_correo_enviado_factura(venta_id, correo_enviado):
    """Guarda solo el resultado de un reenvío manual del correo de la
    factura, sin tocar el resto de los datos ya guardados (a diferencia de
    guardar_resultado_factura, que sobreescribe todo el bloque)."""
    engine = obtener_conexion()
    with engine.begin() as conn:
        conn.execute(text("""
            UPDATE Ventas SET factura_correo_enviado = :correo_enviado WHERE id = :vid
        """), {"correo_enviado": correo_enviado, "vid": venta_id})
    invalidar_cache_ventas()


def actualizar_correo_enviado_nota_credito(venta_id, correo_enviado):
    """Igual que actualizar_correo_enviado_factura(), pero para la nota crédito."""
    engine = obtener_conexion()
    with engine.begin() as conn:
        conn.execute(text("""
            UPDATE Ventas SET nota_credito_correo_enviado = :correo_enviado WHERE id = :vid
        """), {"correo_enviado": correo_enviado, "vid": venta_id})
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


def actualizar_pdf_nota_credito(venta_id, pdf_url, xml_url=None, prefijo=None, numero=None):
    """Completa PDF/XML/número de una nota crédito ya emitida, cuando no llegaron
    en la respuesta de creación."""
    engine = obtener_conexion()
    with engine.begin() as conn:
        conn.execute(text("""
            UPDATE Ventas
            SET nota_credito_pdf_url = COALESCE(:pdf_url, nota_credito_pdf_url),
                nota_credito_xml_url = COALESCE(:xml_url, nota_credito_xml_url),
                nota_credito_prefijo = COALESCE(:prefijo, nota_credito_prefijo),
                nota_credito_numero = COALESCE(:numero, nota_credito_numero)
            WHERE id = :vid
        """), {
            "pdf_url": pdf_url, "xml_url": xml_url,
            "prefijo": prefijo, "numero": numero, "vid": venta_id
        })
    invalidar_cache_ventas()


# ==========================================
# COTIZACIONES (previas a convertirse en una venta real)
# ==========================================
def crear_cotizacion(uid, cliente_id, cajero_id, items, subtotal, descuento, total):
    """Guarda una cotización con sus ítems. A diferencia de una venta real,
    NO descuenta stock de Productos - eso solo ocurre al convertirla en
    venta (convertir_cotizacion_a_venta). `items`: lista de dicts con las
    mismas llaves que usa el carrito del POS (producto_id, nombre,
    codigo_barras, precio_unitario, costo_unitario, cantidad, subtotal,
    iva_porcentaje). Retorna el id de la cotización (su número visible,
    igual que Ventas.id)."""
    engine = obtener_conexion()
    with engine.begin() as conn:
        is_sqlite = "sqlite" in str(engine.url)

        if is_sqlite:
            cur = conn.execute(text('''
                INSERT INTO Cotizaciones (usuario_id, cliente_id, cajero_id, subtotal, descuento, total)
                VALUES (:uid, :cid, :cajero, :sub, :desc, :total)
            '''), {"uid": uid, "cid": cliente_id, "cajero": cajero_id,
                   "sub": subtotal, "desc": descuento, "total": total})
            cotizacion_id = cur.lastrowid
        else:
            res = conn.execute(text('''
                INSERT INTO Cotizaciones (usuario_id, cliente_id, cajero_id, subtotal, descuento, total)
                VALUES (:uid, :cid, :cajero, :sub, :desc, :total) RETURNING id
            '''), {"uid": uid, "cid": cliente_id, "cajero": cajero_id,
                   "sub": subtotal, "desc": descuento, "total": total})
            cotizacion_id = res.scalar()

        for item in items:
            conn.execute(text('''
                INSERT INTO Detalles_Cotizacion
                (cotizacion_id, producto_id, nombre_producto, codigo_barras, cantidad,
                 precio_unitario, costo_unitario, descuento, subtotal, iva_porcentaje)
                VALUES (:cotid, :pid, :nom, :cod, :cant, :pvp, :costo, :desc, :sub, :iva)
            '''), {
                "cotid": cotizacion_id, "pid": item.get("producto_id"),
                "nom": item["nombre"], "cod": item.get("codigo_barras"),
                "cant": item["cantidad"], "pvp": item["precio_unitario"],
                "costo": item.get("costo_unitario", 0),
                "desc": item.get("descuento_item", 0) * item["cantidad"],
                "sub": item["subtotal"], "iva": item.get("iva_porcentaje", 0) or 0,
            })
    invalidar_cache_cotizaciones()
    return cotizacion_id


@st.cache_data(ttl=20)
def obtener_cotizaciones(uid):
    """Lista de cotizaciones del negocio (convertidas o no), más recientes primero."""
    engine = obtener_conexion()
    with engine.connect() as conn:
        return pd.read_sql_query(text('''
            SELECT c.id, c.fecha, c.subtotal, c.descuento, c.total,
                   COALESCE(cl.nombre, 'Sin cliente') as cliente,
                   c.convertida_a_venta_id
            FROM Cotizaciones c
            LEFT JOIN Clientes cl ON c.cliente_id = cl.id
            WHERE c.usuario_id = :uid
            ORDER BY c.id DESC
        '''), con=conn, params={"uid": uid})


def obtener_cotizacion_detalle(uid, cotizacion_id):
    """Encabezado + ítems de una cotización puntual. Sin caché: se consulta
    al abrirla, no en cada rerun de la página."""
    engine = obtener_conexion()
    with engine.connect() as conn:
        encabezado = conn.execute(text('''
            SELECT c.id, c.fecha, c.subtotal, c.descuento, c.total, c.cliente_id,
                   cl.nombre as cliente, cl.documento as cliente_documento,
                   c.convertida_a_venta_id
            FROM Cotizaciones c
            LEFT JOIN Clientes cl ON c.cliente_id = cl.id
            WHERE c.id = :cid AND c.usuario_id = :uid
        '''), {"cid": cotizacion_id, "uid": uid}).fetchone()

        if not encabezado:
            return None, None

        df_items = pd.read_sql_query(text('''
            SELECT id, producto_id, nombre_producto, codigo_barras, cantidad,
                   precio_unitario, costo_unitario, descuento, subtotal, iva_porcentaje
            FROM Detalles_Cotizacion WHERE cotizacion_id = :cid
        '''), con=conn, params={"cid": cotizacion_id})

    return encabezado, df_items


def convertir_cotizacion_a_venta(uid, cotizacion_id, tipo_pago, cajero_id=None,
                                  monto_efectivo=0, monto_transferencia=0, cambio=0,
                                  tipo_cuota="Libre", valor_cuota=0, fecha_limite=None):
    """Convierte una cotización en una venta real (Ventas + Detalles_Venta),
    descontando stock recién en este momento (una cotización nunca lo hizo,
    así que valida que siga habiendo suficiente disponible). Si tipo_pago es
    'Credito', también crea el registro en Creditos (requiere que la
    cotización tenga un cliente asignado). Retorna el id de la nueva venta.
    Lanza ValueError con un mensaje entendible si falta stock o cliente."""
    engine = obtener_conexion()
    with engine.begin() as conn:
        is_sqlite = "sqlite" in str(engine.url)

        cot = conn.execute(text('''
            SELECT cliente_id, subtotal, descuento, total, convertida_a_venta_id
            FROM Cotizaciones WHERE id = :cid AND usuario_id = :uid
        '''), {"cid": cotizacion_id, "uid": uid}).fetchone()

        if not cot:
            raise ValueError("Esa cotización no existe.")
        if cot.convertida_a_venta_id:
            raise ValueError("Esta cotización ya fue convertida en una venta.")
        if tipo_pago == "Credito" and not cot.cliente_id:
            raise ValueError("Esta cotización no tiene cliente asignado - no se puede convertir a crédito sin uno.")

        items = conn.execute(text('''
            SELECT id, producto_id, nombre_producto, codigo_barras, cantidad,
                   precio_unitario, costo_unitario, descuento, subtotal, iva_porcentaje
            FROM Detalles_Cotizacion WHERE cotizacion_id = :cid
        '''), {"cid": cotizacion_id}).fetchall()

        for item in items:
            if item.producto_id:
                stock_actual = conn.execute(text(
                    "SELECT stock_actual FROM Productos WHERE id = :pid AND usuario_id = :uid"
                ), {"pid": item.producto_id, "uid": uid}).scalar()
                if stock_actual is None or float(stock_actual) < float(item.cantidad):
                    raise ValueError(
                        f"Ya no hay suficiente stock de '{item.nombre_producto}' para convertir esta "
                        f"cotización (disponible: {stock_actual if stock_actual is not None else 0})."
                    )

        estado_venta = "Credito" if tipo_pago == "Credito" else "Pagada"

        if is_sqlite:
            cur = conn.execute(text('''
                INSERT INTO Ventas
                (usuario_id, cliente_id, cajero_id, subtotal, descuento, total,
                 tipo_pago, monto_efectivo, monto_transferencia, cambio, estado)
                VALUES (:uid, :cid, :cajero, :sub, :desc, :total,
                        :tipo, :efec, :trans, :cambio, :est)
            '''), {
                "uid": uid, "cid": cot.cliente_id, "cajero": cajero_id,
                "sub": cot.subtotal, "desc": cot.descuento, "total": cot.total,
                "tipo": tipo_pago, "efec": monto_efectivo, "trans": monto_transferencia,
                "cambio": cambio, "est": estado_venta,
            })
            venta_id = cur.lastrowid
        else:
            res = conn.execute(text('''
                INSERT INTO Ventas
                (usuario_id, cliente_id, cajero_id, subtotal, descuento, total,
                 tipo_pago, monto_efectivo, monto_transferencia, cambio, estado)
                VALUES (:uid, :cid, :cajero, :sub, :desc, :total,
                        :tipo, :efec, :trans, :cambio, :est) RETURNING id
            '''), {
                "uid": uid, "cid": cot.cliente_id, "cajero": cajero_id,
                "sub": cot.subtotal, "desc": cot.descuento, "total": cot.total,
                "tipo": tipo_pago, "efec": monto_efectivo, "trans": monto_transferencia,
                "cambio": cambio, "est": estado_venta,
            })
            venta_id = res.scalar()

        for item in items:
            conn.execute(text('''
                INSERT INTO Detalles_Venta
                (venta_id, producto_id, nombre_producto, codigo_barras, cantidad,
                 precio_unitario, costo_unitario, descuento, subtotal, iva_porcentaje)
                VALUES (:vid, :pid, :nom, :cod, :cant, :pvp, :costo, :desc, :sub, :iva)
            '''), {
                "vid": venta_id, "pid": item.producto_id, "nom": item.nombre_producto,
                "cod": item.codigo_barras, "cant": item.cantidad, "pvp": item.precio_unitario,
                "costo": item.costo_unitario, "desc": item.descuento, "sub": item.subtotal,
                "iva": item.iva_porcentaje,
            })
            if item.producto_id:
                conn.execute(text('''
                    UPDATE Productos SET stock_actual = stock_actual - :cant
                    WHERE id = :pid AND stock_actual >= :cant
                '''), {"cant": item.cantidad, "pid": item.producto_id})

        if tipo_pago == "Credito":
            conn.execute(text('''
                INSERT INTO Creditos
                (usuario_id, venta_id, cliente_id, total, saldo_pendiente,
                 fecha_inicio, fecha_limite, tipo_cuota, valor_cuota, estado)
                VALUES (:uid, :vid, :cid, :total, :saldo, :f_ini, :f_lim, :tipo_c, :val_c, 'Activo')
            '''), {
                "uid": uid, "vid": venta_id, "cid": cot.cliente_id,
                "total": cot.total, "saldo": cot.total,
                "f_ini": date.today().strftime('%Y-%m-%d'),
                "f_lim": (fecha_limite or date.today()).strftime('%Y-%m-%d'),
                "tipo_c": tipo_cuota, "val_c": valor_cuota,
            })

        conn.execute(text('''
            UPDATE Cotizaciones SET convertida_a_venta_id = :vid, fecha_conversion = CURRENT_TIMESTAMP
            WHERE id = :cid
        '''), {"vid": venta_id, "cid": cotizacion_id})

    invalidar_cache_cotizaciones()
    invalidar_cache_ventas()
    if tipo_pago == "Credito":
        invalidar_cache_creditos()

    return venta_id


def eliminar_cotizacion(uid, cotizacion_id):
    """Elimina una cotización (y sus ítems) que todavía no se ha convertido
    en venta real. Lanza ValueError si ya fue convertida."""
    engine = obtener_conexion()
    with engine.begin() as conn:
        convertida = conn.execute(text('''
            SELECT convertida_a_venta_id FROM Cotizaciones WHERE id = :cid AND usuario_id = :uid
        '''), {"cid": cotizacion_id, "uid": uid}).scalar()

        if convertida:
            raise ValueError("No se puede eliminar una cotización que ya fue convertida en venta.")

        conn.execute(text("DELETE FROM Detalles_Cotizacion WHERE cotizacion_id = :cid"), {"cid": cotizacion_id})
        conn.execute(text("DELETE FROM Cotizaciones WHERE id = :cid AND usuario_id = :uid"),
                     {"cid": cotizacion_id, "uid": uid})
    invalidar_cache_cotizaciones()


def invalidar_cache_cotizaciones():
    """Llamar tras crear, convertir o eliminar una cotización."""
    obtener_cotizaciones.clear()


# ==========================================
# GASTOS (egresos operativos del negocio - arriendo, servicios, nómina, etc.)
# ==========================================
# A propósito NO incluye compra de mercancía para revender - eso se registra
# en Entradas_Inventario y ya reduce la utilidad vía Detalles_Venta.costo_unitario
# cuando se vende. Si también se contara aquí, se restaría dos veces.
CATEGORIAS_GASTO = [
    "Arriendo", "Servicios públicos", "Nómina y prestaciones",
    "Transporte y domicilios", "Mantenimiento y reparaciones",
    "Papelería y aseo", "Publicidad y marketing", "Tecnología y software",
    "Impuestos y tasas", "Comisiones y gastos bancarios",
    "Honorarios profesionales", "Otro",
]


def crear_gasto(uid, fecha, categoria, descripcion, beneficiario, monto,
                 tipo_pago, comprobante_path=None, notas=None):
    """Registra un gasto operativo del negocio. Retorna el id del gasto."""
    engine = obtener_conexion()
    params = {
        "uid": uid, "fecha": fecha, "cat": categoria, "desc": descripcion,
        "benef": beneficiario or None, "monto": monto, "tipo": tipo_pago,
        "comp": comprobante_path, "notas": notas or None,
    }
    with engine.begin() as conn:
        is_sqlite = "sqlite" in str(engine.url)
        if is_sqlite:
            cur = conn.execute(text('''
                INSERT INTO Gastos
                (usuario_id, fecha, categoria, descripcion, beneficiario, monto,
                 tipo_pago, comprobante_path, notas)
                VALUES (:uid, :fecha, :cat, :desc, :benef, :monto, :tipo, :comp, :notas)
            '''), params)
            gasto_id = cur.lastrowid
        else:
            res = conn.execute(text('''
                INSERT INTO Gastos
                (usuario_id, fecha, categoria, descripcion, beneficiario, monto,
                 tipo_pago, comprobante_path, notas)
                VALUES (:uid, :fecha, :cat, :desc, :benef, :monto, :tipo, :comp, :notas)
                RETURNING id
            '''), params)
            gasto_id = res.scalar()
    invalidar_cache_gastos()
    return gasto_id


def actualizar_gasto(uid, gasto_id, fecha, categoria, descripcion, beneficiario, monto,
                      tipo_pago, comprobante_path=None, notas=None):
    """Edita un gasto ya registrado. Si no se sube un comprobante nuevo, conserva el anterior."""
    engine = obtener_conexion()
    with engine.begin() as conn:
        conn.execute(text('''
            UPDATE Gastos
            SET fecha = :fecha, categoria = :cat, descripcion = :desc,
                beneficiario = :benef, monto = :monto, tipo_pago = :tipo,
                comprobante_path = COALESCE(:comp, comprobante_path), notas = :notas
            WHERE id = :gid AND usuario_id = :uid
        '''), {
            "fecha": fecha, "cat": categoria, "desc": descripcion,
            "benef": beneficiario or None, "monto": monto, "tipo": tipo_pago,
            "comp": comprobante_path, "notas": notas or None,
            "gid": gasto_id, "uid": uid,
        })
    invalidar_cache_gastos()


def eliminar_gasto(uid, gasto_id):
    """Elimina un gasto registrado."""
    engine = obtener_conexion()
    with engine.begin() as conn:
        conn.execute(text("DELETE FROM Gastos WHERE id = :gid AND usuario_id = :uid"),
                     {"gid": gasto_id, "uid": uid})
    invalidar_cache_gastos()


@st.cache_data(ttl=30)
def obtener_gastos_periodo(uid, fecha_inicio, fecha_fin):
    """Gastos registrados en un rango de fechas, para listar/filtrar/exportar."""
    engine = obtener_conexion()
    with engine.connect() as conn:
        return pd.read_sql_query(text("""
            SELECT id, fecha, categoria, descripcion, beneficiario, monto,
                   tipo_pago, comprobante_path, notas
            FROM Gastos
            WHERE usuario_id = :uid
            AND DATE(fecha) >= :f_ini AND DATE(fecha) <= :f_fin
            ORDER BY fecha DESC
        """), con=conn, params={
            "uid": uid,
            "f_ini": fecha_inicio.strftime('%Y-%m-%d'),
            "f_fin": fecha_fin.strftime('%Y-%m-%d'),
        })


def obtener_gasto(uid, gasto_id):
    """Un gasto puntual, para precargar el formulario de edición. Sin caché."""
    engine = obtener_conexion()
    with engine.connect() as conn:
        return conn.execute(text("""
            SELECT id, fecha, categoria, descripcion, beneficiario, monto,
                   tipo_pago, comprobante_path, notas
            FROM Gastos WHERE id = :gid AND usuario_id = :uid
        """), {"gid": gasto_id, "uid": uid}).fetchone()


@st.cache_data(ttl=30)
def obtener_gastos_dia(uid, fecha):
    """Gastos de un día, desglosados por forma de pago - insumo del Cierre de Caja
    para restar del efectivo esperado y de la ganancia del día."""
    engine = obtener_conexion()
    with engine.connect() as conn:
        row = conn.execute(text("""
            SELECT
                COALESCE(SUM(monto), 0) as total,
                COALESCE(SUM(CASE WHEN tipo_pago = 'Efectivo' THEN monto ELSE 0 END), 0) as total_efectivo,
                COALESCE(SUM(CASE WHEN tipo_pago = 'Transferencia' THEN monto ELSE 0 END), 0) as total_transferencia
            FROM Gastos
            WHERE usuario_id = :uid AND DATE(fecha) = :fecha
        """), {"uid": uid, "fecha": fecha}).fetchone()
    return {
        "total": float(row[0]) if row else 0.0,
        "total_efectivo": float(row[1]) if row else 0.0,
        "total_transferencia": float(row[2]) if row else 0.0,
    }


@st.cache_data(ttl=60)
def obtener_gastos_mes(uid, año, mes):
    """Total de gastos de un mes calendario, para el Dashboard."""
    engine = obtener_conexion()
    f_ini = date(año, mes, 1).strftime('%Y-%m-%d')
    f_fin = date(año, mes, calendar.monthrange(año, mes)[1]).strftime('%Y-%m-%d')
    with engine.connect() as conn:
        total = conn.execute(text("""
            SELECT COALESCE(SUM(monto), 0) FROM Gastos
            WHERE usuario_id = :uid AND DATE(fecha) >= :f_ini AND DATE(fecha) <= :f_fin
        """), {"uid": uid, "f_ini": f_ini, "f_fin": f_fin}).scalar()
    return float(total) if total else 0.0


@st.cache_data(ttl=60)
def obtener_gastos_por_categoria(uid, fecha_inicio, fecha_fin):
    """Gastos agrupados por categoría en un período, para el desglose visual."""
    engine = obtener_conexion()
    with engine.connect() as conn:
        return pd.read_sql_query(text("""
            SELECT categoria, COALESCE(SUM(monto), 0) as total, COUNT(*) as cantidad
            FROM Gastos
            WHERE usuario_id = :uid
            AND DATE(fecha) >= :f_ini AND DATE(fecha) <= :f_fin
            GROUP BY categoria
            ORDER BY total DESC
        """), con=conn, params={
            "uid": uid,
            "f_ini": fecha_inicio.strftime('%Y-%m-%d'),
            "f_fin": fecha_fin.strftime('%Y-%m-%d'),
        })


def invalidar_cache_gastos():
    """Llamar después de crear, editar o eliminar un gasto."""
    obtener_gastos_periodo.clear()
    obtener_gastos_dia.clear()
    obtener_gastos_mes.clear()
    obtener_gastos_por_categoria.clear()
    obtener_metricas_mes.clear()
    obtener_ganancia_dia.clear()
    obtener_ganancia_acumulada.clear()


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
    obtener_historial_devoluciones.clear()
    mapa_numeros_venta.clear()


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

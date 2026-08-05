import streamlit as st
from sqlalchemy import create_engine, text, event
import os

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
LOCAL_DB_PATH = os.path.join(BASE_DIR, "myalmacen.db")

# Schema de Postgres que usa MyInv. Por defecto 'public' (comportamiento
# normal). Solo se debe fijar a algo distinto (ej. 'myinv') en secrets.toml
# cuando el proyecto de Supabase se comparte con otra app que ya usa 'public'
# (ej. MyTaller), para no chocar con sus tablas.


@st.cache_resource
def obtener_conexion():
    try:
        db_url = st.secrets["postgres"]["url"]
        pg_schema = st.secrets["postgres"].get("schema", "public")

        if pg_schema != "public":
            # Conexión de arranque (sin search_path fijo) solo para asegurar
            # que el schema propio exista antes de apuntar el pool ahí.
            engine_setup = create_engine(db_url)
            with engine_setup.connect() as conn:
                conn.execute(text(f"CREATE SCHEMA IF NOT EXISTS {pg_schema}"))
                conn.commit()
            engine_setup.dispose()

        engine = create_engine(
            db_url,
            pool_size=10,
            max_overflow=20,
            pool_pre_ping=True,
            pool_recycle=300,
        )

        if pg_schema != "public":
            # El pooler de Supabase ignora el parámetro 'options' del connect_args,
            # así que el search_path se fija a mano en cada conexión física nueva.
            @event.listens_for(engine, "connect")
            def _fijar_search_path(dbapi_connection, connection_record):
                cursor = dbapi_connection.cursor()
                cursor.execute(f"SET search_path TO {pg_schema}")
                cursor.close()
                dbapi_connection.commit()

        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
    except Exception as e:
        st.warning(
            f"No se pudo conectar a Postgres/Supabase, usando base local de respaldo. "
            f"Detalle: {e}"
        )
        engine = create_engine(f"sqlite:///{LOCAL_DB_PATH}")
    return engine


@st.cache_resource
def init_db():
    engine = obtener_conexion()
    is_sqlite = "sqlite" in str(engine.url)

    with engine.begin() as conn:
        if is_sqlite:
            conn.execute(text('''
                CREATE TABLE IF NOT EXISTS Usuarios (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    nombre_negocio TEXT NOT NULL,
                    nombre_dueno TEXT NOT NULL,
                    email TEXT UNIQUE NOT NULL,
                    password TEXT NOT NULL,
                    activo BOOLEAN DEFAULT 0,
                    fecha_pago_limite DATE,
                    token_sesion TEXT,
                    logo_path TEXT,
                    nit TEXT,
                    telefono TEXT,
                    direccion TEXT,
                    fecha_registro TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    alegra_email TEXT,
                    alegra_token TEXT,
                    fe_habilitada BOOLEAN DEFAULT 0
                )
            '''))
            conn.execute(text('''
                CREATE TABLE IF NOT EXISTS Productos (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    usuario_id INTEGER NOT NULL,
                    nombre TEXT NOT NULL,
                    descripcion TEXT,
                    codigo_barras TEXT,
                    codigo_ref TEXT,
                    categoria TEXT DEFAULT 'General',
                    unidad_medida TEXT DEFAULT 'Unidad',
                    stock_actual REAL NOT NULL DEFAULT 0,
                    stock_minimo REAL NOT NULL DEFAULT 2,
                    costo_compra REAL DEFAULT 0,
                    precio_venta REAL NOT NULL DEFAULT 0,
                    activo BOOLEAN DEFAULT 1,
                    fecha_registro TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    alegra_item_id TEXT,
                    iva_porcentaje REAL DEFAULT 0,
                    FOREIGN KEY (usuario_id) REFERENCES Usuarios(id) ON DELETE CASCADE
                )
            '''))
            conn.execute(text('''
                CREATE TABLE IF NOT EXISTS Clientes (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    usuario_id INTEGER NOT NULL,
                    nombre TEXT NOT NULL,
                    telefono TEXT,
                    email TEXT,
                    direccion TEXT,
                    documento TEXT,
                    tipo_documento TEXT DEFAULT 'CC',
                    cupo_credito REAL DEFAULT 0,
                    activo BOOLEAN DEFAULT 1,
                    fecha_registro TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    alegra_contact_id TEXT,
                    FOREIGN KEY (usuario_id) REFERENCES Usuarios(id) ON DELETE CASCADE
                )
            '''))
            conn.execute(text('''
                CREATE TABLE IF NOT EXISTS Proveedores (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    usuario_id INTEGER NOT NULL,
                    nombre TEXT NOT NULL,
                    telefono TEXT,
                    email TEXT,
                    contacto TEXT,
                    nit TEXT,
                    activo BOOLEAN DEFAULT 1,
                    FOREIGN KEY (usuario_id) REFERENCES Usuarios(id) ON DELETE CASCADE
                )
            '''))
            conn.execute(text('''
                CREATE TABLE IF NOT EXISTS Ventas (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    usuario_id INTEGER NOT NULL,
                    cliente_id INTEGER,
                    cajero_id INTEGER,
                    fecha TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    subtotal REAL NOT NULL DEFAULT 0,
                    descuento REAL DEFAULT 0,
                    total REAL NOT NULL DEFAULT 0,
                    tipo_pago TEXT CHECK(tipo_pago IN ('Efectivo','Transferencia','Credito','Mixto')) DEFAULT 'Efectivo',
                    monto_efectivo REAL DEFAULT 0,
                    monto_transferencia REAL DEFAULT 0,
                    cambio REAL DEFAULT 0,
                    estado TEXT CHECK(estado IN ('Pagada','Credito','Anulada')) DEFAULT 'Pagada',
                    notas TEXT,
                    factura_alegra_id TEXT,
                    factura_cufe TEXT,
                    factura_pdf_url TEXT,
                    factura_xml_url TEXT,
                    factura_estado TEXT,
                    factura_prefijo TEXT,
                    factura_numero TEXT,
                    nota_credito_alegra_id TEXT,
                    nota_credito_pdf_url TEXT,
                    nota_credito_xml_url TEXT,
                    nota_credito_prefijo TEXT,
                    nota_credito_numero TEXT,
                    FOREIGN KEY (usuario_id) REFERENCES Usuarios(id) ON DELETE CASCADE,
                    FOREIGN KEY (cliente_id) REFERENCES Clientes(id),
                    FOREIGN KEY (cajero_id) REFERENCES Cajeros(id)
                )
            '''))
            conn.execute(text('''
                CREATE TABLE IF NOT EXISTS Detalles_Venta (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    venta_id INTEGER NOT NULL,
                    producto_id INTEGER,
                    nombre_producto TEXT NOT NULL,
                    codigo_barras TEXT,
                    cantidad INTEGER NOT NULL DEFAULT 1,
                    precio_unitario REAL NOT NULL DEFAULT 0,
                    costo_unitario REAL DEFAULT 0,
                    descuento REAL DEFAULT 0,
                    subtotal REAL NOT NULL DEFAULT 0,
                    iva_porcentaje REAL DEFAULT 0,
                    FOREIGN KEY (venta_id) REFERENCES Ventas(id) ON DELETE CASCADE,
                    FOREIGN KEY (producto_id) REFERENCES Productos(id)
                )
            '''))
            conn.execute(text('''
                CREATE TABLE IF NOT EXISTS Entradas_Inventario (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    usuario_id INTEGER NOT NULL,
                    proveedor_id INTEGER,
                    fecha TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    numero_factura TEXT,
                    total_compra REAL DEFAULT 0,
                    notas TEXT,
                    FOREIGN KEY (usuario_id) REFERENCES Usuarios(id) ON DELETE CASCADE,
                    FOREIGN KEY (proveedor_id) REFERENCES Proveedores(id)
                )
            '''))
            conn.execute(text('''
                CREATE TABLE IF NOT EXISTS Detalles_Entrada (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    entrada_id INTEGER NOT NULL,
                    producto_id INTEGER NOT NULL,
                    cantidad INTEGER NOT NULL DEFAULT 1,
                    costo_unitario REAL NOT NULL DEFAULT 0,
                    subtotal REAL NOT NULL DEFAULT 0,
                    FOREIGN KEY (entrada_id) REFERENCES Entradas_Inventario(id) ON DELETE CASCADE,
                    FOREIGN KEY (producto_id) REFERENCES Productos(id)
                )
            '''))
            conn.execute(text('''
                CREATE TABLE IF NOT EXISTS Creditos (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    usuario_id INTEGER NOT NULL,
                    venta_id INTEGER NOT NULL,
                    cliente_id INTEGER NOT NULL,
                    total REAL NOT NULL DEFAULT 0,
                    saldo_pendiente REAL NOT NULL DEFAULT 0,
                    fecha_inicio DATE NOT NULL,
                    fecha_limite DATE,
                    tipo_cuota TEXT CHECK(tipo_cuota IN ('Libre','Semanal','Quincenal','Mensual')) DEFAULT 'Libre',
                    valor_cuota REAL DEFAULT 0,
                    estado TEXT CHECK(estado IN ('Activo','Pagado','Vencido')) DEFAULT 'Activo',
                    FOREIGN KEY (usuario_id) REFERENCES Usuarios(id) ON DELETE CASCADE,
                    FOREIGN KEY (venta_id) REFERENCES Ventas(id),
                    FOREIGN KEY (cliente_id) REFERENCES Clientes(id)
                )
            '''))
            conn.execute(text('''
                CREATE TABLE IF NOT EXISTS Abonos (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    credito_id INTEGER NOT NULL,
                    usuario_id INTEGER NOT NULL,
                    monto REAL NOT NULL DEFAULT 0,
                    fecha TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    notas TEXT,
                    FOREIGN KEY (credito_id) REFERENCES Creditos(id) ON DELETE CASCADE,
                    FOREIGN KEY (usuario_id) REFERENCES Usuarios(id)
                )
            '''))
            conn.execute(text('''
                CREATE TABLE IF NOT EXISTS Cierres_Caja (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    usuario_id INTEGER NOT NULL,
                    fecha DATE NOT NULL,
                    total_ventas INTEGER DEFAULT 0,
                    total_efectivo REAL DEFAULT 0,
                    total_transferencias REAL DEFAULT 0,
                    total_creditos REAL DEFAULT 0,
                    total_descuentos REAL DEFAULT 0,
                    efectivo_contado REAL DEFAULT 0,
                    diferencia REAL DEFAULT 0,
                    notas TEXT,
                    cerrado_por TEXT,
                    cajero_id INTEGER,
                    fecha_cierre TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    FOREIGN KEY (usuario_id) REFERENCES Usuarios(id) ON DELETE CASCADE,
                    FOREIGN KEY (cajero_id) REFERENCES Cajeros(id)
                )
            '''))
            conn.execute(text('''
                CREATE TABLE IF NOT EXISTS Cajeros (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    usuario_id INTEGER NOT NULL,
                    nombre TEXT NOT NULL,
                    pin TEXT NOT NULL,
                    usuario_login TEXT,
                    token_sesion TEXT,
                    activo BOOLEAN DEFAULT 1,
                    fecha_registro TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    FOREIGN KEY (usuario_id) REFERENCES Usuarios(id) ON DELETE CASCADE
                )
            '''))
        else:
            conn.execute(text('''
                CREATE TABLE IF NOT EXISTS Usuarios (
                    id SERIAL PRIMARY KEY,
                    nombre_negocio TEXT NOT NULL,
                    nombre_dueno TEXT NOT NULL,
                    email TEXT UNIQUE NOT NULL,
                    password TEXT NOT NULL,
                    activo BOOLEAN DEFAULT FALSE,
                    fecha_pago_limite DATE,
                    token_sesion VARCHAR(255),
                    logo_path TEXT,
                    nit TEXT,
                    telefono TEXT,
                    direccion TEXT,
                    fecha_registro TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    alegra_email TEXT,
                    alegra_token TEXT,
                    fe_habilitada BOOLEAN DEFAULT FALSE
                )
            '''))
            conn.execute(text('''
                CREATE TABLE IF NOT EXISTS Cajeros (
                    id SERIAL PRIMARY KEY,
                    usuario_id INTEGER NOT NULL,
                    nombre TEXT NOT NULL,
                    pin TEXT NOT NULL,
                    usuario_login VARCHAR(50),
                    token_sesion VARCHAR(255),
                    activo BOOLEAN DEFAULT TRUE,
                    fecha_registro TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    FOREIGN KEY (usuario_id) REFERENCES Usuarios(id) ON DELETE CASCADE
                )
            '''))
            conn.execute(text('''
                CREATE TABLE IF NOT EXISTS Productos (
                    id SERIAL PRIMARY KEY,
                    usuario_id INTEGER NOT NULL,
                    nombre TEXT NOT NULL,
                    descripcion TEXT,
                    codigo_barras VARCHAR(100),
                    codigo_ref VARCHAR(100),
                    categoria TEXT DEFAULT 'General',
                    unidad_medida VARCHAR(20) DEFAULT 'Unidad',
                    stock_actual NUMERIC(12,3) NOT NULL DEFAULT 0,
                    stock_minimo NUMERIC(12,3) NOT NULL DEFAULT 2,
                    costo_compra NUMERIC(12,2) DEFAULT 0,
                    precio_venta NUMERIC(12,2) NOT NULL DEFAULT 0,
                    activo BOOLEAN DEFAULT TRUE,
                    fecha_registro TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    alegra_item_id TEXT,
                    iva_porcentaje NUMERIC(5,2) DEFAULT 0,
                    FOREIGN KEY (usuario_id) REFERENCES Usuarios(id) ON DELETE CASCADE
                )
            '''))
            conn.execute(text('''
                CREATE TABLE IF NOT EXISTS Clientes (
                    id SERIAL PRIMARY KEY,
                    usuario_id INTEGER NOT NULL,
                    nombre TEXT NOT NULL,
                    telefono TEXT,
                    email TEXT,
                    direccion TEXT,
                    documento TEXT,
                    tipo_documento TEXT DEFAULT 'CC',
                    cupo_credito NUMERIC(12,2) DEFAULT 0,
                    activo BOOLEAN DEFAULT TRUE,
                    fecha_registro TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    alegra_contact_id TEXT,
                    FOREIGN KEY (usuario_id) REFERENCES Usuarios(id) ON DELETE CASCADE
                )
            '''))
            conn.execute(text('''
                CREATE TABLE IF NOT EXISTS Proveedores (
                    id SERIAL PRIMARY KEY,
                    usuario_id INTEGER NOT NULL,
                    nombre TEXT NOT NULL,
                    telefono TEXT,
                    email TEXT,
                    contacto TEXT,
                    nit TEXT,
                    activo BOOLEAN DEFAULT TRUE,
                    FOREIGN KEY (usuario_id) REFERENCES Usuarios(id) ON DELETE CASCADE
                )
            '''))
            conn.execute(text('''
                CREATE TABLE IF NOT EXISTS Ventas (
                    id SERIAL PRIMARY KEY,
                    usuario_id INTEGER NOT NULL,
                    cliente_id INTEGER,
                    cajero_id INTEGER,
                    fecha TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    subtotal NUMERIC(12,2) NOT NULL DEFAULT 0,
                    descuento NUMERIC(12,2) DEFAULT 0,
                    total NUMERIC(12,2) NOT NULL DEFAULT 0,
                    tipo_pago VARCHAR(20) CHECK(tipo_pago IN ('Efectivo','Transferencia','Credito','Mixto')) DEFAULT 'Efectivo',
                    monto_efectivo NUMERIC(12,2) DEFAULT 0,
                    monto_transferencia NUMERIC(12,2) DEFAULT 0,
                    cambio NUMERIC(12,2) DEFAULT 0,
                    estado VARCHAR(20) CHECK(estado IN ('Pagada','Credito','Anulada')) DEFAULT 'Pagada',
                    notas TEXT,
                    factura_alegra_id TEXT,
                    factura_cufe TEXT,
                    factura_pdf_url TEXT,
                    factura_xml_url TEXT,
                    factura_estado TEXT,
                    factura_prefijo TEXT,
                    factura_numero TEXT,
                    nota_credito_alegra_id TEXT,
                    nota_credito_pdf_url TEXT,
                    nota_credito_xml_url TEXT,
                    nota_credito_prefijo TEXT,
                    nota_credito_numero TEXT,
                    FOREIGN KEY (usuario_id) REFERENCES Usuarios(id) ON DELETE CASCADE,
                    FOREIGN KEY (cliente_id) REFERENCES Clientes(id),
                    FOREIGN KEY (cajero_id) REFERENCES Cajeros(id)
                )
            '''))
            conn.execute(text('''
                CREATE TABLE IF NOT EXISTS Detalles_Venta (
                    id SERIAL PRIMARY KEY,
                    venta_id INTEGER NOT NULL,
                    producto_id INTEGER,
                    nombre_producto TEXT NOT NULL,
                    codigo_barras VARCHAR(100),
                    cantidad INTEGER NOT NULL DEFAULT 1,
                    precio_unitario NUMERIC(12,2) NOT NULL DEFAULT 0,
                    costo_unitario NUMERIC(12,2) DEFAULT 0,
                    descuento NUMERIC(12,2) DEFAULT 0,
                    subtotal NUMERIC(12,2) NOT NULL DEFAULT 0,
                    iva_porcentaje NUMERIC(5,2) DEFAULT 0,
                    FOREIGN KEY (venta_id) REFERENCES Ventas(id) ON DELETE CASCADE,
                    FOREIGN KEY (producto_id) REFERENCES Productos(id)
                )
            '''))
            conn.execute(text('''
                CREATE TABLE IF NOT EXISTS Entradas_Inventario (
                    id SERIAL PRIMARY KEY,
                    usuario_id INTEGER NOT NULL,
                    proveedor_id INTEGER,
                    fecha TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    numero_factura TEXT,
                    total_compra NUMERIC(12,2) DEFAULT 0,
                    notas TEXT,
                    FOREIGN KEY (usuario_id) REFERENCES Usuarios(id) ON DELETE CASCADE,
                    FOREIGN KEY (proveedor_id) REFERENCES Proveedores(id)
                )
            '''))
            conn.execute(text('''
                CREATE TABLE IF NOT EXISTS Detalles_Entrada (
                    id SERIAL PRIMARY KEY,
                    entrada_id INTEGER NOT NULL,
                    producto_id INTEGER NOT NULL,
                    cantidad INTEGER NOT NULL DEFAULT 1,
                    costo_unitario NUMERIC(12,2) NOT NULL DEFAULT 0,
                    subtotal NUMERIC(12,2) NOT NULL DEFAULT 0,
                    FOREIGN KEY (entrada_id) REFERENCES Entradas_Inventario(id) ON DELETE CASCADE,
                    FOREIGN KEY (producto_id) REFERENCES Productos(id)
                )
            '''))
            conn.execute(text('''
                CREATE TABLE IF NOT EXISTS Creditos (
                    id SERIAL PRIMARY KEY,
                    usuario_id INTEGER NOT NULL,
                    venta_id INTEGER NOT NULL,
                    cliente_id INTEGER NOT NULL,
                    total NUMERIC(12,2) NOT NULL DEFAULT 0,
                    saldo_pendiente NUMERIC(12,2) NOT NULL DEFAULT 0,
                    fecha_inicio DATE NOT NULL,
                    fecha_limite DATE,
                    tipo_cuota VARCHAR(20) CHECK(tipo_cuota IN ('Libre','Semanal','Quincenal','Mensual')) DEFAULT 'Libre',
                    valor_cuota NUMERIC(12,2) DEFAULT 0,
                    estado VARCHAR(20) CHECK(estado IN ('Activo','Pagado','Vencido')) DEFAULT 'Activo',
                    FOREIGN KEY (usuario_id) REFERENCES Usuarios(id) ON DELETE CASCADE,
                    FOREIGN KEY (venta_id) REFERENCES Ventas(id),
                    FOREIGN KEY (cliente_id) REFERENCES Clientes(id)
                )
            '''))
            conn.execute(text('''
                CREATE TABLE IF NOT EXISTS Abonos (
                    id SERIAL PRIMARY KEY,
                    credito_id INTEGER NOT NULL,
                    usuario_id INTEGER NOT NULL,
                    monto NUMERIC(12,2) NOT NULL DEFAULT 0,
                    fecha TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    notas TEXT,
                    FOREIGN KEY (credito_id) REFERENCES Creditos(id) ON DELETE CASCADE,
                    FOREIGN KEY (usuario_id) REFERENCES Usuarios(id)
                )
            '''))
            conn.execute(text('''
                CREATE TABLE IF NOT EXISTS Cierres_Caja (
                    id SERIAL PRIMARY KEY,
                    usuario_id INTEGER NOT NULL,
                    fecha DATE NOT NULL,
                    total_ventas INTEGER DEFAULT 0,
                    total_efectivo NUMERIC(12,2) DEFAULT 0,
                    total_transferencias NUMERIC(12,2) DEFAULT 0,
                    total_creditos NUMERIC(12,2) DEFAULT 0,
                    total_descuentos NUMERIC(12,2) DEFAULT 0,
                    efectivo_contado NUMERIC(12,2) DEFAULT 0,
                    diferencia NUMERIC(12,2) DEFAULT 0,
                    notas TEXT,
                    cerrado_por TEXT,
                    cajero_id INTEGER,
                    fecha_cierre TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    FOREIGN KEY (usuario_id) REFERENCES Usuarios(id) ON DELETE CASCADE,
                    FOREIGN KEY (cajero_id) REFERENCES Cajeros(id)
                )
            '''))

        # ==========================================
        # ÍNDICES
        # ==========================================
        conn.execute(text("CREATE INDEX IF NOT EXISTS idx_usuarios_token ON Usuarios(token_sesion)"))
        conn.execute(text("CREATE INDEX IF NOT EXISTS idx_productos_usuario ON Productos(usuario_id)"))
        conn.execute(text("CREATE INDEX IF NOT EXISTS idx_productos_barras ON Productos(codigo_barras)"))
        conn.execute(text("CREATE INDEX IF NOT EXISTS idx_productos_usuario_barras ON Productos(usuario_id, codigo_barras)"))
        conn.execute(text("CREATE INDEX IF NOT EXISTS idx_clientes_usuario ON Clientes(usuario_id)"))
        conn.execute(text("CREATE INDEX IF NOT EXISTS idx_proveedores_usuario ON Proveedores(usuario_id)"))
        conn.execute(text("CREATE INDEX IF NOT EXISTS idx_ventas_usuario ON Ventas(usuario_id)"))
        conn.execute(text("CREATE INDEX IF NOT EXISTS idx_ventas_usuario_fecha ON Ventas(usuario_id, fecha)"))
        conn.execute(text("CREATE INDEX IF NOT EXISTS idx_ventas_cliente ON Ventas(cliente_id)"))
        conn.execute(text("CREATE INDEX IF NOT EXISTS idx_detalles_venta ON Detalles_Venta(venta_id)"))
        conn.execute(text("CREATE INDEX IF NOT EXISTS idx_entradas_usuario ON Entradas_Inventario(usuario_id)"))
        conn.execute(text("CREATE INDEX IF NOT EXISTS idx_detalles_entrada ON Detalles_Entrada(entrada_id)"))
        conn.execute(text("CREATE INDEX IF NOT EXISTS idx_creditos_usuario ON Creditos(usuario_id)"))
        conn.execute(text("CREATE INDEX IF NOT EXISTS idx_creditos_cliente ON Creditos(cliente_id)"))
        conn.execute(text("CREATE INDEX IF NOT EXISTS idx_creditos_estado ON Creditos(usuario_id, estado)"))
        conn.execute(text("CREATE INDEX IF NOT EXISTS idx_abonos_credito ON Abonos(credito_id)"))
        conn.execute(text("CREATE INDEX IF NOT EXISTS idx_cajeros_usuario ON Cajeros(usuario_id)"))
        conn.execute(text("CREATE INDEX IF NOT EXISTS idx_cierres_caja_usuario ON Cierres_Caja(usuario_id, fecha)"))

        # ==========================================
        # MIGRACIONES SEGURAS
        # ==========================================
        try:
            if is_sqlite:
                cols = [r[1] for r in conn.execute(text("PRAGMA table_info(Usuarios)")).fetchall()]
                cols_p = [r[1] for r in conn.execute(text("PRAGMA table_info(Productos)")).fetchall()]
                if 'token_sesion' not in cols:
                    conn.execute(text("ALTER TABLE Usuarios ADD COLUMN token_sesion TEXT"))
                if 'logo_path' not in cols:
                    conn.execute(text("ALTER TABLE Usuarios ADD COLUMN logo_path TEXT"))
                if 'nit' not in cols:
                    conn.execute(text("ALTER TABLE Usuarios ADD COLUMN nit TEXT"))
                if 'telefono' not in cols:
                    conn.execute(text("ALTER TABLE Usuarios ADD COLUMN telefono TEXT"))
                if 'direccion' not in cols:
                    conn.execute(text("ALTER TABLE Usuarios ADD COLUMN direccion TEXT"))
                if 'unidad_medida' not in cols_p:
                    conn.execute(text("ALTER TABLE Productos ADD COLUMN unidad_medida TEXT DEFAULT 'Unidad'"))
                cols_v = [r[1] for r in conn.execute(text("PRAGMA table_info(Ventas)")).fetchall()]
                if 'cajero_id' not in cols_v:
                    conn.execute(text("ALTER TABLE Ventas ADD COLUMN cajero_id INTEGER"))
                cols_caj = [r[1] for r in conn.execute(text("PRAGMA table_info(Cajeros)")).fetchall()]
                if 'usuario_login' not in cols_caj:
                    conn.execute(text("ALTER TABLE Cajeros ADD COLUMN usuario_login TEXT"))
                if 'token_sesion' not in cols_caj:
                    conn.execute(text("ALTER TABLE Cajeros ADD COLUMN token_sesion TEXT"))
                cols_dv = [r[1] for r in conn.execute(text("PRAGMA table_info(Detalles_Venta)")).fetchall()]
                if 'descuento' not in cols_dv:
                    conn.execute(text("ALTER TABLE Detalles_Venta ADD COLUMN descuento REAL DEFAULT 0"))
                cols_cc = [r[1] for r in conn.execute(text("PRAGMA table_info(Cierres_Caja)")).fetchall()]
                if 'total_descuentos' not in cols_cc:
                    conn.execute(text("ALTER TABLE Cierres_Caja ADD COLUMN total_descuentos REAL DEFAULT 0"))
                if 'cajero_id' not in cols_cc:
                    conn.execute(text("ALTER TABLE Cierres_Caja ADD COLUMN cajero_id INTEGER"))
                if 'alegra_item_id' not in cols_p:
                    conn.execute(text("ALTER TABLE Productos ADD COLUMN alegra_item_id TEXT"))
                cols_cli = [r[1] for r in conn.execute(text("PRAGMA table_info(Clientes)")).fetchall()]
                if 'tipo_documento' not in cols_cli:
                    conn.execute(text("ALTER TABLE Clientes ADD COLUMN tipo_documento TEXT DEFAULT 'CC'"))
                if 'alegra_contact_id' not in cols_cli:
                    conn.execute(text("ALTER TABLE Clientes ADD COLUMN alegra_contact_id TEXT"))
                if 'factura_alegra_id' not in cols_v:
                    conn.execute(text("ALTER TABLE Ventas ADD COLUMN factura_alegra_id TEXT"))
                if 'factura_cufe' not in cols_v:
                    conn.execute(text("ALTER TABLE Ventas ADD COLUMN factura_cufe TEXT"))
                if 'factura_pdf_url' not in cols_v:
                    conn.execute(text("ALTER TABLE Ventas ADD COLUMN factura_pdf_url TEXT"))
                if 'factura_xml_url' not in cols_v:
                    conn.execute(text("ALTER TABLE Ventas ADD COLUMN factura_xml_url TEXT"))
                if 'factura_estado' not in cols_v:
                    conn.execute(text("ALTER TABLE Ventas ADD COLUMN factura_estado TEXT"))
                if 'factura_prefijo' not in cols_v:
                    conn.execute(text("ALTER TABLE Ventas ADD COLUMN factura_prefijo TEXT"))
                if 'factura_numero' not in cols_v:
                    conn.execute(text("ALTER TABLE Ventas ADD COLUMN factura_numero TEXT"))
                if 'alegra_email' not in cols:
                    conn.execute(text("ALTER TABLE Usuarios ADD COLUMN alegra_email TEXT"))
                if 'alegra_token' not in cols:
                    conn.execute(text("ALTER TABLE Usuarios ADD COLUMN alegra_token TEXT"))
                if 'fe_habilitada' not in cols:
                    conn.execute(text("ALTER TABLE Usuarios ADD COLUMN fe_habilitada BOOLEAN DEFAULT 0"))
                if 'nota_credito_alegra_id' not in cols_v:
                    conn.execute(text("ALTER TABLE Ventas ADD COLUMN nota_credito_alegra_id TEXT"))
                if 'nota_credito_pdf_url' not in cols_v:
                    conn.execute(text("ALTER TABLE Ventas ADD COLUMN nota_credito_pdf_url TEXT"))
                if 'nota_credito_xml_url' not in cols_v:
                    conn.execute(text("ALTER TABLE Ventas ADD COLUMN nota_credito_xml_url TEXT"))
                if 'nota_credito_prefijo' not in cols_v:
                    conn.execute(text("ALTER TABLE Ventas ADD COLUMN nota_credito_prefijo TEXT"))
                if 'nota_credito_numero' not in cols_v:
                    conn.execute(text("ALTER TABLE Ventas ADD COLUMN nota_credito_numero TEXT"))
                if 'iva_porcentaje' not in cols_p:
                    conn.execute(text("ALTER TABLE Productos ADD COLUMN iva_porcentaje REAL DEFAULT 0"))
                if 'iva_porcentaje' not in cols_dv:
                    conn.execute(text("ALTER TABLE Detalles_Venta ADD COLUMN iva_porcentaje REAL DEFAULT 0"))
            else:
                conn.execute(text("ALTER TABLE Usuarios ADD COLUMN IF NOT EXISTS token_sesion VARCHAR(255)"))
                conn.execute(text("ALTER TABLE Usuarios ADD COLUMN IF NOT EXISTS logo_path TEXT"))
                conn.execute(text("ALTER TABLE Usuarios ADD COLUMN IF NOT EXISTS nit TEXT"))
                conn.execute(text("ALTER TABLE Usuarios ADD COLUMN IF NOT EXISTS telefono TEXT"))
                conn.execute(text("ALTER TABLE Usuarios ADD COLUMN IF NOT EXISTS direccion TEXT"))
                conn.execute(text("ALTER TABLE Ventas ADD COLUMN IF NOT EXISTS cajero_id INTEGER"))
                conn.execute(text("ALTER TABLE Cajeros ADD COLUMN IF NOT EXISTS usuario_login VARCHAR(50)"))
                conn.execute(text("ALTER TABLE Cajeros ADD COLUMN IF NOT EXISTS token_sesion VARCHAR(255)"))
                conn.execute(text("ALTER TABLE Productos ADD COLUMN IF NOT EXISTS unidad_medida VARCHAR(20) DEFAULT 'Unidad'"))
                conn.execute(text("ALTER TABLE Productos ALTER COLUMN stock_actual TYPE NUMERIC(12,3)"))
                conn.execute(text("ALTER TABLE Productos ALTER COLUMN stock_minimo TYPE NUMERIC(12,3)"))
                conn.execute(text("ALTER TABLE Detalles_Venta ADD COLUMN IF NOT EXISTS descuento NUMERIC(12,2) DEFAULT 0"))
                conn.execute(text("ALTER TABLE Cierres_Caja ADD COLUMN IF NOT EXISTS total_descuentos NUMERIC(12,2) DEFAULT 0"))
                conn.execute(text("ALTER TABLE Cierres_Caja ADD COLUMN IF NOT EXISTS cajero_id INTEGER"))
                conn.execute(text("ALTER TABLE Productos ADD COLUMN IF NOT EXISTS alegra_item_id TEXT"))
                conn.execute(text("ALTER TABLE Clientes ADD COLUMN IF NOT EXISTS tipo_documento TEXT DEFAULT 'CC'"))
                conn.execute(text("ALTER TABLE Clientes ADD COLUMN IF NOT EXISTS alegra_contact_id TEXT"))
                conn.execute(text("ALTER TABLE Ventas ADD COLUMN IF NOT EXISTS factura_alegra_id TEXT"))
                conn.execute(text("ALTER TABLE Ventas ADD COLUMN IF NOT EXISTS factura_cufe TEXT"))
                conn.execute(text("ALTER TABLE Ventas ADD COLUMN IF NOT EXISTS factura_pdf_url TEXT"))
                conn.execute(text("ALTER TABLE Ventas ADD COLUMN IF NOT EXISTS factura_xml_url TEXT"))
                conn.execute(text("ALTER TABLE Ventas ADD COLUMN IF NOT EXISTS factura_estado TEXT"))
                conn.execute(text("ALTER TABLE Ventas ADD COLUMN IF NOT EXISTS factura_prefijo TEXT"))
                conn.execute(text("ALTER TABLE Ventas ADD COLUMN IF NOT EXISTS factura_numero TEXT"))
                conn.execute(text("ALTER TABLE Usuarios ADD COLUMN IF NOT EXISTS alegra_email TEXT"))
                conn.execute(text("ALTER TABLE Usuarios ADD COLUMN IF NOT EXISTS alegra_token TEXT"))
                conn.execute(text("ALTER TABLE Usuarios ADD COLUMN IF NOT EXISTS fe_habilitada BOOLEAN DEFAULT FALSE"))
                conn.execute(text("ALTER TABLE Ventas ADD COLUMN IF NOT EXISTS nota_credito_alegra_id TEXT"))
                conn.execute(text("ALTER TABLE Ventas ADD COLUMN IF NOT EXISTS nota_credito_pdf_url TEXT"))
                conn.execute(text("ALTER TABLE Ventas ADD COLUMN IF NOT EXISTS nota_credito_xml_url TEXT"))
                conn.execute(text("ALTER TABLE Ventas ADD COLUMN IF NOT EXISTS nota_credito_prefijo TEXT"))
                conn.execute(text("ALTER TABLE Ventas ADD COLUMN IF NOT EXISTS nota_credito_numero TEXT"))
                conn.execute(text("ALTER TABLE Productos ADD COLUMN IF NOT EXISTS iva_porcentaje NUMERIC(5,2) DEFAULT 0"))
                conn.execute(text("ALTER TABLE Detalles_Venta ADD COLUMN IF NOT EXISTS iva_porcentaje NUMERIC(5,2) DEFAULT 0"))
        except Exception as e:
            # No se debe tumbar el arranque de la app por una migración que
            # falle, pero silenciarla del todo (como antes) hace invisibles
            # errores reales - queda al menos en los logs de Streamlit Cloud.
            print(f"[init_db] Error en migraciones: {e}")

        # Estos índices dependen de columnas agregadas arriba por la migración
        # (usuario_login, token_sesion) - deben crearse DESPUÉS de que esas
        # columnas existan, nunca antes, o fallan en una base ya existente
        # que todavía no las tenía.
        try:
            conn.execute(text("CREATE UNIQUE INDEX IF NOT EXISTS idx_cajeros_login ON Cajeros(usuario_login)"))
            conn.execute(text("CREATE INDEX IF NOT EXISTS idx_cajeros_token ON Cajeros(token_sesion)"))
        except Exception as e:
            print(f"[init_db] Error creando índices de Cajeros: {e}")

    return True

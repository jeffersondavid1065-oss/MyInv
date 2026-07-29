import streamlit as st
from sqlalchemy import create_engine, text
import os

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
LOCAL_DB_PATH = os.path.join(BASE_DIR, "myalmacen.db")


@st.cache_resource
def obtener_conexion():
    """
    Crea el engine de SQLAlchemy UNA sola vez por proceso.
    Usa el pooler de Supabase (puerto 6543, modo Transaction).
    """
    try:
        db_url = st.secrets["postgres"]["url"]
        engine = create_engine(
            db_url,
            pool_size=10,
            max_overflow=20,
            pool_pre_ping=True,
            pool_recycle=300,
        )
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
    except Exception as e:
        st.warning(
            f"⚠️ No se pudo conectar a Postgres/Supabase, usando base local de respaldo. "
            f"Detalle: {e}"
        )
        engine = create_engine(f"sqlite:///{LOCAL_DB_PATH}")

    return engine


@st.cache_resource
def init_db():
    """
    Crea todas las tablas si no existen.
    Se ejecuta UNA sola vez por proceso gracias a cache_resource.
    Para forzar re-ejecución: Reboot app en Streamlit Cloud.
    """
    engine = obtener_conexion()
    is_sqlite = "sqlite" in str(engine.url)

    with engine.begin() as conn:
        if is_sqlite:
            # ==========================================
            # SQLITE (desarrollo local)
            # ==========================================
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
                    fecha_registro TIMESTAMP DEFAULT CURRENT_TIMESTAMP
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
                    stock_actual INTEGER NOT NULL DEFAULT 0,
                    stock_minimo INTEGER NOT NULL DEFAULT 2,
                    costo_compra REAL DEFAULT 0,
                    precio_venta REAL NOT NULL DEFAULT 0,
                    activo BOOLEAN DEFAULT 1,
                    fecha_registro TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
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
                    cupo_credito REAL DEFAULT 0,
                    activo BOOLEAN DEFAULT 1,
                    fecha_registro TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
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
                    FOREIGN KEY (usuario_id) REFERENCES Usuarios(id) ON DELETE CASCADE,
                    FOREIGN KEY (cliente_id) REFERENCES Clientes(id)
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
                    subtotal REAL NOT NULL DEFAULT 0,
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
                    efectivo_contado REAL DEFAULT 0,
                    diferencia REAL DEFAULT 0,
                    notas TEXT,
                    cerrado_por TEXT,
                    fecha_cierre TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    FOREIGN KEY (usuario_id) REFERENCES Usuarios(id) ON DELETE CASCADE
                )
            '''))

        else:
            # ==========================================
            # POSTGRES / SUPABASE
            # ==========================================
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
                    fecha_registro TIMESTAMP DEFAULT CURRENT_TIMESTAMP
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
                    stock_actual INTEGER NOT NULL DEFAULT 0,
                    stock_minimo INTEGER NOT NULL DEFAULT 2,
                    costo_compra NUMERIC(12,2) DEFAULT 0,
                    precio_venta NUMERIC(12,2) NOT NULL DEFAULT 0,
                    activo BOOLEAN DEFAULT TRUE,
                    fecha_registro TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
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
                    cupo_credito NUMERIC(12,2) DEFAULT 0,
                    activo BOOLEAN DEFAULT TRUE,
                    fecha_registro TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
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
                    FOREIGN KEY (usuario_id) REFERENCES Usuarios(id) ON DELETE CASCADE,
                    FOREIGN KEY (cliente_id) REFERENCES Clientes(id)
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
                    subtotal NUMERIC(12,2) NOT NULL DEFAULT 0,
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
                    efectivo_contado NUMERIC(12,2) DEFAULT 0,
                    diferencia NUMERIC(12,2) DEFAULT 0,
                    notas TEXT,
                    cerrado_por TEXT,
                    fecha_cierre TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    FOREIGN KEY (usuario_id) REFERENCES Usuarios(id) ON DELETE CASCADE
                )
            '''))

        # ==========================================
        # ÍNDICES (SQLite y Postgres)
        # ==========================================
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
        conn.execute(text("CREATE INDEX IF NOT EXISTS idx_cierres_caja_usuario ON Cierres_Caja(usuario_id, fecha)"))
        conn.execute(text("CREATE INDEX IF NOT EXISTS idx_creditos_usuario ON Creditos(usuario_id)"))
        conn.execute(text("CREATE INDEX IF NOT EXISTS idx_creditos_cliente ON Creditos(cliente_id)"))
        conn.execute(text("CREATE INDEX IF NOT EXISTS idx_creditos_estado ON Creditos(usuario_id, estado)"))
        conn.execute(text("CREATE INDEX IF NOT EXISTS idx_abonos_credito ON Abonos(credito_id)"))

        # ==========================================
        # MIGRACIONES SEGURAS
        # Agrega columnas nuevas sin romper la app
        # si la tabla ya existía sin esas columnas.
        # ==========================================
        try:
            if is_sqlite:
                cols = [r[1] for r in conn.execute(text("PRAGMA table_info(Usuarios)")).fetchall()]
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
            else:
                conn.execute(text("ALTER TABLE Usuarios ADD COLUMN IF NOT EXISTS token_sesion VARCHAR(255)"))
                conn.execute(text("ALTER TABLE Usuarios ADD COLUMN IF NOT EXISTS logo_path TEXT"))
                conn.execute(text("ALTER TABLE Usuarios ADD COLUMN IF NOT EXISTS nit TEXT"))
                conn.execute(text("ALTER TABLE Usuarios ADD COLUMN IF NOT EXISTS telefono TEXT"))
                conn.execute(text("ALTER TABLE Usuarios ADD COLUMN IF NOT EXISTS direccion TEXT"))

            conn.execute(text("CREATE INDEX IF NOT EXISTS idx_usuarios_token ON Usuarios(token_sesion)"))
        except Exception:
            pass

    return True

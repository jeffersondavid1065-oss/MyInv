import streamlit as st
import pandas as pd
import bcrypt
import hashlib
import uuid
from sqlalchemy import text
from db import obtener_conexion, init_db
from queries import obtener_metricas_dashboard
from datetime import datetime, date

# ==========================================
# 1. CONFIGURACIÓN DE PÁGINA
# ==========================================
st.set_page_config(
    page_title="MyAlmacén",
    layout="wide",
    initial_sidebar_state="expanded" if st.session_state.get("auth", {}).get("logged", False) else "collapsed"
)

init_db()

# ==========================================
# 2. SESSION STATE
# ==========================================
if "auth" not in st.session_state:
    st.session_state.auth = {
        "logged": False,
        "user_id": None,
        "nombre_negocio": None,
        "email": None,
        "token": None,
    }

# ==========================================
# 3. AUTO-LOGIN POR TOKEN EN URL
# ==========================================
if not st.session_state.auth["logged"]:
    token_en_url = st.query_params.get("token")
    if token_en_url:
        try:
            engine_verify = obtener_conexion()
            with engine_verify.connect() as conn:
                usuario = conn.execute(
                    text("SELECT id, nombre_negocio, email FROM Usuarios WHERE token_sesion = :token AND activo = TRUE"),
                    {"token": token_en_url}
                ).fetchone()
            if usuario:
                st.session_state.auth = {
                    "logged": True,
                    "user_id": usuario[0],
                    "nombre_negocio": usuario[1],
                    "email": usuario[2],
                    "token": token_en_url,
                }
                st.rerun()
        except Exception:
            pass

# ==========================================
# 4. ESTILOS CSS
# ==========================================
is_logged = st.session_state.auth["logged"]

st.markdown("""
    <style>
    /* ==========================================
       BLOQUEO TOTAL DE ESQUINA SUPERIOR DERECHA
       Oculta: GitHub, Deploy, botón de opciones (3 puntos)
       ========================================== */

    /* Máscara sólida que cubre toda la esquina */
    header::after {
        content: "";
        position: fixed !important;
        top: 0 !important;
        right: 0 !important;
        width: 350px !important;
        height: 60px !important;
        background-color: var(--background-color) !important;
        z-index: 9999999 !important;
        pointer-events: all !important;
    }

    /* Ocultar botón de GitHub */
    [data-testid="stToolbar"] { display: none !important; }
    
    /* Ocultar menú de deploy y opciones */
    [data-testid="stDecoration"] { display: none !important; }
    
    /* Ocultar botón de opciones (3 puntos arriba a la derecha) */
    #MainMenu { visibility: hidden !important; }
    
    /* Ocultar footer de Streamlit */
    footer { visibility: hidden !important; }

    /* Ocultar header de "made with streamlit" */
    [data-testid="stHeader"] { background: transparent !important; }

    /* Animaciones */
    @keyframes fade-in-up {
        0% { opacity: 0; transform: translateY(20px); }
        100% { opacity: 1; transform: translateY(0); }
    }
    [data-testid="stAppViewBlockContainer"] { animation: fade-in-up 0.6s ease-out; }
    """ + ("" if is_logged else """
    /* Ocultar sidebar cuando no está logueado */
    [data-testid="stSidebar"] { display: none !important; }
    [data-testid="stSidebarCollapsedControl"] { display: none !important; }
    """) + """
    </style>
""", unsafe_allow_html=True)

engine = obtener_conexion()

def formato_cop(numero):
    return f"${float(numero):,.0f}".replace(",", ".")

# ==========================================
# 5. PANTALLAS
# ==========================================
if not is_logged:
    # ----------------
    # LOGIN
    # ----------------
    col1, col2, col3 = st.columns([1, 2, 1])
    with col2:
        st.markdown("""
            <div style='text-align: center; margin-bottom: 25px;'>
                <h1 style='font-weight: 800; font-size: 2.5rem; letter-spacing: -1px; margin-bottom: 0;'>
                    My<span style='color: #FF4B4B;'>Almacén</span>
                </h1>
                <p style='color: #64748b; font-size: 0.95rem; margin-top: 5px;'>
                    Gestión inteligente para almacenes y ferreterías
                </p>
            </div>
        """, unsafe_allow_html=True)

        with st.container(border=True):
            st.subheader("Iniciar Sesión")
            email_login = st.text_input("Correo Electrónico", key="login_email")
            pass_login = st.text_input("Contraseña", type="password", key="login_pass")

            st.markdown("")
            if st.button("Ingresar", use_container_width=True, type="primary"):
                if email_login and pass_login:
                    try:
                        with engine.connect() as conn:
                            user = conn.execute(
                                text("SELECT id, nombre_negocio, password, fecha_pago_limite FROM Usuarios WHERE email = :email"),
                                {"email": email_login}
                            ).fetchone()

                        credenciales_ok = False
                        if user:
                            hash_guardado = user[2]
                            if hash_guardado.startswith(("$2a$", "$2b$", "$2y$")):
                                credenciales_ok = bcrypt.checkpw(pass_login.encode(), hash_guardado.encode())
                            else:
                                hash_sha = hashlib.sha256(pass_login.encode()).hexdigest()
                                if hash_sha == hash_guardado:
                                    credenciales_ok = True
                                    nuevo_hash = bcrypt.hashpw(pass_login.encode(), bcrypt.gensalt()).decode()
                                    with engine.begin() as conn_m:
                                        conn_m.execute(
                                            text("UPDATE Usuarios SET password = :pw WHERE id = :uid"),
                                            {"pw": nuevo_hash, "uid": user[0]}
                                        )

                        if credenciales_ok:
                            fecha_limite = user[3]
                            hoy = date.today()
                            if fecha_limite is None or fecha_limite < hoy:
                                st.error("Tu suscripción está inactiva. Contacta al administrador.")
                            else:
                                token_nuevo = str(uuid.uuid4())
                                with engine.begin() as conn_t:
                                    conn_t.execute(
                                        text("UPDATE Usuarios SET token_sesion = :token WHERE id = :uid"),
                                        {"token": token_nuevo, "uid": user[0]}
                                    )
                                st.session_state.auth = {
                                    "logged": True,
                                    "user_id": user[0],
                                    "nombre_negocio": user[1],
                                    "email": email_login,
                                    "token": token_nuevo,
                                }
                                st.query_params["token"] = token_nuevo
                                st.rerun()
                        else:
                            st.error("Correo o contraseña incorrectos.")
                    except Exception as e:
                        st.error(f"Error de conexión: {e}")
                else:
                    st.warning("Completa todos los campos.")

        st.markdown("")
        with st.expander("Registrar Nuevo Almacén"):
            with st.form("form_registro"):
                negocio_reg  = st.text_input("Nombre del Negocio")
                dueno_reg    = st.text_input("Nombre del Dueño")
                email_reg    = st.text_input("Correo Electrónico")
                pass_reg     = st.text_input("Contraseña", type="password")
                st.markdown("")
                if st.form_submit_button("Crear Cuenta", use_container_width=True):
                    if negocio_reg and dueno_reg and email_reg and pass_reg:
                        pass_hash = bcrypt.hashpw(pass_reg.encode(), bcrypt.gensalt()).decode()
                        try:
                            with engine.begin() as conn_reg:
                                conn_reg.execute(
                                    text("""
                                        INSERT INTO Usuarios (nombre_negocio, nombre_dueno, email, password)
                                        VALUES (:negocio, :dueno, :email, :pass)
                                    """),
                                    {"negocio": negocio_reg, "dueno": dueno_reg, "email": email_reg, "pass": pass_hash}
                                )
                            st.success("Cuenta creada. Contacta al administrador para activar tu suscripción.")
                        except Exception as e:
                            st.error(f"Error al registrar: {e}")
                    else:
                        st.warning("Completa todos los campos.")

else:
    # ----------------
    # DASHBOARD PRINCIPAL
    # ----------------
    user_id       = st.session_state.auth["user_id"]
    nombre_negocio = st.session_state.auth["nombre_negocio"]

    st.title("Panel Principal")
    st.markdown(f"Resumen del día para: **{nombre_negocio}**")
    st.markdown("---")

    metricas = obtener_metricas_dashboard(user_id)

    # Fila 1: Ventas del día
    col1, col2, col3, col4 = st.columns(4)
    col1.metric("Ventas Hoy", metricas["ventas_hoy"])
    col2.metric("Ingresos Hoy", formato_cop(metricas["total_ventas_hoy"]))
    col3.metric("Caja Efectivo", formato_cop(metricas["caja_efectivo"]))
    col4.metric("Transferencias", formato_cop(metricas["total_transferencia"]))

    st.markdown("---")

    # Fila 2: Alertas
    col5, col6, col7, col8 = st.columns(4)
    col5.metric("Productos Agotados", metricas["agotados"],
                delta="⚠️ Reabastecer" if metricas["agotados"] > 0 else None,
                delta_color="inverse")
    col6.metric("Por Agotarse", metricas["por_agotarse"],
                delta="⚠️ Revisar" if metricas["por_agotarse"] > 0 else None,
                delta_color="inverse")
    col7.metric("Créditos Vencidos", metricas["creditos_vencidos"],
                delta="⚠️ Cobrar" if metricas["creditos_vencidos"] > 0 else None,
                delta_color="inverse")
    col8.metric("Total en Deudas", formato_cop(metricas["total_deuda"]))

    st.markdown("---")

    # Fila 3: Margen del mes
    hoy = datetime.today()
    from queries import obtener_metricas_mes
    ingresos_mes, costos_mes, margen_mes = obtener_metricas_mes(user_id, hoy.year, hoy.month)

    col9, col10, col11 = st.columns(3)
    col9.metric("Ingresos del Mes", formato_cop(ingresos_mes))
    col10.metric("Costo de Ventas", formato_cop(costos_mes))
    col11.metric("Margen Bruto del Mes", formato_cop(margen_mes),
                 delta_color="inverse" if margen_mes < 0 else "normal")

    st.markdown("---")

    # Alertas visuales
    if metricas["creditos_vencidos"] > 0:
        st.error(f"⚠️ Tienes **{metricas['creditos_vencidos']} créditos vencidos**. Ve a Clientes y Créditos para gestionar los cobros.")

    if metricas["agotados"] > 0:
        st.warning(f"📦 Tienes **{metricas['agotados']} productos agotados**. Ve a Inventario para registrar entradas.")

    if metricas["por_agotarse"] > 0:
        st.info(f"🔔 **{metricas['por_agotarse']} productos** están por agotarse pronto.")

    st.markdown("---")

    # Accesos rápidos
    col_a1, col_a2 = st.columns(2)
    with col_a1:
        with st.container(border=True):
            st.subheader("Accesos Rápidos")
            st.write("• **Punto de Venta** — registrar ventas al mostrador")
            st.write("• **Inventario** — agregar productos y entradas de mercancía")
            st.write("• **Clientes y Créditos** — gestionar deudas y abonos")
            st.write("• **Reportes** — ventas del día, semana o mes en Excel")

    with col_a2:
        with st.container(border=True):
            st.subheader("Resumen del Negocio")
            st.write(f"📅 **Fecha:** {hoy.strftime('%d/%m/%Y')}")
            st.write(f"🏪 **Negocio:** {nombre_negocio}")
            st.write(f"💰 **Ventas hoy:** {metricas['ventas_hoy']} transacciones")
            st.write(f"📊 **Margen del mes:** {formato_cop(margen_mes)}")

    st.markdown("")
    if st.button("Cerrar Sesión"):
        try:
            with engine.begin() as conn_out:
                conn_out.execute(
                    text("UPDATE Usuarios SET token_sesion = NULL WHERE id = :uid"),
                    {"uid": user_id}
                )
        except Exception:
            pass
        st.session_state.auth = {
            "logged": False, "user_id": None,
            "nombre_negocio": None, "email": None, "token": None
        }
        if "token" in st.query_params:
            del st.query_params["token"]
        st.rerun()

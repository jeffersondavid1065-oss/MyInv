import streamlit as st
import os
from sqlalchemy import text
from db import obtener_conexion
from utils import aplicar_estilos, verificar_auth, bloquear_si_cajero
from queries import (
    obtener_credenciales_factus, guardar_credenciales_factus, eliminar_credenciales_factus, tiene_fe_habilitada,
    tiene_iva_habilitado, establecer_declara_iva, obtener_municipio_taller, guardar_municipio_taller,
)
import factus_utils

st.set_page_config(page_title="Configuración", layout="wide")
aplicar_estilos()
user_id, nombre_negocio = verificar_auth()
bloquear_si_cajero()

engine = obtener_conexion()

LOGOS_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "logos")
os.makedirs(LOGOS_DIR, exist_ok=True)

st.title("Configuración del Negocio")
st.markdown(f"Personaliza tu almacén: **{nombre_negocio}**")
st.markdown("---")

# Cargar datos actuales
with engine.connect() as conn:
    datos = conn.execute(
        text("SELECT nombre_negocio, nombre_dueno, email, nit, telefono, direccion, logo_path FROM Usuarios WHERE id = :uid"),
        {"uid": user_id}
    ).fetchone()

nombre_actual   = datos[0] if datos else ""
dueno_actual    = datos[1] if datos else ""
email_actual    = datos[2] if datos else ""
nit_actual      = datos[3] if datos else ""
tel_actual      = datos[4] if datos else ""
dir_actual      = datos[5] if datos else ""
logo_actual     = datos[6] if datos else None

tab_datos, tab_iva, tab_logo, tab_factus = st.tabs(["Datos del Negocio", "Impuestos", "Logotipo", "Facturación Electrónica"])

# ==========================================
# TAB 1: DATOS DEL NEGOCIO
# ==========================================
with tab_datos:
    st.subheader("Información del Negocio")
    st.caption("Esta información aparecerá en tus facturas, tickets y reportes PDF.")

    with st.form("form_datos_negocio"):
        col1, col2 = st.columns(2)
        with col1:
            nit_input      = st.text_input("NIT del Negocio", value=nit_actual or "", placeholder="Ej: 900123456-7")
            telefono_input = st.text_input("Teléfono", value=tel_actual or "", placeholder="Ej: 3001234567")
            email_input    = st.text_input("Email", value=email_actual or "", placeholder="contacto@negocio.com")
        with col2:
            direccion_input = st.text_input("Dirección", value=dir_actual or "", placeholder="Calle 15 # 10-25")
            ciudad_input    = st.text_input("Ciudad", placeholder="Ej: Valledupar, Cesar")

        st.markdown("")
        if st.form_submit_button("Guardar Datos", type="primary"):
            try:
                direccion_completa = direccion_input
                if ciudad_input:
                    direccion_completa = f"{direccion_input}, {ciudad_input}".strip(", ")

                with engine.begin() as conn:
                    conn.execute(text("""
                        UPDATE Usuarios
                        SET nit = :nit, telefono = :tel,
                            direccion = :dir
                        WHERE id = :uid
                    """), {
                        "nit": nit_input or None,
                        "tel": telefono_input or None,
                        "dir": direccion_completa or None,
                        "uid": user_id
                    })

                # Guardar en session_state para uso inmediato en PDFs
                if "taller_config" not in st.session_state:
                    st.session_state.taller_config = {}
                st.session_state.taller_config.update({
                    "nit": nit_input,
                    "telefono": telefono_input,
                    "direccion": direccion_completa,
                    "email": email_input,
                })
                st.success("Datos guardados. Aparecerán en tus próximas facturas.")
            except Exception as e:
                st.error(f"Error al guardar: {e}")

    # Mostrar config actual
    st.markdown("---")
    st.markdown("**Configuración actual guardada:**")
    col_d1, col_d2, col_d3 = st.columns(3)
    col_d1.info(f"**NIT:** {nit_actual or 'Sin configurar'}")
    col_d2.info(f"**Teléfono:** {tel_actual or 'Sin configurar'}")
    col_d3.info(f"**Dirección:** {dir_actual or 'Sin configurar'}")

    # Cargar config al session_state si no está
    if "taller_config" not in st.session_state:
        st.session_state.taller_config = {
            "nit": nit_actual or "",
            "telefono": tel_actual or "",
            "direccion": dir_actual or "",
            "email": email_actual or "",
            "logo_path": logo_actual,
        }

# ==========================================
# TAB IMPUESTOS: ¿DECLARA IVA?
# ==========================================
with tab_iva:
    st.subheader("Impuestos (IVA)")
    st.caption(
        "Indica si tu negocio declara IVA ante la DIAN. Esto controla si se te pide el % "
        "de IVA en tus productos (Inventario), si aparece discriminado en tus ventas y "
        "reportes, y si tus facturas electrónicas lo incluyen."
    )

    declara_actual = tiene_iva_habilitado(user_id)
    opcion_iva = st.radio(
        "¿Tu negocio declara IVA?",
        ["No declaro IVA", "Sí declaro IVA"],
        index=1 if declara_actual else 0,
        key="radio_declara_iva",
    )
    nuevo_valor_iva = (opcion_iva == "Sí declaro IVA")

    if nuevo_valor_iva != declara_actual:
        if st.button("Guardar", type="primary", key="btn_guardar_iva"):
            establecer_declara_iva(user_id, nuevo_valor_iva)
            if nuevo_valor_iva:
                st.success("Listo. Ahora puedes configurar el % de IVA en tus productos desde Inventario.")
            else:
                st.success("Listo. Ya no se mostrarán ni se cobrarán campos de IVA en tu negocio.")
            st.rerun()
    else:
        st.info(f"Configuración actual: **{opcion_iva}**")

    if not declara_actual:
        st.caption(
            "Si activas la Facturación Electrónica pero tu cuenta del proveedor está "
            "configurada como \"Responsable de IVA\", tus facturas de todas formas nunca "
            "van a incluir IVA mientras esta opción diga \"No declaro IVA\" — es a propósito, "
            "para que nunca se cobre IVA por accidente."
        )

# ==========================================
# TAB 2: LOGOTIPO
# ==========================================
with tab_logo:
    st.subheader("Logotipo del Negocio")
    st.caption("Sube tu logo para que aparezca en las facturas y tickets PDF. PNG o JPG, máximo 2MB.")

    col_l1, col_l2 = st.columns([2, 1])

    with col_l1:
        archivo_logo = st.file_uploader(
            "Subir logotipo",
            type=["png", "jpg", "jpeg"],
            help="Recomendado: imagen cuadrada, mínimo 200x200px, fondo blanco o transparente (PNG)."
        )

        if archivo_logo:
            if archivo_logo.size > 2 * 1024 * 1024:
                st.error("El archivo es muy grande. Máximo 2MB.")
            else:
                ext = archivo_logo.name.split(".")[-1].lower()
                logo_filename = f"logo_{user_id}.{ext}"
                logo_path = os.path.join(LOGOS_DIR, logo_filename)

                with open(logo_path, "wb") as f:
                    f.write(archivo_logo.getbuffer())

                with engine.begin() as conn:
                    conn.execute(
                        text("UPDATE Usuarios SET logo_path = :logo WHERE id = :uid"),
                        {"logo": logo_path, "uid": user_id}
                    )

                if "taller_config" not in st.session_state:
                    st.session_state.taller_config = {}
                st.session_state.taller_config["logo_path"] = logo_path

                st.success("Logo subido exitosamente. Aparecerá en tus próximas facturas.")
                st.image(archivo_logo, width=150)

    with col_l2:
        st.markdown("**Logo actual:**")
        if logo_actual and os.path.exists(logo_actual):
            st.image(logo_actual, width=120)
            if st.button("Eliminar logo"):
                try:
                    os.remove(logo_actual)
                    with engine.begin() as conn:
                        conn.execute(
                            text("UPDATE Usuarios SET logo_path = NULL WHERE id = :uid"),
                            {"uid": user_id}
                        )
                    if "taller_config" in st.session_state:
                        st.session_state.taller_config["logo_path"] = None
                    st.success("Logo eliminado.")
                    st.rerun()
                except Exception as e:
                    st.error(f"Error: {e}")
        else:
            st.info("Sin logotipo.\nSe mostrará un placeholder en el PDF.")

# ==========================================
# TAB 3: FACTURACIÓN ELECTRÓNICA
# ==========================================
with tab_factus:
    st.subheader("Facturación Electrónica")

    if not tiene_fe_habilitada(user_id):
        st.info(
            "Esta función todavía no está habilitada para tu negocio. "
            "Contacta al administrador para activarla."
        )
        st.stop()

    st.caption(
        "Conecta tu propia cuenta del proveedor de facturación electrónica para poder "
        "emitir facturas desde tus ventas. Cada negocio usa su propia cuenta — la "
        "factura sale a nombre de tu NIT, no del nuestro."
    )

    creds = obtener_credenciales_factus(user_id)
    conectado = bool(creds and creds.factus_client_id and creds.factus_client_secret)

    if conectado:
        st.success(f"Cuenta conectada: **{creds.factus_username or creds.factus_client_id}**")

        col_a1, col_a2 = st.columns(2)
        with col_a1:
            if st.button("Probar conexión", use_container_width=True):
                with st.spinner("Probando..."):
                    ok, msg = factus_utils.probar_conexion(
                        creds.factus_client_id, creds.factus_client_secret,
                        creds.factus_username, creds.factus_password,
                    )
                if ok:
                    st.success(msg)
                else:
                    st.error(msg)
        with col_a2:
            if st.button("Desconectar cuenta", use_container_width=True):
                eliminar_credenciales_factus(user_id)
                st.success("Cuenta desconectada.")
                st.rerun()

        st.markdown("---")
        st.markdown("**Cambiar de cuenta:**")

    with st.form("form_factus"):
        st.caption(
            "Consigue estos datos en tu cuenta de Factus: "
            "Configuración → \"Credenciales de autenticación\"."
        )
        client_id_input = st.text_input("Client ID")
        client_secret_input = st.text_input("Client Secret", type="password")
        username_input = st.text_input("Usuario (correo)", placeholder="tucorreo@ejemplo.com")
        password_input = st.text_input("Contraseña", type="password")

        if st.form_submit_button("Guardar y probar conexión", type="primary"):
            if not client_id_input or not client_secret_input or not username_input or not password_input:
                st.warning("Completa los cuatro campos.")
            else:
                with st.spinner("Validando credenciales..."):
                    ok, msg = factus_utils.probar_conexion(
                        client_id_input, client_secret_input, username_input, password_input
                    )
                if ok:
                    guardar_credenciales_factus(user_id, client_id_input, client_secret_input, username_input, password_input)
                    st.success("Credenciales válidas y guardadas. Ya puedes facturar electrónicamente.")
                    st.rerun()
                else:
                    st.error(f"No se guardó: {msg}")

    st.markdown("---")
    st.markdown("**Municipio del negocio (DIVIPOLA):**")
    st.caption(
        "Código DIVIPOLA de tu ciudad, requerido por algunas cuentas de Factus para timbrar "
        "la factura ante la DIAN (ej. 20001 para Valledupar). Se usa para todos tus clientes."
    )
    municipio_actual = obtener_municipio_taller(user_id)
    municipio_input = st.text_input(
        "Código DIVIPOLA", value=municipio_actual or "", placeholder="Ej: 20001", key="municipio_code_taller_input"
    )
    if st.button("Guardar municipio", key="btn_guardar_municipio"):
        guardar_municipio_taller(user_id, municipio_input.strip() or None)
        st.success("Municipio guardado.")
        st.rerun()

st.markdown("---")
st.caption("Después de configurar tu logo y datos, descarga una factura de prueba desde el **Punto de Venta** para verificar cómo queda.")

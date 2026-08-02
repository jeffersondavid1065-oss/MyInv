import streamlit as st
import os
from sqlalchemy import text
from db import obtener_conexion
from utils import aplicar_estilos, verificar_auth

st.set_page_config(page_title="Configuración", layout="wide")
aplicar_estilos()
user_id, nombre_negocio = verificar_auth()

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

tab_datos, tab_logo = st.tabs(["Datos del Negocio", "Logotipo"])

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

st.markdown("---")
st.caption("Después de configurar tu logo y datos, descarga una factura de prueba desde el **Punto de Venta** para verificar cómo queda.")

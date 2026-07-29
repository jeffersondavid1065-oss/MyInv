import streamlit as st


def aplicar_estilos(is_logged=True):
    """
    Aplica los estilos CSS globales de MyAlmacén en cualquier página.
    Recibe is_logged para ocultar el sidebar solo en el login.
    """
    st.markdown("""
        <style>
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

        """ + ("" if is_logged else """
        [data-testid="stSidebar"] { display: none !important; }
        [data-testid="stSidebarCollapsedControl"] { display: none !important; }
        """) + """

        [data-testid="stSidebarNav"] ul li:last-child {
            margin-top: 50px !important;
            border-top: 1px solid rgba(255, 255, 255, 0.12) !important;
            padding-top: 10px !important;
        }

        @keyframes fade-in-up {
            0% { opacity: 0; transform: translateY(20px); }
            100% { opacity: 1; transform: translateY(0); }
        }
        [data-testid="stAppViewBlockContainer"] { animation: fade-in-up 0.6s ease-out; }
        div[data-testid="stVerticalBlock"] > div { animation: fade-in-up 0.5s ease-out; }
        </style>
    """, unsafe_allow_html=True)


def verificar_auth():
    """
    Verifica que el usuario esté autenticado.
    Si no lo está, muestra mensaje y detiene la página.
    Retorna: (user_id, nombre_negocio) si está autenticado.
    """
    if "auth" not in st.session_state:
        st.session_state.auth = {
            "logged": False,
            "user_id": None,
            "nombre_negocio": None,
            "email": None,
            "token": None,
        }

    if not st.session_state.auth["logged"]:
        aplicar_estilos(is_logged=False)
        st.warning("Debes iniciar sesión en la página principal para acceder a este módulo.")
        st.stop()

    aplicar_estilos(is_logged=True)

    return (
        st.session_state.auth["user_id"],
        st.session_state.auth["nombre_negocio"]
    )

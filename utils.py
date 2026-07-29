import streamlit as st


def aplicar_estilos():
    """
    Aplica los estilos CSS globales de MyAlmacén en cualquier página.
    Llama esta función al inicio de cada página, después de set_page_config.
    
    Oculta:
    - Botón de GitHub (esquina superior derecha)
    - Menú de 3 puntos (opciones)
    - Botón de deploy
    - Footer de Streamlit
    - Acceso al código fuente
    """
    st.markdown("""
        <style>
        /* ==========================================
           BLOQUEO TOTAL DE ESQUINA SUPERIOR DERECHA
           ========================================== */

        /* Máscara sólida */
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

        /* Ocultar toolbar (GitHub, deploy) */
        [data-testid="stToolbar"] { display: none !important; }

        /* Ocultar decoración */
        [data-testid="stDecoration"] { display: none !important; }

        /* Ocultar menú principal (3 puntos) */
        #MainMenu { visibility: hidden !important; }

        /* Ocultar footer */
        footer { visibility: hidden !important; }

        /* Animación de entrada */
        @keyframes fade-in-up {
            0% { opacity: 0; transform: translateY(20px); }
            100% { opacity: 1; transform: translateY(0); }
        }
        [data-testid="stAppViewBlockContainer"] {
            animation: fade-in-up 0.6s ease-out;
        }
        div[data-testid="stVerticalBlock"] > div {
            animation: fade-in-up 0.5s ease-out;
        }
        </style>
    """, unsafe_allow_html=True)


def verificar_auth():
    """
    Verifica que el usuario esté autenticado.
    Si no lo está, muestra mensaje y detiene la página.
    Llama esta función en cada página después de aplicar_estilos().
    
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
        st.warning("Debes iniciar sesión en la página principal para acceder a este módulo.")
        st.stop()

    return (
        st.session_state.auth["user_id"],
        st.session_state.auth["nombre_negocio"]
    )

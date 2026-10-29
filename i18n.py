import streamlit as st

from translations import LANGUAGE_NAMES, TRANSLATIONS


DEFAULT_LANGUAGE = "ar"
LANGUAGE_KEY = "language"
PREFERENCE_KEY = "_language_preference"


def get_language() -> str:
    """Return the persistent language preference, defaulting to Arabic."""
    language = st.session_state.get(PREFERENCE_KEY, st.session_state.get(LANGUAGE_KEY, DEFAULT_LANGUAGE))

    if language not in TRANSLATIONS:
        return DEFAULT_LANGUAGE

    return language


def t(key: str, **params) -> str:
    """Get a translated text and fill any named placeholders."""
    language = get_language()

    text = TRANSLATIONS[language].get(
        key,
        TRANSLATIONS[DEFAULT_LANGUAGE].get(key, key),
    )

    return text.format(**params)


def render_language_selector(page_key="root") -> None:
    """Show the selector only when multiple languages are available."""
    languages = [
        code for code in LANGUAGE_NAMES
        if code in TRANSLATIONS
    ]

    if len(languages) <= 1:
        return

    preference = get_language()
    st.session_state.setdefault(PREFERENCE_KEY, preference)
    st.session_state[LANGUAGE_KEY] = preference
    widget_key = "_language_widget_" + page_key
    if st.session_state.get(widget_key) != preference:
        st.session_state[widget_key] = preference

    def remember_language():
        chosen = st.session_state[widget_key]
        st.session_state[PREFERENCE_KEY] = chosen
        st.session_state[LANGUAGE_KEY] = chosen

    st.sidebar.selectbox(
        t("app.language"),
        options=languages,
        format_func=lambda code: LANGUAGE_NAMES[code],
        key=widget_key,
        on_change=remember_language,
    )


def ensure_language(page_key="page") -> None:
    """Render on direct page runs too, once per Streamlit script run."""
    if not st.session_state.get("_entry_language_active", False):
        render_language_selector(page_key)
    apply_text_direction()


def apply_text_direction() -> None:
    """Set both directions explicitly so switching back to English also works."""
    direction,align=("rtl","right") if get_language()=="ar" else ("ltr","left")
    st.markdown(f"""<style>
        [data-testid="stAppViewContainer"], [data-testid="stSidebar"], input, textarea {{
            direction:{direction};text-align:{align};
        }}
        code,pre,[data-testid="stJson"],iframe {{direction:ltr;text-align:left;}}
    </style>""",unsafe_allow_html=True)

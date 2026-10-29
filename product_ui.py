"""Presentation only: brand assets, responsive styling and a short home hero."""
import html
from pathlib import Path

from ui_i18n import st, msg
from i18n import get_language

ASSETS=Path(__file__).resolve().parent/'assets'


def apply_brand(*,root=False):
    if st.session_state.get('_entry_language_active') and not root:return
    st.markdown((ASSETS/'theme.css').read_text(encoding='utf-8'),unsafe_allow_html=True)
    direction,align=('rtl','right') if get_language()=='ar' else ('ltr','left')
    st.markdown(f'<style>[data-testid="stMain"]{{direction:{direction};text-align:{align};}}</style>',unsafe_allow_html=True)
    st.logo(str(ASSETS/'savebite-logo.svg'),icon_image=str(ASSETS/'savebite-mark.svg'),size='large')


def home_hero():
    copy,visual=st.columns([1.05,1],gap='large',vertical_alignment='center')
    with copy:
        eyebrow=html.escape(msg('خيرك يوصل.'))
        title=msg('أكل زيادة؟<br><span>خلّيه يوصل.</span>')
        subtitle=html.escape(msg('شارك وجباتك الفائضة مع الجهات المحتاجة في مدينة نصر.'))
        st.markdown(f'<div class="sb-hero-copy"><div class="sb-eyebrow">{eyebrow}</div><h1>{title}</h1><p>{subtitle}</p></div>',unsafe_allow_html=True)
        if st.button('إضافة تبرع',type='primary',icon=':material/add:',width='stretch',key='home_primary_donate'):
            st.switch_page('pages/3_donate.py')
        st.page_link('pages/1_map.py',label='الخريطة',icon=':material/map:')
    with visual:
        st.image(str(ASSETS/'food-sharing.png'),width='stretch')


def section_heading(title):
    st.subheader(title)

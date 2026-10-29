"""SaveBite: gated native navigation and session-bound service authorization."""
from ui_i18n import st
st.set_page_config(page_title='SaveBite', page_icon='🍽️', layout='wide')

import auth
import database
from i18n import apply_text_direction, render_language_selector
from product_ui import apply_brand
from auth_ui import login_screen, account_page, clear_private_state


@st.cache_resource
def setup_database(schema_version=7):
    database.init_db()
    auth.init_auth_db()
    import workflow
    workflow.init_workflow_db()
    return True


setup_database()
render_language_selector()
apply_text_direction()
apply_brand(root=True)

with auth.use_session(st.session_state.get('_auth_token')):
    try:
        user = auth.current_user()
    except auth.AccessDenied:
        if st.session_state.get('_auth_token'):
            clear_private_state()
            st.rerun()
        if st.session_state.pop('_signed_out_notice', False):
            st.success('تم تغيير كلمة المرور. سجّل الدخول من جديد.')
        # Protected deep links show sign-in without a native "page not found" dialog.
        login_pages = [st.Page(login_screen, title='تسجيل الدخول', default=True)]
        login_pages += [st.Page(login_screen, title='تسجيل الدخول', url_path=path, visibility='hidden')
                        for path in ('donate', 'map', 'admin', 'account')]
        st.navigation(login_pages, position='hidden').run()
        st.stop()

    from savebite_ui import home_page
    home = st.Page(home_page, title='الرئيسية', icon=':material/home:', default=True)
    donate = st.Page('pages/3_donate.py', title='إضافة تبرع', icon=':material/add_circle:', url_path='donate')
    map_page = st.Page('pages/1_map.py', title='الخريطة', icon=':material/map:', url_path='map')
    account = st.Page(account_page, title='حسابي', icon=':material/person:', url_path='account')
    pages = [home, donate, map_page]
    if user['role'] == 'admin':
        pages.append(st.Page('pages/2_beneficiary_needs.py', title='الإدارة', icon=':material/settings:', url_path='admin'))
    else:
        from auth_ui import restricted_page
        pages.append(st.Page(restricted_page, title='الإدارة', url_path='admin', visibility='hidden'))
    pages.append(account)
    st.session_state['savebite_home_page'] = home
    page = st.navigation(pages)
    with st.sidebar:
        st.caption(user['display_name'])
        if st.button('تسجيل الخروج', icon=':material/logout:', width='stretch', key='auth_sign_out'):
            auth.logout(st.session_state.get('_auth_token'))
            clear_private_state()
            st.rerun()

    if st.session_state.pop('open_donation_page', False):
        st.session_state.pop('donation_saved_summary', None)
        st.switch_page(donate)
    st.session_state['_entry_language_active'] = True
    try:
        page.run()
    except auth.AccessDenied as error:
        st.error(str(error))
    finally:
        st.session_state.pop('_entry_language_active', None)

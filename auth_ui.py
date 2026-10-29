"""Small bilingual account screens; authorization is also checked in services."""
import sqlite3

import auth
from ui_i18n import st
from i18n import get_language


def clear_private_state():
    language = get_language()
    for key in list(st.session_state):
        del st.session_state[key]
    st.session_state['language'] = language
    st.session_state['_language_preference'] = language


def require_login():
    try:
        return auth.current_user()
    except auth.AccessDenied:
        st.info('سجّل الدخول للمتابعة.')
        st.stop()


def require_admin_page():
    user = require_login()
    if user['role'] != 'admin':
        st.error('هذه العملية متاحة للإدارة فقط.')
        st.stop()
    return user


def restricted_page():
    require_login()
    st.info('هذه العملية متاحة للإدارة فقط.')
    st.page_link(st.session_state['savebite_home_page'], label='الرجوع للرئيسية', icon=':material/home:')


def login_screen():
    st.markdown('<style>[data-testid="stMainBlockContainer"]{max-width:680px;}</style>', unsafe_allow_html=True)
    st.title('أهلًا بك في SaveBite')
    if not auth.has_admin():
        st.info('حساب الإدارة لم يتم تجهيزه بعد.')
        st.caption('صاحب المشروع: افتح SETUP_ADMIN.bat على جهاز التشغيل مرة واحدة.')
        return
    sign_in, sign_up = st.tabs(['تسجيل الدخول', 'إنشاء حساب مطعم'])
    with sign_in:
        with st.form('sign_in', clear_on_submit=True):
            username = st.text_input('اسم المستخدم', max_chars=50, key='login_username')
            password = st.text_input('كلمة المرور', type='password', max_chars=128, key='login_password')
            submit = st.form_submit_button('تسجيل الدخول', type='primary')
        if submit:
            try:
                token = auth.login(username, password)
            except ValueError as error:
                st.error(str(error))
            except sqlite3.Error:
                st.error('تعذر الاتصال بالبيانات؛ حاول مرة أخرى.')
            else:
                clear_private_state()
                st.session_state['_auth_token'] = token
                st.rerun()
        st.caption('نسيت كلمة المرور؟ تواصل مع مدير المشروع.')
    with sign_up:
        with st.form('sign_up', clear_on_submit=True):
            name = st.text_input('اسم المطعم أو المتبرع', max_chars=100)
            username = st.text_input('اسم المستخدم', max_chars=50, key='new_username')
            password = st.text_input('كلمة المرور', type='password', max_chars=128, key='new_password')
            repeat = st.text_input('تأكيد كلمة المرور', type='password', max_chars=128)
            st.caption('اسم المستخدم بالإنجليزي، وكلمة المرور 12 حرفًا على الأقل.')
            submit = st.form_submit_button('إنشاء الحساب', type='primary')
        if submit:
            if password != repeat:
                st.error('كلمتا المرور غير متطابقتين.')
            else:
                try:
                    auth.register_restaurant(username, name, password)
                except ValueError as error:
                    st.error(str(error))
                except sqlite3.Error:
                    st.error('تعذر إنشاء الحساب؛ حاول مرة أخرى.')
                else:
                    st.success('تم إنشاء الحساب. سجّل الدخول من التبويب الأول.')


def account_page():
    user = require_login()
    st.title('حسابي')
    st.write(user['display_name'])
    with st.form('change_password', clear_on_submit=True):
        old = st.text_input('كلمة المرور الحالية', type='password', max_chars=128)
        new = st.text_input('كلمة المرور الجديدة', type='password', max_chars=128)
        repeat = st.text_input('تأكيد كلمة المرور', type='password', max_chars=128)
        submit = st.form_submit_button('تغيير كلمة المرور')
    if submit:
        if new != repeat:
            st.error('كلمتا المرور غير متطابقتين.')
            return
        try:
            auth.change_password(old, new)
        except ValueError as error:
            st.error(str(error))
        else:
            clear_private_state()
            st.session_state['_signed_out_notice'] = True
            st.rerun()


def account_admin():
    require_admin_page()
    rows = auth.list_restaurants()
    if not rows:
        st.info('لا توجد حسابات مطاعم مسجلة بعد.')
        return
    by_id = {r['id']: r for r in rows}
    key = st.selectbox('حساب المطعم', list(by_id), index=None, placeholder='اختار حساب المطعم…',
        format_func=lambda key: by_id[key]['display_name']+' · '+by_id[key]['username'])
    if key is None:
        return
    row = by_id[key]
    with st.form('manage_account_'+str(key), clear_on_submit=True):
        active = st.checkbox('الحساب نشط', value=bool(row['active']))
        new = st.text_input('كلمة مرور جديدة (اختياري)', type='password', max_chars=128)
        current = st.text_input('كلمة المرور الحالية', type='password', max_chars=128)
        submit = st.form_submit_button('حفظ إعدادات الحساب')
    if submit:
        try:
            auth.confirm_password(current)
            auth.manage_restaurant(key, active=active, new_password=new or None)
        except ValueError as error:
            st.error(str(error))
        else:
            st.success('تم تحديث الحساب وتسجيل خروجه من الجلسات السابقة.')
